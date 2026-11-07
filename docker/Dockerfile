# syntax=docker/dockerfile:1
# navigation_hub のイメージ(ナビゲーション一式の木の根)。リポジトリのルートを context にしてビルドする:
#   docker build -f docker/Dockerfile -t navigation_hub .
#
# Rust のプラグインは C ABI なので、juiz は pip の juiz-cli をそのまま使う(版は Cargo.toml の juiz_sdk と揃える)。
# 各アプリのイメージは別のコンテナなので、app.conf の IPC の subsystem を HTTP に置き換えて動かす(docker/hub-entrypoint.py)。
ARG JUIZ_VERSION=0.2.4

# プラグインをビルドしてテストする
FROM rust:1-bookworm AS build
ARG JUIZ_VERSION
WORKDIR /src
COPY . .
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/src/target \
    grep -qx "juiz_sdk = \"=${JUIZ_VERSION}\"" Cargo.toml \
    && cargo test --workspace --locked \
    && cargo build -p navigation_hub --release --locked \
    && target=$(rustc -vV | sed -n 's/^host: //p') \
    && mkdir -p "/plugins/$target" \
    && cp target/release/libnavigation_hub.so "/plugins/$target/"

FROM python:3.12-slim-bookworm
ARG JUIZ_VERSION
RUN pip install --no-cache-dir "juiz-cli==${JUIZ_VERSION}"
# app/ と同じ形にする(conf からの相対パスでプラグインを探す)
COPY app/app.conf /opt/navigation_hub/
COPY --from=build /plugins /opt/navigation_hub/plugins
COPY LICENSE-MIT LICENSE-APACHE /opt/navigation_hub/
COPY docker/hub-entrypoint.py /usr/local/bin/navigation_hub-entrypoint
ENV JUIZ_APP_DIR=/opt/navigation_hub
WORKDIR /opt/navigation_hub
# 木の葉(juiz の HTTP)。mobile_sim は localization_amcl の下にあるので、ここには書かない
ENV AMCL_URL=http://localization_amcl:8002
ENV PLANNER_URL=http://planner_dijkstra:8003
ENV DWA_URL=http://planner_dwa:8004
EXPOSE 8006
ENTRYPOINT ["navigation_hub-entrypoint"]
