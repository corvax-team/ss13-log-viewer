import hashlib
import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path

from PIL import Image

log = logging.getLogger("viewer.maps")

TILE = 32
SCALE = 0.5
RENDER_VERSION = "2"
PAD_TILES = 3
CENTCOM = "_maps/map_files/generic/CentCom.dmm"
LAVALAND = "_maps/map_files/Mining/Lavaland.dmm"
LEVEL_RE = re.compile(r"-(\d+)\.png$")

Image.MAX_IMAGE_PIXELS = None


class MapStore:
    def __init__(self, game_dir, out_dir, dmm_tools):
        self.game = Path(game_dir)
        self.out = Path(out_dir)
        self.dmm_tools = dmm_tools
        self.lock = threading.Lock()

    def available(self):
        return self.game.exists() and os.access(self.dmm_tools, os.X_OK)

    def meta(self, map_name):
        folder = self._folder(map_name)
        if not folder:
            return None
        meta_file = folder / "meta.json"
        if not meta_file.exists():
            return None
        return json.loads(meta_file.read_text(encoding="utf-8"))

    def image(self, map_name, z):
        folder = self._folder(map_name)
        if not folder:
            return None
        path = folder / f"z{z}.webp"
        return path if path.exists() else None

    def rendering(self):
        return self.lock.locked()

    def render_missing(self):
        if not self.lock.acquire(blocking=False):
            return
        try:
            self._render_all()
        finally:
            self.lock.release()

    def _render_all(self):
        for config in sorted(self.game.glob("_maps/*.json")):
            try:
                self._render_map(config)
            except Exception as error:
                log.warning("render of %s failed: %s", config.name, error)

    def _folder(self, map_name):
        config = self._config_for(map_name)
        if not config:
            return None
        return self.out / _slug(map_name) / self._version(config)

    def _config_for(self, map_name):
        for config in self.game.glob("_maps/*.json"):
            data = json.loads(config.read_text(encoding="utf-8"))
            if data.get("map_name") == map_name:
                return data
        return None

    def _version(self, config):
        station = self.game / "_maps" / config["map_path"] / config["map_file"]
        digest = hashlib.md5(RENDER_VERSION.encode())
        for file in (station, self.game / CENTCOM, self.game / LAVALAND):
            if file.exists():
                digest.update(file.read_bytes())
        return digest.hexdigest()[:12]

    def _render_map(self, config_file):
        config = json.loads(config_file.read_text(encoding="utf-8"))
        name = config.get("map_name")
        station = self.game / "_maps" / config["map_path"] / config["map_file"]
        if not name or not station.exists() or config_file.name.startswith(("runtimestation", "multiz", "gateway")):
            return
        folder = self.out / _slug(name) / self._version(config)
        if (folder / "meta.json").exists():
            return
        log.info("rendering %s", name)
        work = folder / "render"
        work.mkdir(parents=True, exist_ok=True)
        levels = {}
        z = 1
        for dmm, label in ((self.game / CENTCOM, "ЦК"), (station, name), (self.game / LAVALAND, "Лаваленд")):
            if dmm == self.game / LAVALAND and config.get("minetype") not in (None, "lavaland"):
                continue
            for png in self._minimap(dmm, work):
                target = folder / f"z{z}.webp"
                crop = _shrink(png, target)
                png.unlink()
                width, height = _size(target)
                levels[str(z)] = {
                    "name": label if dmm != station else f"{name}, уровень {LEVEL_RE.search(png.name).group(1)}",
                    "width": width, "height": height,
                    "left": crop[0], "top": crop[1], "full_height": crop[2],
                }
                z += 1
        work.rmdir()
        (folder / "meta.json").write_text(json.dumps({"map": name, "scale": SCALE, "tile": TILE, "levels": levels}, ensure_ascii=False), encoding="utf-8")

    def _minimap(self, dmm, work):
        subprocess.run(
            [self.dmm_tools, "-e", "tgstation.dme", "minimap", "-o", str(work), str(dmm.relative_to(self.game))],
            cwd=self.game, check=True, capture_output=True,
        )
        return sorted(work.glob(f"{dmm.stem}-*.png"), key=lambda p: int(LEVEL_RE.search(p.name).group(1)))


def _shrink(png, target):
    with Image.open(png) as image:
        rgb = image.convert("RGB")
        box = rgb.getbbox() or (0, 0, rgb.width, rgb.height)
        pad = PAD_TILES * TILE
        box = (max(0, box[0] - pad), max(0, box[1] - pad), min(rgb.width, box[2] + pad), min(rgb.height, box[3] + pad))
        cropped = rgb.crop(box)
        size = (int(cropped.width * SCALE), int(cropped.height * SCALE))
        cropped.resize(size, Image.LANCZOS).save(target, "WEBP", quality=82, method=4)
        return box[0], box[1], rgb.height


def _size(path):
    with Image.open(path) as image:
        return image.size


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
