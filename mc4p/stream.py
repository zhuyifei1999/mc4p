# -*- coding: utf-8 -*-

# This source file is part of mc4p,
# the Minecraft Portable Protocol-Parsing Proxy.

# This program is free software. It comes without any warranty, to
# the extent permitted by applicable law. You can redistribute it
# and/or modify it under the terms of the Do What The Fuck You Want
# To Public License, Version 2, as published by Sam Hocevar. See
# http://www.wtfpl.net/txt/copying/ for more details

from __future__ import (
    division, absolute_import, print_function, unicode_literals)

import logging
import zlib

import gevent.lock

from mc4p import protocol
from mc4p import encryption

logger = logging.getLogger("stream")

BUFFER_SIZE = 1 << 20


class PacketStream(object):
    def __init__(self, direction, version=0):
        self.protocol = protocol.get_protocol_version(version)
        self.context = self.protocol.directions[direction].handshake
        self.partner = None
        self._cipher = None
        self._compression_threshold = None

    @property
    def compression_threshold(self):
        return self._compression_threshold

    @compression_threshold.setter
    def compression_threshold(self, threshold):
        self._compression_threshold = threshold
        if self.partner:
            self.partner._compression_threshold = threshold

    def pair(self, stream):
        self.partner = stream
        stream.partner = self

    def change_context(self, context):
        self.context = context
        self.protocol = context.protocol
        logger.debug("Switching state to %s" % context)
        if self.partner:
            self.partner.context = context.protocol.directions[
                self.partner.context.direction
            ].states[context.state]
            self.partner.protocol = context.protocol

    def enable_encryption(self, shared_secret):
        self._cipher = encryption.AES128CFB8(shared_secret)


class BufferedPacketStream(PacketStream):
    def __init__(self, direction, version=0):
        super(BufferedPacketStream, self).__init__(direction, version)
        self.buf = memoryview(bytearray(BUFFER_SIZE))
        self.write_pos = 0
        self.read_pos = 0
        # when read_pos == write_pos, indicate if buffer is full or empty
        self.full = False
        self._lock = gevent.lock.BoundedSemaphore()

    def enable_encryption(self, shared_secret):
        assert not self.bytes_used
        super(BufferedPacketStream, self).enable_encryption(shared_secret)
        self.buf = memoryview(bytearray(BUFFER_SIZE))

    def _write(self, buf_func):
        if self.read_pos > self.write_pos:
            part = self.buf[self.write_pos:self.read_pos]
        else:
            part = self.buf[self.write_pos:]

        if self.full:
            raise IOError("Buffer overflow")

        n = buf_func(part)
        if n:
            logger.debug('recv {} bytes'.format(n))

            if self._cipher is not None:
                part[:n] = self._cipher.decrypt(part[:n].tobytes())

            self.write_pos = (self.write_pos + n) % BUFFER_SIZE
            self.full = self.read_pos == self.write_pos

        return n

    @property
    def bytes_used(self):
        if self.write_pos > self.read_pos:
            return self.write_pos - self.read_pos
        elif self.read_pos == self.write_pos:
            return BUFFER_SIZE if self.full else 0
        else:
            return BUFFER_SIZE - self.read_pos + self.write_pos

    @property
    def bytes_avail(self):
        return BUFFER_SIZE - self.bytes_used

    def _read(self, n=None):
        if n is None:
            n = self.bytes_used
        elif n > self.bytes_used:
            raise PartialPacketException
        data = self.buf[self.read_pos:self.read_pos + n]
        self.read_pos += n
        if self.read_pos > BUFFER_SIZE:
            logger.debug("Rewinding buffer")
            self.read_pos -= BUFFER_SIZE
            data = data.tobytes() + self.buf[:self.read_pos].tobytes()
        return data


class BufferedPacketInputStream(BufferedPacketStream):
    def recv_from(self, sock):
        with self._lock:
            return self._write(sock.recv_into)

    def read_packet(self):
        with self._lock:
            last_boundary = self.read_pos
            try:
                length = self._read_varint()
                if self.compression_threshold is not None:
                    uncompressed_length, varint_length = self._read_varint(True)
                    length -= varint_length
                else:
                    uncompressed_length = 0

                data = BufferView(self._read(length))
                if uncompressed_length:
                    data = CompressedData(data, uncompressed_length)
            except PartialPacketException:
                self.read_pos = last_boundary
                raise
            else:
                packet = self.context.read_packet(data)
                new_context = self.context.handle_packet(packet, self)
                if new_context:
                    self.change_context(new_context)

                self.full = False
                return packet

    def read_packets(self):
        try:
            while True:
                yield self.read_packet()
        except PartialPacketException:
            pass

    def _read_varint(self, return_length=False):
        value = 0
        for i in range(5):
            b = ord(self._read(1)[0])
            value |= (b & 0x7F) << 7 * i
            if not b & 0x80:
                if return_length:
                    return value, i + 1
                else:
                    return value
        raise IOError("Encountered varint longer than 5 bytes")


class PacketOutputStream(PacketStream):
    def _emit(self, packet):
        data = packet._emit(self.compression_threshold)

        new_context = self.context.handle_packet(packet, self)
        if new_context:
            self.change_context(new_context)

        if self._cipher is not None:
            data = self._cipher.encrypt(data.tobytes())

        return data

    def send(self, sock, packet):
        sock.sendall(self._emit(packet))

    def flush(self, sock):
        pass


class BufferedPacketOutputStream(PacketOutputStream, BufferedPacketStream):
    def send(self, sock, packet):
        data = self._emit(packet)
        if len(data) > self.bytes_avail:
            self.flush(sock)

        if len(data) > self.bytes_avail:
            raise IOError("Buffer overflow")

        with self._lock:
            data = [data]  # Python variable scoping
            while data[0]:
                def buf_func(buf):
                    n = min(len(data[0]), len(buf))
                    buf[:n] = data[0][:n]
                    data[0] = data[0][n:]
                    return n
                self._write(buf_func)

    def flush(self, sock):
        with self._lock:
            data = self._read()
            if data:
                logger.debug('real send {} bytes'.format(len(data)))
                sock.sendall(data)


class PartialPacketException(Exception):
    pass


class BufferView(protocol.PacketData):
    pass


class CompressedData(protocol.PacketData):
    CHUNK_SIZE = 128

    def __init__(self, data, uncompressed_length):
        self.data = data
        self.length = uncompressed_length

        self.read_pos = 0
        self.decompressed_data = b''
        self.decompress_object = zlib.decompressobj()

    def decompress(self, length):
        while length + self.read_pos > len(self.decompressed_data):
            limit = min(self.CHUNK_SIZE,
                        len(self.data) - self.data.read_pos)

            if limit <= 0:
                raise IOError("Buffer underflow")

            chunk = self.data.read_bytes(limit).tobytes()

            if self.decompress_object.unconsumed_tail:
                chunk = self.decompress_object.unconsumed_tail + chunk

            self.decompressed_data += self.decompress_object.decompress(chunk)

    def read(self):
        self.decompress(self.length - self.read_pos)
        return memoryview(self.decompressed_data)

    def read_bytes(self, n=None):
        if n is None:
            n = self.length - self.read_pos
        elif self.length < self.read_pos + n:
            raise IOError("Buffer underflow")
        self.decompress(n)
        original_position = self.read_pos
        self.read_pos += n
        return memoryview(self.decompressed_data)[
            original_position:self.read_pos]

    def read_compressed(self):
        return self.data.read()
