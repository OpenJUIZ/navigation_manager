import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from navigation_manager import stack
from navigation_manager.stack import (App, StackError, collect, juiz_version, leaf_run_command, make_leaf,
                                      write_leaf_conf)

ROOT = Path(__file__).resolve().parents[2]

DWA_CONF = """
"name": "planner_dwa"
"brokers":
  - { "type_name": "ipc", "name": "planner_dwa" }
"subsystems":
  - { "package": "mobile_sim", "type_name": "ipc", "name": "mobile_sim" }
"containers":
  - "type_name": "dwa"
    "name": "dwa0"
    "bridge": { "amcl": "http://localhost:8002" }
    "processes":
      - { "type_name": "dwa_step", "name": "step0", "subscribes": { "odom": "odom", "scan": "scan", "x": "other" },
          "publishes": ["cmd_vel"] }
"""


def make_package(path: Path, name: str, extra=None, version="0.1.0"):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("juizapp.yaml", f"format: 1\nname: {name}\nversion: {version}\nkind: app\nentry: app.conf\n")
        z.writestr("app.conf", f'"name": "{name}"\n')
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return path


class LeafTest(unittest.TestCase):
    def test_bundled_rules(self):
        # リポジトリの app/leaves の置き換えで、subsystems が消え、トピック名とコンテナの引数が替わる
        import yaml
        rules = yaml.safe_load((ROOT / "app" / "leaves" / "planner_dwa.yaml").read_text(encoding="utf-8"))
        conf = yaml.safe_load(DWA_CONF)
        leaf = make_leaf(conf, rules)
        self.assertNotIn("subsystems", leaf)
        self.assertIn("subsystems", conf)  # 元は変えない
        c = leaf["containers"][0]
        self.assertIsNone(c["bridge"])
        p = c["processes"][0]
        self.assertEqual(p["subscribes"], {"odom": "nav/odom", "scan": "nav/scan", "x": "other"})
        self.assertEqual(p["publishes"], ["cmd_vel_dwa"])
        self.assertEqual(leaf["brokers"], conf["brokers"])

    def test_leaf_run_command(self):
        # juiz 0.2.5 以降: 葉の .juizapp は juiz run で、依存先につながず、トピック名は --remap で替える
        import yaml
        with tempfile.TemporaryDirectory() as d:
            pkg = Path(d) / "planner_dijkstra.juizapp"
            with zipfile.ZipFile(pkg, "w") as z:
                z.writestr("juizapp.yaml", "format: 1\nname: planner_dijkstra\nversion: 0.2.0\nkind: app\n"
                                           "depends:\n  mobile_sim: '>=0.3.1, <0.4'\n")
            app = App(pkg)
            self.assertEqual(app.depends, ["mobile_sim"])
            rules = yaml.safe_load((ROOT / "app" / "leaves" / "planner_dijkstra.yaml").read_text(encoding="utf-8"))
            self.assertEqual(leaf_run_command("juiz", app, rules),
                             (["juiz", "run", str(app.path), "--depend", "mobile_sim=none",
                               "--remap", "amcl_pose=nav/amcl_pose"], {}))
            cmd, _ = leaf_run_command("juiz", app, {"subscribes": {"odom": "nav/odom"}, "publishes": {"cmd_vel": "x"}})
            self.assertEqual(cmd[-4:], ["--remap", "odom=nav/odom", "--remap", "cmd_vel=x"])
            # コンテナの引数の置き換えは --remap で表せない。juiz_run の env で代わりができるときだけ juiz run
            self.assertIsNone(leaf_run_command("juiz", app, {"container_args": {"dwa0": {"bridge": None}}}))
            dwa = yaml.safe_load((ROOT / "app" / "leaves" / "planner_dwa.yaml").read_text(encoding="utf-8"))
            cmd, env = leaf_run_command("juiz", app, dwa)
            self.assertIn("cmd_vel=cmd_vel_dwa", cmd)
            self.assertIn("lidar_mount=nav/lidar_mount", cmd)
            self.assertEqual(env, {"DWA_BRIDGE_AMCL_URL": "", "DWA_BRIDGE_PLANNER_URL": ""})

    def test_juiz_version(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "juiz"
            fake.write_text("#!/bin/sh\necho juiz 0.2.5\n")
            fake.chmod(0o755)
            if os.name != "nt":
                self.assertEqual(juiz_version(str(fake)), (0, 2, 5))
            self.assertEqual(juiz_version(str(Path(d) / "none")), (0,))

    def test_write_leaf_conf(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "app.conf").write_text(DWA_CONF, encoding="utf-8")
            out = write_leaf_conf(Path(d), "app.conf", {"publishes": {"cmd_vel": "x"}})
            self.assertEqual(out, Path(d) / "leaf.conf")
            leaf = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(leaf["containers"][0]["processes"][0]["publishes"], ["x"])


class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def all_packages(self):
        paths = []
        for n in stack.ORDER:
            extra = {}
            if n == "navigation_hub":
                extra = {"leaves/planner_dijkstra.yaml": "subscribes: {amcl_pose: nav/amcl_pose}\n",
                         "leaves/planner_dwa.yaml": "publishes: {cmd_vel: cmd_vel_dwa}\n"}
            paths.append(str(make_package(self.dir / f"{n}-0.1.0.juizapp", n, extra)))
        return paths

    def test_package(self):
        p = make_package(self.dir / "a.juizapp", "planner_dwa", {"web/index.html": "x"})
        app = App(p)
        self.assertEqual((app.name, app.version, app.entry, app.is_package), ("planner_dwa", "0.1.0", "app.conf", True))
        self.assertIsNone(app.read("leaves/planner_dwa.yaml"))
        d = app.extract(self.dir / "out")
        self.assertTrue((d / "web" / "index.html").exists())

    def test_directory(self):
        # 開発中のリポジトリの app/(juizapp.yaml がある)
        app = App(ROOT / "app")
        self.assertEqual(app.name, "navigation_hub")
        self.assertFalse(app.is_package)
        self.assertIn("nav/amcl_pose", app.read("leaves/planner_dijkstra.yaml"))
        self.assertEqual(app.extract(self.dir), app.path)

    def test_directory_without_manifest(self):
        (self.dir / "app.conf").write_text('"name": "mobile_sim"\n', encoding="utf-8")
        self.assertEqual(App(self.dir).name, "mobile_sim")

    def test_not_a_package(self):
        f = self.dir / "x.juizapp"
        f.write_text("not a zip")
        with self.assertRaisesRegex(StackError, "not a .juizapp"):
            App(f)
        with zipfile.ZipFile(f, "w") as z:
            z.writestr("app.conf", "")
        with self.assertRaisesRegex(StackError, "no juizapp.yaml"):
            App(f)
        with self.assertRaisesRegex(StackError, "does not exist"):
            App(self.dir / "missing.juizapp")

    def test_unsafe_entry(self):
        p = make_package(self.dir / "a.juizapp", "planner_dwa", {"../evil": "x"})
        with self.assertRaisesRegex(StackError, "unsafe entry"):
            App(p).extract(self.dir / "out")
        self.assertFalse((self.dir / "evil").exists())

    def test_collect(self):
        apps = collect(list(reversed(self.all_packages())))
        self.assertEqual(sorted(apps), sorted(stack.ORDER))

    def test_collect_missing_and_twice(self):
        paths = self.all_packages()
        with self.assertRaisesRegex(StackError, r"missing: planner_dwa\n.*planner_dwa/releases"):
            collect([p for p in paths if "planner_dwa" not in p])
        with self.assertRaisesRegex(StackError, "given twice"):
            collect(paths + [str(make_package(self.dir / "dup.juizapp", "mobile_sim"))])
        with self.assertRaisesRegex(StackError, "not part of the navigation stack"):
            collect(paths + [str(make_package(self.dir / "t.juizapp", "teleop_twist"))])


if __name__ == "__main__":
    unittest.main()


class StartOrRestartTest(unittest.TestCase):
    def setUp(self):
        self.s = stack.Stack("juiz", Path(tempfile.mkdtemp()))

    def test_started_first_time(self):
        with mock.patch.object(self.s, "start") as start, mock.patch.object(self.s, "restart") as restart:
            self.s.start_or_restart("navigation_hub", ["juiz"], Path("."), None)
        start.assert_called_once_with("navigation_hub", ["juiz"], Path("."), None)
        restart.assert_not_called()

    def test_restarts_once(self):
        with mock.patch.object(self.s, "start", side_effect=StackError("navigation_hub exited (code 1)")), \
                mock.patch.object(self.s, "restart") as restart:
            self.s.start_or_restart("navigation_hub", ["juiz"], Path("."), None)
        restart.assert_called_once_with("navigation_hub", ["juiz"], Path("."), None)

    def test_second_failure_raises(self):
        with mock.patch.object(self.s, "start", side_effect=StackError("navigation_hub exited (code 1)")), \
                mock.patch.object(self.s, "restart", side_effect=StackError("navigation_hub exited (code 1)")):
            with self.assertRaises(StackError):
                self.s.start_or_restart("navigation_hub", ["juiz"], Path("."), None)
