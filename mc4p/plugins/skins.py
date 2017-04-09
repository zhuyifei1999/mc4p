# -*- coding: utf-8 -*-

# This source file is part of mc4p,
# the Minecraft Portable Protocol-Parsing Proxy.

# This program is free software. It comes without any warranty, to
# the extent permitted by applicable law. You can redistribute it
# and/or modify it under the terms of the Do What The Fuck You Want
# To Public License, Version 2, as published by Sam Hocevar. See
# http://www.wtfpl.net/txt/copying/ for more details

from __future__ import absolute_import, unicode_literals

import json
import logging

import threading
import requests

from mc4p import plugin


logger = logging.getLogger('plugin.skins')


def load_skin(user):
    logger.info('Loading skin for {}'.format(user))
    uuid = requests.post('https://api.mojang.com/profiles/minecraft',
                         data=json.dumps([user]),
                         headers={'Content-type': 'application/json',
                                  'Accept': 'application/json'}).json()
    if not uuid:
        return None

    uuid = uuid[0]['id']
    profile = requests.get(
        'https://sessionserver.mojang.com/session/minecraft/'
        'profile/' + uuid + '?unsigned=false').json()
    properties = profile['properties']
    for user_property in properties:
        if user_property['name'] == 'textures':
            textures = user_property
            break
    else:
        raise AssertionError

    return textures['value'], textures.get('signature')


class SkinsPlugin(
    plugin.RequireUsernamePlugin,
    plugin.DBMPlugin,
    plugin.CommandPlugin
):
    @staticmethod
    def dbmkey(username, typ='skins'):
        return 'skins:{}:{}'.format(typ, username).encode('utf-8')

    def username_loaded(self, proxy):
        username = self.dbm.get(self.dbmkey(proxy.username, 'usernames'))
        username = username.decode('utf-8') if username else proxy.username
        skinkey = self.dbmkey(proxy.username)
        if skinkey not in self.dbm:
            self.dbm[skinkey] = json.dumps(load_skin(username))

    @plugin.Plugin.packet_handler(plugin.CLIENT_PROTOCOL.play.PlayerListItem)
    def set_skin(self, conn, packet):
        if packet.action == 0:
            for player in packet.players:
                skinkey = self.dbmkey(player.data.name)
                if skinkey in self.dbm:
                    skindata = json.loads(self.dbm[skinkey])
                    if skindata is None:
                        continue

                    value, signature = skindata
                    for user_property in player.data.properties:
                        if user_property.name == 'textures':
                            user_property.value = value
                            user_property.is_signed = bool(signature)
                            user_property.signature = signature
                            break
                    else:
                        user_property = player.data.properties._type._item
                        user_property = user_property.new_dummy(
                            player.data.properties)
                        user_property.name = 'textures'
                        user_property.value = value
                        user_property.is_signed = bool(signature)
                        user_property.signature = signature
                        player.data.properties.append(user_property)

    @plugin.Plugin.packet_handler(plugin.SERVER_PROTOCOL.play.ChatMessage)
    def skin_command(self, conn, packet):
        mapkey = self.dbmkey(conn.proxy.username, 'usernames')
        if packet.message.startswith('!skin '):
            target = packet.message[len('!skin '):]
            if ' ' in target or len(target) > 16:
                return self.command_error(
                    conn.proxy, '!skin: Not accepting this username')
            if target == conn.proxy.username:
                try:
                    del self.dbm[mapkey]
                except KeyError:
                    pass
            else:
                self.dbm[mapkey] = target.encode('utf-8')

            def async_load_skin():
                self.command_status(
                    conn.proxy, '!skin: Loading skin for %s' % target)
                skinkey = self.dbmkey(conn.proxy.username)
                self.dbm[skinkey] = json.dumps(load_skin(target))
                self.command_success(
                    conn.proxy, '!skin: Skin has been set to %s' % target)

            threading.Thread(target=async_load_skin).start()
            return True


def load_plugin():
    return SkinsPlugin()
