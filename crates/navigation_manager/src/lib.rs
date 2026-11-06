//! navigation_manager の juiz コンポーネント。
//!
//! navigation_manager はナビゲーション一式の根で、localization_amcl(その下に mobile_sim)・planner_dijkstra・planner_dwa を
//! subsystem に持つ。juiz 0.2.4 では葉どうしの間でトピックが自動では中継されない(experiments/m0_topic_relay)ので、
//! 葉から上がってきたトピックをここで受けて、`nav/...` の名前で出し直す。同じ名前で出し直すと自分で受けて回り続けるため、名前を変える。
//!
//! | Process | 引数 | 戻り値(conf の publishes で出す先) |
//! | --- | --- | --- |
//! | `nav_relay` | `value` | `value` をそのまま(odom → nav/odom など) |
//! | `nav_set_goal` | `goal`(トピック goal) | ゴールの Pose2DStamped(nav/goal) |
//! | `nav_cancel` | なし | 0 の Twist2D(cmd_vel。止める) |
//! | `nav_get_status` | なし | 状態(nav_status) |
//! | `nav_on_pose` | `pose`(amcl_pose) | `pose` をそのまま(nav/amcl_pose) |
//! | `nav_on_path` | `path`(path) | `path` をそのまま(nav/path) |
//! | `nav_on_local_status` | `status`(dwa_status) | `status` をそのまま |
//! | `nav_gate_cmd` | `cmd`(cmd_vel_dwa) | FOLLOWING なら `cmd`、それ以外は 0(cmd_vel) |
//!
//! 戻り値がトピックに出るのは、EC やトピックで動いたとき(execute)だけで、HTTP の call では出ない。
//! そのためゴールはトピック goal に流し(HTTP なら /api/topic/push)、キャンセルは HTTP の execute で動かす。
//!
//! 状態機械の中身は `navigation_manager_core` にある。

use juiz_sdk::anyhow::anyhow;
use juiz_sdk::prelude::*;
use navigation_manager_core::{json, Navigator, Params};
use std::time::{SystemTime, UNIX_EPOCH};

pub use navigation_manager_core as core;

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn err(message: String) -> JuizError {
    JuizError::InvalidValueError { message }
}

/// 受けた値をそのまま返す。conf で subscribes と publishes に別の名前を書いて、トピックを出し直すのに使う
#[juiz_component_process(
    description = "Return the value as is. Used to republish a topic under another name"
    arguments = { default = { value = {} } }
)]
fn nav_relay(value: Value) -> JuizResult<Value> {
    Ok(value)
}

pub struct NavManager {
    pub nav: Navigator,
}

#[juiz_component_container(
    description = "Navigation state machine: goal -> plan -> follow -> arrive, with replanning and a velocity gate"
    arguments = {
        default = {
            xy_goal_tolerance = 0.25
            yaw_goal_tolerance = 0.35
            planner_patience = 5.0
            max_planning_retries = 3
        }
        description = {
            xy_goal_tolerance = "Distance to the goal regarded as arrived [m]"
            yaw_goal_tolerance = "Heading difference regarded as arrived [rad]"
            planner_patience = "Seconds to wait for a path before replanning"
            max_planning_retries = "Replans allowed for one goal before FAILED"
        }
    }
)]
fn nav_manager(
    _name: String,
    xy_goal_tolerance: f64,
    yaw_goal_tolerance: f64,
    planner_patience: f64,
    max_planning_retries: i64,
) -> JuizResult<Box<NavManager>> {
    if max_planning_retries < 0 {
        return Err(anyhow!(err("max_planning_retries must be >= 0".into())));
    }
    Ok(Box::new(NavManager {
        nav: Navigator::new(Params {
            xy_goal_tolerance,
            yaw_goal_tolerance,
            planner_patience,
            max_planning_retries: max_planning_retries as u32,
        }),
    }))
}

#[juiz_component_container_process(
    container_type = "nav_manager"
    description = "Set a goal (Pose2DStamped or Pose2D, map frame). Returns the goal as Pose2DStamped for the planner"
    arguments = { default = { goal = {} } }
)]
fn nav_set_goal(container: &mut ContainerImpl<NavManager>, goal: Value) -> JuizResult<Value> {
    let g = json::pose_from_value(&goal)
        .ok_or_else(|| anyhow!(err(format!("goal is not a pose: {goal}"))))?;
    let t = now();
    let g = container.nav.set_goal(g, t);
    Ok(json::pose_stamped(&g, t))
}

#[juiz_component_container_process(
    container_type = "nav_manager"
    description = "Cancel the goal. Returns a zero Twist2D to stop the robot"
)]
fn nav_cancel(container: &mut ContainerImpl<NavManager>) -> JuizResult<Value> {
    container.nav.cancel(now());
    Ok(json::twist_value(&Default::default()))
}

#[juiz_component_container_process(
    container_type = "nav_manager"
    description = "Navigation status. Also checks timeouts (bind it to an EC)"
)]
fn nav_get_status(container: &mut ContainerImpl<NavManager>) -> JuizResult<Value> {
    container.nav.tick(now());
    Ok(jvalue!(container.nav.status()))
}

#[juiz_component_container_process(
    container_type = "nav_manager"
    description = "Receive the estimated pose (amcl_pose) and return it as is"
    arguments = { default = { pose = {} } }
)]
fn nav_on_pose(container: &mut ContainerImpl<NavManager>, pose: Value) -> JuizResult<Value> {
    if let Some(p) = json::pose_from_value(&pose) {
        container.nav.on_pose(p, now());
    }
    Ok(pose)
}

#[juiz_component_container_process(
    container_type = "nav_manager"
    description = "Receive the global path and return it as is"
    arguments = { default = { path = {} } }
)]
fn nav_on_path(container: &mut ContainerImpl<NavManager>, path: Value) -> JuizResult<Value> {
    let (ok, message) = json::path_result(&path);
    container.nav.on_path(ok, &message, now());
    Ok(path)
}

#[juiz_component_container_process(
    container_type = "nav_manager"
    description = "Receive the local planner status (dwa_status) and return it as is"
    arguments = { default = { status = {} } }
)]
fn nav_on_local_status(
    container: &mut ContainerImpl<NavManager>,
    status: Value,
) -> JuizResult<Value> {
    container
        .nav
        .on_local_status(&json::local_status(&status), now());
    Ok(status)
}

#[juiz_component_container_process(
    container_type = "nav_manager"
    description = "Pass the velocity command only while following a path, otherwise zero"
    arguments = { default = { cmd = {} } }
)]
fn nav_gate_cmd(container: &mut ContainerImpl<NavManager>, cmd: Value) -> JuizResult<Value> {
    let c = json::twist_from_value(&cmd).unwrap_or_default();
    Ok(json::twist_value(&container.nav.gate(c)))
}

juiz_component_manifest!(
    component_name = "navigation_manager"
    containers = {
        nav_manager = [
            nav_set_goal,
            nav_cancel,
            nav_get_status,
            nav_on_pose,
            nav_on_path,
            nav_on_local_status,
            nav_gate_cmd
        ]
    }
    processes = [
        nav_relay
    ]
);
