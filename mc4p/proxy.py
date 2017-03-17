# -*- coding: utf-8 -*-

# This source file is part of mc4p,
# the Minecraft Portable Protocol-Parsing Proxy.

# This program is free software. It comes without any warranty, to
# the extent permitted by applicable law. You can redistribute it
# and/or modify it under the terms of the Do What The Fuck You Want
# To Public License, Version 2, as published by Sam Hocevar. See
# http://www.wtfpl.net/txt/copying/ for more details

from __future__ import absolute_import, unicode_literals

import logging

from mc4p import network


logger = logging.getLogger("proxy")


class ProxyServer(network.Server):
    def __init__(self, addr, remote_addr):
        super(ProxyServer, self).__init__(addr, ProxyClientHandler)
        self.remote_addr = remote_addr


class ProxyClientHandler(network.ClientHandler):
    def init(self):
        self.real_server = ProxyClient(self.server.remote_addr, self)
        self.real_server.start()

    def handle_disconnect(self):
        super(ProxyClientHandler, self).handle_disconnect()
        self.real_server.close()

    def handle_packet(self, packet):
        self.real_server.send(packet)
        return True

    def handle_packet_error(self, error):
        logger.error("%s caused an error: %s" % (self, error))
        self.close()
        return True

    def __str__(self):
        return "Client %s:%d" % self.addr

    def debug_send_packet(self, packet):
        logger.debug('M->C: %s', packet)

    def debug_recv_packet(self, packet):
        logger.debug('C->M: %s', packet)


class ProxyClient(network.Client):
    def __init__(self, addr, server):
        super(ProxyClient, self).__init__(addr, version=0)
        self.real_client = server

    def handle_packet(self, packet):
        self.real_client.send(packet)
        return True

    def handle_disconnect(self):
        super(ProxyClient, self).handle_disconnect()
        self.real_server.close()

    def __str__(self):
        return "Server %s:%d" % self.addr

    def debug_send_packet(self, packet):
        logging.debug('M->S: %s', packet)

    def debug_recv_packet(self, packet):
        logging.debug('S->M: %s', packet)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    server = ProxyServer(("", 25566), ("localhost", 25565))
    import cProfile
    cProfile.run("server.run()", "/tmp/stats.dat")
