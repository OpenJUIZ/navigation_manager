#!/bin/sh
# プラグインをビルドし、app/plugins/<ホストの target triple>/ に置く(app/app.conf を開発中に動かすため)
# 使い方: scripts/dev_plugin.sh [--release]
set -eu
cd "$(dirname "$0")/.."
profile=debug
if [ "${1:-}" = "--release" ]; then
  profile=release
  cargo build -p navigation_hub --release
else
  cargo build -p navigation_hub
fi
target=$(rustc -vV | sed -n 's/^host: //p')
case "$target" in
  *apple-darwin) lib=libnavigation_hub.dylib ;;
  *windows*) lib=navigation_hub.dll ;;
  *) lib=libnavigation_hub.so ;;
esac
mkdir -p "app/plugins/$target"
cp "target/$profile/$lib" "app/plugins/$target/"
echo "app/plugins/$target/$lib"
