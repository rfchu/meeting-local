"""Exercise the downloader against TLS, without using a public service."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import ssl
import subprocess
from threading import Event, Thread
import time

import pytest

from app import model_download
from test_model_import import URL, complete, importer, make_zip


@contextmanager
def tls_server(root, handler):
    key, cert = root / 'key.pem', root / 'cert.pem'
    config = root / 'openssl.cnf'
    config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=v3\nprompt=no\n'
                      '[dn]\nCN=localhost\n[v3]\nsubjectAltName=DNS:localhost\n'
                      'basicConstraints=critical,CA:TRUE\n')
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', str(key), '-out', str(cert), '-days', '1', '-config', str(config)],
                   check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    with ThreadingHTTPServer(('127.0.0.1', 0), handler) as server:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'https://localhost:{server.server_port}', cert
        finally:
            server.shutdown()
            thread.join()


def allow_fixture_host(monkeypatch):
    public_addresses = model_download.public_addresses
    monkeypatch.setattr(model_download, 'public_addresses', lambda host, port:
                        ['127.0.0.1'] if host == 'localhost' else public_addresses(host, port))


def test_download_over_tls_with_failures_and_retry(importer, monkeypatch):
    client, source, data = importer
    root = data.parent
    archive = make_zip(root / 'source.zip', [('models/test/model.bin', b'good')])
    payload = archive.read_bytes()
    release = Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            redirects = {'/downgrade': 'http://localhost/model.zip',
                         '/private': f'https://127.0.0.1:{self.server.server_port}/model.zip',
                         '/malformed': 'https://[not-a-host',
                         '/safe': '/model.zip'}
            if self.path in redirects:
                self.send_response(302)
                self.send_header('Location', redirects[self.path])
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            if self.path == '/drop' and requests.count('/drop') == 1:
                self.wfile.write(payload[:16])
                self.connection.shutdown(socket.SHUT_WR)
            elif self.path == '/stall':
                self.wfile.write(payload[:16])
                release.wait(5)
            else:
                self.wfile.write(payload)

    allow_fixture_host(monkeypatch)
    with tls_server(root, Handler) as (origin, cert):
        def request(path):
            assert client.post(URL + '/download', json={'url': origin + path}).status_code == 202

        request('/model.zip')
        assert complete(client)['error_code'] == 'certificate_error'
        assert not requests
        monkeypatch.setattr(model_download.certifi, 'where', lambda: str(cert))
        for path in ('/downgrade', '/private', '/malformed'):
            request(path)
            assert complete(client)['error_code'] == 'unsafe_redirect'
        assert '/model.zip' not in requests
        request('/drop')
        assert complete(client)['error_code'] == 'network_error'
        assert not list((data / 'model-imports').iterdir())
        request('/stall')
        try:
            deadline = time.monotonic() + 3
            while client.get(URL).json()['downloaded_bytes'] < 16 and time.monotonic() < deadline:
                time.sleep(.01)
            status = client.get(URL).json()
            assert status['phase'] == 'downloading'
            assert status['downloaded_bytes'] == 16 and status['download_total_bytes'] == len(payload)
            assert client.delete(URL).status_code == 202
        finally:
            release.set()
        assert complete(client)['phase'] == 'cancelled'
        assert not list((data / 'model-imports').iterdir())
        request('/drop')
        assert complete(client)['phase'] == 'ready'
        stage, = (data / 'model-imports').iterdir()
        assert (stage / 'models/test/model.bin').read_bytes() == b'good'
        request('/safe')
        assert complete(client)['reused']
        assert list((data / 'model-imports').iterdir()) == [stage]


@pytest.mark.parametrize('url', ['http://example.com/a', 'https://user@example.com/a',
                                 'https://example.com/a#', 'https://example.com:0/a'])
def test_reject_unsafe_download_url(importer, url):
    client, _, _ = importer
    assert client.post(URL + '/download', json={'url': url}).status_code == 422
    assert client.get(URL).json()['phase'] == 'idle'


def test_download_does_not_dial_private_addresses(monkeypatch):
    monkeypatch.setattr(model_download.socket, 'getaddrinfo', lambda *_a, **_k:
                        [(None, None, None, None, ('8.8.8.8', 443)),
                         (None, None, None, None, ('::ffff:127.0.0.1', 443))])
    with pytest.raises(model_download.ModelPackError, match='unsafe_download_url'):
        model_download.public_addresses('example.com', 443)
