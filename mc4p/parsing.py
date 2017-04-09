# -*- coding: utf-8 -*-

# This source file is part of mc4p,
# the Minecraft Portable Protocol-Parsing Proxy.

# This program is free software. It comes without any warranty, to
# the extent permitted by applicable law. You can redistribute it
# and/or modify it under the terms of the Do What The Fuck You Want
# To Public License, Version 2, as published by Sam Hocevar. See
# http://www.wtfpl.net/txt/copying/ for more details

from __future__ import division, absolute_import, unicode_literals

import collections
import json
import logging
import struct
import uuid

from mc4p import util


logger = logging.getLogger("parsing")


class Field(object):
    _NEXT_ID = 1

    def __init__(self):
        self._order_id = Field._NEXT_ID
        Field._NEXT_ID += 1

    @classmethod
    def parse(cls, data, parent):
        return None

    @classmethod
    def prepare(cls, data, parent):
        """Used to set stray length fields"""
        pass

    @classmethod
    def emit(cls, value, parent):
        return b""

    def format(self, value):
        return str(value)

    def __str__(self):
        return self.__class__.__name__


class Empty(Field):
    @classmethod
    def parse(cls, data, parent=None):
        return None

    @classmethod
    def emit(cls, value, parent=None):
        return b''

    @classmethod
    def format(cls, value):
        return ''


def simple_type_field(name, format):
    format = b">" + format
    length = struct.calcsize(format)

    class SimpleType(Field):
        @classmethod
        def parse(cls, data, parent=None):
            return struct.unpack(format, data.read_bytes(length))[0]

        @classmethod
        def emit(cls, value, parent=None):
            return struct.pack(format, value)

    SimpleType.__name__ = name
    return SimpleType


Byte = simple_type_field(b"Byte", b"b")
Short = simple_type_field(b"Short", b"h")
Int = simple_type_field(b"Int", b"i")
Long = simple_type_field(b"Long", b"q")
Float = simple_type_field(b"Float", b"f")
Double = simple_type_field(b"Double", b"d")

UnsignedByte = simple_type_field(b"UnsignedByte", b"B")
UnsignedShort = simple_type_field(b"UnsignedShort", b"H")
UnsignedInt = simple_type_field(b"UnsignedInt", b"I")
UnsignedLong = simple_type_field(b"UnsignedLong", b"Q")


class Bool(Field):
    @classmethod
    def parse(cls, data, parent=None):
        return struct.unpack(b"b", data.read_bytes(1))[0] != 0

    @classmethod
    def emit(cls, value, parent=None):
        return struct.pack(b"b", 1 if value else 0)


