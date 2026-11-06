#!/usr/bin/env python3
"""ナビゲーション一式(mobile_sim・localization_amcl・planner_dijkstra・planner_dwa・navigation_hub)を
navigation_hub を根にした木の形で起動し、その後に Python の navigation_manager(HTTP 8005、画面つき)を起動する。
--test を付けると、ゴールを出して着くまでを確かめて止める。

    navigation_hub (8006) ─┬─ localization_amcl (8002) ── mobile_sim (8000)
                           ├─ planner_dijkstra (8003)
                           └─ planner_dwa (8004)
    navigation_manager (8005, Python): 各アプリを HTTP で呼んでゴール・引き直し・キャンセル・初期位置を受け持つ

使い方:
  python3 scripts/run_stack.py --mobile-sim <dir> --amcl <dir> --planner <dir> --dwa <dir> [--test]

<dir> はそれぞれのアプリの app.conf があるディレクトリ(.juizapp を展開したもの、または開発中のリポジトリの app/)。
planner_dijkstra・planner_dwa は scripts/make_leaf_conf.py で葉の conf(leaf.conf)を作って、そのディレクトリで juiz -f で起動する
(juiz run は depends の mobile_sim を subsystem に足してしまい、mobile_sim の master が 2 つになるため)。
起動の順は葉が先、根が最後(根を先に起動すると葉につながらない)。juiz は環境変数 JUIZ で別のものを指定できる。
"""
import argparse
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_leaf_conf import make_leaf  # noqa: E402

import yaml  # noqa: E402

MANAGER = "http://localhost:8005"
SIM = "http://localhost:8000"


def api(base, path, query=None, body=None, method="GET", timeout=5):
    url = f"{base}/api/{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def call(base, name, ptype, container, args=None):
    ident = f"{base}/container_process/{name}::{ptype}:{container}"
    return api(base, "container_process/call", {"identifier": ident}, args or {}, "PATCH")


