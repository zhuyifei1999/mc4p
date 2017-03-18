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

from mc4p import network


class Proxy(object):
    def __init__(self, proxyserver, client, server):
        self.proxyserver = proxyserver
        self.client = client
        self.server = server
        self.plugins = self.proxyserver.plugins


class ProxyServer(network.Server):
    def __init__(self, addr, remote_addr, plugins=()):
        super(ProxyServer, self).__init__(addr, ProxyClientHandler)
        self.remote_addr = remote_addr
        self.plugins = plugins

        for plugin in self.plugins:
            plugin.on_enable(self)

    def run(self):
        try:
            super(ProxyServer, self).run()
        finally:
            for plugin in self.plugins:
                plugin.on_disable(self)


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
    parser.add_argument('-v', '--verbose',
                        help='verbose mode',
                        action='store_true')
    parser.add_argument('-p', '--plugin',
                        help='name of plugin to load, appended args will be '
                             'passed to the plugin',
                        action='append',
                        nargs='+')
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    plugins = []

    for plugin in args.plugin:
        pname, pargs = plugin[0], plugin[1:]
        module = importlib.import_module('mc4p.plugins.%s' % pname)
        plugins.append(module.load_plugin(*pargs))

    server = ProxyServer(
        ('', args.port), ('localhost', args.remote_port), plugins)
    server.run()
