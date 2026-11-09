# navigation_manager 計画

ナビゲーション一式(mobile_sim・localization_amcl・planner_dijkstra・planner_dwa)をまとめて動かす。2 つの部品からなる。

| 部品 | 中身 | 役目 |
|---|---|---|
| navigation_hub(`app/`・`crates/navigation_hub`、juizapp、HTTP 8006) | Rust の juiz プラグイン(`nav_relay` だけ) | 木の根。葉どうしのトピック(センサー・推定姿勢・速度指令)を別の名前で出し直す |
| navigation_manager(`manager/`、Python、HTTP 8005) | 標準ライブラリだけの普通の Python のアプリ | 判断と指示。画面のイベントを受けて、各アプリの Process を HTTP で呼ぶ。状態機械、画面 |

2026-11-06 に ysuga と決めた分担: 高い頻度のデータの受け渡しは juiz のトピック(navigation_hub)、
ゴール・引き直し・キャンセル・初期位置のような「必要なときだけ」の指示は Python から HTTP で呼ぶ。
juiz の Process は戻り値を必ず publish する(HTTP の `call` では publish しない)ので、指示を juiz のトピックで書くと
「必要なときだけ出す」が書きにくい。Python から `call` すれば、呼んだときだけ動く。

## 1. 木の形

