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
起動時に mobile_sim の地図を planner_dijkstra・localization_amcl に配る。
`--map map.yaml` を付けると、代わりにその地図(ROS の map_server の形。SLAM で作って保存したものなど)を配り、画面にもその地図を描く。
画像は PNG か PGM で、YAML の `image` に YAML から見た相対パスで書く。
ブラウザで http://localhost:8005/ を開き、地図をクリックしてドラッグするとゴールを出せる。局所計画・障害物・粒子も表示する。

```
curl -X POST localhost:8005/api/goal -H 'Content-Type: application/json' -d '{"x": 0.0, "y": 1.5, "theta": 0.0}'
curl -X POST localhost:8005/api/cancel
curl -X POST localhost:8005/api/distribute_map     # mobile_sim の地図を配り直す
curl -X POST localhost:8005/api/load_map -H 'Content-Type: application/json' -d '{"path": "/home/me/maps/map.yaml"}'   # 地図の YAML を配る
```

画面の「地図を読む…」で、地図の YAML と画像を一緒に選んでも配れる。navigation_manager だけを起動するときも同じく
`navigation-manager --map map.yaml` で渡せる。

```
```

`--test` を付けると、地図の配布(`--map` があればその地図)、ゴールに着くまで、表示(粒子・局所計画・障害物)、キャンセルで止まることを確かめて終わる(CI の e2e)。

## Docker で動かす

```
docker compose -f docker/compose.yml up --build
```

http://localhost:8005/ を開く。mobile_sim・localization_amcl は ghcr のイメージをそのまま、planner_dijkstra・planner_dwa は
ghcr のイメージから葉の conf を入れたイメージを作って使う(docker/leaf.Dockerfile)。navigation_hub と navigation_manager のイメージは
ghcr.io/openjuiz/navigation_hub・ghcr.io/openjuiz/navigation_manager(amd64/arm64)。

## リリース

タグ v* で、navigation_hub の .juizapp と navigation_manager の Python パッケージ(wheel・sdist)を Releases に、
イメージを ghcr に出す。版は app/juizapp.yaml と manager/pyproject.toml で揃える。

## 開発

```
cargo test --workspace
python3 scripts/smoke_test.py                              # navigation_hub のプラグインを juiz で読み込む
(cd manager && python3 -m unittest discover -s tests)      # 状態機械
```

## ライセンス

MIT OR Apache-2.0
