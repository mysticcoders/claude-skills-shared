"""Background HTTP server for serving a local `dist/` directory to Playwright."""

from __future__ import annotations

import http.server
import socketserver
import threading
from pathlib import Path
from typing import Optional


class StaticServer:
    """Threaded HTTP server rooted at a directory. Picks a free port on start()."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._httpd: Optional[socketserver.TCPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port: int = 0

    def start(self) -> int:
        root_str = str(self.root)

        class _Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=root_str, **kwargs)

            def log_message(self, format, *args):
                return  # silence access log noise

        # Port 0 = let the OS pick a free port.
        self._httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        self._httpd.allow_reuse_address = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def __enter__(self) -> "StaticServer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def discover_dist_pages(dist_dir: Path) -> list[str]:
    """Find all .html files under a dist directory, return URL paths relative to root."""
    paths: list[str] = []
    root = dist_dir.resolve()
    for html_file in sorted(root.rglob("*.html")):
        rel = html_file.relative_to(root)
        if rel.name == "index.html":
            parent = rel.parent
            url_path = "/" if str(parent) == "." else f"/{parent.as_posix()}/"
        else:
            url_path = f"/{rel.as_posix()}"
        paths.append(url_path)
    return paths