def push(base, topic, value):
    url = f"{base}/api/topic/push?topic_name=" + urllib.parse.quote(topic)
    req = urllib.request.Request(
        url, data=json.dumps({"input": value}).encode(), method="PATCH", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        r.read()


class Stack:
    def __init__(self, juiz, logdir):
        self.juiz = juiz
        self.logdir = Path(logdir)
        self.procs = []

    def start(self, name, cwd, conf, port, extra=(), cmd=None):
        # JUIZ_APP_DIR は起動したディレクトリから見て解決されるので、絶対パスにする
        cwd = Path(cwd).resolve()
        log = open(self.logdir / f"{name}.log", "wb")
        env = dict(os.environ, JUIZ_APP_DIR=str(cwd))
        if cmd is None:
            cmd = [self.juiz, "-f", conf, "-d", *extra]
        else:
            env["PYTHONPATH"] = str(ROOT / "manager") + os.pathsep + env.get("PYTHONPATH", "")
        p = subprocess.Popen(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        self.procs.append((name, p))
        for _ in range(150):
            if p.poll() is not None:
                self.fail(f"{name} exited")
            try:
                api(f"http://localhost:{port}", "container/list" if cmd[0] == self.juiz else "status", timeout=1)
                print(f"started {name} (:{port})", flush=True)
                return
            except OSError:
                time.sleep(0.2)
        self.fail(f"{name} did not become ready")

    def fail(self, message):
        print(f"FAILED: {message}", flush=True)
        for name, _ in self.procs:
            # バックトレースの行(番号付きのフレームと "at ...")は省く
            lines = [
                ln for ln in (self.logdir / f"{name}.log").read_text(errors="replace").splitlines()
                if not ln.strip().startswith("at ") and not ln.strip().split(":")[0].isdigit()
            ]
            print(f"--- {name}.log (last lines without backtraces)\n" + "\n".join(lines[-20:]))
        self.stop()
        sys.exit(1)

    def stop(self):
        for _, p in reversed(self.procs):
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
        for _, p in self.procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)


def leaf_conf(app_dir):
    conf = yaml.safe_load((Path(app_dir) / "app.conf").read_text())
    rules = yaml.safe_load((ROOT / "app" / "leaves" / f"{conf['name']}.yaml").read_text())
    out = Path(app_dir) / "leaf.conf"
    out.write_text(json.dumps(make_leaf(conf, rules), indent=2, ensure_ascii=False) + "\n")
    return out.name


def status():
    return api(MANAGER, "status")


def run_test(stack, goal, timeout):
    """ゴールを出して、navigation_manager が SUCCEEDED になり、mobile_sim の真の姿勢がゴールの近くにあることを確かめる"""
    # 自己位置推定が落ち着くまで待つ(amcl_pose が navigation_manager に届くこと)
    end = time.time() + 30
    while status().get("pose") is None:
        if time.time() > end:
            stack.fail("amcl_pose did not reach navigation_manager")
        time.sleep(0.5)
    print("amcl_pose reached navigation_manager:", status()["pose"], flush=True)
    if status().get("map_source") != "mobile_sim":
        stack.fail(f"the mobile_sim map was not distributed: {status()}")
    v = api(MANAGER, "view")
    print(f"view: {len(v['particles'])} particles", flush=True)
    if not v["particles"]:
        stack.fail("the view has no particles")

    x, y, theta = goal
    api(MANAGER, "goal", body={"x": x, "y": y, "theta": theta}, method="POST")
    print(f"goal ({x}, {y}, {theta})", flush=True)
    end = time.time() + timeout
    last = None
    seen = set()
    while time.time() < end:
        s = status()
        seen.add(s["state"])
        if (s["state"], s["message"]) != last:
            print(f"  {s['state']}: {s['message']} (distance {s['goal_distance']})", flush=True)
            last = (s["state"], s["message"])
        if s["state"] in ("succeeded", "failed"):
            break
        time.sleep(0.5)
    if s["state"] != "succeeded":
        stack.fail(f"navigation did not succeed: {s}")
    if "following" not in seen:
        stack.fail(f"never followed a path: {seen}")
    truth = call(SIM, "get_pose0", "mobile_sim_get_pose", "sim0", {"index": 0})["pose"]
    d = math.hypot(truth["position"]["x"] - x, truth["position"]["y"] - y)
    print(f"true pose {truth} ({d:.2f} m from the goal)", flush=True)
    if d > 0.5:
        stack.fail(f"the robot is {d:.2f} m from the goal")

    # 2 つ目のゴールを出して、追っている途中でキャンセルすると止まる
    api(MANAGER, "goal", body={"x": -2.0, "y": -2.0, "theta": 0.0}, method="POST")
    end = time.time() + 20
    while status()["state"] != "following":
        if time.time() > end:
            stack.fail(f"second goal was not followed: {status()}")
        time.sleep(0.2)
    time.sleep(2.0)
    v = api(MANAGER, "view")
    print(f"view while following: {len(v['local_plan'])} local plan points, {len(v['obstacles'])} obstacles", flush=True)
    if not v["local_plan"] or not v["obstacles"]:
        stack.fail("the view has no local plan or obstacles while following")
    s = api(MANAGER, "cancel", body={}, method="POST")
    if s["state"] != "canceled":
        stack.fail(f"cancel did not cancel: {s}")
    time.sleep(1.0)  # 減速を待つ
    a = call(SIM, "get_pose0", "mobile_sim_get_pose", "sim0", {"index": 0})["pose"]["position"]
    time.sleep(1.0)
    b = call(SIM, "get_pose0", "mobile_sim_get_pose", "sim0", {"index": 0})["pose"]["position"]
    moved = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
    print(f"canceled; moved {moved:.3f} m in 1 s after stopping", flush=True)
    if moved > 0.05:
        stack.fail(f"the robot kept moving after cancel ({moved:.3f} m in 1 s)")
    print("end-to-end test passed", flush=True)


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mobile-sim", required=True)
    ap.add_argument("--amcl", required=True)
    ap.add_argument("--planner", required=True)
    ap.add_argument("--dwa", required=True)
    ap.add_argument("--hub", default=str(ROOT / "app"), help="navigation_hub の app.conf があるディレクトリ")
    ap.add_argument("--test", action="store_true", help="ゴールを出して着くまでを確かめて止める")
    ap.add_argument("--goal", default="0.0,1.5,0.0", help="x,y,theta(map 座標系、--test で使う)")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--logdir", default=None)
    a = ap.parse_args()

    juiz = os.environ.get("JUIZ") or shutil.which("juiz") or sys.exit("juiz not found (pip install juiz-cli)")
    logdir = Path(a.logdir).resolve() if a.logdir else tempfile.mkdtemp(prefix="nav_stack_")
    Path(logdir).mkdir(parents=True, exist_ok=True)
    print(f"logs: {logdir}", flush=True)
    stack = Stack(juiz, logdir)
    # SIGTERM(docker stop や kill)でも Ctrl-C と同じく、起動したものを止めてから終わる
    signal.signal(signal.SIGTERM, _interrupt)
    # 前に起動したものが残っていると、そちらにつないでしまうので止める
    for port in (8000, 8002, 8003, 8004, 8005, 8006):
        try:
            urllib.request.urlopen(f"http://localhost:{port}/", timeout=0.5)
        except urllib.error.HTTPError:
            sys.exit(f"port {port} is already in use. Stop the running apps first")
        except OSError:
            continue
        sys.exit(f"port {port} is already in use. Stop the running apps first")
    try:
        # mobile_sim は基礎(kind: base)。juiz run と同じく IPC 名 mobile_sim を持たせる
        stack.start("mobile_sim", a.mobile_sim, "app.conf", 8000, ["--ipc", "mobile_sim"])
        stack.start("localization_amcl", a.amcl, "app.conf", 8002)
        stack.start("planner_dijkstra", a.planner, leaf_conf(a.planner), 8003)
        stack.start("planner_dwa", a.dwa, leaf_conf(a.dwa), 8004)
        stack.start("navigation_hub", a.hub, "app.conf", 8006)
        stack.start("navigation_manager", ROOT, None, 8005,
                    cmd=[sys.executable, "-m", "navigation_manager", "--port", "8005"])
        if a.test:
            run_test(stack, tuple(float(v) for v in a.goal.split(",")), a.timeout)
        else:
            print(f"running. open {MANAGER}/ . Ctrl-C to stop", flush=True)
            signal.pause()
    except KeyboardInterrupt:
        pass
    finally:
        stack.stop()


if __name__ == "__main__":
    main()
