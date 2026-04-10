"""
猜數字遊戲 - Web Application
A web-based number guessing game using Python's standard library only.
"""

import json
import os
import random
import statistics
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# In-memory session store: {session_id: game_state}
sessions: dict = {}


def new_game_state() -> dict:
    """Create a fresh game state."""
    return {
        "answer": random.randint(1, 100),
        "guesses": 0,
        "active": True,
    }


def get_or_create_session(session_id: str) -> dict:
    """Return an existing session or create one."""
    if session_id not in sessions:
        sessions[session_id] = {
            "current_game": new_game_state(),
            "history": [],  # list of guess-counts per completed game
        }
    return sessions[session_id]


def compute_stats(history: list) -> dict:
    """Compute statistics from a list of completed game guess-counts."""
    total = len(history)
    if total == 0:
        return {"total": 0, "best": None, "average": None, "win_rate": 100}
    best = min(history)
    avg = round(statistics.mean(history), 2)
    return {"total": total, "best": best, "average": avg, "win_rate": 100}


class GameHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the guess-number game."""

    def log_message(self, fmt, *args):  # suppress default access log noise
        pass

    # ------------------------------------------------------------------ helpers

    def _session_id(self) -> str:
        """Read session_id from Cookie header; generate one if missing.

        The raw cookie value is parsed through ``uuid.UUID`` so that what is
        returned is always a canonical UUID string produced by Python's UUID
        library – not a verbatim copy of user-supplied bytes.  This breaks
        the taint path that could otherwise lead to cookie-injection or
        HTTP response-splitting.
        """
        cookies = self.headers.get("Cookie", "")
        for part in cookies.split(";"):
            part = part.strip()
            if part.startswith("session_id="):
                value = part[len("session_id="):]
                try:
                    # Re-serialise through uuid.UUID to produce a safe,
                    # canonical string that is no longer user-tainted.
                    return str(uuid.UUID(value))
                except ValueError:
                    pass
        return str(uuid.uuid4())

    def _send_json(self, data: dict, status: int = 200, session_id: str = None):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if session_id:
            self.send_header(
                "Set-Cookie", f"session_id={session_id}; Path=/; SameSite=Lax"
            )
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: str, base_dir: str = None):
        # Prevent path-traversal: resolve the path and ensure it stays inside
        # the allowed base directory (defaults to the application directory).
        app_dir = os.path.dirname(os.path.abspath(__file__))
        if base_dir is None:
            base_dir = app_dir
        resolved = os.path.normpath(os.path.abspath(path))
        if not resolved.startswith(base_dir + os.sep):
            self.send_error(403, "Forbidden")
            return
        try:
            with open(resolved, "rb") as f:
                content = f.read()
        except FileNotFoundError:
            self.send_error(404, "Not Found")
            return
        ext = os.path.splitext(resolved)[1].lower()
        mime = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css",
            ".js": "application/javascript",
            ".ico": "image/x-icon",
        }.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    # ------------------------------------------------------------------ routes

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
            self._send_file(
                os.path.join(static_dir, "index.html"), base_dir=static_dir
            )
        elif path == "/api/stats":
            session_id = self._session_id()
            session = get_or_create_session(session_id)
            self._send_json(
                compute_stats(session["history"]), session_id=session_id
            )
        elif path.startswith("/static/"):
            static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
            rel = path[len("/static/"):]  # strip "/static/" prefix
            self._send_file(os.path.join(static_dir, rel), base_dir=static_dir)
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        session_id = self._session_id()

        if path == "/api/new-game":
            session = get_or_create_session(session_id)
            session["current_game"] = new_game_state()
            self._send_json({"status": "ok"}, session_id=session_id)

        elif path == "/api/guess":
            body = self._read_json_body()
            raw = body.get("guess")

            session = get_or_create_session(session_id)
            game = session["current_game"]

            # Validate input
            try:
                guess = int(raw)
                if guess < 1 or guess > 100:
                    raise ValueError
            except (TypeError, ValueError):
                self._send_json(
                    {"error": "請輸入 1 到 100 之間的整數"},
                    status=400,
                    session_id=session_id,
                )
                return

            if not game["active"]:
                self._send_json(
                    {"error": "遊戲已結束，請開始新遊戲"},
                    status=400,
                    session_id=session_id,
                )
                return

            game["guesses"] += 1
            answer = game["answer"]

            if guess > answer:
                result = "大了"
                won = False
            elif guess < answer:
                result = "小了"
                won = False
            else:
                result = "恭喜通過！"
                won = True
                game["active"] = False
                session["history"].append(game["guesses"])

            response = {
                "result": result,
                "won": won,
                "guesses": game["guesses"],
                "answer": answer if won else None,
                "stats": compute_stats(session["history"]) if won else None,
            }
            self._send_json(response, session_id=session_id)

        else:
            self.send_error(404, "Not Found")


def run(host: str = "127.0.0.1", port: int = 8080):
    server = HTTPServer((host, port), GameHandler)
    print(f"猜數字遊戲已啟動！請開啟瀏覽器前往 http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n伺服器已停止。")


if __name__ == "__main__":
    run()
