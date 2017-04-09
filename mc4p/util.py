# -*- coding: utf-8 -*-

# This source file is part of mc4p,
# the Minecraft Portable Protocol-Parsing Proxy.

# This program is free software. It comes without any warranty, to
# the extent permitted by applicable law. You can redistribute it
# and/or modify it under the terms of the Do What The Fuck You Want
# To Public License, Version 2, as published by Sam Hocevar. See
# http://www.wtfpl.net/txt/copying/ for more details

from __future__ import absolute_import, unicode_literals

import re


class StringEnum(object):
    """

    """
    def __init__(self, *values):
        self._values = values
        for value in values:
            setattr(self, value, value)

    def __getitem__(self, index):
        return self._values[index]

    def index(self, value):
        return self._values.index(value)

    def __iter__(self):
        return iter(self._values)


def combine_memoryview(*data_parts):
    return b"".join(
        part.tobytes() if isinstance(part, memoryview) else part
        for part in data_parts
    )


COLOR_PATTERN = re.compile("§.")


def parse_chat(chat_obj):
    """
    Reduces a JSON chat object to a string with all formatting removed.
    """
    if isinstance(chat_obj, basestring):
        return strip_color(chat_obj)
    elif not isinstance(chat_obj, dict):
        return ""

    if isinstance(chat_obj.get('text'), basestring):
        text = chat_obj['text']
    elif isinstance(chat_obj.get('translate'), basestring):
        if "with" in chat_obj and isinstance(chat_obj['with'], list):
            args = ", ".join(
                arg for arg in chat_obj['with'] if isinstance(arg, basestring)
            )
        else:
            args = ""
        text = "<%s(%s)>" % (chat_obj['translate'], args)
    elif isinstance(chat_obj.get('selector'), basestring):
        text = chat_obj['selector']
    else:
        text = ""

    if isinstance(chat_obj.get('extra'), list):
        text += "".join(parse_chat(extra) for extra in chat_obj['extra'])

    return strip_color(text)


def strip_color(string):
    return COLOR_PATTERN.sub("", string)
