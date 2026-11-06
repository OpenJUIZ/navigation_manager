//! navigation_manager の状態機械。juiz に依存しない。
//!
//! ```text
//! IDLE ──goal──→ PLANNING ──path ok──→ FOLLOWING ──到着──→ SUCCEEDED
//!                  │ path ng / 時間切れ     │ 立ち往生(dwa_status の stuck)
//!                  ↓                        ↓
//!               (引き直し) ←────────────── PLANNING
//!                  │ max_replans を超えた
//!                  ↓
//!               FAILED
//! cancel: どの状態からでも CANCELED
//! ```
//!
//! 速度指令の門番([`Navigator::gate`])は FOLLOWING のときだけ指令を通し、それ以外は 0 にする。

use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::f64::consts::PI;

/// 平面上の姿勢(map 座標系)
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize, Default)]
pub struct Pose2D {
    pub x: f64,
    pub y: f64,
    pub theta: f64,
}

/// 平面上の速度(ロボット座標系)
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize, Default)]
pub struct Twist2D {
    pub vx: f64,
    pub vy: f64,
    pub omega: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NavState {
    Idle,
    Planning,
    Following,
    Succeeded,
    Failed,
    Canceled,
}

impl NavState {
    pub fn as_str(&self) -> &'static str {
        match self {
            NavState::Idle => "idle",
            NavState::Planning => "planning",
            NavState::Following => "following",
            NavState::Succeeded => "succeeded",
            NavState::Failed => "failed",
            NavState::Canceled => "canceled",
        }
    }
}

/// 設定。名前は ROS の move_base・dwa_local_planner に合わせる
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Params {
    /// 到着とみなす位置の差 [m]
    pub xy_goal_tolerance: f64,
    /// 到着とみなす向きの差 [rad]
    pub yaw_goal_tolerance: f64,
    /// ゴールを出してから経路が来るまで待つ時間 [s](move_base の planner_patience)
    pub planner_patience: f64,
    /// 経路の引き直しの上限(move_base の max_planning_retries)
    pub max_planning_retries: u32,
}

impl Default for Params {
    fn default() -> Self {
        Self {
            xy_goal_tolerance: 0.25,
            yaw_goal_tolerance: 0.35,
            planner_patience: 5.0,
            max_planning_retries: 3,
        }
    }
}

/// 外から見える状態(トピック nav_status・nav_get_status)
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Status {
    pub state: NavState,
    pub goal: Option<Pose2D>,
    pub pose: Option<Pose2D>,
    /// ゴールまでの直線距離 [m]
    pub goal_distance: Option<f64>,
    /// 今のゴールで経路を引き直した回数
    pub retries: u32,
    /// 状態が変わったわけ
    pub message: String,
    /// ゴールの通し番号(ゴールが来るたびに 1 増える)
    pub goal_seq: u64,
}

#[derive(Debug, Clone)]
pub struct Navigator {
    params: Params,
    state: NavState,
    goal: Option<Pose2D>,
    pose: Option<Pose2D>,
    retries: u32,
    since: f64,
    message: String,
    goal_seq: u64,
    /// 経路の引き直しを頼みたいゴール。[`Navigator::take_plan_request`] で取り出す
    plan_request: Option<Pose2D>,
}

/// 角度を -π〜π に
pub fn normalize_angle(a: f64) -> f64 {
    let mut a = (a + PI) % (2.0 * PI);
    if a < 0.0 {
        a += 2.0 * PI;
    }
    a - PI
}

impl Navigator {
    pub fn new(params: Params) -> Self {
        Self {
            params,
            state: NavState::Idle,
            goal: None,
            pose: None,
            retries: 0,
            since: 0.0,
            message: String::new(),
            goal_seq: 0,
            plan_request: None,
        }
    }

    pub fn params(&self) -> &Params {
        &self.params
    }

    pub fn state(&self) -> NavState {
        self.state
    }

    fn set_state(&mut self, state: NavState, now: f64, message: impl Into<String>) {
        self.state = state;
        self.since = now;
        self.message = message.into();
    }

    /// 新しいゴール。経路の計画を頼む(戻り値を planner に出す)
    pub fn set_goal(&mut self, goal: Pose2D, now: f64) -> Pose2D {
        self.goal = Some(goal);
        self.goal_seq += 1;
        self.retries = 0;
        self.plan_request = None;
        self.set_state(NavState::Planning, now, "goal received");
        goal
    }

    pub fn cancel(&mut self, now: f64) {
        if matches!(self.state, NavState::Planning | NavState::Following) {
            self.set_state(NavState::Canceled, now, "canceled");
        }
        self.plan_request = None;
    }

