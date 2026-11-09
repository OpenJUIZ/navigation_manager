# navigation-manager

Navigation manager for the [JUIZ](https://github.com/OpenJUIZ/juiz) 2D navigation stack
([mobile_sim](https://github.com/OpenJUIZ/mobile_sim), [localization_amcl](https://github.com/OpenJUIZ/localization_amcl),
[planner_dijkstra](https://github.com/OpenJUIZ/planner_dijkstra), [planner_dwa](https://github.com/OpenJUIZ/planner_dwa) and
navigation_hub from [navigation_manager](https://github.com/OpenJUIZ/navigation_manager)).
It runs the goal → plan → follow → arrive state machine over HTTP and serves a browser UI on http://localhost:8005/.

## Start the whole stack

Download the five `.juizapp` packages from the Releases of each repository, then:

```
pip install juiz-cli==0.2.5 navigation-manager
navigation-manager stack mobile_sim-0.3.3.juizapp localization_amcl-0.1.1.juizapp \
    planner_dijkstra-0.2.0.juizapp planner_dwa-0.1.0.juizapp navigation_hub-0.3.2.juizapp [--map map.yaml]
```

`navigation-manager stack` starts the apps in the order the stack needs (navigation_hub as the root of one tree),
then the manager itself. `--map` distributes a map YAML (ROS map_server format, e.g. one made with SLAM) instead of the
mobile_sim map. Ctrl-C stops everything. Works on Linux, macOS and Windows.

To run only the manager against apps that are already running: `navigation-manager [--map map.yaml]`.

See the [repository](https://github.com/OpenJUIZ/navigation_manager) for details (in Japanese).

## License

MIT OR Apache-2.0
