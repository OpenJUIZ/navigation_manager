# M0: 木の形でのトピック中継の検証

navigation_manager を根にして 4 つのアプリを subsystem にしたとき、juiz 0.2.4 のトピックがどこまで届くかを確かめた実験。
アプリの代わりに、トピックを出すだけ・受けて表示するだけの試験用プラグイン(`probe/`)を使う。

```
          manager (8100)  goal を出す / amcl_pose・path・odom を受ける
   ┌────────┬───────┼──────────┐
  sim      amcl    planner     dwa
 (8101)   (8102)   (8103)     (8104)
 odom を出す amcl_pose を出す path を出す cmd_vel を出す
 cmd_vel を受ける odom を受ける amcl_pose・goal を受ける path・amcl_pose を受ける
```

## 動かし方

```
pip install juiz-cli==0.2.4
(cd experiments/m0_topic_relay/probe && cargo build)
experiments/m0_topic_relay/run.sh plain   # juiz の自動中継に任せる
experiments/m0_topic_relay/run.sh relay   # manager が odom を nav/odom として出し直す
```

## 結果(2026-11-06、juiz 0.2.4、HTTP ブローカー、葉を先に起動して最後に manager)

```
mode=plain                         mode=relay
sim                                sim
amcl                               amcl     odom x7
planner  goal x8                   planner  goal x8
dwa                                dwa
manager  amcl_pose x7 odom x7 path x7   manager  amcl_pose x7 odom x7 path x7
```

- 根 → 葉(goal)と、葉 → 根(odom・amcl_pose・path)は届く。
- 葉 → 根 → 別の葉(odom → amcl、amcl_pose → planner・dwa、path → dwa、cmd_vel → sim)は届かない。
  各 System がトピックの同期(`setup_topic_synchronization`)をするのは自分の起動時だけで、葉が起動した時点ではまだ根がいないため。
- manager が受けたトピックを同じ名前で出し直すと、自分が出したものを自分で受けて無限に回る。
- manager が別の名前(`nav/odom`)で出し直し、葉がその名前を購読すれば届く(mode=relay)。
- manager を先に起動すると、葉につながらず起動に失敗する。

→ navigation_manager は、葉どうしのトピックを自分で受けて別名(`nav/...`)で出し直す方式にする。
