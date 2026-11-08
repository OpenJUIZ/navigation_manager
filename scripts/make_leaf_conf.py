#!/usr/bin/env python3
"""planner_dijkstra・planner_dwa の app.conf から、navigation_manager の下で動かす「葉」の conf を作る。

使い方: python3 scripts/make_leaf_conf.py <アプリの app.conf> <出力する conf>

置き換えは app/leaves/<アプリ名>.yaml に書く(どのアプリかは conf の name で決める)。
- subsystems を消す(mobile_sim は localization_amcl の下にあり、葉から直接つなぐと mobile_sim の master が 2 つになる)
- subscribes・publishes のトピック名を navigation_manager が出し直す名前(nav/...)に替える
- コンテナの引数を上書きする(planner_dwa の bridge: null など)
navigation-manager stack は同じことを manager/navigation_manager/stack.py の make_leaf で行う(こちらは Docker の葉のイメージ用)。
元の conf をそのまま使うので、アプリの版が上がってもこの表だけ見直せばよい。juiz -f で読めるよう JSON で書き出す。
"""
import json
import sys
from pathlib import Path

import yaml

LEAVES = Path(__file__).resolve().parent.parent / "app" / "leaves"


def rename(topic, table):
    return table.get(topic, topic)


def make_leaf(conf, rules):
    conf = dict(conf)
    conf.pop("subsystems", None)
    subs = rules.get("subscribes", {})
    pubs = rules.get("publishes", {})
    overrides = rules.get("container_args", {})

    def fix_process(p):
        if "subscribes" in p:
            p["subscribes"] = {arg: rename(t, subs) for arg, t in p["subscribes"].items()}
        if "publishes" in p:
            p["publishes"] = [rename(t, pubs) for t in p["publishes"]]

    for c in conf.get("containers", []):
        for k, v in overrides.get(c.get("name"), {}).items():
            c[k] = v
        for p in c.get("processes", []):
            fix_process(p)
    for p in conf.get("processes", []):
        fix_process(p)
    return conf


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    conf = yaml.safe_load(src.read_text())
    rules_file = LEAVES / f"{conf['name']}.yaml"
    if not rules_file.exists():
        sys.exit(f"no rules for {conf['name']}: {rules_file}")
    leaf = make_leaf(conf, yaml.safe_load(rules_file.read_text()))
    dst.write_text(json.dumps(leaf, indent=2, ensure_ascii=False) + "\n")
    print(dst)


if __name__ == "__main__":
    main()
