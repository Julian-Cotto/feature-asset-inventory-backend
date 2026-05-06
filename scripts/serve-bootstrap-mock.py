import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = int(os.environ.get("BOOTSTRAP_PORT", "3050"))

BASE_DIR = Path(__file__).parent.parent
BOOTSTRAP_FILE = BASE_DIR / "contracts" / "bootstrap-response.local.json"

ALLOWED_ORIGINS = {
    "http://localhost:3000",
    "http://localhost:3200",
    "http://localhost:3300",
    "http://localhost:5173",
}


class Handler(BaseHTTPRequestHandler):
    def _get_allowed_origin(self):
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            return origin
        return None

    def _set_cors_headers(self):
        origin = self._get_allowed_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/bootstrap":
            self.send_response(200)
            self._set_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()

            content = BOOTSTRAP_FILE.read_text(encoding="utf-8")
            self.wfile.write(content.encode("utf-8"))
            return

        self.send_response(404)
        self._set_cors_headers()
        self.end_headers()


if __name__ == "__main__":
    print(f"Serving bootstrap mock on http://localhost:{PORT}/bootstrap", flush=True)
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
      server.serve_forever()
    except KeyboardInterrupt:
      print("\n[serve-bootstrap-mock] stopped", file=sys.stderr, flush=True)