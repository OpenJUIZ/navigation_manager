"""状態機械を動かし、その Action に従って各アプリを HTTP で呼ぶ。

- 周期処理(既定 5 Hz): localization_amcl の推定姿勢と planner_dwa の状態を読み、状態機械に渡す
- ゴール: planner_dijkstra の set_goal を呼んで経路を受け、planner_dwa の set_path に渡す(引き直しも同じ)
- キャンセル・到着・失敗: planner_dwa の cancel を呼んで止める
- 初期位置: mobile_sim の真の姿勢(または指定の姿勢)を localization_amcl の set_initial_pose に渡す
"""
from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional

from .apps import AppError, Apps
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
            return s

    def view(self) -> dict:
        """画面用: 状態、真の姿勢、推定姿勢、経路、局所計画"""
        s = self.status()
        try:
            s["true_pose"] = _pose_dict(self.apps.true_pose())
        except AppError:
            s["true_pose"] = None
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
