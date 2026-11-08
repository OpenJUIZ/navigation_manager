# syntax=docker/dockerfile:1
# Python の navigation_manager のイメージ。リポジトリのルートを context にしてビルドする:
#   docker build -f docker/manager.Dockerfile -t navigation_manager .
# juiz は入れない(各アプリを HTTP で呼ぶ。PyYAML は stack 用で、ここでは使わない)
FROM python:3.12-slim-bookworm
COPY manager /src/manager
COPY LICENSE-MIT LICENSE-APACHE /src/manager/
RUN pip install --no-cache-dir /src/manager && rm -rf /src
EXPOSE 8005
ENTRYPOINT ["navigation-manager"]
# 既定は docker/compose.yml のサービス名。navigation_hub も応答するまで待つ
CMD ["--sim", "http://mobile_sim:8000", "--amcl", "http://localization_amcl:8002", \
     "--planner", "http://planner_dijkstra:8003", "--dwa", "http://planner_dwa:8004", \
     "--hub", "http://navigation_hub:8006", "--wait", "300"]
