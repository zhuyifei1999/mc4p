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

from mc4p import protocol
from mc4p import encryption

logger = logging.getLogger("stream")

BUFFER_SIZE = 1 << 16


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


class BufferedPacketInputStream(PacketStream):
    def __init__(self, direction, version=0):
        super(BufferedPacketInputStream, self).__init__(direction, version)
        self.buf = memoryview(bytearray(BUFFER_SIZE))
        self.write_pos = 0
        self.read_pos = 0
        # when read_pos == write_pos, indicate if buffer is full or empty
        self.full = False

    def enable_encryption(self, shared_secret):
        assert self.read_pos == self.write_pos
        super(BufferedPacketInputStream, self).enable_encryption(shared_secret)
        self.buf = memoryview(bytearray(BUFFER_SIZE))

    def recv_from(self, sock):
        if self.read_pos > self.write_pos:
            part = self.buf[self.write_pos:self.read_pos]
        else:
            part = self.buf[self.write_pos:]

        if self.read_pos == self.write_pos and self.full:
            raise IOError("Buffer overflow")

        n = sock.recv_into(part)

        if self._cipher is not None:
            part[:] = self._cipher.decrypt(part.tobytes())

        self.write_pos += n
        self.full = self.read_pos == self.write_pos

        assert self.write_pos <= BUFFER_SIZE
        if self.write_pos == BUFFER_SIZE:
            self.write_pos = 0

        return n

    def bytes_used(self):
        if self.write_pos > self.read_pos:
            return self.write_pos - self.read_pos
        elif self.read_pos == self.write_pos:
            return BUFFER_SIZE if self.full else 0
        else:
            return BUFFER_SIZE - self.read_pos + self.write_pos

    def read_packet(self):
        last_boundary = self.read_pos
        try:
            length = self.read_varint()
            if self.compression_threshold is not None:
                uncompressed_length, varint_length = self.read_varint(True)
                length -= varint_length
            else:
                uncompressed_length = 0

            data = self.get_data(length)
            if uncompressed_length > 0:
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

    def read_varint(self, return_length=False):
        value = 0
        bytes_used = self.bytes_used()
        for i in range(5):
            if i >= bytes_used:
                raise PartialPacketException()
            b = ord(self.buf[self.read_pos])
            self.read_pos = (self.read_pos + 1) % BUFFER_SIZE
            value |= (b & 0x7F) << 7 * i
            if not b & 0x80:
                if return_length:
                    return value, i + 1
                else:
                    return value
        raise IOError("Encountered varint longer than 5 bytes")

    def get_data(self, n):
        if n > self.bytes_used():
            raise PartialPacketException()
        view = BufferView(self.buf, self.read_pos, n)
        self.read_pos = (self.read_pos + n) % BUFFER_SIZE
        return view


class PacketOutputStream(PacketStream):
    def __init__(self, direction, version=0):
        super(PacketOutputStream, self).__init__(direction, version)
        self.encrypted = False
        self.compressed = False

    def emit(self, packet):
        data = packet._emit(self.compression_threshold)

        new_context = self.context.handle_packet(packet, self)
        if new_context:
            self.change_context(new_context)

        if self._cipher is not None:
            data = self._cipher.encrypt(data.tobytes())

        return data


class PartialPacketException(Exception):
    pass


class BufferView(protocol.PacketData):
    def __init__(self, bfr, offset, length):
        data = bfr[offset:offset + length]
        if offset + length > BUFFER_SIZE:
            logger.debug("Rewinding buffer")
            limit = offset + length - BUFFER_SIZE
            data = data.tobytes() + bfr[:limit].tobytes()
        super(BufferView, self).__init__(data)


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