    /// 自己位置推定の結果(amcl_pose)
    pub fn on_pose(&mut self, pose: Pose2D, now: f64) {
        self.pose = Some(pose);
        if self.state == NavState::Following && self.arrived() {
            self.set_state(NavState::Succeeded, now, "goal reached");
        }
    }

    /// 大域経路の結果(path の ok)
    pub fn on_path(&mut self, ok: bool, message: &str, now: f64) {
        if self.state != NavState::Planning {
            return;
        }
        if ok {
            self.set_state(NavState::Following, now, "following the path");
        } else {
            self.retry(now, &format!("planning failed: {message}"));
        }
    }

    /// 局所計画の状態(dwa_status の status)。stuck なら経路を引き直す
    pub fn on_local_status(&mut self, status: &str, now: f64) {
        if self.state == NavState::Following && status == "stuck" {
            self.retry(now, "local planner is stuck");
        }
    }

    /// 周期処理。経路が来ないまま planner_patience を過ぎたら引き直す
    pub fn tick(&mut self, now: f64) {
        if self.state == NavState::Planning && now - self.since > self.params.planner_patience {
            self.retry(now, "no path within planner_patience");
        }
    }

    fn retry(&mut self, now: f64, why: &str) {
        if self.retries >= self.params.max_planning_retries {
            self.plan_request = None;
            self.set_state(
                NavState::Failed,
                now,
                format!("{why} (gave up after {} retries)", self.retries),
            );
            return;
        }
        self.retries += 1;
        self.plan_request = self.goal;
        self.set_state(
            NavState::Planning,
            now,
            format!(
                "{why}; replanning ({}/{})",
                self.retries, self.params.max_planning_retries
            ),
        );
    }

    /// 引き直しを頼みたいゴールを取り出す(1 回だけ返す)
    pub fn take_plan_request(&mut self) -> Option<Pose2D> {
        self.plan_request.take()
    }

    fn arrived(&self) -> bool {
        match (self.goal, self.pose) {
            (Some(g), Some(p)) => {
                (g.x - p.x).hypot(g.y - p.y) <= self.params.xy_goal_tolerance
                    && normalize_angle(g.theta - p.theta).abs() <= self.params.yaw_goal_tolerance
            }
            _ => false,
        }
    }

    /// 速度指令の門番。FOLLOWING のときだけ通す
    pub fn gate(&self, cmd: Twist2D) -> Twist2D {
        if self.state == NavState::Following {
            cmd
        } else {
            Twist2D::default()
        }
    }

    pub fn status(&self) -> Status {
        Status {
            state: self.state,
            goal: self.goal,
            pose: self.pose,
            goal_distance: match (self.goal, self.pose) {
                (Some(g), Some(p)) => Some((g.x - p.x).hypot(g.y - p.y)),
                _ => None,
            },
            retries: self.retries,
            message: self.message.clone(),
            goal_seq: self.goal_seq,
        }
    }
}

/// juiz のトピックの値(JSON)との受け渡し
pub mod json {
    use super::*;

    fn num(v: &Value, path: &[&str]) -> Option<f64> {
        let mut cur = v;
        for k in path {
            cur = cur.get(k)?;
        }
        cur.as_f64()
    }

    /// Pose2DStamped(`{header, pose: {position: {x, y}, theta}}`)か Pose2D を読む
    pub fn pose_from_value(v: &Value) -> Option<Pose2D> {
        let p = v.get("pose").unwrap_or(v);
        Some(Pose2D {
            x: num(p, &["position", "x"])?,
            y: num(p, &["position", "y"])?,
            theta: num(p, &["theta"]).unwrap_or(0.0),
        })
    }

    /// map 座標系の Pose2DStamped にする。時刻は now [s]
    pub fn pose_stamped(p: &Pose2D, now: f64) -> Value {
        let sec = now.floor();
        serde_json::json!({
            "header": {
                "stamp": { "sec": sec as i64, "nanosec": ((now - sec) * 1e9) as i64 },
                "frame_id": "map"
            },
            "pose": { "position": { "x": p.x, "y": p.y }, "theta": p.theta }
        })
    }

    /// Twist2D(`{linear: {x, y}, angular}`)を読む
    pub fn twist_from_value(v: &Value) -> Option<Twist2D> {
        Some(Twist2D {
            vx: num(v, &["linear", "x"])?,
            vy: num(v, &["linear", "y"]).unwrap_or(0.0),
            omega: num(v, &["angular"]).unwrap_or(0.0),
        })
    }

    pub fn twist_value(t: &Twist2D) -> Value {
        serde_json::json!({ "linear": { "x": t.vx, "y": t.vy }, "angular": t.omega })
    }

    /// planner_dijkstra の path(`{ok, message, poses, ...}`)の ok と message
    pub fn path_result(v: &Value) -> (bool, String) {
        let ok = v.get("ok").and_then(Value::as_bool).unwrap_or(false);
        let message = v
            .get("message")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        (ok, message)
    }

