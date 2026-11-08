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

公開版の .juizapp と pip だけで動く(ビルドも clone も要らない)。Linux・macOS・Windows で同じ。

```
pip install juiz-cli==0.2.4 navigation-manager
navigation-manager stack mobile_sim-0.3.3.juizapp localization_amcl-0.1.0.juizapp \
    planner_dijkstra-0.1.0.juizapp planner_dwa-0.1.0.juizapp navigation_hub-0.3.0.juizapp
```

.juizapp はそれぞれのリポジトリの Releases から取る
([mobile_sim](https://github.com/OpenJUIZ/mobile_sim/releases)・[localization_amcl](https://github.com/OpenJUIZ/localization_amcl/releases)・
[planner_dijkstra](https://github.com/OpenJUIZ/planner_dijkstra/releases)・[planner_dwa](https://github.com/OpenJUIZ/planner_dwa/releases)・
[navigation_hub](https://github.com/OpenJUIZ/navigation_manager/releases))。並べる順は問わない。`scripts/fetch_apps.sh` で navigation_hub 以外をまとめて取れる。

`navigation-manager stack` は次の順に起動し、Ctrl-C で逆の順に止める(Windows では子を別のプロセスグループで起動し、Ctrl+Break で止める)。

| 順 | アプリ | 起動のしかた |
| --- | --- | --- |
| 1 | mobile_sim(基礎、8000) | `juiz run` |
| 2 | localization_amcl(8002) | `juiz run`(依存の mobile_sim に IPC でつなぐ) |
| 3・4 | planner_dijkstra(8003)・planner_dwa(8004) | .juizapp を一時ディレクトリに展開し、navigation_hub の .juizapp の `leaves/` の置き換えで葉の conf を作って `juiz -f`(`juiz run` だと依存の mobile_sim を subsystem に足してしまい、mobile_sim の master が 2 つになるため) |
| 5 | navigation_hub(8006) | `juiz run`(根は最後。先に起動すると葉につながらない) |
| 6 | navigation_manager(8005) | 同じ Python で起動する |

各アプリのログは一時ディレクトリ(`--logdir` で指定できる)に出す。前の演習の teleop_twist(8002)などが動いていると、ポートが使用中として起動しない。
juiz は PATH にあるものを使う(環境変数 `JUIZ` で別のものを指定できる)。.juizapp の代わりに app.conf のあるディレクトリも渡せる(そのときは `juiz -f`)。
開発中は `python3 scripts/run_stack.py --mobile-sim <dir> --amcl <dir> --planner <dir> --dwa <dir> [--hub <dir>]` でも同じことができる
(`--hub` を省くと `scripts/dev_plugin.sh` でビルドした app/ を使う)。

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
`navigation-manager --map map.yaml` で渡せる。`--manager-arg=<引数>` で stack から navigation_manager に引数を渡せる。

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
Python パッケージを PyPI(`navigation-manager`、Trusted Publishing)に、イメージを ghcr に出す。
版は Cargo.toml・app/juizapp.yaml・manager/pyproject.toml で揃える。
PyPI の Trusted Publisher は Owner `OpenJUIZ`・Repository `navigation_manager`・Workflow `release.yml`・Environment `pypi`。

## 開発

```
cargo test --workspace
python3 scripts/smoke_test.py                              # navigation_hub のプラグインを juiz で読み込む
(cd manager && python3 -m unittest discover -s tests)      # 状態機械・地図・stack(pyyaml が要る)
```

## ライセンス

MIT OR Apache-2.0
