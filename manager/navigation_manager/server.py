"""navigation_manager の HTTP サーバー(画面と API)。標準ライブラリだけで書く。

| メソッド・パス | 内容 |
| --- | --- |
| GET /                       | 画面(web/index.html) |
| GET /api/status             | 状態(state・goal・pose・goal_distance・retries・message・local など) |
| GET /api/view               | 画面用: 状態 + 真の姿勢 + 経路 |
| GET /api/map                | mobile_sim の地図の画像 |
| GET /api/map_info           | 地図の解像度・原点・大きさ |
| POST /api/goal              | `{x, y, theta}` ゴールを出す |
| POST /api/cancel            | キャンセル |
| POST /api/initial_pose      | `{x, y, theta}`、または空で mobile_sim の真の姿勢を localization_amcl の初期位置にする |
"""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from .apps import AppError
from .manager import Manager
from .state import Pose

log = logging.getLogger("navigation_manager")


def _pose(body: dict) -> Pose:
    try:
        return Pose(float(body["x"]), float(body["y"]), float(body.get("theta", 0.0)))
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"need {{x, y, theta}}: {e}") from e


def make_handler(manager: Manager):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, value, code: int = 200) -> None:
            self._send(code, json.dumps(value).encode(), "application/json")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n == 0:
                return {}
            return json.loads(self.rfile.read(n))

        def do_GET(self):
            try:
                if self.path in ("/", "/index.html"):
                    page = resources.files("navigation_manager").joinpath("web/index.html").read_bytes()
                    return self._send(200, page, "text/html; charset=utf-8")
                if self.path == "/api/status":
                    return self._json(manager.status())
                if self.path == "/api/view":
                    return self._json(manager.view())
                if self.path == "/api/map":
                    ctype, png = manager.apps.map_png()
                    return self._send(200, png, ctype or "image/png")
                if self.path == "/api/map_info":
                    return self._json(manager.apps.map_info())
                self._json({"error": "not found"}, 404)
            except AppError as e:
                self._json({"error": str(e)}, 502)

        def do_POST(self):
            try:
                body = self._body()
                if self.path == "/api/goal":
                    return self._json(manager.set_goal(_pose(body)))
                if self.path == "/api/cancel":
                    return self._json(manager.cancel())
                if self.path == "/api/initial_pose":
                    p = manager.set_initial_pose(_pose(body) if body else None)
                    return self._json({"x": p.x, "y": p.y, "theta": p.theta})
                self._json({"error": "not found"}, 404)
            except ValueError as e:
                self._json({"error": str(e)}, 400)
            except AppError as e:
                self._json({"error": str(e)}, 502)

    return Handler


def serve(manager: Manager, host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(manager))