    /// planner_dwa の dwa_status の status
    pub fn local_status(v: &Value) -> String {
        v.get("status")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pose(x: f64, y: f64, theta: f64) -> Pose2D {
        Pose2D { x, y, theta }
    }

    #[test]
    fn goal_plan_follow_arrive() {
        let mut n = Navigator::new(Params::default());
        assert_eq!(n.state(), NavState::Idle);
        n.set_goal(pose(1.0, 2.0, 0.0), 0.0);
        assert_eq!(n.state(), NavState::Planning);
        n.on_path(true, "", 0.5);
        assert_eq!(n.state(), NavState::Following);
        n.on_pose(pose(0.0, 0.0, 0.0), 1.0);
        assert_eq!(n.state(), NavState::Following);
        n.on_pose(pose(1.1, 2.1, 0.1), 2.0);
        assert_eq!(n.state(), NavState::Succeeded);
    }

    #[test]
    fn arrival_needs_heading() {
        let mut n = Navigator::new(Params::default());
        n.set_goal(pose(1.0, 0.0, 0.0), 0.0);
        n.on_path(true, "", 0.0);
        n.on_pose(pose(1.0, 0.0, 1.0), 1.0);
        assert_eq!(n.state(), NavState::Following);
        // -π と π は同じ向き
        n.set_goal(pose(1.0, 0.0, PI), 2.0);
        n.on_path(true, "", 2.0);
        n.on_pose(pose(1.0, 0.0, -PI + 0.01), 3.0);
        assert_eq!(n.state(), NavState::Succeeded);
    }

    #[test]
    fn gate_passes_only_while_following() {
        let mut n = Navigator::new(Params::default());
        let cmd = Twist2D {
            vx: 0.3,
            vy: 0.0,
            omega: 0.1,
        };
        assert_eq!(n.gate(cmd), Twist2D::default());
        n.set_goal(pose(1.0, 0.0, 0.0), 0.0);
        assert_eq!(n.gate(cmd), Twist2D::default());
        n.on_path(true, "", 0.0);
        assert_eq!(n.gate(cmd), cmd);
        n.cancel(1.0);
        assert_eq!(n.state(), NavState::Canceled);
        assert_eq!(n.gate(cmd), Twist2D::default());
    }

    #[test]
    fn stuck_replans_then_fails() {
        let mut n = Navigator::new(Params {
            max_planning_retries: 2,
            ..Params::default()
        });
        let g = pose(3.0, 0.0, 0.0);
        n.set_goal(g, 0.0);
        n.on_path(true, "", 0.0);
        for i in 1..=2 {
            n.on_local_status("stuck", i as f64);
            assert_eq!(n.state(), NavState::Planning);
            assert_eq!(n.take_plan_request(), Some(g));
            assert_eq!(n.take_plan_request(), None);
            n.on_path(true, "", i as f64);
        }
        n.on_local_status("stuck", 9.0);
        assert_eq!(n.state(), NavState::Failed);
        assert_eq!(n.take_plan_request(), None);
    }

    #[test]
    fn planner_patience_and_failed_path() {
        let mut n = Navigator::new(Params::default());
        n.set_goal(pose(1.0, 0.0, 0.0), 0.0);
        n.tick(4.0);
        assert_eq!(n.status().retries, 0);
        n.tick(5.5);
        assert_eq!(n.status().retries, 1);
        n.on_path(false, "no route", 6.0);
        assert_eq!(n.status().retries, 2);
        assert!(n.status().message.contains("no route"));
    }

    #[test]
    fn new_goal_resets_retries() {
        let mut n = Navigator::new(Params::default());
        n.set_goal(pose(1.0, 0.0, 0.0), 0.0);
        n.on_path(false, "", 0.0);
        assert_eq!(n.status().retries, 1);
        n.set_goal(pose(2.0, 0.0, 0.0), 1.0);
        assert_eq!(n.status().retries, 0);
        assert_eq!(n.status().goal_seq, 2);
    }

    #[test]
    fn json_round_trip() {
        let p = pose(1.5, -2.0, 0.5);
        let v = json::pose_stamped(&p, 12.25);
        assert_eq!(json::pose_from_value(&v), Some(p));
        assert_eq!(v["header"]["stamp"]["sec"], 12);
        let t = Twist2D {
            vx: 0.2,
            vy: 0.0,
            omega: -0.3,
        };
        assert_eq!(json::twist_from_value(&json::twist_value(&t)), Some(t));
        let path = serde_json::json!({"ok": false, "message": "blocked", "poses": []});
        assert_eq!(json::path_result(&path), (false, "blocked".to_string()));
        assert_eq!(
            json::local_status(&serde_json::json!({"status": "stuck"})),
            "stuck"
        );
    }
}
