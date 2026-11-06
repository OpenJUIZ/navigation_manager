//! navigation_hub の juiz コンポーネント。
//!
//! navigation_hub はナビゲーション一式の木の根で、localization_amcl(その下に mobile_sim)・planner_dijkstra・planner_dwa を
//! subsystem に持つ。juiz 0.2.4 では葉どうしの間でトピックが自動では中継されない(experiments/m0_topic_relay)ので、
//! 葉から上がってきたトピックをここで受けて、別の名前で出し直す。同じ名前で出し直すと自分で受けて回り続けるため、名前を変える。
//!
//! どのトピックをどの名前で出し直すかは app/app.conf の `subscribes`・`publishes` に書く。
//! ゴールや引き直し・キャンセルなどの判断は Python の navigation_manager(manager/)が HTTP で各アプリを呼んで行う。

use juiz_sdk::prelude::*;

/// 受けた値をそのまま返す。conf で subscribes と publishes に別の名前を書いて、トピックを出し直すのに使う
#[juiz_component_process(
    description = "Return the value as is. Used to republish a topic under another name"
    arguments = { default = { value = {} } }
)]
fn nav_relay(value: Value) -> JuizResult<Value> {
    Ok(value)
}

juiz_component_manifest!(
    component_name = "navigation_hub"
    processes = [
        nav_relay
    ]
);
