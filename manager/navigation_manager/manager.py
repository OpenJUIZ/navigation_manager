"""状態機械を動かし、その Action に従って各アプリを HTTP で呼ぶ。

- 周期処理(既定 5 Hz): localization_amcl の推定姿勢と planner_dwa の状態を読み、状態機械に渡す
- ゴール: planner_dijkstra の set_goal を呼んで経路を受け、planner_dwa の set_path に渡す(引き直しも同じ)
- キャンセル・到着・失敗: planner_dwa の cancel を呼んで止める
- 初期位置: mobile_sim の真の姿勢(または指定の姿勢)を localization_amcl の set_initial_pose に渡す
- 地図の配布: mobile_sim の地図(get_map・get_map_info)、または地図の YAML(SLAM で作ったものなど)を
  planner_dijkstra と localization_amcl の set_map に渡す。配った地図を覚えておき、画面にはその地図を描く
"""
from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional

from .apps import AppError, Apps, map_parts
from .maps import MapData, load_map_file, load_map_upload
from .state import PLAN, STOP, Navigator, Params, Pose

log = logging.getLogger("navigation_manager")


class Manager:
    def __init__(self, apps: Apps, params: Optional[Params] = None, rate: float = 5.0):
        self.apps = apps
        self.nav = Navigator(params or Params())
        self.rate = rate
        self.lock = threading.RLock()
        self.path: Optional[dict] = None
        self.local: Optional[dict] = None
        self.last_error: Optional[str] = None
        self.map: Optional[MapData] = None   # 配った地図。None なら各アプリの地図のまま(画面は mobile_sim の地図)
        self.map_version = 0                  # 地図を配るたびに増やす(画面が描き直すため)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # 外からのイベント(UI・API)
    def set_goal(self, goal: Pose) -> dict:
        with self.lock:
            self._run(self.nav.set_goal(goal, time.time()))
            return self.status()

    def cancel(self) -> dict:
        with self.lock:
            self._run(self.nav.cancel(time.time()))
            return self.status()

    def set_initial_pose(self, pose: Optional[Pose] = None) -> Pose:
        """pose を省くと mobile_sim の真の姿勢を使う"""
        p = pose or self.apps.true_pose()
        if p is None:
            raise AppError("mobile_sim did not return a pose")
        self.apps.set_initial_pose(p)
        return p

    def distribute_map(self) -> dict:
        """mobile_sim の地図を planner_dijkstra と localization_amcl に渡す。追っている途中なら止める"""
        png_type, png = self.apps.map_png()
        info = self.apps.map_info()
        return self._distribute(MapData(png, dict(info), "mobile_sim"))

    def load_map(self, path) -> dict:
        """地図の YAML(とそこに書かれた画像)を読んで、planner_dijkstra と localization_amcl に渡す"""
        return self._distribute(load_map_file(path))

    def load_map_upload(self, yaml_text: str, images: dict, name: Optional[str] = None) -> dict:
        """画面から送られた地図の YAML と画像を読んで渡す"""
        return self._distribute(load_map_upload(yaml_text, images, name))

    def _distribute(self, m: MapData) -> dict:
        parts = map_parts(m.png, m.info)
        with self.lock:
            if self.nav.state in ("planning", "following"):
                self._run(self.nav.cancel(time.time()))
            self.apps.set_planner_map(parts)
            self.apps.set_amcl_map(parts)
            self.map = m
            self.map_version += 1
            self.path = None
        log.info("distributed the %s map (%sx%s, %s m/px) to planner_dijkstra and localization_amcl",
                 m.source, m.info.get("width"), m.info.get("height"), m.info.get("resolution"))
        return {"source": m.source, "info": m.info}

    # 画面に描く地図: 配った地図、まだ配っていなければ mobile_sim の地図
    def map_png(self):
        m = self.map
        if m is not None:
            return "image/png", m.png
        return self.apps.map_png()

    def map_info(self) -> dict:
        m = self.map
        if m is not None:
            return dict(m.info, source=m.source)
        return self.apps.map_info()

    # 周期処理
    def step(self) -> None:
        with self.lock:
            now = time.time()
            try:
                pose = self.apps.estimated_pose()
                if pose is not None:
                    self._run(self.nav.on_pose(pose, now))
                self.local = self.apps.local_status()
                self._run(self.nav.on_local_status(str(self.local.get("status", "")), now))
                self.last_error = None
            except AppError as e:
                self.last_error = str(e)
                log.warning("%s", e)

    def _run(self, actions: List[str]) -> None:
        for a in actions:
            if a == PLAN:
                self._plan()
            elif a == STOP:
                try:
                    self.apps.stop()
                except AppError as e:
                    self.last_error = str(e)
                    log.warning("stop failed: %s", e)

    def _plan(self) -> None:
        goal = self.nav.goal
        if goal is None:
            return
        try:
            path = self.apps.plan(goal)
        except AppError as e:
            path = {"ok": False, "message": str(e), "poses": []}
        self.path = path
        ok = bool(path.get("ok"))
        if ok:
            try:
                self.apps.follow(path)
            except AppError as e:
                ok = False
                path = dict(path, message=f"planner_dwa refused the path: {e}")
        # 失敗なら状態機械が引き直しか FAILED を決め、また PLAN を返すことがある
        self._run(self.nav.on_plan_result(ok, str(path.get("message", "")), time.time()))

    def status(self) -> dict:
        with self.lock:
            s = self.nav.status()
            s["local"] = self.local
            s["path_ok"] = None if self.path is None else bool(self.path.get("ok"))
            s["error"] = self.last_error
            s["map_source"] = None if self.map is None else self.map.source
            s["map_version"] = self.map_version
            return s

    def view(self) -> dict:
        """画面用: 状態、真の姿勢、推定姿勢、大域経路、局所計画(DWA の最良の軌道)、障害物(スキャン)、粒子"""
        s = self.status()
        try:
            s["true_pose"] = _pose_dict(self.apps.true_pose())
        except AppError:
            s["true_pose"] = None
        try:
            a = self.apps.amcl_state()
            s["particles"] = a.get("particles") or []
            s["particle_count"] = a.get("particle_count")
        except AppError:
            s["particles"], s["particle_count"] = [], None
        try:
            d = self.apps.local_state()
            best = d.get("best") or {}
            s["local_plan"] = best.get("points") or []
            s["obstacles"] = d.get("obstacles") or []
        except AppError:
            s["local_plan"], s["obstacles"] = [], []
        with self.lock:
            s["path"] = self.path
        return s

    def start(self) -> None:
        def loop():
            period = 1.0 / self.rate
            while not self._stop.is_set():
                t = time.time()
                self.step()
                self._stop.wait(max(0.0, period - (time.time() - t)))

        self._thread = threading.Thread(target=loop, name="navigation_manager", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(2.0)


def _pose_dict(p: Optional[Pose]) -> Optional[dict]:
    return None if p is None else {"x": p.x, "y": p.y, "theta": p.theta}
