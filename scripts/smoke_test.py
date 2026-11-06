#!/usr/bin/env python3
"""app/smoke_test.conf を juiz で起動し、HTTP から Process を呼んだりトピックを流したりして状態機械を確かめる。

使い方: python3 scripts/smoke_test.py   (先に scripts/dev_plugin.sh。juiz は環境変数 JUIZ で別のものを指定できる)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://localhost:8015"


def api(path, query=None, body=None, method="GET"):
    url = f"{BASE}/api/{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def call(name, ptype, args=None):
    """nav_manager0 の ContainerProcess を呼ぶ"""
    ident = f"{BASE}/container_process/{name}::{ptype}:nav_manager0"
    return api("container_process/call", {"identifier": ident}, args or {}, "PATCH")


def execute(name, ptype):
    """nav_manager0 の ContainerProcess を execute する(戻り値がトピックに出る)"""
    ident = f"{BASE}/container_process/{name}::{ptype}:nav_manager0"
    return api("container_process/execute", {"identifier": ident}, {}, "PATCH")


def call_process(name, ptype, args):
    ident = f"{BASE}/process/{name}::{ptype}"
    return api("process/call", {"identifier": ident}, args, "PATCH")


def push(topic, value):
    url = f"{BASE}/api/topic/push?topic_name=" + urllib.parse.quote(topic)
    req = urllib.request.Request(
        url, data=json.dumps({"input": value}).encode(), method="PATCH", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        r.read()


def status():
    return call("get_status0", "nav_get_status")


def wait_state(state, timeout=3.0):
    end = time.time() + timeout
    s = status()
    while s["state"] != state and time.time() < end:
        time.sleep(0.1)
        s = status()
    assert s["state"] == state, f"state is {s['state']}, expected {state}: {s}"
    return s


def pose(x, y, theta=0.0):
    return {"header": {"stamp": {"sec": 0, "nanosec": 0}, "frame_id": "map"},
            "pose": {"position": {"x": x, "y": y}, "theta": theta}}


def twist(vx, omega):
    return {"linear": {"x": vx, "y": 0.0}, "angular": omega}


def main():
    juiz = os.environ.get("JUIZ", "juiz")
    log = tempfile.TemporaryFile()
    proc = subprocess.Popen([juiz, "-f", "smoke_test.conf", "-d"], cwd=ROOT / "app", stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(150):
            if proc.poll() is not None:
                log.seek(0)
                sys.exit("juiz exited:\n" + log.read().decode(errors="replace"))
            try:
                if api("container/list"):
                    break
            except OSError:
                pass
            time.sleep(0.2)

        # 中継: 値をそのまま返す
        v = {"a": 1, "b": [1, 2]}
        assert call_process("relay_odom", "nav_relay", {"value": v}) == v

        assert status()["state"] == "idle"
        # 経路を追っていないときは速度指令を止める
        assert call("gate_cmd0", "nav_gate_cmd", {"cmd": twist(0.3, 0.1)}) == twist(0.0, 0.0)

        # ゴールは call でも渡せる(戻り値は nav/goal に出す Pose2DStamped)
        g = call("set_goal0", "nav_set_goal", {"goal": pose(1.0, 2.0)})
        assert g["pose"] == {"position": {"x": 1.0, "y": 2.0}, "theta": 0.0}, g
        assert g["header"]["frame_id"] == "map"
        wait_state("planning")
        # ふだんはトピック goal に流す
        push("goal", pose(1.0, 2.0))
        s = wait_state("planning")
        assert s["goal_seq"] == 2 and s["goal"] == {"x": 1.0, "y": 2.0, "theta": 0.0}, s

        # トピック path(planner_dijkstra の出力)を流すと FOLLOWING
        push("path", {"ok": True, "message": "", "poses": []})
        wait_state("following")
        assert call("gate_cmd0", "nav_gate_cmd", {"cmd": twist(0.3, 0.1)}) == twist(0.3, 0.1)

        # planner_dwa が stuck を出すと引き直す
        push("dwa_status", {"status": "stuck"})
        s = wait_state("planning")
        assert s["retries"] == 1, s
        push("path", {"ok": True, "message": "", "poses": []})
        wait_state("following")

        # 推定姿勢がゴールに着けば SUCCEEDED
        push("amcl_pose", pose(0.0, 0.0))
        s = status()
        assert s["state"] == "following" and abs(s["goal_distance"] - 5 ** 0.5) < 1e-6, s
        push("amcl_pose", pose(1.1, 2.1, 0.1))
        wait_state("succeeded")
        assert call("gate_cmd0", "nav_gate_cmd", {"cmd": twist(0.3, 0.1)}) == twist(0.0, 0.0)

        # キャンセル
        push("goal", pose(3.0, 0.0))
        wait_state("planning")
        execute("cancel0", "nav_cancel")
        wait_state("canceled")
        assert call("cancel0", "nav_cancel") == twist(0.0, 0.0)
        print("smoke test passed")
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
