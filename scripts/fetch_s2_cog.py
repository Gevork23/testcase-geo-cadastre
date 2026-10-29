"""Load best Sentinel-2 L2A scene -> 4 bands (B02/B03/B04/B08) -> EPSG:32637 GeoTIFF.

Anonymous AWS Open Data access — no CDSE registration needed.
Uses rasterio + WarpedVRT directly (bypasses stackstac dtype/fill_value quirks).
"""
import os
from pathlib import Path

# GDAL/AWS env for anonymous access to public COGs on S3
os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("AWS_REGION", "us-west-2")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

import urllib.request

import numpy as np
import pystac_client
import rasterio
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT

STAC_URL = "https://earth-search.aws.element84.com/v1"
AOI_BBOX = (39.747125, 47.121225, 39.772875, 47.138775)  # lon/lat
DATETIME = "2025-06-01/2025-09-30"
MAX_CLOUD = 10
BANDS = ["blue", "green", "red", "nir"]  # Earth Search keys = B02, B03, B04, B08
RES = 10  # metres
OUT_PATH = Path("data/sentinel2/aoi_s2_l2a_2025_summer.tif")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    # 1) Search and pick least cloudy scene
    catalog = pystac_client.Client.open(STAC_URL)
    items = list(
        catalog.search(
            collections=["sentinel-2-l2a"],
            bbox=list(AOI_BBOX),
            datetime=DATETIME,
            query={"eo:cloud_cover": {"lt": MAX_CLOUD}},
        ).items()
    )
    if not items:
        raise SystemExit("No suitable scenes. Widen date/cloud thresholds.")
    best = min(items, key=lambda it: it.properties["eo:cloud_cover"])
    print(f"Selected: {best.id} | {best.datetime} | cloud {best.properties['eo:cloud_cover']}%")

    # 2) Compute target grid (EPSG:32637, 10 m, AOI bbox)
    t = Transformer.from_crs("EPSG:4326", "EPSG:32637", always_xy=True)
    corners = [
        (AOI_BBOX[0], AOI_BBOX[1]), (AOI_BBOX[2], AOI_BBOX[1]),
        (AOI_BBOX[0], AOI_BBOX[3]), (AOI_BBOX[2], AOI_BBOX[3]),
    ]
    xs, ys = zip(*[t.transform(lon, lat) for lon, lat in corners])
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    width = int(np.ceil((x_max - x_min) / RES))
    height = int(np.ceil((y_max - y_min) / RES))
    transform = from_bounds(x_min, y_min, x_max, y_max, width, height)
    print(f"Target grid: {width} x {height} px @ {RES} m, EPSG:32637")

    # 3) Quick reachability check on one COG
    try:
        req = urllib.request.Request(best.assets["blue"].href, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"COG HEAD ok: HTTP {r.status}, size={r.headers.get('Content-Length')} bytes")
    except Exception as e:
        print(f"WARNING: COG HEAD failed: {e}")

    # 4) Read + warp each band into the target grid
    stacked = np.zeros((len(BANDS), height, width), dtype=np.uint16)
    for i, band in enumerate(BANDS):
        href = best.assets[band].href
        print(f"  Reading {band.upper()} ({i + 1}/{len(BANDS)}) ...", flush=True)
        with rasterio.open(href) as src:
            with WarpedVRT(
                src,
                crs="EPSG:32637",
                transform=transform,
                width=width,
                height=height,
                resampling=Resampling.nearest,
            ) as vrt:
                stacked[i] = vrt.read(1)
        print(f"    OK: min={stacked[i].min()} max={stacked[i].max()}")

    # 5) Write GeoTIFF
    with rasterio.open(
        OUT_PATH,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=len(BANDS),
        dtype="uint16",
        crs="EPSG:32637",
        transform=transform,
        compress="deflate",
    ) as dst:
        for i, band in enumerate(BANDS):
            dst.write(stacked[i], i + 1)
            dst.set_band_description(i + 1, band)

    print(f"Wrote {OUT_PATH}")
    print(f"Shape: {stacked.shape}, dtype: {stacked.dtype}")


if __name__ == "__main__":
    main()