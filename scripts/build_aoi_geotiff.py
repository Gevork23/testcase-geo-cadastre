"""Stitch cached ESRI tiles -> GeoTIFF in EPSG:32637 (UTM 37N).

Writes data/imagery/aoi_esri_utm37n.tif (chunked rasterio, no MemoryError).
Embeds ESRI attribution as GDAL metadata + .attribution.txt sidecar (T11).
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path

import numpy as np
import rasterio
import yaml
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

LOG = logging.getLogger("build_geotiff")

TILE_PX = 256
EARTH_CIRCUM_M = 40075016.68557849
WEBMERC_HALF = EARTH_CIRCUM_M / 2.0
ATTRIBUTION = (
    "Imagery source: Esri World Imagery. "
    "Attribution: Esri, Maxar, Earthstar Geographics, and the GIS User Community. "
    "Access: XYZ tiles from server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer. "
    "Usage: research/educational, no redistribution. "
    "Imagery date: see DECISIONS.md T11 (checked via World Imagery Wayback). "
    "Processed by CadastreCheck (T10)."
)


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


def tile_range(bbox, z, pad):
    min_lon, min_lat, max_lon, max_lat = bbox
    x0, y0 = lonlat_to_tile(min_lon, max_lat, z)
    x1, y1 = lonlat_to_tile(max_lon, min_lat, z)
    n = 2 ** z
    return max(0, x0 - pad), max(0, y0 - pad), min(n - 1, x1 + pad), min(n - 1, y1 + pad)


def mosaic_profile_3857(x0, y0, x1, y1, z):
    nx, ny = x1 - x0 + 1, y1 - y0 + 1
    width, height = nx * TILE_PX, ny * TILE_PX
    tile_span = EARTH_CIRCUM_M / (2 ** z)   # meters per tile at zoom z (EPSG:3857)
    pixel = tile_span / TILE_PX             # meters per pixel
    west = x0 * tile_span - WEBMERC_HALF
    north = WEBMERC_HALF - y0 * tile_span
    if abs(west) > WEBMERC_HALF or abs(north) > WEBMERC_HALF:
        raise ValueError(
            f"mosaic origin outside web mercator: west={west:.1f} north={north:.1f} "
            f"(x0={x0}, y0={y0}, z={z})"
        )
    return {
        "driver": "GTiff", "width": width, "height": height, "count": 3,
        "dtype": "uint8", "crs": "EPSG:3857", "nodata": 0,
        "transform": from_origin(west, north, pixel, pixel),
        "compress": "deflate", "tiled": True,
        "blockxsize": TILE_PX, "blockysize": TILE_PX, "BIGTIFF": "IF_SAFER",
    }


def build_mosaic_3857(cache_root, x0, y0, x1, y1, z, dst_path):
    profile = mosaic_profile_3857(x0, y0, x1, y1, z)
    LOG.info("mosaic_start size=%dx%d dst=%s", profile["width"], profile["height"], dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    filled, missing = 0, 0
    with rasterio.open(dst_path, "w", **profile) as dst:
        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                tile_path = cache_root / str(tx) / f"{ty}.jpg"
                # rasterio-stubs declares Window.__init__ with no args (broken stubs);
                # runtime signature is Window(col_off, row_off, width, height).
                win = Window((tx - x0) * TILE_PX, (ty - y0) * TILE_PX, TILE_PX, TILE_PX)  # type: ignore[call-arg]
                blank = np.zeros((3, TILE_PX, TILE_PX), dtype=np.uint8)
                if not tile_path.exists():
                    LOG.warning("missing tile z=%d x=%d y=%d; filling 0", z, tx, ty)
                    dst.write(blank, window=win); missing += 1; continue
                try:
                    with rasterio.open(tile_path) as src:
                        data = src.read()
                        if data.shape[0] == 1:
                            data = np.repeat(data, 3, axis=0)
                        elif data.shape[0] > 3:
                            data = data[:3]
                        if data.shape[0] < 3:
                            pad = np.zeros((3 - data.shape[0], TILE_PX, TILE_PX), dtype=np.uint8)
                            data = np.concatenate([data, pad], axis=0)
                        if data.shape[1:] != (TILE_PX, TILE_PX):
                            LOG.warning("tile size mismatch %s: %s", tile_path, data.shape)
                            continue
                        dst.write(data, window=win); filled += 1
                except Exception as e:
                    LOG.warning("tile read failed %s: %s", tile_path, e)
                    dst.write(blank, window=win); missing += 1
    LOG.info("mosaic_done filled=%d missing=%d", filled, missing)


def reproject_to_utm37n(src_path, dst_path, target_res_m=None, target_crs="EPSG:32637"):
    with rasterio.open(src_path) as src:
        if target_res_m is None:
            cy = (src.bounds.top + src.bounds.bottom) / 2
            lat = math.degrees(
                2 * math.atan(math.exp(cy / WEBMERC_HALF * math.pi)) - math.pi / 2
            )
            target_res_m = abs(src.transform.a) * math.cos(math.radians(lat))
        if not (0.2 <= target_res_m <= 1.0):
            raise ValueError(
                f"target_res_m={target_res_m:.4f} outside 0.2-1.0; "
                f"likely bad source bounds top={src.bounds.top:.1f} bottom={src.bounds.bottom:.1f}"
            )
        LOG.info("reproject_start src=%s target_res_m=%.4f", src_path, target_res_m)
        with WarpedVRT(
            src, crs=target_crs, resampling=Resampling.bilinear,
            resolution=(target_res_m, target_res_m),
            src_nodata=0, nodata=0,
        ) as vrt:
            profile = vrt.profile.copy()
            profile.update(driver="GTiff", compress="deflate", tiled=True,
                           blockxsize=512, blockysize=512, BIGTIFF="IF_SAFER",
                           nodata=0)
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(dst_path, "w", **profile) as dst:
                for _, window in dst.block_windows(1):
                    dst.write(vrt.read(window=window), window=window)
                dst.update_tags(
                    TIFFTAG_IMAGEDESCRIPTION=ATTRIBUTION,
                    ATTRIBUTION=ATTRIBUTION,
                    SOURCE="ESRI World Imagery XYZ tiles",
                    CRS_ORIGINAL="EPSG:3857",
                    T10_NOTE="Reprojected to EPSG:32637 (UTM 37N) for metric ops.",
                )
    LOG.info("reproject_done dst=%s", dst_path)


def write_attribution_file(dst_path):
    txt = dst_path.with_suffix(".attribution.txt")
    txt.write_text(ATTRIBUTION + "\n", encoding="utf-8")
    LOG.info("attribution_written path=%s", txt)


def verify_output(out_path, expected_crs="EPSG:32637"):
    """Sanity-check the final GeoTIFF. Raises on bad CRS/res/bounds."""
    with rasterio.open(out_path) as src:
        LOG.info("verify_done crs=%s bounds=%s shape=%s res=%.4f count=%d nodata=%s",
                 src.crs, src.bounds, src.shape, src.res[0], src.count, src.nodata)
        if src.crs is None or src.crs.to_string() != expected_crs:
            raise ValueError(f"unexpected CRS: {src.crs}, expected {expected_crs}")
        if not (0.2 <= src.res[0] <= 1.0):
            raise ValueError(f"output res {src.res[0]:.4f} m/px out of 0.2-1.0")
        # UTM 37N near Rostov: easting ~557k, northing ~5.22M
        if not (300_000 < src.bounds.left < 800_000):
            raise ValueError(f"easting out of range: {src.bounds.left}")
        if not (4_500_000 < src.bounds.bottom < 6_000_000):
            raise ValueError(f"northing out of range: {src.bounds.bottom}")
        tags = src.tags()
        if "ATTRIBUTION" not in tags:
            LOG.warning("ATTRIBUTION tag missing from GeoTIFF; sidecar .attribution.txt still written")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--aoi", default="aoi.geojson")
    p.add_argument("--padding", type=int, default=None, help="override imagery.padding")
    p.add_argument("--cache-dir", default="data/cache/esri_tiles")
    p.add_argument("--mosaic-out", default="data/imagery/aoi_esri_3857.tif")
    p.add_argument("--out", default="data/imagery/aoi_esri_utm37n.tif")
    p.add_argument("--target-res-m", type=float, default=None)
    p.add_argument("--keep-mosaic", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = load_config(args.config)
    z = int(cfg["imagery"]["zoom_level"])
    target_crs = str(cfg["imagery"]["target_crs"])
    bbox = load_bbox(args.aoi)
    padding = args.padding if args.padding is not None else int(cfg["imagery"].get("padding", 1))
    x0, y0, x1, y1 = tile_range(bbox, z, padding)
    cache_root = Path(args.cache_dir) / str(z)
    if not cache_root.exists():
        LOG.error("cache not found: %s", cache_root); sys.exit(1)

    mosaic_path = Path(args.mosaic_out)
    out_path = Path(args.out)

    build_mosaic_3857(cache_root, x0, y0, x1, y1, z, mosaic_path)
    reproject_to_utm37n(mosaic_path, out_path, args.target_res_m, target_crs)
    write_attribution_file(out_path)
    verify_output(out_path, target_crs)

    if not args.keep_mosaic:
        mosaic_path.unlink(missing_ok=True)
        LOG.info("mosaic_removed path=%s", mosaic_path)


if __name__ == "__main__":
    main()