class VarInt(Field):
    @classmethod
    def parse(cls, data, parent=None):
        value = 0
        for i in range(5):
            # ord() is about 3x as fast as struct.unpack() for single bytes
            b = ord(data.read_bytes(1).tobytes())
            value |= (b & 0x7F) << 7 * i
            if not b & 0x80:
                return value
        raise IOError("Encountered varint longer than 5 bytes")

    @classmethod
    def emit(cls, value, parent=None):
        return b"".join(
            struct.pack(
                b">B",
                (value >> i * 7) & 0x7f | (value >> (i + 1) * 7 > 0) << 7
            )
            for i in range(((value.bit_length() - 1) // 7 + 1) or 1)
        )


class String(Field):
    @classmethod
    def parse(cls, data, parent=None):
        return unicode(data.read_bytes(VarInt.parse(data)).tobytes(),
                       encoding="utf-8")

    @classmethod
    def emit(cls, value, parent=None):
        return VarInt.emit(len(value)) + value.encode("utf-8")

    def format(self, value):
        return value


class Json(Field):
    @classmethod
    def parse(cls, data, parent=None):
        return json.loads(String.parse(data, parent))

    @classmethod
    def emit(cls, value, parent=None):
        return String.emit(json.dumps(value), parent)


class Chat(Json):
    @classmethod
    def format(cls, value):
        return util.parse_chat(value)


class UUID(Field):
    @classmethod
    def parse(cls, data, parent=None):
        return uuid.UUID(bytes=data.read_bytes(16).tobytes())

    @classmethod
    def emit(cls, value, parent=None):
        return value.bytes

    @classmethod
    def format(cls, value):
        return str(value)


class Position(Field):
    @classmethod
    def parse(cls, data, parent=None):
        value = struct.unpack(">Q", data.read_bytes(8))[0]

        x = value >> 38
        if x & 0x2000000:
            x = x - 0x4000000

        y = value >> 26 & 0xfff
        if y & 0x800:
            y = y - 0x4000000

        z = value & 0x4fff
        if z & 0x2000000:
            z = z - 0x4000000

        return (x, y, z)

    @classmethod
    def emit(cls, value, parent=None):
        x, y, z = value
        value = ((x & 0x3ffffff) << 38) | ((y & 0xfff) << 26) | (z & 0x3ffffff)
        return struct.pack(">Q", value)[0]

    @classmethod
    def format(cls, value):
        return "x: %d y: %d z: %d" % value


class Data(Field):
    def __init__(self, size=None):
        super(Data, self).__init__()
        self._size = size

    def parse(self, data, parent=None):
        if self._size is None:
            length = None
        else:
            length = self._size.parse(data, parent)
        return data.read_bytes(length)

    def emit(self, value, parent=None):
        if self._size is None:
            return value
        else:
            return util.combine_memoryview(
                self._size.emit(len(value), parent),
                value
            )

    def format(self, value):
        if value is None:
            return "None"
        if len(value) < 100:
            try:
                value = value.tobytes()
            except AttributeError:
                pass
            return "<Data: %s>" % " ".join("%02x" % ord(c) for c in value)
        else:
            return "<Data: %d bytes>" % len(value)


class _SubStructure(object):
    def __init__(self, parent, type):
        self._parent = parent
        self._type = type
        self._is_ready = False

    def _ready(self):
        self._is_ready = True

    def _make_dirty(self):
        if self._parent is not None and self._is_ready:
            self._parent._make_dirty()

    def __setattr__(self, attr, value):
        super(_SubStructure, self).__setattr__(attr, value)
        if attr[0] != "_":
            self._make_dirty()

    def __setitem__(self, key, value):
        super(_SubStructure, self).__setitem__(key, value)
        self._make_dirty()


class _Array(_SubStructure, list):
    def __init__(self, size, parent, type):
        _SubStructure.__init__(self, parent, type)
        list.__init__(self)
        self._size = size


class Array(Field):
    def __init__(self, size, item):
        super(Array, self).__init__()
        self._size = size
        self._item = item

    def parse(self, data, parent=None):
        size = self._size.parse(data, parent)
        arr = _Array(size, parent, self)
        for i in range(size):
            arr.append(self._item.parse(data, arr))
        arr._ready()
        return arr

    def emit(self, value, parent=None):
        return util.combine_memoryview(
            self._size.emit(len(value), parent),
            *(self._item.emit(val, value) for val in value)
        )

    def format(self, value):
        if value is None:
            return "None"
        if len(value) < 10:
            return "<Array: %s>" % ", ".join(
                self._item.format(val) for val in value)
        else:
            return "<Array: %d items>" % len(value)


class _SubFields(_SubStructure):
    pass


class SubFields(Field):
    def __init__(self, **kwargs):
        super(SubFields, self).__init__()
        self.subfields = collections.OrderedDict(sorted(
            ((name, field) for name, field in kwargs.iteritems()),
            key=lambda i: i[1]._order_id
        ))

    def new_dummy(self, parent):
        subfields = _SubFields(parent, self)
        for key, val in self.subfields.iteritems():
            setattr(subfields, key, None)
        subfields._ready()
        return subfields

    def parse(self, data, parent=None):
        subfields = _SubFields(parent, self)
        for key, val in self.subfields.iteritems():
            setattr(subfields, key, val.parse(data, subfields))
        subfields._ready()
        return subfields

    def emit(self, value, parent=None):
        return util.combine_memoryview(
            *(val.emit(getattr(value, key), value)
              for key, val in self.subfields.iteritems())
        )

    def format(self, value):
        if value is None:
            return "None"
        return "<SubFields: %s>" % ", ".join(
            '{}: {}'.format(key, val.format(getattr(value, key)))
            for key, val in self.subfields.iteritems())


class Switch(Field):
    def __init__(self, cond, valdict):
        super(Switch, self).__init__()
        self.cond = cond
        self.valdict = valdict

    def parse(self, data, parent=None):
        return self.valdict[self.cond(parent)].parse(data, parent)

    def emit(self, value, parent=None):
        return self.valdict[self.cond(parent)].emit(value, parent)

    def format(self, value):
        if value is None:
            return "None"
        return "<Switch: %s>" % repr(value)


class Optional(Field):
    def __init__(self, cond, val):
        super(Optional, self).__init__()
        self.cond = cond
        self.val = val

    def parse(self, data, parent=None):
        if self.cond(parent):
            return self.val.parse(data, parent)
        else:
            return None

    def emit(self, value, parent=None):
        if self.cond(parent):
            return self.val.emit(value, parent)
        else:
            return b''

    def format(self, value):
        return "<Optional: %s>" % repr(value)
