#!/usr/bin/env python3
"""navigation_hub をコンテナで動かす。

app.conf の IPC のブローカーと subsystem を、環境変数 AMCL_URL・PLANNER_URL・DWA_URL の HTTP に置き換えた conf を作る。
根は葉より後に起動しないとつながらないので、3 つが応答するまで待ってから juiz -f で動かす。
localization_amcl が応答するのは mobile_sim につながった後なので、mobile_sim も先に動いている。
"""
import os
import sys
import time
import urllib.parse
import urllib.request

APP = os.environ.get("NAV_HUB_DIR", "/opt/navigation_hub")
CONF = f"{APP}/.docker.conf"

IPC_BROKER = '''"brokers":
  - "type_name": "ipc"
    "name": "navigation_hub"
'''
IPC_SUBSYSTEMS = '''"subsystems":
  - "package": "localization_amcl"
    "type_name": "ipc"
    "name": "localization_amcl"
  - "type_name": "ipc"
    "name": "planner_dijkstra"
  - "type_name": "ipc"
    "name": "planner_dwa"
'''


def main():
    urls = [os.environ.get(k, "").rstrip("/") for k in ("AMCL_URL", "PLANNER_URL", "DWA_URL")]
    hosts = [urllib.parse.urlparse(u).netloc for u in urls]
    if not all(hosts):
        sys.exit("AMCL_URL, PLANNER_URL and DWA_URL must be http://<host>:<port>")
    src = open(f"{APP}/app.conf").read()
    if IPC_BROKER not in src or IPC_SUBSYSTEMS not in src:
        sys.exit("cannot make the conf: check the brokers/subsystems of app.conf")
    http = '"subsystems":\n' + "".join(f'  - "type_name": "http"\n    "name": "{h}"\n' for h in hosts)
    open(CONF, "w").write(src.replace(IPC_BROKER, "").replace(IPC_SUBSYSTEMS, http))
    for url in urls:
        for _ in range(600):
            try:
                urllib.request.urlopen(url + "/api/container/list", timeout=2).read()
                break
            except Exception:
                time.sleep(0.5)
        else:
            sys.exit(f"{url} did not answer")
        print(f"{url} is up", flush=True)
    os.execvp("juiz", ["juiz", "-f", CONF, "-d", *sys.argv[1:]])


if __name__ == "__main__":
    main()
