#!/bin/sh
# ナビゲーション一式の公開版の .juizapp(mobile_sim・localization_amcl・planner_dijkstra・planner_dwa)を <出力先> に取ってくる。
# navigation_hub は含めない(このリポジトリで作ったもの、または Releases のものを別に渡す)。
# 版は環境変数 MOBILE_SIM_VERSION・AMCL_VERSION・DIJKSTRA_VERSION・DWA_VERSION で替えられる。
# 使い方: scripts/fetch_apps.sh [<出力先>](既定 deps/)
set -eu
out=${1:-deps}
mkdir -p "$out"
fetch() {
  repo=$1 name=$2 version=$3
  url="https://github.com/OpenJUIZ/$repo/releases/download/v$version/$name-$version.juizapp"
  echo "$url"
  curl -fsSL --retry 3 -o "$out/$name-$version.juizapp" "$url"
}
fetch mobile_sim mobile_sim "${MOBILE_SIM_VERSION:-0.3.4}"
fetch localization_amcl localization_amcl "${AMCL_VERSION:-0.1.2}"
fetch planner_dijkstra planner_dijkstra "${DIJKSTRA_VERSION:-0.2.1}"
fetch planner_dwa planner_dwa "${DWA_VERSION:-0.1.1}"