juiz では、トピックを受ける側が出す側を `subsystems` に書き、購読の要求はその木をたどる。
1 つの System が覚える master は 1 つだけなので、2 つの System から subsystem にされると(星形)トピックが届かない。
輪にすると起動時に止まる(OpenJUIZ/juiz#449)。そこで navigation_hub を根にした 1 本の木にする。

```
navigation_hub (8006) ─┬─ localization_amcl (8002) ── mobile_sim (8000)   ← AMCL の今の conf のまま
                       ├─ planner_dijkstra (8003)                         ← 葉。mobile_sim にはつながない
                       └─ planner_dwa (8004)                              ← 葉。mobile_sim にはつながない

navigation_manager (8005, Python) ── HTTP ──→ 4 つのアプリ
```

- mobile_sim は localization_amcl の下に残す(2026-11-06 に ysuga が決定)。AMCL は高い頻度の scan を直接受けられ、conf を変えずに済む。
- planner_dijkstra・planner_dwa は mobile_sim に直接つながない。つなぐと mobile_sim の master が 2 つになり、トピックが届かなくなる。
  `juiz run` は juizapp.yaml の `depends` を自動で subsystem に足すので、この 2 つは `juiz run --depend mobile_sim=none` で起動する
  (juiz 0.2.5 から。それまでは葉の conf を `scripts/make_leaf_conf.py` で作って `juiz -f` で起動していた。Docker の葉のイメージは今もそうする)。
- 根(navigation_hub)は 4 つの後に起動する。先に起動すると葉につながらず、起動に失敗する。navigation_manager は最後。
- この起動は `navigation-manager stack <.juizapp…>`(wheel に入っている)が全部行う。mobile_sim・localization_amcl・navigation_hub は `juiz run`、
  葉の 2 つも `juiz run` で、navigation_hub の .juizapp の `leaves/` の subscribes・publishes を `--remap 旧=新` にして渡す
  (juiz#451、0.3.1 までは .juizapp を展開して葉の conf を作り `juiz -f`)。planner_dwa の bridge は `juiz run` では conf を上書きできないので、
  環境変数 DWA_BRIDGE_AMCL_URL・DWA_BRIDGE_PLANNER_URL を空にして止める(Docker の葉と同じ)。
  navigation_hub を `juiz run` で起動しても木がつながり、ゴールに着くことを 2026-11-08 に公開版で確かめた。
  ただし根が起動直後に葉へつなぎ損ねることがある(CI で `receiving a response from ipc://localization_amcl failed` が出て、推定姿勢が planner に届かなかった)。
  そのため stack は根の起動後、planner_dijkstra の始点と planner_dwa の推定姿勢・障害物が埋まる(トピックが木を通って葉に届く)まで待ち、
  20 秒で届かなければ根を 1 度だけ起動し直す。

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
| mobile_sim の master が 2 つ | 根に何も届かない |

## 3. データの流れ

### juiz のトピック(navigation_hub が出し直す)

| トピック | 出す側 | navigation_hub | 受ける側 |
|---|---|---|---|
| odom・scan・lidar_mount | mobile_sim | → `nav/odom`・`nav/scan`・`nav/lidar_mount` | localization_amcl(元の名前で直接)、planner_dwa |
| amcl_pose | localization_amcl | → `nav/amcl_pose` | planner_dijkstra(始点)、planner_dwa |
| cmd_vel_dwa | planner_dwa | → `cmd_vel` | mobile_sim(AMCL の下、2 段下り) |

### HTTP の呼び出し(navigation_manager が呼ぶ)

| いつ | 呼ぶもの |
|---|---|
| 周期(5 Hz) | localization_amcl `amcl_get_pose`(到着の判定)、planner_dwa `dwa_get_status`(立ち往生) |
| ゴール・引き直し | planner_dijkstra `planner_set_goal`(計画して経路を返す)→ planner_dwa `dwa_set_path` |
| キャンセル・到着・失敗 | planner_dwa `dwa_cancel` |
| 初期位置 | mobile_sim `mobile_sim_get_pose`(真の姿勢)→ localization_amcl `amcl_set_initial_pose` |
| 起動時・画面のボタン(地図の配布) | mobile_sim `mobile_sim_get_map`・`mobile_sim_get_map_info`、または地図の YAML(`--map`・`/api/load_map`・画面の「地図を読む」)→ planner_dijkstra `planner_set_map`・localization_amcl `amcl_set_map`(multipart の PUT。追っている途中なら先に止める) |
| 画面 | 配った地図(manager が覚えている。まだ配っていなければ mobile_sim `mobile_sim_get_map`・`mobile_sim_get_map_info`)、mobile_sim `mobile_sim_get_pose`、localization_amcl `amcl_get_state`(粒子)、planner_dwa `dwa_get_state`(局所計画・障害物) |

経路はトピック path を使わず、manager が planner から受けて DWA に渡す(どのゴールの経路かを manager が知っているため)。

## 4. 状態機械(manager/navigation_manager/state.py)

```
IDLE ──goal──→ PLANNING ──path ok──→ FOLLOWING ──到着──→ SUCCEEDED
                 │ path ng                 │ planner_dwa の status が stuck
                 ↓                         ↓
              (引き直し、max_planning_retries まで) → PLANNING
                 │ 上限を超えた
                 ↓
              FAILED
cancel: PLANNING・FOLLOWING から CANCELED
```

- 到着: 推定姿勢とゴールの距離 ≤ `xy_goal_tolerance`(0.25 m)、向きの差 ≤ `yaw_goal_tolerance`(0.35 rad)。到着したら planner_dwa を止める。
- 名前と既定値は ROS の move_base(`max_planning_retries` 3)と dwa_local_planner に合わせる。
- 状態機械は「何をすべきか」(PLAN・STOP)を返すだけで、HTTP で呼ぶのは manager.py。単体テストは manager/tests。

## 5. 画面と API(HTTP 8005)

画面: 地図(配った地図。mobile_sim か、`--map`・「地図を読む」で読んだ YAML)、真の姿勢、推定姿勢、大域経路、局所計画(DWA の最良の軌道)、障害物(DWA が使うスキャン点)、粒子(AMCL)、ゴール。局所計画・障害物・粒子は表示を切り替えられる。地図をクリックしてドラッグすると、その向きのゴールを出す。キャンセル、初期位置を真の姿勢に、mobile_sim の地図を配る、地図を読む(YAML と画像を選ぶ)。

| API | 内容 |
|---|---|
| GET /api/status・/api/view | 状態(画面用は真の姿勢・経路・局所計画・障害物・粒子つき) |
| GET /api/map・/api/map_info | 画面に描く地図(配った地図、まだ配っていなければ mobile_sim の地図)。map_info は width・height・source つき |
| POST /api/goal `{x, y, theta}` | ゴール |
| POST /api/cancel | キャンセル |
| POST /api/initial_pose `{x, y, theta}` または空 | localization_amcl の初期位置(空なら mobile_sim の真の姿勢) |
| POST /api/distribute_map | mobile_sim の地図を planner_dijkstra・localization_amcl に渡す(起動時にも渡す。`--no-distribute-map` で止める) |
| POST /api/load_map `{path}` または `{yaml, images, name}` | 地図の YAML(ROS の map_server の形、画像は PNG・PGM)を読んで同じく渡す。起動時は `--map`。画面からは YAML と画像を base64 で送る |

## 6. マイルストーン

| | 内容 | 状態 |
|---|---|---|
| M0 | 木の中継の検証(experiments/m0_topic_relay、CI で再現) | 済(#1) |
| M1 | 計画書と骨組み | 済(#1) |
| M2 | 葉の conf と起動スクリプト、4 アプリそろえた結合テスト | 済(#2) |
| M3 | Python の navigation_manager に作り直す(状態機械・HTTP での指示・画面)。Rust 側は navigation_hub(中継だけ)に | 済(#3) |
| M4 | 地図の配布(mobile_sim の地図を planner・AMCL に set_map で渡す)、局所計画・スキャン・粒子の表示 | 済(#4) |
| M5 | Docker(5 つ + manager の compose、amd64/arm64)、リリース(navigation_hub の .juizapp、manager の Python パッケージ) | 済(#5) |
| M6 | 地図の YAML を配る(`--map`・`/api/load_map`・画面の「地図を読む」)、画面に配った地図を描く、e2e に SLAM の地図 | この PR |

## 7. Docker とリリース(M5)

コンテナどうしは IPC でつながらないので、木の枝はすべて HTTP にする(木の形は同じ。HTTP でも到着とキャンセルを確かめた)。

| サービス | イメージ | 起動のしかた |
|---|---|---|
| mobile_sim | ghcr.io/openjuiz/mobile_sim | そのまま |
| localization_amcl | ghcr.io/openjuiz/localization_amcl | そのまま(entrypoint が mobile_sim を HTTP の subsystem にする) |
| planner_dijkstra・planner_dwa | 元のイメージから docker/leaf.Dockerfile で作る | 元の app.conf から make_leaf_conf.py で葉の conf を作ってイメージに入れ、`juiz -f` で動かす(元の entrypoint は mobile_sim を subsystem にしてしまうため) |
| navigation_hub | ghcr.io/openjuiz/navigation_hub(docker/Dockerfile) | docker/hub-entrypoint.py が subsystem を HTTP に置き換え、3 つの葉が応答してから起動する(根は最後) |
| navigation_manager | ghcr.io/openjuiz/navigation_manager(docker/manager.Dockerfile) | 全部が応答するまで待ってから起動する(`--hub`) |

- CI(.github/workflows/docker.yml): amd64・arm64 でイメージを作り、compose で一式を動かして `run_stack.py --attach --test`。main で :edge、タグ v* で :<版> と :latest を公開する。
- リリース(.github/workflows/release.yml): navigation_hub の .juizapp(5 ターゲット、app/leaves も入れる)と navigation_manager の wheel・sdist。2 つは同じ版で出す。

## 8. 未決事項

- 地図の出どころ: mobile_sim の地図のほか、M6 で SLAM(mapper_gmapping)で作って保存した地図の YAML を配れるようにした。走りながら地図を作る SLAM ナビは後回し。
- navigation_manager の配り方: pip(Releases の wheel、または `pip install ./manager`)と Docker(M5)。juizapp には入れない。
