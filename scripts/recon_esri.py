# scripts/recon_esri.py
"""Recon: AOI bbox -> ESRI tile grid -> zoom selection. No network."""
from __future__ import annotations
import json, math, sys
from pathlib import Path

TILE_PX = 256
AVG_TILE_BYTES = 50 * 1024  # rough guess for ESRI World Imagery JPG
EARTH_CIRCUM_M = 40075016.68557849
ZOOMS = (16, 17, 18, 19)


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


def main(aoi_path: str) -> None:
    min_lon, min_lat, max_lon, max_lat = load_bbox(aoi_path)
    lat_mid = (min_lat + max_lat) / 2
    print(f"AOI bbox : lon [{min_lon:.6f}, {max_lon:.6f}]  lat [{min_lat:.6f}, {max_lat:.6f}]")
    print(f"span     : {(max_lon-min_lon):.6f} lon  {(max_lat-min_lat):.6f} lat  (mid-lat {lat_mid:.4f})")
    print()
    hdr = f"{'z':>2} | {'grid':>9} | {'tiles':>6} | {'+1 pad':>11} | {'tiles':>6} | {'res m/px':>9} | {'~MB @50KB':>10}"
    print(hdr)
    print("-" * len(hdr))
    for z in ZOOMS:
        x0, y0 = lonlat_to_tile(min_lon, max_lat, z)  # NW
        x1, y1 = lonlat_to_tile(max_lon, min_lat, z)  # SE
        nx, ny = x1 - x0 + 1, y1 - y0 + 1
        nxp, nyp = nx + 2, ny + 2
        res = EARTH_CIRCUM_M / (TILE_PX * 2 ** z) * math.cos(math.radians(lat_mid))
        mb = nxp * nyp * AVG_TILE_BYTES / (1024 * 1024)
        print(f"{z:>2} | {f'{nx}x{ny}':>9} | {nx*ny:>6} | {f'{nxp}x{nyp}':>11} | {nxp*nyp:>6} | {res:>9.3f} | {mb:>10.1f}")
    print()
    print("Ориентир: z18 ≈ 0.4 м/px, z19 ≈ 0.2 м/px. Требование 0.3–0.6 м/px -> z18.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "aoi.geojson")