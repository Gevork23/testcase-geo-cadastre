"""Idempotent ESRI World Imagery XYZ downloader for AOI bbox.

Reads aoi.geojson + config.yaml, computes tile range at configured zoom,
downloads each tile to data/cache/esri_tiles/{z}/{x}/{y}.jpg.
Second run makes 0 HTTP requests for cached tiles (idempotent).
ESRI attribution: Esri, Maxar, Earthstar Geographics, and the GIS User Community.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from pathlib import Path

import requests
import yaml

LOG = logging.getLogger("fetch_esri")

ESRI_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
TILE_PX = 256
EARTH_CIRCUM_M = 40075016.68557849
USER_AGENT = "CadastreCheck/1.0 (research; +https://github.com/Gevork23/testcase-geo-cadastre)"


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_bbox(path: str) -> tuple[float, float, float, float]:
    aoi = json.loads(Path(path).read_text(encoding="utf-8"))
    feats = aoi.get("features") or [aoi]
    geom = feats[0]["geometry"]
    if geom["type"] == "Polygon":
        rings = geom["coordinates"]
    elif geom["type"] == "MultiPolygon":
        rings = [ring for poly in geom["coordinates"] for ring in poly]
    else:
        raise SystemExit(f"unsupported geometry: {geom['type']}")
    lons = [c[0] for ring in rings for c in ring]
    lats = [c[1] for ring in rings for c in ring]
    return min(lons), min(lats), max(lons), max(lats)


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def tile_range(
    bbox: tuple[float, float, float, float], z: int, pad: int
) -> tuple[int, int, int, int]:
    min_lon, min_lat, max_lon, max_lat = bbox
    x0, y0 = lonlat_to_tile(min_lon, max_lat, z)  # NW
    x1, y1 = lonlat_to_tile(max_lon, min_lat, z)  # SE
    n = 2 ** z
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(n - 1, x1 + pad)
    y1 = min(n - 1, y1 + pad)
    return x0, y0, x1, y1


def download_tile(
    session: requests.Session, z: int, x: int, y: int, dst: Path, retries: int
) -> str:
    if dst.exists() and dst.stat().st_size > 1024:
        return "cached"
    dst.parent.mkdir(parents=True, exist_ok=True)
    url = ESRI_URL.format(z=z, x=x, y=y)
    last_err: str | None = None
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200 and len(r.content) > 1024:
                tmp = dst.with_suffix(".tmp")
                tmp.write_bytes(r.content)
                tmp.replace(dst)
                return "downloaded"
            if r.status_code == 404:
                LOG.warning("404 z=%d x=%d y=%d", z, x, y)
                return "missing"
            last_err = f"HTTP {r.status_code}"
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < retries:
            time.sleep(2 ** (attempt - 1))
    LOG.error("FAIL z=%d x=%d y=%d after %d attempts: %s", z, x, y, retries, last_err)
    return "failed"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--aoi", default="aoi.geojson")
    p.add_argument("--padding", type=int, default=None, help="override imagery.padding")
    p.add_argument("--cache-dir", default="data/cache/esri_tiles")
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--refresh", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    cfg = load_config(args.config)
    z = int(cfg["imagery"]["zoom_level"])
    bbox = load_bbox(args.aoi)
    padding = args.padding if args.padding is not None else int(cfg["imagery"].get("padding", 1))
    x0, y0, x1, y1 = tile_range(bbox, z, padding)
    nx, ny = x1 - x0 + 1, y1 - y0 + 1
    total = nx * ny
    LOG.info("fetch_start z=%d grid=%dx%d tiles=%d padding=%d cache=%s",
             z, nx, ny, total, padding, args.cache_dir)

    cache_root = Path(args.cache_dir) / str(z)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    stats = {"cached": 0, "downloaded": 0, "missing": 0, "failed": 0}
    t0 = time.time()
    for i, (tx, ty) in enumerate(
        ((x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)), 1
    ):
        dst = cache_root / str(tx) / f"{ty}.jpg"
        if args.refresh and dst.exists():
            dst.unlink()
        status = download_tile(session, z, tx, ty, dst, args.retries)
        stats[status] += 1
        if i % 50 == 0 or i == total:
            LOG.info("progress %d/%d cached=%d downloaded=%d missing=%d failed=%d elapsed=%.1fs",
                     i, total, stats["cached"], stats["downloaded"],
                     stats["missing"], stats["failed"], time.time() - t0)

    LOG.info("fetch_done z=%d tiles=%d cached=%d downloaded=%d missing=%d failed=%d",
             z, total, stats["cached"], stats["downloaded"],
             stats["missing"], stats["failed"])
    if stats["failed"] > 0:
        LOG.error("non-zero failed count; rerun (idempotent)")
        sys.exit(1)


if __name__ == "__main__":
    main()