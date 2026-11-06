# navigation_manager

[JUIZ](https://github.com/OpenJUIZ/juiz) で作った 2 次元のナビゲーション一式
([mobile_sim](https://github.com/OpenJUIZ/mobile_sim)・[localization_amcl](https://github.com/OpenJUIZ/localization_amcl)・
planner_dijkstra・planner_dwa)の根になる juizapp。

- 葉どうしのトピックを受けて `nav/...` の名前で出し直す(juiz 0.2.4 では葉どうしの間で自動では中継されないため)
- ゴール → 経路の計画 → 追従 → 到着 の状態機械、経路の引き直し、速度指令の門番

```
navigation_manager (HTTP 8005)
 ├─ localization_amcl ── mobile_sim
 ├─ planner_dijkstra
 └─ planner_dwa
```

計画とトピックの一覧は [docs/plan.md](docs/plan.md)、木の形を決めた実験は [experiments/m0_topic_relay](experiments/m0_topic_relay)。

## 開発

```
scripts/dev_plugin.sh            # プラグインをビルドして app/plugins/<target>/ に置く
cargo test --workspace
pip install juiz-cli==0.2.4
python3 scripts/smoke_test.py    # app/smoke_test.conf を起動して HTTP から確かめる
```

## 一式を動かす

```
python3 scripts/run_stack.py --mobile-sim <dir> --amcl <dir> --planner <dir> --dwa <dir>
curl -X PATCH 'localhost:8005/api/topic/push?topic_name=goal' -H 'Content-Type: application/json' \
  -d '{"input": {"header": {"stamp": {"sec": 0, "nanosec": 0}, "frame_id": "map"}, "pose": {"position": {"x": 0.0, "y": 1.5}, "theta": 0.0}}}'
```

`<dir>` はそれぞれのアプリの app.conf があるディレクトリ(.juizapp を展開したもの、またはリポジトリの app/)。

## ライセンス

MIT OR Apache-2.0
