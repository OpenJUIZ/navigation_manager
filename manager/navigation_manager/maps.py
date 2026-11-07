"""地図の YAML(ROS の map_server・SLAM の mapper が書き出す形)と画像を読む。標準ライブラリだけで書く。

    image: map.png          # YAML から見た相対パス、または絶対パス。PNG か PGM(P5・P2)
    resolution: 0.05        # m/px
    origin: [-10.025, -10.025, 0]
    occupied_thresh: 0.65
    free_thresh: 0.196
    negate: 0

読んだ地図は planner_dijkstra の planner_set_map・localization_amcl の amcl_set_map にそのまま渡せる形
(apps.map_parts)と、画面に描くための大きさ(width・height)を持つ。PGM は PNG(8 bit グレー)に直して渡す。
"""
from __future__ import annotations

import ast
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class MapError(ValueError):
    pass


@dataclass
class MapData:
    png: bytes
    info: dict          # resolution・origin・negate・occupied_thresh・free_thresh・width・height
    source: str         # 画面・status に出す名前(YAML のファイル名、または mobile_sim)


def parse_yaml(text: str) -> dict:
    """地図の YAML を読む。PyYAML があればそれを使い、無ければ `key: value` の 1 段だけの形を読む"""
    try:
        import yaml  # type: ignore
    except ImportError:
        return _parse_flat_yaml(text)
    try:
        v = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise MapError(f"the map YAML is not valid: {e}") from e
    if not isinstance(v, dict):
        raise MapError("the map YAML must be a mapping (image, resolution, origin, ...)")
    return v


def _parse_flat_yaml(text: str) -> dict:
    out = {}
    for n, line in enumerate(text.splitlines(), 1):
        s = _strip_comment(line).strip()
        if not s or s == "---":
            continue
        key, sep, value = s.partition(":")
        if not sep or not key.strip():
            raise MapError(f"line {n}: expected `key: value`: {line!r}")
        out[key.strip()] = _scalar(value.strip())
    return out


def _strip_comment(line: str) -> str:
    quote = None
    for i, c in enumerate(line):
        if quote:
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _scalar(v: str):
    if v == "":
        return None
    if v.startswith("[") and v.endswith("]"):
        return [_scalar(x.strip()) for x in v[1:-1].split(",") if x.strip()]
    if v[0] in "'\"" and v[-1] == v[0] and len(v) >= 2:
        return v[1:-1]
    low = v.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    try:
        return ast.literal_eval(v) if v[0] in "+-.0123456789" else v
    except (ValueError, SyntaxError):
        return v


def png_size(png: bytes):
    if not png.startswith(PNG_SIGNATURE) or png[12:16] != b"IHDR":
        raise MapError("the map image is not a PNG")
    return struct.unpack(">II", png[16:24])


def pgm_to_png(data: bytes) -> bytes:
    """PGM(P5 バイナリ・P2 テキスト、8 bit または 16 bit)を 8 bit グレーの PNG にする"""
    magic = data[:2]
    if magic not in (b"P5", b"P2"):
        raise MapError("the map image is not a PGM (P5/P2)")
    # ヘッダ: magic, width, height, maxval(空白と # のコメントで区切る)
    fields, i = [], 2
    while len(fields) < 3:
        while i < len(data) and data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b"#":
            while i < len(data) and data[i:i + 1] not in (b"\n", b"\r"):
                i += 1
            continue
        j = i
        while j < len(data) and not data[j:j + 1].isspace():
            j += 1
        if j == i:
            raise MapError("the PGM header is truncated")
        fields.append(int(data[i:j]))
        i = j
    w, h, maxval = fields
    if not (0 < maxval < 65536):
        raise MapError(f"the PGM maxval {maxval} is not supported")
    if magic == b"P5":
        body = data[i + 1:]
        if maxval < 256:
            px = body[:w * h]
            if len(px) < w * h:
                raise MapError("the PGM image data is truncated")
            if maxval != 255:
                px = bytes(min(255, v * 255 // maxval) for v in px)
        else:
            if len(body) < w * h * 2:
                raise MapError("the PGM image data is truncated")
            px = bytes(v * 255 // maxval for v in struct.unpack(f">{w * h}H", body[:w * h * 2]))
    else:
        values = [int(t) for t in data[i:].split()]
        if len(values) < w * h:
            raise MapError("the PGM image data is truncated")
        px = bytes(min(255, v * 255 // maxval) for v in values[:w * h])
    return encode_gray_png(w, h, px)


def encode_gray_png(w: int, h: int, px: bytes) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + px[y * w:(y + 1) * w] for y in range(h))
    return (PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def to_png(data: bytes, name: str) -> bytes:
    if data.startswith(PNG_SIGNATURE):
        return data
    if data[:2] in (b"P5", b"P2"):
        return pgm_to_png(data)
    raise MapError(f"{name}: the map image must be PNG or PGM")


def build_map(meta: dict, read_image: Callable[[str], bytes], source: str) -> MapData:
    """YAML の中身と、image の名前から中身を返す関数で地図を作る"""
    try:
        image = meta["image"]
        resolution = float(meta["resolution"])
        origin = [float(v) for v in meta["origin"]]
    except KeyError as e:
        raise MapError(f"the map YAML has no {e.args[0]!r}") from e
    except (TypeError, ValueError) as e:
        raise MapError(f"the map YAML has a bad resolution or origin: {e}") from e
    if resolution <= 0:
        raise MapError("resolution must be positive")
    if len(origin) == 2:
        origin.append(0.0)
    if len(origin) != 3:
        raise MapError("origin must be [x, y, yaw]")
    png = to_png(read_image(str(image)), str(image))
    w, h = png_size(png)
    info = {
        "resolution": resolution,
        "origin": origin,
        "negate": bool(int(meta.get("negate", 0) or 0)),
        "occupied_thresh": float(meta.get("occupied_thresh", 0.65)),
        "free_thresh": float(meta.get("free_thresh", 0.196)),
        "width": w,
        "height": h,
    }
    return MapData(png, info, source)


def load_map_file(path) -> MapData:
    """地図の YAML のパスから読む。image は YAML のあるディレクトリから見る"""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise MapError(f"cannot read the map YAML {p}: {e}") from e

    def read_image(name: str) -> bytes:
        ip = Path(name) if Path(name).is_absolute() else p.parent / name
        try:
            return ip.read_bytes()
        except OSError as e:
            raise MapError(f"cannot read the map image {ip}: {e}") from e

    return build_map(parse_yaml(text), read_image, p.name)


def load_map_upload(yaml_text: str, images: Dict[str, bytes], name: Optional[str] = None) -> MapData:
    """画面から送られた YAML と画像(ファイル名 → 中身)から読む。image はファイル名で探す(ディレクトリは見ない)"""
    def read_image(image: str) -> bytes:
        base = image.replace("\\", "/").rsplit("/", 1)[-1]
        if base not in images:
            raise MapError(f"the map YAML refers to {image!r}; select that image together with the YAML")
        return images[base]

    return build_map(parse_yaml(yaml_text), read_image, name or "map.yaml")
