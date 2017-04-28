# -*- coding: utf-8 -*-

# This source file is part of mc4p,
# the Minecraft Portable Protocol-Parsing Proxy.

# This program is free software. It comes without any warranty, to
# the extent permitted by applicable law. You can redistribute it
# and/or modify it under the terms of the Do What The Fuck You Want
# To Public License, Version 2, as published by Sam Hocevar. See
# http://www.wtfpl.net/txt/copying/ for more details

from __future__ import absolute_import, unicode_literals

import collections
import errno
import gc
import logging

import threading

try:
    import socketserver
except ImportError:  # PY2
    import SocketServer as socketserver

import socket

from mc4p import stream
from mc4p import protocol


def _packet_handler_key(packet):
    return (packet._state, packet._name)


class _MetaEndpoint(type):
    def __init__(cls, name, bases, nmspc):
        super(_MetaEndpoint, cls).__init__(name, bases, nmspc)

        if hasattr(cls, 'class_packet_handlers'):
            handlers = cls.class_packet_handlers.copy()
        else:
            handlers = collections.defaultdict(set)

        for fname, f in cls.__dict__.iteritems():
            if not (callable(f) and hasattr(f, "_handled_packets")):
                continue

            for packet in f._handled_packets:
                key = _packet_handler_key(packet)
                handlers[key].add(fname)

        cls.class_packet_handlers = handlers

    @staticmethod
    def packet_handler(packet):
        def packet_handler_wrapper(f):
            if not hasattr(f, "_handled_packets"):
                f._handled_packets = []
            f._handled_packets.append(packet)
            return f

        return packet_handler_wrapper


class Endpoint(threading.Thread):
    __metaclass__ = _MetaEndpoint

    def __init__(self, sock, incoming_direction, version=0):
        super(Endpoint, self).__init__()
        self.sock = sock

        self.logger = logging.getLogger("network.endpoint")

        self.packet_handler = self._instance_packet_handler

        self.input_direction = incoming_direction
        self.input_stream = stream.BufferedPacketInputStream(
            self.input_direction, version
        )

        self.output_direction = protocol.Direction.opposite_direction(
            incoming_direction
        )
        self.output_stream = stream.BufferedPacketOutputStream(
            self.output_direction, version
        )

        self.input_stream.pair(self.output_stream)
        self.instance_packet_handlers = {
            k: [getattr(self.__class__, f) for f in v]
            for k, v in self.class_packet_handlers.iteritems()
        }

        self._send_lock = threading.Lock()

        self.disconnect_handlers = []
        self._disconnect_reason = None
        self.connected = True
        self.init()

    @property
    def input_protocol(self):
        return self.input_stream.protocol.directions[self.input_direction]

    @property
    def output_protocol(self):
        return self.output_stream.protocol.directions[self.output_direction]

    def init(self):
        pass

    def _handle_disconnect(self):
        for handler in self.disconnect_handlers:
            handler()
        self.handle_disconnect()

    def handle_disconnect(self):
        self.logger.info("Disconnect: %s" % self._disconnect_reason)

    def disconnect_handler(self, f):
        self.register_disconnect_handler(f)
        return f

    def register_disconnect_handler(self, f):
        self.disconnect_handlers.append(f)

    def unregister_disconnect_handler(self, f):
        self.disconnect_handlers.remove(f)

    def handle_packet(self, packet):
        return False

    def _call_packet_handlers(self, packet):
        key = _packet_handler_key(packet)
        handlers = self.instance_packet_handlers.get(key)
        if handlers:
            # handlers might unregister themselves, so we need to copy the list
            for handler in tuple(handlers):
                if handler(self, packet):
                    return True

    def _instance_packet_handler(self, packet):
        def packet_handler_wrapper(f):
            self.register_packet_handler(packet, f)
            return f
        return packet_handler_wrapper

    def register_packet_handler(self, packet, f):
        key = _packet_handler_key(packet)
        self.instance_packet_handlers.setdefault(key, []).append(f)

    def unregister_packet_handler(self, packet, f):
        key = _packet_handler_key(packet)
        self.instance_packet_handlers[key].remove(f)

    def handle_packet_error(self, error):
        return False

    def close(self, reason=None):
        if self.connected:
            if self._disconnect_reason is None:
                self._disconnect_reason = (
                    reason or "Connection closed by proxy layer")
            self.connected = False

            try:
                if self.output_direction == protocol.Direction.client_bound:
                    self.send(
                        getattr(self.output_stream.context, 'Disconnect')(
                            reason=self._disconnect_reason
                        ))
                self.flush()
            except Exception:
                pass

            self.sock.close()
            self._handle_disconnect()

    def send(self, packet):
        try:
            with self._send_lock:
                self.debug_send_packet(packet)

                if packet._direction not in (None, self.output_direction):
                    self.logger.warn(
                        'Packet %s direction mismatch! Expected: %s Got: %s',
                        packet, self.output_direction, packet._direction)

                self.output_stream.send(self.sock, packet)
        except socket.error as e:
            if e.errno == errno.EPIPE:
                self.close(str(e))
            else:
                raise

    def run(self):
        while self.connected:
            try:
                self.recv()
            except EOFError:
                self.close("Connection closed")
                break
            except Exception as e:
                if self.connected:
                    self.logger.exception(e)
                self.close(str(e))
                break

    def recv(self):
        self.flush()
        read_bytes = self.input_stream.recv_from(self.sock)
        if not read_bytes:
            raise EOFError()
        for packet in self.input_stream.read_packets():
            try:
                self.debug_recv_packet(packet)
                if not self._call_packet_handlers(packet):
                    self.handle_packet(packet)
            except Exception as e:
                self.logger.exception(
                    'Exception occured while handling packet %s' % packet)
                if not self.handle_packet_error(e):
                    raise

    def flush(self):
        self.output_stream.flush(self.sock)

    def debug_send_packet(self, packet):
        pass

    def debug_recv_packet(self, packet):
        pass


class DisconnectException(Exception):
    def __init__(self, message=None):
        if isinstance(message, unicode):
            message = message.encode("utf8")

        super(DisconnectException, self).__init__(message)


class ClientHandler(Endpoint):
    def __init__(self, sock, addr, server, version=0):
        self.addr = addr
        self.server = server
        super(ClientHandler, self).__init__(
            sock, protocol.Direction.server_bound, version
        )


class Server(socketserver.ForkingTCPServer, object):
    def __init__(self, addr, handler=ClientHandler):
        super(Server, self).__init__(addr, handler)
        self.logger = logging.getLogger("network.server")
        self.logger.info("Listening on %s:%d" % addr)

    def finish_request(self, sock, addr):
        self.logger.info(
            "Incoming connection from host %s port %d" % addr[:2])
        self.RequestHandlerClass(sock, addr, self).run()
        self.logger.info(
            "Garbage collected %d objects", gc.collect())

    def run(self):
        try:
            self.serve_forever()
        except socket.error, e:
            self.logger.error(e)


class Client(Endpoint):
    def __init__(self, addr, version=None):
        if version is None:
            version = protocol.MAX_PROTOCOL_VERSION

        self.logger = logging.getLogger("network.client")

        self.addr = addr

        self.logger.debug("Connecting")
        sock = socket.create_connection(self.addr)
        self.logger.info("Connected")

        super(Client, self).__init__(
            sock, protocol.Direction.client_bound, version
        )
