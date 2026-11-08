#!/usr/bin/env python3
"""開発用: リポジトリから navigation-manager stack を動かす(CI の e2e、Docker の確かめ)。

    python3 scripts/run_stack.py --mobile-sim <dir|.juizapp> --amcl <dir|.juizapp> --planner <dir|.juizapp> \\
        --dwa <dir|.juizapp> [--hub <dir|.juizapp>] [--map map.yaml] [--test]
    python3 scripts/run_stack.py --attach --test      # docker compose などで動いている一式を確かめる

起動の中身は manager/navigation_manager/stack.py(pip で入れたときの `navigation-manager stack`)と同じ。
--hub を省くとリポジトリの app/(scripts/dev_plugin.sh でプラグインをビルドしておく)を使う。
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "manager"))
from navigation_manager import e2e, stack  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mobile-sim")
    ap.add_argument("--amcl")
    ap.add_argument("--planner")
    ap.add_argument("--dwa")
    ap.add_argument("--hub", default=str(ROOT / "app"), help="navigation_hub の app.conf があるディレクトリか .juizapp")
    ap.add_argument("--map")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--goal", default="0.0,1.5,0.0")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--logdir")
    ap.add_argument("--attach", action="store_true",
                    help="起動せず、もう動いている一式(docker compose など)に --test を走らせる")
    a = ap.parse_args()
    if a.attach:
        if not a.test:
            ap.error("--attach needs --test")
        end = time.time() + 180
        while True:
            try:
                e2e.status()
                break
            except OSError:
                if time.time() > end:
                    sys.exit(f"navigation_manager did not answer at {e2e.MANAGER}")
                time.sleep(1.0)
        try:
            e2e.run_test(tuple(float(v) for v in a.goal.split(",")), a.timeout,
                         Path(a.map).name if a.map else "mobile_sim")
        except e2e.E2EError as e:
            sys.exit(f"FAILED: {e}")
        return
    missing = [k for k in ("mobile_sim", "amcl", "planner", "dwa") if getattr(a, k) is None]
    if missing:
        ap.error("need " + ", ".join("--" + k.replace("_", "-") for k in missing))
    argv = [a.mobile_sim, a.amcl, a.planner, a.dwa, a.hub, "--goal", a.goal, "--timeout", str(a.timeout)]
    for opt in ("map", "logdir"):
        if getattr(a, opt):
            argv += [f"--{opt}", getattr(a, opt)]
    if a.test:
        argv.append("--test")
    stack.main(argv)


if __name__ == "__main__":
    main()
