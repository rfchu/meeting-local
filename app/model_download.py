"""Download a bounded model ZIP over verified HTTPS."""
import http.client
import ipaddress
import re
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

import certifi

from .model_pack import ModelPackError

READ_SIZE = 1024 * 1024
TIMEOUT = 5
MAX_SECONDS = 2 * 60 * 60
REDIRECTS = {301, 302, 303, 307, 308}


def download_url(url):
    try:
        if len(url) > 4096 or re.search(r'[\x00-\x20\x7f\\]', url):
            raise ValueError
        parts = urlsplit(url)
        if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
                or '#' in url or parts.port == 0):
            raise ValueError
        parts.hostname.encode('ascii')
        return parts
    except (ValueError, UnicodeError) as error:
        raise ModelPackError('unsafe_download_url') from error


def public_addresses(host, port):
    addresses = list(dict.fromkeys(
        item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ))
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if '%' in address or not ip.is_global or ip.is_multicast:
            raise ModelPackError('unsafe_download_url')
    if not addresses:
        raise ModelPackError('network_error')
    return addresses


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Keep TLS verification on the hostname while dialing a checked address."""
    def __init__(self, host, port, context, addresses):
        super().__init__(host, port, timeout=TIMEOUT, context=context)
        self.addresses = addresses
        self._create_connection = self.dial

    def dial(self, _address, timeout, source_address):
        last_error = None
        for address in self.addresses:
            try:
                return socket.create_connection((address, self.port), timeout, source_address)
            except OSError as error:
                last_error = error
        raise last_error or OSError('no usable address')


def download_zip(url, target, limit, check_cancelled, report):
    deadline = time.monotonic() + MAX_SECONDS

    def check():
        check_cancelled()
        if time.monotonic() >= deadline:
            raise ModelPackError('network_error')

    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    redirected = False
    for _ in range(6):
        check()
        try:
            parts = download_url(url)
            port = parts.port or 443
            addresses = public_addresses(parts.hostname, port)
        except ModelPackError as error:
            if redirected and error.code == 'unsafe_download_url':
                raise ModelPackError('unsafe_redirect') from error
            raise
        except OSError as error:
            raise ModelPackError('network_error') from error
        connection = PinnedHTTPSConnection(parts.hostname, port, context, addresses)
        try:
            path = parts.path or '/'
            if parts.query:
                path += '?' + parts.query
            try:
                connection.request('GET', path, headers={'Accept-Encoding': 'identity'})
                response = connection.getresponse()
            except ssl.SSLCertVerificationError as error:
                raise ModelPackError('certificate_error') from error
            except (http.client.HTTPException, OSError, UnicodeError) as error:
                raise ModelPackError('network_error') from error
            if response.status in REDIRECTS:
                location = response.getheader('Location')
                if not location or re.search(r'[\x00-\x20\x7f\\]', location):
                    raise ModelPackError('unsafe_redirect')
                try:
                    url = urljoin(url, location)
                except ValueError as error:
                    raise ModelPackError('unsafe_redirect') from error
                redirected = True
                continue
            if response.status != 200 or response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise ModelPackError('download_response')
            if response.getheader('Transfer-Encoding', '').lower() not in ('', 'chunked'):
                raise ModelPackError('download_response')
            lengths = response.headers.get_all('Content-Length', [])
            if (len(lengths) > 1 or (lengths and (
                    not re.fullmatch(r'[0-9]{1,20}', lengths[0]) or response.chunked))):
                raise ModelPackError('download_response')
            length = int(lengths[0]) if lengths else None
            if length is not None and length > limit:
                raise ModelPackError('download_limit')
            downloaded = 0
            report(downloaded, length)
            with target.open('xb') as output:
                while True:
                    check()
                    try:
                        chunk = response.read1(min(READ_SIZE, limit - downloaded + 1))
                    except (http.client.HTTPException, OSError) as error:
                        raise ModelPackError('network_error') from error
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > limit:
                        raise ModelPackError('download_limit')
                    output.write(chunk)
                    report(downloaded, length)
            if length is not None and downloaded != length:
                raise ModelPackError('network_error')
            check()
            return
        finally:
            connection.close()
    raise ModelPackError('unsafe_redirect')
