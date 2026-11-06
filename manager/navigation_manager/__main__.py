"""navigation_manager を起動する。

    python3 -m navigation_manager [--port 8005] [--sim URL] [--amcl URL] [--planner URL] [--dwa URL]

先にナビゲーション一式(mobile_sim・localization_amcl・planner_dijkstra・planner_dwa・navigation_hub)を
起動しておく(scripts/run_stack.py)。ブラウザで http://localhost:8005/ を開く。
"""
from __future__ import annotations

import argparse
import logging
import time

from .apps import Apps, Urls, ready
from .manager import Manager
from .server import serve
from .state import Params


def main(argv=None) -> None:
    d = Urls()
    p = Params()
    ap = argparse.ArgumentParser(prog="navigation-manager", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8005)
    ap.add_argument("--sim", default=d.sim, help="mobile_sim の URL")
    ap.add_argument("--amcl", default=d.amcl, help="localization_amcl の URL")
    ap.add_argument("--planner", default=d.planner, help="planner_dijkstra の URL")
    ap.add_argument("--dwa", default=d.dwa, help="planner_dwa の URL")
    ap.add_argument("--rate", type=float, default=5.0, help="周期処理の周波数 [Hz]")
    ap.add_argument("--xy-goal-tolerance", type=float, default=p.xy_goal_tolerance)
    ap.add_argument("--yaw-goal-tolerance", type=float, default=p.yaw_goal_tolerance)
    ap.add_argument("--max-planning-retries", type=int, default=p.max_planning_retries)
    ap.add_argument("--initial-pose-from-sim", action="store_true",
                    help="起動時に mobile_sim の真の姿勢を localization_amcl の初期位置にする")
    ap.add_argument("--wait", type=float, default=30.0, help="各アプリが応答するまで待つ秒数")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("navigation_manager")

    urls = Urls(a.sim, a.amcl, a.planner, a.dwa)
    end = time.time() + a.wait
    for name in ("sim", "amcl", "planner", "dwa"):
        url = getattr(urls, name)
        while not ready(url):
            if time.time() > end:
                raise SystemExit(f"{name} is not running at {url}")
            time.sleep(0.5)
    manager = Manager(Apps(urls),
                      Params(a.xy_goal_tolerance, a.yaw_goal_tolerance, a.max_planning_retries), a.rate)
    if a.initial_pose_from_sim:
        p0 = manager.set_initial_pose()
        log.info("initial pose from mobile_sim: %s", p0)
    manager.start()
    server = serve(manager, a.host, a.port)
    log.info("navigation_manager on http://localhost:%d/", a.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        manager.shutdown()


if __name__ == "__main__":
    main()
