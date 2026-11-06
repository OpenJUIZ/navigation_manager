# navigation_manager

[JUIZ](https://github.com/OpenJUIZ/juiz) で作った 2 次元のナビゲーション一式
([mobile_sim](https://github.com/OpenJUIZ/mobile_sim)・[localization_amcl](https://github.com/OpenJUIZ/localization_amcl)・
[planner_dijkstra](https://github.com/OpenJUIZ/planner_dijkstra)・[planner_dwa](https://github.com/OpenJUIZ/planner_dwa))をまとめて動かす。

- **navigation_hub**(`app/`、juizapp、HTTP 8006): 木の根。葉どうしのトピックを別の名前で出し直す(juiz 0.2.4 では葉どうしの間で自動では中継されないため)
- **navigation_manager**(`manager/`、Python、HTTP 8005): ゴール → 計画 → 追従 → 到着の状態機械と画面。各アプリを HTTP で呼ぶ

```
navigation_hub ─┬─ localization_amcl ── mobile_sim
                ├─ planner_dijkstra
                └─ planner_dwa
navigation_manager (Python) ── HTTP ──→ 4 つのアプリ
```

計画とデータの流れは [docs/plan.md](docs/plan.md)、木の形を決めた実験は [experiments/m0_topic_relay](experiments/m0_topic_relay)。

## 一式を動かす

```
pip install juiz-cli==0.2.4 pyyaml
scripts/dev_plugin.sh            # navigation_hub のプラグインをビルドして app/plugins/<target>/ に置く
python3 scripts/run_stack.py --mobile-sim <dir> --amcl <dir> --planner <dir> --dwa <dir>
```

`<dir>` はそれぞれのアプリの app.conf があるディレクトリ(.juizapp を展開したもの、またはリポジトリの app/)。
ブラウザで http://localhost:8005/ を開き、地図をクリックしてドラッグするとゴールを出せる。

```
curl -X POST localhost:8005/api/goal -H 'Content-Type: application/json' -d '{"x": 0.0, "y": 1.5, "theta": 0.0}'
curl -X POST localhost:8005/api/cancel
```

`--test` を付けると、ゴールに着くまでとキャンセルで止まることを確かめて終わる(CI の e2e)。

## 開発

```
cargo test --workspace
python3 scripts/smoke_test.py                              # navigation_hub のプラグインを juiz で読み込む
(cd manager && python3 -m unittest discover -s tests)      # 状態機械
```

## ライセンス

MIT OR Apache-2.0
