"""一式が動いているところに、ゴールを出して着くまでと、キャンセルで止まることを確かめる(navigation-manager stack --test、CI の e2e)。

navigation_manager がゴールで SUCCEEDED になり、mobile_sim の真の姿勢がゴールの近くにあること、表示(粒子・局所計画・障害物)、
キャンセルで止まることを見る。失敗したら E2EError を出す。
"""
import json
import math
import time
import urllib.parse
import urllib.request

MANAGER = "http://127.0.0.1:8005"
SIM = "http://127.0.0.1:8000"


class E2EError(Exception):
    pass


def fail(message):
    raise E2EError(message)


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


def status():
    return api(MANAGER, "status")


def run_test(goal, timeout, map_source="mobile_sim"):
    """ゴールを出して、navigation_manager が SUCCEEDED になり、mobile_sim の真の姿勢がゴールの近くにあることを確かめる"""
    # 自己位置推定が落ち着くまで待つ(amcl_pose が navigation_manager に届くこと)
    end = time.time() + 30
    while status().get("pose") is None:
        if time.time() > end:
            fail("amcl_pose did not reach navigation_manager")
        time.sleep(0.5)
    print("amcl_pose reached navigation_manager:", status()["pose"], flush=True)
    if status().get("map_source") != map_source:
        fail(f"the {map_source} map was not distributed: {status()}")
    info = api(MANAGER, "map_info")
    print(f"map: {map_source} ({info['width']}x{info['height']}, {info['resolution']} m/px, origin {info['origin']})",
          flush=True)
    v = api(MANAGER, "view")
    print(f"view: {len(v['particles'])} particles", flush=True)
    if not v["particles"]:
        fail("the view has no particles")

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
        fail(f"navigation did not succeed: {s}")
    if "following" not in seen:
        fail(f"never followed a path: {seen}")
    truth = call(SIM, "get_pose0", "mobile_sim_get_pose", "sim0", {"index": 0})["pose"]
    d = math.hypot(truth["position"]["x"] - x, truth["position"]["y"] - y)
    print(f"true pose {truth} ({d:.2f} m from the goal)", flush=True)
    if d > 0.5:
        fail(f"the robot is {d:.2f} m from the goal")

    # 2 つ目のゴールを出して、追っている途中でキャンセルすると止まる
    api(MANAGER, "goal", body={"x": -2.0, "y": -2.0, "theta": 0.0}, method="POST")
    end = time.time() + 20
    while status()["state"] != "following":
        if time.time() > end:
            fail(f"second goal was not followed: {status()}")
        time.sleep(0.2)
    time.sleep(2.0)
    v = api(MANAGER, "view")
    print(f"view while following: {len(v['local_plan'])} local plan points, {len(v['obstacles'])} obstacles", flush=True)
    if not v["local_plan"] or not v["obstacles"]:
        fail("the view has no local plan or obstacles while following")
    s = api(MANAGER, "cancel", body={}, method="POST")
    if s["state"] != "canceled":
        fail(f"cancel did not cancel: {s}")
    time.sleep(1.0)  # 減速を待つ
    a = call(SIM, "get_pose0", "mobile_sim_get_pose", "sim0", {"index": 0})["pose"]["position"]
    time.sleep(1.0)
    b = call(SIM, "get_pose0", "mobile_sim_get_pose", "sim0", {"index": 0})["pose"]["position"]
    moved = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
    print(f"canceled; moved {moved:.3f} m in 1 s after stopping", flush=True)
    if moved > 0.05:
        fail(f"the robot kept moving after cancel ({moved:.3f} m in 1 s)")
    print("end-to-end test passed", flush=True)
