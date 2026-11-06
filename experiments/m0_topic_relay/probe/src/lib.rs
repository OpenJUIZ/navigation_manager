use juiz_sdk::prelude::*;
fn now() -> u128 { std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_millis() % 100000 }
#[juiz_component_process]
fn src_odom() -> JuizResult<Capsule> {
    let s = format!("odom@{}", now());
    println!("PROBE SEND odom: {}", s);
    Ok(jvalue!(s).into())
}
#[juiz_component_process]
fn src_goal() -> JuizResult<Capsule> {
    let s = format!("goal@{}", now());
    println!("PROBE SEND goal: {}", s);
    Ok(jvalue!(s).into())
}
#[juiz_component_process]
fn src_amcl_pose() -> JuizResult<Capsule> {
    let s = format!("amcl_pose@{}", now());
    println!("PROBE SEND amcl_pose: {}", s);
    Ok(jvalue!(s).into())
}
#[juiz_component_process]
fn src_path() -> JuizResult<Capsule> {
    let s = format!("path@{}", now());
    println!("PROBE SEND path: {}", s);
    Ok(jvalue!(s).into())
}
#[juiz_component_process]
fn src_cmd_vel() -> JuizResult<Capsule> {
    let s = format!("cmd_vel@{}", now());
    println!("PROBE SEND cmd_vel: {}", s);
    Ok(jvalue!(s).into())
}
#[juiz_component_process]
fn echo_odom(arg1: String) -> JuizResult<Capsule> {
    println!("PROBE RECV odom: {}", arg1);
    Ok(jvalue!(format!("{}>odom", arg1)).into())
}
#[juiz_component_process]
fn echo_amcl_pose(arg1: String) -> JuizResult<Capsule> {
    println!("PROBE RECV amcl_pose: {}", arg1);
    Ok(jvalue!(format!("{}>amcl_pose", arg1)).into())
}
#[juiz_component_process]
fn echo_goal(arg1: String) -> JuizResult<Capsule> {
    println!("PROBE RECV goal: {}", arg1);
    Ok(jvalue!(format!("{}>goal", arg1)).into())
}
#[juiz_component_process]
fn echo_path(arg1: String) -> JuizResult<Capsule> {
    println!("PROBE RECV path: {}", arg1);
    Ok(jvalue!(format!("{}>path", arg1)).into())
}
#[juiz_component_process]
fn echo_cmd_vel(arg1: String) -> JuizResult<Capsule> {
    println!("PROBE RECV cmd_vel: {}", arg1);
    Ok(jvalue!(format!("{}>cmd_vel", arg1)).into())
}
juiz_component_manifest!( component_name = "nav_probe" processes = [ src_odom, src_goal, src_amcl_pose, src_path, src_cmd_vel, echo_odom, echo_amcl_pose, echo_goal, echo_path, echo_cmd_vel ] );
