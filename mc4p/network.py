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
import logging

import gevent.server
import gevent.socket
from gevent import event

from mc4p import stream
from mc4p import protocol
from mc4p import util
from mc4p import dns


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
            if callable(f) and hasattr(f, "_handled_packets"):
                for packets in f._handled_packets:
                    key = _packet_handler_key(packets)
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


class Endpoint(gevent.Greenlet):
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
        self.output_stream = stream.PacketOutputStream(
            self.output_direction, version
        )

        self.input_stream.pair(self.output_stream)
        self.instance_packet_handlers = {
            k: [getattr(self, f) for f in v]
            for k, v in self.class_packet_handlers.iteritems()
        }

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
                if handler(packet):
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

    def wait_for_packet(self, packets, timeout=None):
        self.logger.debug("Waiting for %s" % packets)

        result = event.AsyncResult()

        if not hasattr(packets, "__iter__"):
            packets = (packets,)

        @self.disconnect_handler
        def async_result_packet_handler(packet_=None):
            if packet_:
                result.set(packet_)
            else:
                result.set_exception(
                    DisconnectException(self._disconnect_reason))

        for packet in packets:
            self.register_packet_handler(packet, async_result_packet_handler)

        try:
            return result.get(timeout=timeout)
        except gevent.Timeout:
            return None
        finally:
            self.unregister_disconnect_handler(async_result_packet_handler)
            for packet in packets:
                self.unregister_packet_handler(packet,
                                               async_result_packet_handler)

    def wait_for_multiple(self, packets, timeout=None, max_delay=0.2):
        packets_received = []

        while True:
            packet = self.wait_for_packet(
                packets, timeout=max_delay if packets_received else timeout
            )
            if packet:
                packets_received.append(packet)
            else:
                break

        return packets_received

    def handle_packet_error(self, error):
        return False

    def close(self, reason=None):
        if self.connected:
            if self._disconnect_reason is None:
                self._disconnect_reason = (
                    reason or "Connection closed by us")
            self.connected = False
            self.sock.close()
            self._handle_disconnect()

    def send(self, packet):
        self.debug_send_packet(packet)
        data = self.output_stream.emit(packet)

        try:
            if isinstance(data, util.CombinedMemoryView):
                for part in data.data_parts:
                    self.sock.sendall(part)
            else:
                self.sock.sendall(data)
        except gevent.socket.error as e:
            if e.errno == errno.EPIPE:
                self.close(e.message)

    def _run(self):
        while self.connected:
            try:
                self._recv()
            except EOFError:
                self.close("Connection closed")
                break
            except Exception as e:
                self.close(e.message)
                break
            gevent.sleep()

    def _recv(self):
        read_bytes = self.sock.recv_into(self.input_stream.write_buffer())
        if not read_bytes:
            raise EOFError()
        self.input_stream.added_bytes(read_bytes)
        for packet in self.input_stream.read_packets():
            try:
                self.debug_recv_packet(packet)
                if not self._call_packet_handlers(packet):
                    self.handle_packet(packet)
                gevent.sleep()
            except Exception as e:
                self.logger.exception(
                    'Exception occured while handling packet {}'.format(packet))
                if not self.handle_packet_error(e):
                    raise

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


class Server(gevent.server.StreamServer):
    def __init__(self, addr, handler=ClientHandler):
        super(Server, self).__init__(addr)
        self.handler = handler
        self.logger = logging.getLogger("network.server")
        self.logger.info("Listening on %s:%d" % addr)

    def handle(self, sock, addr):
        self.logger.info("Incoming connection from %s:%d" % addr)
        handler = self.handler(sock, addr, self)
        handler.run()

    def run(self):
        try:
            super(Server, self).serve_forever()
        except gevent.socket.error, e:
            self.logger.error(e)


class Client(Endpoint):
    def __init__(self, addr, version=None):
        if version is None:
            version = protocol.MAX_PROTOCOL_VERSION

        self.logger = logging.getLogger("network.client")

        self.original_addr = addr
        self.addr = dns.resolve(*addr)

        if self.addr is None:
            raise Exception("Could not resolve hostname")

        self.logger.debug("Connecting")
        sock = gevent.socket.create_connection(self.addr)
        self.logger.info("Connected")

        super(Client, self).__init__(
            sock, protocol.Direction.client_bound, version
        )
