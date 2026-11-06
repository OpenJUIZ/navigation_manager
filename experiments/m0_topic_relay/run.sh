#!/bin/bash
# M0: juiz のトピックが「根 1 つ + 葉 4 つ」の木のどこまで届くかを確かめる。
# 使い方: experiments/m0_topic_relay/run.sh [plain|relay]
#   plain: 葉どうしのトピックを juiz の自動中継に任せる
#   relay: manager が odom を受けて nav/odom として出し直し、amcl は nav/odom を購読する
# 前提: juiz 0.2.4(pip install juiz-cli==0.2.4。JUIZ で別のバイナリを指定できる)と、
#       probe を cargo build したもの(probe/target/debug/libnav_probe.so)
set -u
cd "$(dirname "$0")/conf"
JZ=${JUIZ:-juiz}
MODE=${1:-plain}
WORK=$(mktemp -d)
cp *.conf "$WORK/"
if [ "$MODE" = relay ]; then
  python3 - "$WORK" <<'PY'
import sys, os
w = sys.argv[1]
m = os.path.join(w, "manager.conf"); s = open(m).read()
s = s.replace("    subscribes:\n      arg1: odom\n", "    subscribes:\n      arg1: odom\n    publishes:\n      - nav/odom\n")
open(m, "w").write(s)
a = os.path.join(w, "amcl.conf"); t = open(a).read().replace("arg1: odom", "arg1: nav/odom")
open(a, "w").write(t)
PY
fi
sed -i "s|path: \"../probe/target/debug\"|path: \"$PWD/../probe/target/debug\"|" "$WORK"/*.conf
pids=()
for n in sim amcl planner dwa manager; do
  "$JZ" -f "$WORK/$n.conf" -d > "$WORK/$n.log" 2>&1 & pids+=($!)
  sleep 2
done
sleep 6
kill "${pids[@]}" 2>/dev/null; wait 2>/dev/null
echo "mode=$MODE (受け取ったトピックと回数)"
for n in sim amcl planner dwa manager; do
  printf "%-8s" "$n"; grep -o "PROBE RECV [a-z_]*" "$WORK/$n.log" | sort | uniq -c | awk '{printf " %s x%s", $4, $1}'; echo
done
echo "logs: $WORK"
