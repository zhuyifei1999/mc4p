# -*- coding: utf-8 -*-

# This source file is part of mc4p,
# the Minecraft Portable Protocol-Parsing Proxy.

# This program is free software. It comes without any warranty, to
# the extent permitted by applicable law. You can redistribute it
# and/or modify it under the terms of the Do What The Fuck You Want
# To Public License, Version 2, as published by Sam Hocevar. See
# http://www.wtfpl.net/txt/copying/ for more details

from __future__ import absolute_import, unicode_literals

import importlib
import logging
from multiprocessing.managers import BaseManager

from mc4p import network, rcon


class Proxy(object):
    def __init__(self, proxyserver, client, server):
        self.proxyserver = proxyserver
        self.client = client
        self.server = server
        self.plugins = self.proxyserver.plugins
        self.rcon = self.proxyserver.rcon


class ProxyServer(network.Server):
    def __init__(self, addr, remote_addr, plugins=(), rcon=None):
        super(ProxyServer, self).__init__(addr, ProxyClientHandler)
        self.remote_addr = remote_addr
        self.plugins = plugins
        self.manager = type(str('MCManager'), (BaseManager,), {})

        if rcon:
            self.has_rcon = True
            self.manager.register(str('rcon'), lambda: self._rcon)
        else:
            self.has_rcon = False

        for plugin in self.plugins:
            plugin.on_enable(self)

    def run(self):
        try:
            self.manager = self.manager()
            self.manager.start()
            super(ProxyServer, self).run()
        finally:
            for plugin in self.plugins:
                plugin.on_disable(self)
            self.manager.shutdown()

    @property
    def rcon(self):
        if self.has_rcon:
            return self.manager.rcon()


class ProxyClientHandler(network.ClientHandler):
    def init(self):
        self.logger = logging.getLogger("proxy.client")
        self.real_server = ProxyClient(self.server.remote_addr, self)

        self.proxy = Proxy(self.server, self, self.real_server)
        self.real_server.proxy = self.proxy

        for plugin in self.proxy.plugins:
            plugin.register_packet_handlers(self.proxy)

        self.real_server.start()

        for plugin in self.proxy.plugins:
            plugin.on_connect(self.proxy)

    def recv(self):
        super(ProxyClientHandler, self).recv()
        self.real_server.flush()

    def handle_disconnect(self):
        super(ProxyClientHandler, self).handle_disconnect()
        self.real_server.close('Client disconnected')

        for plugin in self.proxy.plugins:
            plugin.on_disconnect(self.proxy)

    def handle_packet(self, packet):
        self.real_server.send(packet)
        return True

    def handle_packet_error(self, error):
        self.logger.error("%s caused an error: %s" % (self, error))
        self.close('Packet error')
        return True

    def debug_send_packet(self, packet):
        self.logger.debug('send: %s', packet)

    def debug_recv_packet(self, packet):
        self.logger.debug('recv: %s', packet)


class ProxyClient(network.Client):
    def __init__(self, addr, server):
        super(ProxyClient, self).__init__(addr, version=0)
        self.logger = logging.getLogger("proxy.server")
        self.real_client = server

    def handle_packet(self, packet):
        self.real_client.send(packet)
        return True

    def recv(self):
        super(ProxyClient, self).recv()
        self.real_client.flush()

    def handle_disconnect(self):
        super(ProxyClient, self).handle_disconnect()
        self.real_client.close('Server disconnected')

    def debug_send_packet(self, packet):
        self.logger.debug('send: %s', packet)

    def debug_recv_packet(self, packet):
        self.logger.debug('recv: %s', packet)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='start mc4p proxy server')
    parser.add_argument('port',
                        help='port the proxy server should listen on',
                        type=int)
    parser.add_argument('remote_port',
                        help='port the proxy server should connect to',
                        type=int)
    parser.add_argument('--remote_host',
                        help='host the proxy server should connect to',
                        default='localhost')
    parser.add_argument('--rcon',
                        help='Rcon connection to the server',
                        nargs=2,
                        metavar=('port', 'password'))
    parser.add_argument('-v', '--verbose',
                        help='verbose mode',
                        action='store_true')
    parser.add_argument('-p', '--plugin',
                        help='name of plugin to load, appended args will be '
                             'passed to the plugin',
                        action='append',
                        nargs='+',
                        metavar=('name', 'parameters'))
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if args.rcon:
        rcon_port, rcon_password = args.rcon
        server_rcon = rcon.Rcon(('localhost', int(rcon_port)), rcon_password)
    else:
        server_rcon = None

    plugins = []

    for plugin in args.plugin or []:
        pname, pargs = plugin[0], plugin[1:]
        module = importlib.import_module('mc4p.plugins.%s' % pname)
        plugins.append(module.load_plugin(*pargs))

    server = ProxyServer(
        ('', args.port), (args.remote_host, args.remote_port), plugins, server_rcon)
    server.run()
