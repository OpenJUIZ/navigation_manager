import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from navigation_manager import stack
from navigation_manager.stack import App, StackError, check_juiz, collect, leaf_args

ROOT = Path(__file__).resolve().parents[2]

def make_package(path: Path, name: str, extra=None, version="0.1.0", depends=""):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("juizapp.yaml", f"format: 1\nname: {name}\nversion: {version}\nkind: app\nentry: app.conf\n{depends}")
        z.writestr("app.conf", f'"name": "{name}"\n')
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return path


class LeafTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_bundled_rules(self):
        # リポジトリの app/leaves の置き換えから、依存につながない --depend と、トピック名の --remap を作る
        import yaml
        rules = yaml.safe_load((ROOT / "app" / "leaves" / "planner_dwa.yaml").read_text(encoding="utf-8"))
        app = App(make_package(self.dir / "dwa.juizapp", "planner_dwa", depends="depends:\n  mobile_sim: '>=0.3.1'\n"))
        args = leaf_args(app, rules)
        self.assertEqual(args[:2], ["--depend", "mobile_sim=none"])
        remaps = [args[i + 1] for i in range(len(args)) if args[i] == "--remap"]
        self.assertIn("odom=nav/odom", remaps)
        self.assertIn("path=nav/path", remaps)
        self.assertIn("cmd_vel=cmd_vel_dwa", remaps)
        self.assertEqual(len(remaps), 6)
        # bridge は juiz run の引数では止められないので、環境変数で止める
        self.assertEqual(stack.LEAF_ENV["planner_dwa"], {"DWA_BRIDGE_AMCL_URL": "", "DWA_BRIDGE_PLANNER_URL": ""})

    def test_no_depends(self):
        app = App(make_package(self.dir / "p.juizapp", "planner_dijkstra"))
        self.assertEqual(leaf_args(app, {"subscribes": {"amcl_pose": "nav/amcl_pose"}}),
                         ["--remap", "amcl_pose=nav/amcl_pose"])
        self.assertEqual(leaf_args(app, {}), [])

    def test_conflicting_rules(self):
        app = App(make_package(self.dir / "p.juizapp", "planner_dwa"))
        with self.assertRaisesRegex(StackError, "renames x to both"):
            leaf_args(app, {"subscribes": {"x": "a"}, "publishes": {"x": "b"}})

    def test_check_juiz(self):
        # juiz run --help に --remap が出ない juiz は古い
        fake = self.dir / "fake_juiz.py"
        fake.write_text("import sys\nprint('Usage: juiz run [OPTIONS] <PACKAGE>' + (' --remap' if 'new' in sys.argv[0] else ''))\n")
        new = self.dir / "new_juiz.py"
        new.write_text(fake.read_text())
        with self.assertRaisesRegex(StackError, "too old"):
            check_juiz_with(fake)
        check_juiz_with(new)


def check_juiz_with(script: Path):
    # check_juiz は [juiz, "run", "--help"] を走らせる。Python のスクリプトを juiz の代わりにする
    orig = stack.subprocess.run

    def run(cmd, **kw):
        return orig([sys.executable, str(script)] + cmd[1:], **kw)

    stack.subprocess.run = run
    try:
        check_juiz("juiz")
    finally:
        stack.subprocess.run = orig


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
        self.assertEqual(app.read("web/index.html"), "x")
        self.assertEqual(app.depends, [])

    def test_directory(self):
        # 開発中のリポジトリの app/(juizapp.yaml がある)
        app = App(ROOT / "app")
        self.assertEqual(app.name, "navigation_hub")
        self.assertFalse(app.is_package)
        self.assertIn("nav/amcl_pose", app.read("leaves/planner_dijkstra.yaml"))
        self.assertEqual(app.depends, ["localization_amcl"])

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
