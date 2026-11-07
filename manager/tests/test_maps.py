import base64
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zlib
from pathlib import Path

from navigation_manager.apps import map_parts
from navigation_manager.manager import Manager
from navigation_manager.maps import (
    MapError,
    _parse_flat_yaml,
    load_map_file,
    load_map_upload,
    parse_yaml,
    pgm_to_png,
    png_size,
)
from navigation_manager.server import serve
from navigation_manager.state import FOLLOWING, Pose

DATA = Path(__file__).resolve().parent / "data"
SLAM_MAP = DATA / "slam_map" / "map.yaml"

SIM_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + (200).to_bytes(4, "big") + (100).to_bytes(4, "big")
SIM_INFO = {"resolution": 0.05, "origin": [-5.0, -2.5, 0.0], "width": 200, "height": 100}


def png_pixels(png):
    """encode_gray_png が書いた 8 bit グレーの PNG(IDAT 1 つ)から画素を取り出す"""
    w, h = png_size(png)
    i = png.index(b"IDAT")
    n = int.from_bytes(png[i - 4:i], "big")
    raw = zlib.decompress(png[i + 4:i + 4 + n])
    return w, h, b"".join(raw[y * (w + 1) + 1:(y + 1) * (w + 1)] for y in range(h))


class FakeApps:
    """manager が呼ぶ各アプリの代わり。地図を渡された回数と中身を覚える"""

    def __init__(self):
        self.planner_maps, self.amcl_maps, self.stopped = [], [], 0

    def map_png(self):
        return "image/png", SIM_PNG

    def map_info(self):
        return dict(SIM_INFO)

    def set_planner_map(self, parts):
        self.planner_maps.append(parts)

    def set_amcl_map(self, parts):
        self.amcl_maps.append(parts)

    def stop(self):
        self.stopped += 1


class YamlTest(unittest.TestCase):
    def test_flat_parser_reads_map_server_yaml(self):
        v = _parse_flat_yaml(
            "# comment\nimage: 'my map.pgm'  # trailing\nresolution: 0.050000\n"
            "origin: [-10.025, -10.025, 0.000000]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\nmode: trinary\n")
        self.assertEqual(v["image"], "my map.pgm")
        self.assertEqual(v["resolution"], 0.05)
        self.assertEqual(v["origin"], [-10.025, -10.025, 0.0])
        self.assertEqual(v["negate"], 0)
        self.assertEqual(v["mode"], "trinary")

    def test_flat_parser_rejects_non_mapping_lines(self):
        with self.assertRaises(MapError):
            _parse_flat_yaml("image map.png\n")

    def test_parse_yaml_gives_the_same_as_the_flat_parser(self):
        text = SLAM_MAP.read_text()
        self.assertEqual(parse_yaml(text), _parse_flat_yaml(text))


class PgmTest(unittest.TestCase):
    def test_p5_to_png(self):
        png = pgm_to_png(b"P5\n# made by map_saver\n3 2\n255\n" + bytes([0, 100, 205, 254, 255, 1]))
        self.assertEqual(png_pixels(png), (3, 2, bytes([0, 100, 205, 254, 255, 1])))

    def test_p2_with_smaller_maxval_is_scaled(self):
        png = pgm_to_png(b"P2 2 1 100\n0 100\n")
        self.assertEqual(png_pixels(png), (2, 1, bytes([0, 255])))

    def test_truncated_pgm(self):
        with self.assertRaises(MapError):
            pgm_to_png(b"P5\n3 2\n255\n\x00\x00")


class LoadMapTest(unittest.TestCase):
    def test_slam_map(self):
        m = load_map_file(SLAM_MAP)
        self.assertEqual(m.source, "map.yaml")
        self.assertEqual((m.info["width"], m.info["height"]), (384, 384))
        self.assertEqual(m.info["origin"], [-10.025, -10.025, 0.0])
        self.assertFalse(m.info["negate"])
        p = map_parts(m.png, m.info)
        self.assertEqual((p["resolution"], p["occupied_thresh"], p["free_thresh"]), (0.05, 0.65, 0.196))
        self.assertTrue(p["image"].startswith(b"\x89PNG"))

    def test_image_relative_to_the_yaml_and_pgm(self):
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "maps"
            sub.mkdir()
            (sub / "room.pgm").write_bytes(b"P5 2 2 255 " + bytes([0, 255, 205, 0]))
            (sub / "room.yaml").write_text("image: room.pgm\nresolution: 0.1\norigin: [1, 2]\nnegate: 1\n")
            m = load_map_file(sub / "room.yaml")
        self.assertEqual(m.source, "room.yaml")
        self.assertEqual(m.info["origin"], [1.0, 2.0, 0.0])
        self.assertTrue(m.info["negate"])
        self.assertEqual(png_pixels(m.png), (2, 2, bytes([0, 255, 205, 0])))

    def test_missing_image(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "m.yaml").write_text("image: none.png\nresolution: 0.05\norigin: [0, 0, 0]\n")
            with self.assertRaisesRegex(MapError, "none.png"):
                load_map_file(Path(d) / "m.yaml")

    def test_missing_keys(self):
        with self.assertRaisesRegex(MapError, "resolution"):
            load_map_upload("image: m.png\norigin: [0, 0, 0]\n", {"m.png": SIM_PNG})

    def test_upload_finds_the_image_by_file_name(self):
        m = load_map_upload("image: /home/me/maps/m.png\nresolution: 0.05\norigin: [0, 0, 0]\n", {"m.png": SIM_PNG}, "m.yaml")
        self.assertEqual((m.info["width"], m.info["height"], m.source), (200, 100, "m.yaml"))
        with self.assertRaisesRegex(MapError, "select that image"):
            load_map_upload("image: other.png\nresolution: 0.05\norigin: [0, 0, 0]\n", {"m.png": SIM_PNG})

    def test_not_an_image(self):
        with self.assertRaisesRegex(MapError, "PNG or PGM"):
            load_map_upload("image: m.jpg\nresolution: 0.05\norigin: [0, 0, 0]\n", {"m.jpg": b"\xff\xd8\xff"})


