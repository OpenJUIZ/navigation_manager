"""ナビゲーション一式を .juizapp から起動する(navigation-manager stack)。

    navigation-manager stack mobile_sim-0.3.3.juizapp localization_amcl-0.1.0.juizapp \\
        planner_dijkstra-0.1.0.juizapp planner_dwa-0.1.0.juizapp navigation_hub-0.4.0.juizapp [--map map.yaml]

navigation_hub を根にした 1 本の木の形で、決まった順に起動する。最後に navigation_manager(HTTP 8005、画面つき)を起動する。

    navigation_hub (8006) ─┬─ localization_amcl (8002) ── mobile_sim (8000)
                           ├─ planner_dijkstra (8003)
                           └─ planner_dwa (8004)

1. mobile_sim(基礎)        juiz run
2. localization_amcl        juiz run(依存の mobile_sim に IPC でつなぐ)
3. planner_dijkstra         juiz run --depend mobile_sim=none --remap amcl_pose=nav/amcl_pose
4. planner_dwa              juiz run --depend mobile_sim=none --remap odom=nav/odom ... --remap cmd_vel=cmd_vel_dwa
5. navigation_hub           juiz run(依存の localization_amcl と、葉の 2 つに IPC でつなぐ)
6. navigation_manager       この Python で動かす。各アプリを HTTP で呼ぶ

葉(planner_dijkstra・planner_dwa)は依存の mobile_sim につながない(--depend mobile_sim=none)。つなぐと mobile_sim の master が
2 つになってトピックが届かなくなるため(juiz#449)。代わりに navigation_hub が nav/... の名前で出し直すトピックを受ける。
付け替えるトピック名(--remap)は navigation_hub の .juizapp に入っている leaves/<アプリ名>.yaml の subscribes・publishes から作る。
planner_dwa の bridge(AMCL・planner を HTTP で読みに行く仕組み)は、環境変数 DWA_BRIDGE_AMCL_URL・DWA_BRIDGE_PLANNER_URL を
空にして使わない(大域経路は navigation_manager が dwa_set_path で渡す)。
根(navigation_hub)は葉より後に起動する(先に起動すると葉につながらない)。
--remap と --depend 名前=none を使うので、juiz 0.2.5 以降が要る。

.juizapp の代わりに、app.conf のあるディレクトリ(.juizapp を展開したものや、開発中のリポジトリの app/)も渡せる。
葉のディレクトリは juiz pack で一時ディレクトリに .juizapp にしてから juiz run で、ほかは juiz -f で起動する。
juiz は PATH にあるもの(pip install juiz-cli)を使う。環境変数 JUIZ で別のものを指定できる。
Ctrl-C で、起動したものを逆の順に止める。Windows でも動く(子は別のプロセスグループで起動し、Ctrl+Break で止める)。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - pip install navigation-manager で入る
    yaml = None

PACKAGE_DIR = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORTS = {
    "mobile_sim": 8000,
    "localization_amcl": 8002,
    "planner_dijkstra": 8003,
    "planner_dwa": 8004,
    "navigation_manager": 8005,
    "navigation_hub": 8006,
}
# 起動の順。葉が先、根(navigation_hub)が最後
ORDER = ["mobile_sim", "localization_amcl", "planner_dijkstra", "planner_dwa", "navigation_hub"]
LEAVES = ("planner_dijkstra", "planner_dwa")
# 葉で使わない仕組みを止める環境変数(planner_dwa の bridge。空にすると読みに行かない)
LEAF_ENV = {
    "planner_dwa": {"DWA_BRIDGE_AMCL_URL": "", "DWA_BRIDGE_PLANNER_URL": ""},
}
# --remap と --depend 名前=none(juiz#452)が入った版
JUIZ_REQUIRED = "0.2.5"
RELEASES = {
    "mobile_sim": "https://github.com/OpenJUIZ/mobile_sim/releases",
    "localization_amcl": "https://github.com/OpenJUIZ/localization_amcl/releases",
    "planner_dijkstra": "https://github.com/OpenJUIZ/planner_dijkstra/releases",
    "planner_dwa": "https://github.com/OpenJUIZ/planner_dwa/releases",
    "navigation_hub": "https://github.com/OpenJUIZ/navigation_manager/releases",
}
WINDOWS = os.name == "nt"


class StackError(Exception):
    pass


def _load_yaml(text: str):
    if yaml is None:
        raise StackError("PyYAML is needed to read app.conf. Install it with: pip install pyyaml")
    return yaml.safe_load(text)


class App:
    """一式の 1 つのアプリ。.juizapp のファイルか、app.conf のあるディレクトリ"""

    def __init__(self, path: Path):
        self.path = path.resolve()
        self.is_package = self.path.is_file()
        if self.is_package:
            if not zipfile.is_zipfile(self.path):
                raise StackError(f"{path} is not a .juizapp (not a zip file)")
            with zipfile.ZipFile(self.path) as z:
                try:
                    manifest = _load_yaml(z.read("juizapp.yaml").decode())
                except KeyError:
                    raise StackError(f"{path} is not a .juizapp (no juizapp.yaml at the root)") from None
            self.name = manifest["name"]
            self.version = str(manifest.get("version", ""))
            self.entry = manifest.get("entry", "app.conf")
            self.depends = list((manifest.get("depends") or {}).keys())
        elif self.path.is_dir():
            m = self.path / "juizapp.yaml"
            manifest = _load_yaml(m.read_text(encoding="utf-8")) if m.exists() else {}
            self.entry = manifest.get("entry", "app.conf")
            conf = self.path / self.entry
            if not conf.exists():
                raise StackError(f"{path} has no {self.entry}")
            self.name = manifest.get("name") or _load_yaml(conf.read_text(encoding="utf-8"))["name"]
            self.version = str(manifest.get("version", "dev"))
            self.depends = list((manifest.get("depends") or {}).keys())
        else:
            raise StackError(f"{path} does not exist")

    def __repr__(self):
        return f"{self.name} {self.version} ({self.path})"

    def read(self, rel: str) -> Optional[str]:
        """パッケージ・ディレクトリの中のファイルを読む。無ければ None"""
        if self.is_package:
            with zipfile.ZipFile(self.path) as z:
                try:
                    return z.read(rel).decode()
                except KeyError:
                    return None
        p = self.path / rel
        return p.read_text(encoding="utf-8") if p.exists() else None


def collect(paths: List[str]) -> Dict[str, App]:
    apps: Dict[str, App] = {}
    for p in paths:
        app = App(Path(p))
        if app.name not in PORTS or app.name == "navigation_manager":
            raise StackError(f"{p}: {app.name} is not part of the navigation stack (need {', '.join(ORDER)})")
        if app.name in apps:
            raise StackError(f"{app.name} is given twice: {apps[app.name].path} and {app.path}")
        apps[app.name] = app
    missing = [n for n in ORDER if n not in apps]
    if missing:
        raise StackError("missing: " + ", ".join(missing) + "\n"
                         + "\n".join(f"  {n}: download the .juizapp from {RELEASES[n]}" for n in missing))
    return apps


def leaf_args(app: App, rules: dict) -> List[str]:
    """葉を juiz run で起動するときの引数。依存にはつながず(--depend 名前=none)、
    トピック名を rules(navigation_hub の leaves/<名前>.yaml)の subscribes・publishes で付け替える(--remap 旧=新)"""
    args: List[str] = []
    for dep in app.depends:
        args += ["--depend", f"{dep}=none"]
    remaps: Dict[str, str] = {}
    for key in ("subscribes", "publishes"):
        for old, new in (rules.get(key) or {}).items():
            if remaps.get(old, new) != new:
                raise StackError(f"leaves/{app.name}.yaml renames {old} to both {remaps[old]} and {new}")
            remaps[old] = new
    for old, new in remaps.items():
        args += ["--remap", f"{old}={new}"]
    return args


def check_juiz(juiz: str) -> None:
    """juiz run が --remap を知っているか(juiz 0.2.5 以降)"""
    try:
        out = subprocess.run([juiz, "run", "--help"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise StackError(f"cannot run {juiz}: {e}") from None
    if "--remap" not in out.stdout + out.stderr:
        raise StackError(f"{juiz} is too old: juiz {JUIZ_REQUIRED} or later is needed "
                         f"(juiz run --remap). Update it with: pip install -U juiz-cli")


def pack(juiz: str, app: App, work: Path, logdir: Path) -> Path:
    """開発用: 葉のディレクトリ(プラグインを置いた app/ など)を juiz pack で work/ に .juizapp にする"""
    out = work / f"{app.name}-{app.version}.juizapp"
    with open(logdir / f"{app.name}.pack.log", "wb") as log:
        r = subprocess.run([juiz, "pack", str(app.path), "-o", str(out)], stdout=log, stderr=subprocess.STDOUT)
    if r.returncode != 0 or not out.exists():
        raise StackError(f"juiz pack {app.path} failed (see {logdir / (app.name + '.pack.log')})")
    return out


def find_juiz() -> str:
    j = os.environ.get("JUIZ")
    if j:
        return j
    j = shutil.which("juiz")
    if j:
        return j
    # venv を有効にせずに navigation-manager を動かしたときは、同じ venv の juiz を探す
    here = Path(sys.executable).parent
    for name in ("juiz.exe", "juiz") if WINDOWS else ("juiz",):
        for d in (here, here / "Scripts", here / "bin"):
            if (d / name).exists():
                return str(d / name)
    raise StackError("juiz is not found. Install it with: pip install juiz-cli")


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0


def get_json(url: str, timeout: float = 1.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


class Stack:
    def __init__(self, juiz: str, logdir: Path):
        self.juiz = juiz
        self.logdir = logdir
        self.procs: List[tuple] = []

    def start(self, name: str, cmd: List[str], cwd: Path, env_extra: Optional[dict] = None,
              ready_path: str = "api/container/list", wait: float = 60.0):
        log = open(self.logdir / f"{name}.log", "wb")
        env = dict(os.environ, **(env_extra or {}))
        kw = {}
        if WINDOWS:
            # Ctrl-C が子に届かないよう別のプロセスグループにし、止めるときは Ctrl+Break を送る
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        p = subprocess.Popen(cmd, cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT, env=env, **kw)
        log.close()
        self.procs.append((name, p))
        url = f"http://{HOST}:{PORTS[name]}/{ready_path}"
        end = time.time() + wait
        while True:
            if p.poll() is not None:
                raise StackError(f"{name} exited (code {p.returncode})")
            try:
                get_json(url)
                break
            except (OSError, ValueError):
                if time.time() > end:
                    raise StackError(f"{name} did not answer at {url}")
                time.sleep(0.2)
        print(f"started {name} (:{PORTS[name]})", flush=True)

    def restart(self, name: str, *launch):
        """name を止めて、ログを <name>.1.log に残してから起動し直す"""
        for i, (n, p) in enumerate(self.procs):
            if n == name:
                if p.poll() is None:
                    _terminate(p)
                    try:
                        p.wait(10)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        p.wait(5)
                del self.procs[i]
                break
        log = self.logdir / f"{name}.log"
        if log.exists():
            log.replace(self.logdir / f"{name}.1.log")
        end = time.time() + 10
        while port_in_use(PORTS[name]) and time.time() < end:
            time.sleep(0.2)
        self.start(name, *launch)

    def check(self):
        for name, p in self.procs:
            if p.poll() is not None:
                raise StackError(f"{name} exited (code {p.returncode})")

    def tail(self, lines: int = 20) -> str:
        out = []
        for name, _ in self.procs:
            f = self.logdir / f"{name}.log"
            if not f.exists():
                continue
            # バックトレースの行(番号付きのフレームと "at ...")は省く
            text = [ln for ln in f.read_text(errors="replace").splitlines()
                    if not ln.strip().startswith("at ") and not ln.strip().split(":")[0].isdigit()]
            out.append(f"--- {name}.log (last lines)\n" + "\n".join(text[-lines:]))
        return "\n".join(out)

    def stop(self):
        for _, p in reversed(self.procs):
            if p.poll() is None:
                _terminate(p)
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
        for _, p in self.procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()


def _call(port: int, container: str, name: str, type_name: str):
    base = f"http://{HOST}:{port}"
    ident = urllib.parse.quote(f"{base}/container_process/{name}::{type_name}:{container}", safe="")
    req = urllib.request.Request(f"{base}/api/container_process/call?identifier={ident}", data=b"{}", method="PATCH",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=2.0) as r:
        return json.loads(r.read())


def tree_ready() -> bool:
    """navigation_hub が出し直すトピックが葉に届いているか: planner_dijkstra に推定姿勢(始点)、
    planner_dwa に推定姿勢とスキャン(障害物)"""
    try:
        planner = _call(PORTS["planner_dijkstra"], "planner0", "get_state0", "planner_get_state")
        dwa = _call(PORTS["planner_dwa"], "dwa0", "get_state0", "dwa_get_state")
    except (OSError, ValueError):
        return False
    return bool(planner.get("start")) and bool(dwa.get("amcl_pose")) and bool(dwa.get("obstacles"))


def wait_tree(timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if tree_ready():
            print("topics reach the leaves through navigation_hub", flush=True)
            return True
        time.sleep(0.5)
    return False


def _terminate(p: subprocess.Popen):
    try:
        if WINDOWS:
            p.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(p.pid, signal.SIGTERM)
    except OSError:
        pass


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def run(a) -> int:
    apps = collect(a.packages)
    juiz = find_juiz()
    check_juiz(juiz)
    busy = [f"{n} ({port})" for n, port in PORTS.items() if port_in_use(port)]
    if busy:
        raise StackError("these ports are already in use: " + ", ".join(busy)
                         + ". Stop the running apps first (teleop_twist also uses 8002)")
    hub = apps["navigation_hub"]
    rules = {}
    for leaf in LEAVES:
        text = hub.read(f"leaves/{leaf}.yaml")
        if text is None:
            raise StackError(f"{hub.path} has no leaves/{leaf}.yaml (use navigation_hub 0.2.0 or later)")
        rules[leaf] = _load_yaml(text) or {}

    work = Path(tempfile.mkdtemp(prefix="navigation_stack_"))
    logdir = Path(a.logdir).resolve() if a.logdir else work / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    print(f"juiz: {juiz}\nlogs: {logdir}", flush=True)
    for n in ORDER:
        print(f"  {apps[n]}", flush=True)
    stack = Stack(juiz, logdir)
    signal.signal(signal.SIGTERM, _interrupt)
    if WINDOWS and hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _interrupt)
    code = 0
    try:
        for n in ORDER:
            app = apps[n]
            if n in LEAVES:
                package = app.path if app.is_package else pack(juiz, app, work, logdir)
                launch = ([juiz, "run", str(package)] + leaf_args(app, rules[n]), work, LEAF_ENV.get(n))
            elif app.is_package:
                launch = ([juiz, "run", str(app.path)], work, None)
            else:
                cmd = [juiz, "-f", str(app.path / app.entry), "-d"]
                if n == "mobile_sim":
                    cmd += ["--ipc", "mobile_sim"]  # juiz run の基礎と同じく IPC 名を持たせる
                launch = (cmd, app.path, {"JUIZ_APP_DIR": str(app.path)})
            if n == "navigation_hub":
                try:
                    stack.start(n, *launch)
                except StackError as e:
                    # 起動直後に葉や localization_amcl の IPC の応答待ちが切れて終わることがある。1 度だけ起動し直す
                    print(f"{e}; restarting navigation_hub", flush=True)
                    stack.restart(n, *launch)
            else:
                stack.start(n, *launch)
        # 根が葉につなぎ損ねることがある(起動直後の IPC の応答待ちが切れるなど)。トピックが木を通って葉に届くまで待ち、
        # 届かなければ根を 1 度だけ起動し直す
        for attempt in range(2):
            if wait_tree(a.tree_timeout):
                break
            if attempt == 1:
                raise StackError("topics do not reach planner_dijkstra / planner_dwa through navigation_hub "
                                 "(see navigation_hub.log)")
            print("topics do not reach the leaves yet; restarting navigation_hub", flush=True)
            stack.restart("navigation_hub", *launch)
        manager = [sys.executable, "-m", "navigation_manager", "--port", str(PORTS["navigation_manager"])]
        for opt, n in (("--sim", "mobile_sim"), ("--amcl", "localization_amcl"),
                       ("--planner", "planner_dijkstra"), ("--dwa", "planner_dwa")):
            manager += [opt, f"http://{HOST}:{PORTS[n]}"]
        if a.map:
            manager += ["--map", str(Path(a.map).resolve())]
        manager += a.manager_args or []
        pythonpath = str(PACKAGE_DIR.parent) + os.pathsep + os.environ.get("PYTHONPATH", "")
        stack.start("navigation_manager", manager, work, {"PYTHONPATH": pythonpath}, ready_path="api/status")
        if a.test:
            from .e2e import run_test
            run_test(tuple(float(v) for v in a.goal.split(",")), a.timeout,
                     Path(a.map).name if a.map else "mobile_sim")
        else:
            print(f"running. open http://localhost:{PORTS['navigation_manager']}/ . Ctrl-C to stop", flush=True)
            while True:
                stack.check()
                time.sleep(0.5)
    except KeyboardInterrupt:
        print("stopping", flush=True)
    except Exception as e:  # noqa: BLE001 - 何で止まっても、起動したものを止めてログを見せる
        print(f"FAILED: {e}", flush=True)
        print(stack.tail(), flush=True)
        code = 1
    finally:
        stack.stop()
        if not a.logdir:
            print(f"logs are kept in {logdir}", flush=True)
        for f in work.glob("*.juizapp"):
            f.unlink()
    return code


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="navigation-manager stack", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("packages", nargs="+", metavar="PACKAGE",
                    help="5 つのアプリの .juizapp(または app.conf のあるディレクトリ)。順は問わない")
    ap.add_argument("--map", metavar="YAML", help="配る地図の YAML(SLAM で作ったものなど)。省くと mobile_sim の地図")
    ap.add_argument("--logdir", help="各アプリのログの置き場(既定は一時ディレクトリ)")
    ap.add_argument("--test", action="store_true",
                    help="ゴールを出して着くまでと、キャンセルで止まることを確かめて終わる")
    ap.add_argument("--goal", default="0.0,1.5,0.0", help="x,y,theta(map 座標系、--test で使う)")
    ap.add_argument("--timeout", type=float, default=120.0, help="--test でゴールに着くまで待つ秒数")
    ap.add_argument("--tree-timeout", type=float, default=20.0, help=argparse.SUPPRESS)
    ap.add_argument("--manager-arg", dest="manager_args", action="append", metavar="ARG",
                    help="navigation_manager にそのまま渡す引数(--manager-arg=--initial-pose-from-sim など、何度でも)")
    a = ap.parse_args(argv)
    try:
        sys.exit(run(a))
    except StackError as e:
        sys.exit(f"navigation-manager stack: {e}")
