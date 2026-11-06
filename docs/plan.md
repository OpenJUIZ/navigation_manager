# navigation_manager 計画

ナビゲーション一式(mobile_sim・localization_amcl・planner_dijkstra・planner_dwa)の根になる juizapp。
葉どうしのトピックの中継と、ゴールから到着までの状態機械を受け持つ。

設計の議論: プロジェクトのスレッド「Design navigation_manager integration module」と設計案のドキュメント。

## 1. 木の形

juiz では、トピックを受ける側が出す側を `subsystems` に書き、購読の要求はその木をたどる。
1 つの System が覚える master は 1 つだけなので、2 つの System から subsystem にされると(星形)トピックが届かない。
輪にすると起動時に止まる(OpenJUIZ/juiz#449)。そこで navigation_manager を根にした 1 本の木にする。

```
navigation_manager (HTTP 8005, IPC navigation_manager)
 ├─ localization_amcl (8002)  ── mobile_sim (8000)   ← AMCL の今の conf のまま
 ├─ planner_dijkstra (8003)                          ← 葉。mobile_sim にはつながない
 └─ planner_dwa (8004)                               ← 葉。mobile_sim にはつながない
```

- mobile_sim は localization_amcl の下に残す(2026-11-06 に ysuga が決定)。AMCL は高い頻度の scan を直接受けられ、conf を変えずに済む。
- planner_dijkstra・planner_dwa は mobile_sim に直接つながない。つなぐと mobile_sim の master が 2 つになり、manager にトピックが届かなくなる(下の M0)。
  `juiz run` は juizapp.yaml の `depends` を自動で subsystem に足すので、この 2 つは「navigation_manager の下で動かす」conf を `juiz -f` で起動する。
- 根は最後に起動する。先に起動すると葉につながらず、起動に失敗する。

## 2. M0 で確かめたこと(experiments/m0_topic_relay)

juiz 0.2.4。トピックを出すだけ・受けるだけの試験用プラグインで確かめた。

| 経路 | 結果 |
|---|---|
| 根 → 葉 | 届く |
| 葉 → 根(2 段上りも) | 届く |
| 葉 → 根 → 別の葉 | 届かない(各 System がトピックをつなぐのは自分の起動時だけで、そのとき根がまだいない) |
| 根が受けたトピックを同じ名前で出し直す | 自分で受けて無限に回る |
| 根が別の名前(`nav/odom`)で出し直し、葉がその名前を購読 | 届く |
| 根 → AMCL → mobile_sim(2 段下り) | 届く |
| mobile_sim の master が 2 つ | manager に何も届かない |

## 3. トピック

| トピック | 出す側 | 受ける側 | navigation_manager の Process |
|---|---|---|---|
| odom・scan・lidar_mount | mobile_sim | localization_amcl(直接)、manager | `nav_relay` → `nav/odom`・`nav/scan`・`nav/lidar_mount` |
| `nav/odom`・`nav/scan`・`nav/lidar_mount` | manager | planner_dwa | |
| amcl_pose | localization_amcl | manager | `nav_on_pose`(到着の判定)→ `nav/amcl_pose` |
| `nav/amcl_pose` | manager | planner_dijkstra(始点)、planner_dwa | |
| goal | 画面・他のアプリ・HTTP の topic/push | manager | `nav_set_goal` → `nav/goal` |
| `nav/goal` | manager(`nav_set_goal`) | planner_dijkstra | |
| path | planner_dijkstra | manager | `nav_on_path`(成否)→ `nav/path` |
| `nav/path` | manager | planner_dwa | |
| cmd_vel_dwa | planner_dwa | manager | `nav_gate_cmd`(FOLLOWING のときだけ通す)→ cmd_vel |
| cmd_vel | manager(`nav_gate_cmd`・`nav_cancel`) | mobile_sim(AMCL の下、2 段下り) | |
| dwa_status | planner_dwa | manager | `nav_on_local_status`(stuck なら引き直し) |
| nav_status | manager(`nav_get_status`、2 Hz) | UI など | |

値はすべて JSON(`Value`)のまま受け渡すので、juiz の型の検査はかからない。

Process の戻り値がトピックに出るのは、EC やトピックで動いたとき(execute)だけで、HTTP の `call` では出ない。
そのためゴールはトピック goal に流す(HTTP なら `/api/topic/push?topic_name=goal`)。キャンセルは `/api/container_process/execute` で動かす。

## 3.1 起動(scripts/run_stack.py)

葉を先に、根を最後に起動する。planner_dijkstra・planner_dwa は、それぞれの app.conf から `scripts/make_leaf_conf.py` で葉の conf を作り
(置き換えの表は `app/leaves/<アプリ名>.yaml`)、`juiz -f` で起動する。アプリの版が上がっても、表だけ見直せばよい。

```
python3 scripts/run_stack.py --mobile-sim <dir> --amcl <dir> --planner <dir> --dwa <dir> [--test]
```

`--test` はゴール (0.0, 1.5, 0) を出し、SUCCEEDED になって mobile_sim の真の姿勢がゴールから 0.5 m 以内にあることを確かめる(CI の e2e)。
地図は、今は 3 つとも同じ既定の地図(mobile_sim の assets/maps/default.yaml と同じもの)を自分で読んでいる。
実行中に地図を替えたときに配る仕組み(mobile_sim の get_map・get_map_info → planner_set_map・amcl_set_map)は M4 の UI と一緒に作る。

## 4. 状態機械(crates/navigation_manager_core)

```
IDLE ──goal──→ PLANNING ──path ok──→ FOLLOWING ──到着──→ SUCCEEDED
                 │ path ng / planner_patience 切れ │ dwa_status が stuck
                 ↓                                 ↓
              (引き直し、max_planning_retries まで) → PLANNING
                 │ 上限を超えた
                 ↓
              FAILED
cancel: PLANNING・FOLLOWING から CANCELED(0 の速度を出す)
```

- 到着: 推定姿勢とゴールの距離 ≤ `xy_goal_tolerance`(0.25 m)、向きの差 ≤ `yaw_goal_tolerance`(0.35 rad)。
- 名前と既定値は ROS の move_base(`planner_patience` 5 s、`max_planning_retries` 3)と dwa_local_planner に合わせる。
- 速度の門番: FOLLOWING のときだけ planner_dwa の指令を通し、それ以外は 0 を出す。

### 引き直しのときにゴールを出し直す方法(M3 で決める)

juiz の Process は、戻り値を必ず publish する(Err を返すと publish しないが、System をまたぐ転送では失敗が続くと転送先から外される)。
そのため「引き直すときだけ nav/goal を出す」Process を素直には書けない。状態機械は引き直しのゴールを `take_plan_request` で 1 回だけ返す作りにしてあり、
出し方は M3 で次のどれかに決める。

1. manager が自分の `nav_set_goal` を HTTP で呼び直す(juiz の変更は要らない)
2. planner_dijkstra に、ゴールの通し番号が同じなら計画し直さない作りを足し、manager は FOLLOWING 以外の間 nav/goal を出し続ける
3. juiz に「値が無いときは publish しない」戻り値を足す(issue を出す)

## 5. マイルストーン

| | 内容 | 状態 |
|---|---|---|
| M0 | 木の中継の検証(experiments/m0_topic_relay、CI で再現) | 済 |
| M1 | 計画書、Rust の骨組み(core の状態機械 + juiz の component)、app.conf、juizapp.yaml、CI(check・smoke・M0) | 済(#1) |
| M2 | planner_dijkstra・planner_dwa の葉の conf(app/leaves/)と起動スクリプト、4 アプリそろえた結合テスト(ゴールに着く) | この PR |
| M3 | 状態機械の仕上げ(引き直しのゴールの出し方、初期位置、自己位置の喪失) | |
| M4 | 地図の配布、統合 UI(HTTP 8005: 地図・真の姿勢・推定姿勢・大域経路・局所経路・スキャン、ゴール指定、キャンセル、状態) | |
| M5 | Docker(5 つまとめた compose、amd64/arm64)、リリース(.juizapp) | |

## 6. 未決事項

- 初期位置: シミュレーションでは mobile_sim の真値から自動で与えるか(推奨)、UI で与えるか。
- 地図の出どころ: まずは mobile_sim の地図で自己位置推定ナビだけにし、mapper_gmapping を使う SLAM ナビは後回し(推奨)。
- 各アプリの UI は残したまま、統合 UI を足す(推奨)。
