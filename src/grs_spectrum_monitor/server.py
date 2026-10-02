"""A página do Spectrum Monitor e três rotas JSON.

    GET /                      a página
    GET /api/state             contexto da passagem e situação de cada rádio
    GET /api/spectrum[?radio=] o último espectro de cada rádio (banda + zoom)

Biblioteca padrão só (`http.server`): uma página e três leituras não pedem
framework. Sem autenticação e sem escrita: o monitor só mostra.
"""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from grs_spectrum_monitor.feed import MonitorFeed


def _page() -> bytes:
    return resources.files("grs_spectrum_monitor").joinpath("page.html").read_bytes()


def make_handler(feed: MonitorFeed) -> type[BaseHTTPRequestHandler]:
    page = _page()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            # A página consulta várias vezes por segundo; logar cada GET
            # afogaria o log.
            pass

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload) -> None:
            self._send(HTTPStatus.OK, json.dumps(payload).encode(), "application/json")

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/":
                self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            elif url.path == "/api/state":
                self._json(feed.state())
            elif url.path == "/api/spectrum":
                radio = parse_qs(url.query).get("radio", [None])[0]
                self._json(feed.spectrum(radio))
            else:
                self._send(HTTPStatus.NOT_FOUND, b'{"error": "rota desconhecida"}',
                           "application/json")

    return Handler


def start_server(feed: MonitorFeed, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(feed))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="http").start()
    return server
