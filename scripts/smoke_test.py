#!/usr/bin/env python3
"""app/smoke_test.conf を juiz で起動し、navigation_hub のプラグインを読み込めて nav_relay を HTTP から呼べることを確かめる。

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
BASE = "http://localhost:8016"


def api(path, query=None, body=None, method="GET"):
    url = f"{BASE}/api/{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


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
                if api("process/list"):
                    break
            except OSError:
                pass
            time.sleep(0.2)
        v = {"header": {"frame_id": "odom0"}, "pose": {"position": {"x": 1.0, "y": 2.0}, "theta": 0.5}}
        ident = f"{BASE}/process/relay_odom::nav_relay"
        got = api("process/call", {"identifier": ident}, {"value": v}, "PATCH")
        assert got == v, got
        print("smoke test passed")
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
