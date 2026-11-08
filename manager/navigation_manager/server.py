"""navigation_manager の HTTP サーバー(画面と API)。標準ライブラリだけで書く。

| メソッド・パス | 内容 |
| --- | --- |
| GET /                       | 画面(web/index.html) |
| GET /api/status             | 状態(state・goal・pose・goal_distance・retries・message・local など) |
| GET /api/view               | 画面用: 状態 + 真の姿勢 + 経路 + 局所計画 + 障害物 + 粒子 |
| GET /api/map                | 配った地図の画像(まだ配っていなければ mobile_sim の地図) |
| GET /api/map_info           | その地図の解像度・原点・大きさ(width・height)・source |
| POST /api/goal              | `{x, y, theta}` ゴールを出す |
| POST /api/cancel            | キャンセル |
| POST /api/initial_pose      | `{x, y, theta}`、または空で mobile_sim の真の姿勢を localization_amcl の初期位置にする |
| POST /api/distribute_map    | mobile_sim の地図を planner_dijkstra と localization_amcl に渡す |
| POST /api/load_map          | 地図の YAML を読んで同じく渡す。`{path}`(manager から見たパス)、または `{yaml, images: {ファイル名: base64}, name}`(画面から送る) |
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import urlsplit

from .apps import AppError
from .manager import Manager
from .state import Pose

log = logging.getLogger("navigation_manager")


def _pose(body: dict) -> Pose:
    try:
        return Pose(float(body["x"]), float(body["y"]), float(body.get("theta", 0.0)))
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"need {{x, y, theta}}: {e}") from e


def _load_map(manager: Manager, body: dict) -> dict:
    if body.get("path"):
        return manager.load_map(str(body["path"]))
    if isinstance(body.get("yaml"), str):
        try:
            images = {str(k): base64.b64decode(v, validate=True) for k, v in (body.get("images") or {}).items()}
        except (binascii.Error, TypeError, ValueError) as e:
            raise ValueError(f"images must be base64: {e}") from e
        return manager.load_map_upload(body["yaml"], images, body.get("name"))
    raise ValueError("need {path} or {yaml, images}")


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
            body = json.loads(self.rfile.read(n))
            if not isinstance(body, dict):
                raise ValueError("the request body must be a JSON object")
            return body

        def do_GET(self):
            path = urlsplit(self.path).path
            try:
                if path in ("/", "/index.html"):
                    page = resources.files("navigation_manager").joinpath("web/index.html").read_bytes()
                    return self._send(200, page, "text/html; charset=utf-8")
                if path == "/api/status":
                    return self._json(manager.status())
                if path == "/api/view":
                    return self._json(manager.view())
                if path == "/api/map":
                    ctype, png = manager.map_png()
                    return self._send(200, png, ctype or "image/png")
                if path == "/api/map_info":
                    return self._json(manager.map_info())
                self._json({"error": "not found"}, 404)
            except AppError as e:
                self._json({"error": str(e)}, 502)

        def do_POST(self):
            path = urlsplit(self.path).path
            try:
                body = self._body()
                if path == "/api/goal":
                    return self._json(manager.set_goal(_pose(body)))
                if path == "/api/cancel":
                    return self._json(manager.cancel())
                if path == "/api/initial_pose":
                    p = manager.set_initial_pose(_pose(body) if body else None)
                    return self._json({"x": p.x, "y": p.y, "theta": p.theta})
                if path == "/api/distribute_map":
                    return self._json(manager.distribute_map())
                if path == "/api/load_map":
                    return self._json(_load_map(manager, body))
                self._json({"error": "not found"}, 404)
            except ValueError as e:
                self._json({"error": str(e)}, 400)
            except AppError as e:
                self._json({"error": str(e)}, 502)

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind は socket.getfqdn で名前を逆引きする。macOS などでは数十秒かかることがあるので省く
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def serve(manager: Manager, host: str, port: int) -> ThreadingHTTPServer:
    return _Server((host, port), make_handler(manager))
