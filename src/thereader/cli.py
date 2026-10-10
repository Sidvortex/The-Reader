"""The `the-reader` command: starts the local server and opens the browser."""

from __future__ import annotations

import argparse
import socket
import threading
import webbrowser
from pathlib import Path


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="the-reader",
        description="The Reader: read a codebase layer by layer, with maps, flowcharts and plain-English explanations.",
        epilog="The Reader by Ravada Siddharth (Sidvortex). Free for non-commercial use with credit; see LICENSE.md.",
    )
    ap.add_argument("path", nargs="?",
                    help="a Python file, or a project folder to read as a whole (optional; you can also open one from the page)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = ap.parse_args()

    if not _port_free(args.host, args.port):
        ap.exit(1, f"Port {args.port} is already in use. The Reader may already be running at "
                   f"http://{args.host}:{args.port}, or start another copy with --port {args.port + 1}\n")

    import uvicorn

    from .analyzer import UnsupportedLanguage
    from .server import allow_any_host, app, open_initial

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        allow_any_host()
    if args.path:
        path = Path(args.path).expanduser()
        if not path.exists():
            ap.error(f"no such file or folder: {path}")
        try:
            open_initial(path)
        except UnsupportedLanguage as e:
            ap.error(str(e))
        if path.is_dir():
            print(f"Reading the project in {path.resolve()} …")

    url = f"http://{args.host}:{args.port}"
    print(f"The Reader is running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
