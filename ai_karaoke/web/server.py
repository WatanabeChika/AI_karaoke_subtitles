"""Command-line launcher for the local AI Karaoke web application."""

from __future__ import annotations

import argparse
from http.server import ThreadingHTTPServer

from ai_karaoke.interfaces.console import configure_runtime_output
from ai_karaoke.web.handler import KaraokeWebHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Local web GUI for ai_karaoke")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=7860, help="Bind port")
    args = parser.parse_args()
    configure_runtime_output(False)
    server = ThreadingHTTPServer((args.host, args.port), KaraokeWebHandler)
    print(f"Web GUI running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWeb GUI stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
