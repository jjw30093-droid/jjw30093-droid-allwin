"""Tiny loopback-only reverse proxy for a production-like local preview."""

from __future__ import annotations

import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class PreviewUpstreamError(RuntimeError):
    pass


def _probe_http(port: int, path: str) -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        response.read()
        return 200 <= response.status < 500
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def check_upstreams(backend_port: int, frontend_port: int) -> None:
    unavailable = []
    if not _probe_http(backend_port, "/healthz"):
        unavailable.append("backend")
    if not _probe_http(frontend_port, "/"):
        unavailable.append("frontend")
    if unavailable:
        raise PreviewUpstreamError(
            f"preview upstream unavailable: {', '.join(unavailable)}"
        )


def serve(listen_port: int, backend_port: int, frontend_port: int) -> None:
    check_upstreams(backend_port, frontend_port)

    class Proxy(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:
            return

        def _target(self) -> tuple[str, int]:
            if self.path.startswith(("/api/", "/healthz", "/readyz")):
                return ("127.0.0.1", backend_port)
            return ("127.0.0.1", frontend_port)

        def _proxy(self) -> None:
            target = self._target()
            length = self.headers.get("Content-Length")
            body = self.rfile.read(int(length)) if length else None
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in HOP_BY_HOP and key.lower() != "host"
            }
            headers["Host"] = f"{target[0]}:{target[1]}"
            connection = http.client.HTTPConnection(*target, timeout=60)
            try:
                connection.request(self.command, self.path, body=body, headers=headers)
                response = connection.getresponse()
                payload = response.read()
                self.send_response(response.status, response.reason)
                for key, value in response.getheaders():
                    if key.lower() not in HOP_BY_HOP and key.lower() != "content-length":
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)
            except (OSError, http.client.HTTPException):
                payload = b"preview upstream temporarily unavailable"
                self.send_response(502)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)
            finally:
                connection.close()

        do_GET = _proxy
        do_HEAD = _proxy
        do_POST = _proxy
        do_PUT = _proxy
        do_PATCH = _proxy
        do_DELETE = _proxy
        do_OPTIONS = _proxy

    ThreadingHTTPServer(("127.0.0.1", listen_port), Proxy).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--backend-port", type=int, required=True)
    parser.add_argument("--frontend-port", type=int, required=True)
    args = parser.parse_args()
    try:
        serve(args.listen_port, args.backend_port, args.frontend_port)
    except PreviewUpstreamError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
