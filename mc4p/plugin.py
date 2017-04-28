# -*- coding: utf-8 -*-

# This source file is part of mc4p,
# the Minecraft Portable Protocol-Parsing Proxy.

# This program is free software. It comes without any warranty, to
# the extent permitted by applicable law. You can redistribute it
# and/or modify it under the terms of the Do What The Fuck You Want
# To Public License, Version 2, as published by Sam Hocevar. See
# http://www.wtfpl.net/txt/copying/ for more details

from __future__ import absolute_import, unicode_literals

import redis

from mc4p import protocol

REFERENCE_PROTOCOL = protocol.get_latest_protocol()
CLIENT_PROTOCOL = REFERENCE_PROTOCOL.client_bound
SERVER_PROTOCOL = REFERENCE_PROTOCOL.server_bound


class Plugin(object):
    def on_enable(self, server):
        pass

    def on_disable(self, server):
        pass

    def on_connect(self, proxy):
        pass

    def on_disconnect(self, proxy):
        pass

    @staticmethod
    def packet_handler(packet):
        def packet_handler_wrapper(f):
            if not hasattr(f, "_handled_packets"):
                f._handled_packets = []
            f._handled_packets.append(packet)
            return f

        return packet_handler_wrapper

    def register_packet_handlers(self, proxy):
        for cls in self.__class__.__mro__:
            for fname, f in cls.__dict__.iteritems():
                if not (callable(f) and hasattr(f, "_handled_packets")):
                    continue

                for packet in f._handled_packets:
                    # Make overrides work
                    f = getattr(self, fname)

                    if packet._direction == protocol.Direction.client_bound:
                        proxy.server.register_packet_handler(
                            packet, f)
                    if packet._direction == protocol.Direction.server_bound:
                        proxy.client.register_packet_handler(
                            packet, f)


class RequireUsernamePlugin(Plugin):
    @Plugin.packet_handler(CLIENT_PROTOCOL.login.LoginSuccess)
    def fetch_username(self, conn, packet):
        conn.proxy.username = packet.username
        return self.username_loaded(conn.proxy)

    def username_loaded(self, proxy):
        pass


class RedisPlugin(Plugin):
    def on_connect(self, proxy):
        if not hasattr(proxy, 'redis'):
            # TODO: implement plugin dependency and allow custom params
            proxy.redis = redis.StrictRedis(host='localhost', port=6379, db=0)

    def on_disconnect(self, proxy):
        if proxy.redis is not None:
            proxy.redis = None


class RconPlugin(Plugin):
    def on_connect(self, proxy):
        if proxy.rcon is None:
            raise RuntimeError('Rcon required for plugin {}'.format(
                self.__class__.__name__))

    def execute_rcon(self, proxy, cmd):
        return proxy.rcon.execute(cmd)


class CommandPlugin(Plugin):
    def command_status(self, proxy, msg):
        proxy.client.send(CLIENT_PROTOCOL.play.ChatMessage(
            message={'text': msg},
            position=1
        ))
        return True

    def command_success(self, proxy, msg):
        proxy.client.send(CLIENT_PROTOCOL.play.ChatMessage(
            message={'text': msg, 'color': 'green'},
            position=1
        ))
        return True

    def command_error(self, proxy, msg):
        proxy.client.send(CLIENT_PROTOCOL.play.ChatMessage(
            message={'text': msg, 'color': 'red'},
            position=1
        ))
        return True