class ManagerMapTest(unittest.TestCase):
    def test_load_map_distributes_and_shows_it(self):
        apps = FakeApps()
        m = Manager(apps)
        self.assertEqual(m.map_info(), SIM_INFO)            # 配る前は mobile_sim の地図を描く
        self.assertIsNone(m.status()["map_source"])
        out = m.load_map(str(SLAM_MAP))
        self.assertEqual(out["source"], "map.yaml")
        self.assertEqual(len(apps.planner_maps), 1)
        self.assertEqual(len(apps.amcl_maps), 1)
        self.assertEqual(apps.planner_maps[0]["origin"], [-10.025, -10.025, 0.0])
        self.assertEqual(m.map_png(), ("image/png", SLAM_MAP.with_name("map.png").read_bytes()))
        self.assertEqual(m.map_info()["width"], 384)
        s = m.status()
        self.assertEqual((s["map_source"], s["map_version"]), ("map.yaml", 1))

    def test_distribute_mobile_sim_map_replaces_the_loaded_map(self):
        apps = FakeApps()
        m = Manager(apps)
        m.load_map(str(SLAM_MAP))
        m.distribute_map()
        self.assertEqual(m.status()["map_source"], "mobile_sim")
        self.assertEqual(m.status()["map_version"], 2)
        self.assertEqual(m.map_png()[1], SIM_PNG)
        self.assertEqual(apps.amcl_maps[-1]["origin"], [-5.0, -2.5, 0.0])

    def test_loading_a_map_while_following_stops(self):
        apps = FakeApps()
        m = Manager(apps)
        m.nav.state = FOLLOWING
        m.nav.goal = Pose(1.0, 1.0, 0.0)
        m.load_map(str(SLAM_MAP))
        self.assertEqual(m.status()["state"], "canceled")
        self.assertEqual(apps.stopped, 1)

    def test_bad_map_keeps_the_current_one(self):
        apps = FakeApps()
        m = Manager(apps)
        m.load_map(str(SLAM_MAP))
        with self.assertRaises(MapError):
            m.load_map(str(DATA / "no_such_map.yaml"))
        self.assertEqual(m.status()["map_source"], "map.yaml")
        self.assertEqual(len(apps.amcl_maps), 1)


class ServerMapTest(unittest.TestCase):
    def setUp(self):
        self.apps = FakeApps()
        self.manager = Manager(self.apps)
        self.server = serve(self.manager, "127.0.0.1", 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method="GET" if body is None else "POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_load_map_by_path(self):
        code, body = self.request("/api/load_map", {"path": str(SLAM_MAP)})
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(body)["source"], "map.yaml")
        code, info = self.request("/api/map_info")
        self.assertEqual(json.loads(info)["width"], 384)
        code, png = self.request("/api/map?v=1")      # 画面はキャッシュを避けるためにクエリを付ける
        self.assertEqual((code, png), (200, SLAM_MAP.with_name("map.png").read_bytes()))

    def test_load_map_upload(self):
        png = SLAM_MAP.with_name("map.png").read_bytes()
        code, body = self.request("/api/load_map", {
            "yaml": SLAM_MAP.read_text(), "name": "my_map.yaml",
            "images": {"map.png": base64.b64encode(png).decode()}})
        self.assertEqual(code, 200, body)
        self.assertEqual(json.loads(self.request("/api/status")[1])["map_source"], "my_map.yaml")
        self.assertEqual(self.apps.planner_maps[0]["image"], png)

    def test_load_map_errors_are_400(self):
        for body in ({}, {"path": str(DATA / "no_such_map.yaml")},
                     {"yaml": SLAM_MAP.read_text(), "images": {}},
                     {"yaml": SLAM_MAP.read_text(), "images": {"map.png": "not base64!"}}):
            code, out = self.request("/api/load_map", body)
            self.assertEqual(code, 400, (body, out))
            self.assertIn("error", json.loads(out))
        self.assertEqual(self.apps.amcl_maps, [])


if __name__ == "__main__":
    unittest.main()
