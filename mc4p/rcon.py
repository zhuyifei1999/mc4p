# -*- coding: utf-8 -*-

# This source file is part of mc4p,
# the Minecraft Portable Protocol-Parsing Proxy.

# This program is free software. It comes without any warranty, to
# the extent permitted by applicable law. You can redistribute it
# and/or modify it under the terms of the Do What The Fuck You Want
# To Public License, Version 2, as published by Sam Hocevar. See
# http://www.wtfpl.net/txt/copying/ for more details

from __future__ import absolute_import, unicode_literals

import errno
import logging
import struct

import gevent.lock
import gevent.select
import gevent.socket

from mc4p import dns

logger = logging.getLogger('rcon')


class Rcon(object):
    def __init__(self, addr, password):
        self.addr = dns.resolve(*addr)
        self.password = password
        self.sock = None
        self._send_lock = gevent.lock.BoundedSemaphore()

        if self.addr is None:
            raise RuntimeError("Could not resolve hostname")

    def reconnect(self):
        if self.sock is not None:
            self.sock.close()

        self.sock = gevent.socket.create_connection(self.addr)
        self._login()

    def execute(self, cmd):
        logger.info('Executing Rcon command: %s', cmd)
        if self.sock is None or self.sock.closed:
            logger.warn('Rcon not connected, reconnecting')
            self.reconnect()
        try:
            return self._send(2, cmd)
        except gevent.socket.error as e:
            if e.errno == errno.EPIPE:
                logger.warn('Server closed Rcon connection, reconnecting')
                self.reconnect()
                return self._send(2, cmd)
            else:
                raise

    def _login(self):
        logger.info('Logging in to Rcon')
        self._send(3, self.password)

    def _send(self, out_typ, out_data, max_wait=0):
        # Adapted somewhat from MCRcon code
        with self._send_lock:
            out_payload = struct.pack('<ii', 0, out_typ)
            out_payload += out_data.encode('utf8') + b'\x00\x00'
            out_length = struct.pack('<i', len(out_payload))
            self.sock.send(out_length + out_payload)

            in_data = ''

            while True:
                in_length = self.sock.recv(4)
                if not in_length:
                    logger.info('Server closed Rcon connection')
                    break  # EOF/EOT

                in_length, = struct.unpack('<i', in_length)
                in_payload = self.sock.recv(in_length)
                in_id, in_type = struct.unpack('<ii', in_payload[:8])

                assert in_payload[-2:] == b'\x00\x00', 'Incorrect padding'

                if in_id == -1:
                    raise RuntimeError('Rcon Login failed')

                in_data += in_payload[8:-2].decode('utf8')

                if not gevent.select.select([self.sock], [], [], max_wait)[0]:
                    break

            return in_data
