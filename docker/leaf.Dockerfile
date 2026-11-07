# syntax=docker/dockerfile:1
# planner_dijkstra・planner_dwa のイメージを、navigation_hub の下で動く「葉」にする。リポジトリのルートを context にしてビルドする:
#   docker build -f docker/leaf.Dockerfile --build-arg APP=planner_dwa --build-arg BASE=ghcr.io/openjuiz/planner_dwa:latest .
#
# 元のイメージの entrypoint は mobile_sim を subsystem にするが、そうすると mobile_sim の master が 2 つになりトピックが届かない。
# そこで元のイメージの app.conf から scripts/make_leaf_conf.py と app/leaves/<APP>.yaml で葉の conf を作り(run_stack.py と同じ)、
# それを juiz -f で動かす。
ARG APP
ARG BASE

FROM ${BASE} AS base

FROM python:3.12-slim-bookworm AS conf
ARG APP
RUN pip install --no-cache-dir pyyaml
COPY scripts/make_leaf_conf.py /work/scripts/
COPY app/leaves /work/app/leaves
COPY --from=base /opt/${APP}/app.conf /work/app.conf
RUN python3 /work/scripts/make_leaf_conf.py /work/app.conf /work/leaf.conf

FROM base
ARG APP
COPY --from=conf /work/leaf.conf /opt/${APP}/leaf.conf
ENV NAV_LEAF_CONF=/opt/${APP}/leaf.conf
# planner_dwa の bridge(AMCL・planner を HTTP で読みに行く仕組み)は葉では使わない
ENV DWA_BRIDGE_AMCL_URL=
ENV DWA_BRIDGE_PLANNER_URL=
ENTRYPOINT ["sh", "-c", "exec juiz -f \"$NAV_LEAF_CONF\" -d \"$@\"", "--"]
