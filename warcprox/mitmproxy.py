# vim:set sw=4 et:

from __future__ import absolute_import

try:
    import http.server as http_server
except ImportError:
    import BaseHTTPServer as http_server

try:
    import urllib.parse as urllib_parse
except ImportError:
    import urlparse as urllib_parse

import os
import socket
import logging
import ssl

import socks


class MitmProxyHandler(http_server.BaseHTTPRequestHandler):
    logger = logging.getLogger("warcprox.mitmproxy.MitmProxyHandler")

    def __init__(self, request, client_address, server):
        self.is_connect = False
        self._headers_buffer = []
        http_server.BaseHTTPRequestHandler.__init__(self, request, client_address, server)

    def _determine_host_port(self):
        # Get hostname and port to connect to
        if self.is_connect:
            self.hostname, self.port = self.path.split(':')
        else:
            self.url = self.path
            u = urllib_parse.urlparse(self.url)
            if u.scheme != 'http':
                raise Exception('Unknown scheme %s' % repr(u.scheme))
            self.hostname = u.hostname
            self.port = u.port or 80
            self.path = urllib_parse.urlunparse(
                urllib_parse.ParseResult(
                    scheme='',
                    netloc='',
                    params=u.params,
                    path=u.path or '/',
                    query=u.query,
                    fragment=u.fragment
                )
            )

    def _connect_to_host(self):
        # Connect to destination
        if "HTTP_PROXY" in os.environ:
            split_result = urllib_parse.urlsplit(os.environ["HTTP_PROXY"])

            self._proxy_sock = socks.socksocket()

            self._proxy_sock.set_proxy(
                proxy_type=socks.PROXY_TYPE_HTTP,
                addr=split_result.hostname,
                port=split_result.port,
                username=split_result.username,
                password=split_result.password,
            )

        else:
            self._proxy_sock = socket.socket()

        self._proxy_sock.settimeout(60)
        self._proxy_sock.connect((self.hostname, int(self.port)))

        # Wrap socket if SSL is required
        if self.is_connect:
            try:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                self._proxy_sock = context.wrap_socket(self._proxy_sock, server_hostname=self.hostname)
            except AttributeError:
                try:
                    self._proxy_sock = ssl.wrap_socket(self._proxy_sock)
                except ssl.SSLError:
                    self.logger.warn("failed to establish ssl connection to {}; python ssl library does not support SNI, considering upgrading to python >= 2.7.9 or python 3.4".format(self.hostname))
                    raise

    def _transition_to_ssl(self):
        self.request = self.connection = ssl.wrap_socket(self.connection,
                server_side=True, certfile=self.server.ca.cert_for_host(self.hostname))

    def do_CONNECT(self):
        self.is_connect = True
        try:
            # Connect to destination first
            self._determine_host_port()
            self._connect_to_host()

            # If successful, let's do this!
            self.send_response(200, 'Connection established')
            self.end_headers()
            self._transition_to_ssl()
        except Exception as e:
            try:
                if type(e) is socket.timeout:
                    self.send_error(504, str(e))
                else:
                    self.send_error(500, str(e))
            except Exception as f:
                self.logger.warn("failed to send error response ({}) to proxy client: {}".format(e, f))
            return

        # Reload!
        self.setup()
        self.handle_one_request()

    def _construct_tunneled_url(self):
        if int(self.port) == 443:
            netloc = self.hostname
        else:
            netloc = '{}:{}'.format(self.hostname, self.port)

        result = urllib_parse.urlunparse(
            urllib_parse.ParseResult(
                scheme='https',
                netloc=netloc,
                params='',
                path=self.path,
                query='',
                fragment=''
            )
        )

        return result

    def do_COMMAND(self):
        if not self.is_connect:
            try:
                # Connect to destination
                self._determine_host_port()
                self._connect_to_host()
                assert self.url
            except Exception as e:
                self.send_error(500, str(e))
                return
        else:
            # if self.is_connect we already connected in do_CONNECT
            self.url = self._construct_tunneled_url()

        self._proxy_request()


    def _proxy_request(self):
        raise Exception('_proxy_request() not implemented in MitmProxyHandler, must be implemented in subclass!')

    def __getattr__(self, item):
        if item.startswith('do_'):
            return self.do_COMMAND

    def log_error(self, fmt, *args):
        self.logger.error("{0} - - [{1}] {2}".format(self.address_string(),
            self.log_date_time_string(), fmt % args))

    def log_message(self, fmt, *args):
        self.logger.info("{} {} - - [{}] {}".format(self.__class__.__name__,
            self.address_string(), self.log_date_time_string(), fmt % args))


