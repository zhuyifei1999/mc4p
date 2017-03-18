# -*- coding: utf-8 -*-

# This source file is part of mc4p,
# the Minecraft Portable Protocol-Parsing Proxy.

# This program is free software. It comes without any warranty, to
# the extent permitted by applicable law. You can redistribute it
# and/or modify it under the terms of the Do What The Fuck You Want
# To Public License, Version 2, as published by Sam Hocevar. See
# http://www.wtfpl.net/txt/copying/ for more details

from __future__ import absolute_import, unicode_literals

from mc4p import plugin


class GamemodePlugin(
    plugin.RequireUsernamePlugin,
    plugin.CommandPlugin,
    plugin.RconPlugin
):
    def __init__(self, allowed_gamemodes=()):
        self.allowed_gamemodes = allowed_gamemodes

    @plugin.Plugin.packet_handler(plugin.SERVER_PROTOCOL.play.ChatMessage)
    def skin_command(self, conn, packet):
        if packet.message.startswith('!gm '):
            target = packet.message[len('!gm '):]
            try:
                target = int(target)
            except ValueError:
                return self.command_error(
                    conn.proxy, '!gm: Invalid numeric gamemode')

            if self.allowed_gamemodes and target not in self.allowed_gamemodes:
                return self.command_error(
                    conn.proxy, '!gm: Unaccepted Gamemode')

            ret = self.execute_rcon(conn.proxy, 'gamemode {} {}'.format(
                target, conn.proxy.username))
            if not ret:
                return self.command_success(conn.proxy, '!gm: Executed')
            else:
                return self.command_status(
                    conn.proxy, '!gm: {}'.format(ret))


def load_plugin(*args):
    return GamemodePlugin(map(int, args))
