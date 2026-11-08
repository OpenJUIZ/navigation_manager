"""ナビゲーションの状態機械。HTTP にも juiz にも依存しない。

    IDLE ──goal──→ PLANNING ──path ok──→ FOLLOWING ──到着──→ SUCCEEDED
                     │ path ng                 │ 立ち往生(planner_dwa の status が stuck)
                     ↓                         ↓
                  (引き直し、max_planning_retries まで) → PLANNING
                     │ 上限を超えた
                     ↓
                  FAILED
    cancel: PLANNING・FOLLOWING から CANCELED

状態機械は「何をすべきか」を Action で返すだけで、実際に各アプリを呼ぶのは manager.py。
名前と既定値は ROS の move_base(planner_patience・max_planning_retries)と dwa_local_planner に合わせる。
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Tuple

IDLE = "idle"
PLANNING = "planning"
FOLLOWING = "following"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELED = "canceled"

# Action: manager.py が各アプリに対して行うこと
PLAN = "plan"  # planner_dijkstra に goal で計画させ、結果の経路を planner_dwa に渡す
STOP = "stop"  # planner_dwa を止める(dwa_cancel)


@dataclass
class Pose:
    x: float
    y: float
    theta: float = 0.0


@dataclass
class Params:
    xy_goal_tolerance: float = 0.25
    yaw_goal_tolerance: float = 0.35
    max_planning_retries: int = 3


def normalize_angle(a: float) -> float:
    return (a + math.pi) % (2.0 * math.pi) - math.pi


@dataclass
class Navigator:
    params: Params = field(default_factory=Params)
    state: str = IDLE
    goal: Optional[Pose] = None
    # 経路の終わり。planner_dijkstra が障害物に近すぎるゴールを寄せたときは寄せた先(向きはゴールの向き)、それ以外は goal
    target: Optional[Pose] = None
    pose: Optional[Pose] = None
    retries: int = 0
    message: str = ""
    goal_seq: int = 0
    since: float = 0.0

    def _set(self, state: str, now: float, message: str) -> None:
        self.state = state
        self.since = now
        self.message = message

    def set_goal(self, goal: Pose, now: float) -> List[str]:
        self.goal = goal
        self.target = goal
        self.goal_seq += 1
        self.retries = 0
        self._set(PLANNING, now, "goal received")
        return [PLAN]

    def cancel(self, now: float) -> List[str]:
        if self.state in (PLANNING, FOLLOWING):
            self._set(CANCELED, now, "canceled")
        return [STOP]

    def on_pose(self, pose: Pose, now: float) -> List[str]:
        """推定姿勢(localization_amcl)。経路を追っている間にゴールに着けば SUCCEEDED"""
        self.pose = pose
        if self.state == FOLLOWING and self.arrived():
            self._set(SUCCEEDED, now, "goal reached")
            return [STOP]
        return []

    def on_plan_result(self, ok: bool, message: str, now: float,
                       goal_moved_to: Optional[Tuple[float, float]] = None) -> List[str]:
        """planner_dijkstra の計画の結果。goal_moved_to はゴールを寄せた先(経路の goal_moved_to)。着いたかはそこで見る"""
        if self.state != PLANNING:
            return []
        if ok:
            g = self.goal
            self.target = g if goal_moved_to is None or g is None else Pose(goal_moved_to[0], goal_moved_to[1], g.theta)
            self._set(FOLLOWING, now, f"following the path ({message})" if message else "following the path")
            return []
        return self._retry(now, f"planning failed: {message}")

    def on_local_status(self, status: str, now: float) -> List[str]:
        """planner_dwa の状態。stuck なら経路を引き直す"""
        if self.state == FOLLOWING and status == "stuck":
            return self._retry(now, "local planner is stuck")
        return []

    def _retry(self, now: float, why: str) -> List[str]:
        if self.retries >= self.params.max_planning_retries:
            self._set(FAILED, now, f"{why} (gave up after {self.retries} retries)")
            return [STOP]
        self.retries += 1
        self._set(PLANNING, now, f"{why}; replanning ({self.retries}/{self.params.max_planning_retries})")
        return [PLAN]

    def arrived(self) -> bool:
        g = self.target or self.goal
        if g is None or self.pose is None:
            return False
        p = self.pose
        return (
            math.hypot(g.x - p.x, g.y - p.y) <= self.params.xy_goal_tolerance
            and abs(normalize_angle(g.theta - p.theta)) <= self.params.yaw_goal_tolerance
        )

    def status(self) -> dict:
        d = None
        if self.goal is not None and self.pose is not None:
            d = math.hypot(self.goal.x - self.pose.x, self.goal.y - self.pose.y)
        return {
            "state": self.state,
            "goal": asdict(self.goal) if self.goal else None,
            "target": asdict(self.target) if self.target else None,
            "pose": asdict(self.pose) if self.pose else None,
            "goal_distance": d,
            "retries": self.retries,
            "message": self.message,
            "goal_seq": self.goal_seq,
        }
