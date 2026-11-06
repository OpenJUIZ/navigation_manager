"""ナビゲーション一式の各アプリを juiz の HTTP API で呼ぶ。

| アプリ | 既定の URL | 使う ContainerProcess |
| --- | --- | --- |
| mobile_sim | http://localhost:8000 | sim0: get_pose0・get_map0・get_map_info0・get_state0 |
| localization_amcl | http://localhost:8002 | amcl0: get_pose0・set_initial_pose0・get_state0 |
| planner_dijkstra | http://localhost:8003 | planner0: set_goal0(計画して経路を返す)・get_path0 |
| planner_dwa | http://localhost:8004 | dwa0: set_path0・cancel0・get_status0 |

ContainerProcess の ID は `<URL>/container_process/<name>::<type_name>:<container>`。
HTTP の call は戻り値を返すだけで、トピックには出さない(必要な受け渡しは manager が自分で呼ぶ)。
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from .state import Pose


class AppError(Exception):
    pass


@dataclass
class Urls:
    sim: str = "http://localhost:8000"
    amcl: str = "http://localhost:8002"
    planner: str = "http://localhost:8003"
    dwa: str = "http://localhost:8004"


def _request(url: str, body: Any = None, method: str = "GET", timeout: float = 5.0) -> Tuple[str, bytes]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        raise AppError(f"{url}: HTTP {e.code} {e.read().decode(errors='replace')[:300]}") from e
    except OSError as e:
        raise AppError(f"{url}: {e}") from e


def call(base: str, container: str, name: str, type_name: str, args: Optional[dict] = None, raw: bool = False):
    ident = f"{base}/container_process/{name}::{type_name}:{container}"
    url = f"{base}/api/container_process/call?identifier=" + urllib.parse.quote(ident, safe="")
    ctype, body = _request(url, args or {}, "PATCH")
    if raw:
        return ctype, body
    return json.loads(body) if body else None


def ready(base: str) -> bool:
    try:
        _request(f"{base}/api/container/list", timeout=1.0)
        return True
    except AppError:
        return False


def pose_from_value(v: Any) -> Optional[Pose]:
    """Pose2DStamped(`{header, pose: {position: {x, y}, theta}}`)・Pose2D・`{x, y, theta}` を読む"""
    if not isinstance(v, dict):
        return None
    p = v.get("pose", v)
    if isinstance(p, dict) and "position" in p:
        return Pose(float(p["position"]["x"]), float(p["position"]["y"]), float(p.get("theta", 0.0)))
    if isinstance(p, dict) and "x" in p and "y" in p:
        return Pose(float(p["x"]), float(p["y"]), float(p.get("theta", 0.0)))
    return None


def pose_stamped(p: Pose, frame_id: str = "map") -> dict:
    t = time.time()
    return {
        "header": {"stamp": {"sec": int(t), "nanosec": int((t - int(t)) * 1e9)}, "frame_id": frame_id},
        "pose": {"position": {"x": p.x, "y": p.y}, "theta": p.theta},
    }


class Apps:
    def __init__(self, urls: Urls):
        self.urls = urls

    # mobile_sim
    def true_pose(self) -> Optional[Pose]:
        return pose_from_value(call(self.urls.sim, "sim0", "get_pose0", "mobile_sim_get_pose", {"index": 0}))

    def map_png(self) -> Tuple[str, bytes]:
        return call(self.urls.sim, "sim0", "get_map0", "mobile_sim_get_map", raw=True)

    def map_info(self) -> dict:
        return call(self.urls.sim, "sim0", "get_map_info0", "mobile_sim_get_map_info")

    def sim_state(self) -> dict:
        return call(self.urls.sim, "sim0", "get_state0", "mobile_sim_get_state")

    # localization_amcl
    def estimated_pose(self) -> Optional[Pose]:
        return pose_from_value(call(self.urls.amcl, "amcl0", "get_pose0", "amcl_get_pose"))

    def set_initial_pose(self, p: Pose) -> None:
        call(self.urls.amcl, "amcl0", "set_initial_pose0", "amcl_set_initial_pose",
             {"pose": pose_stamped(p), "cov_xx": -1.0, "cov_yy": -1.0, "cov_aa": -1.0})

    # planner_dijkstra
    def plan(self, goal: Pose) -> dict:
        """ゴールを渡して計画させる。始点はトピック nav/amcl_pose で planner に届いている推定姿勢"""
        return call(self.urls.planner, "planner0", "set_goal0", "planner_set_goal", {"goal": pose_stamped(goal)})

    # planner_dwa
    def follow(self, path: dict) -> None:
        call(self.urls.dwa, "dwa0", "set_path0", "dwa_set_path", {"path": path})

    def stop(self) -> None:
        call(self.urls.dwa, "dwa0", "cancel0", "dwa_cancel")

    def local_status(self) -> dict:
        return call(self.urls.dwa, "dwa0", "get_status0", "dwa_get_status")
