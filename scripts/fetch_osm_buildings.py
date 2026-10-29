"""T13: fetch OSM buildings for AOI via Overpass and write GeoPackage."""
from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
AOI_BBOX = (47.121225, 39.747125, 47.138775, 39.772875)  # S, W, N, E
RAW = Path("data/osm/buildings_aoi_raw.json")
OUT = Path("data/osm/buildings_aoi.gpkg")

HEADERS = {
    "User-Agent": "CadastreCheck-T13/1.0 (research; OSM weak label)",
}


def _polygon_from_coords(coords: list[tuple[float, float]]):
    if len(coords) < 4:
        return None
    if coords[0] != coords[-1]:
        coords = coords + [coords[0]]
    try:
        poly = Polygon(coords)
        if not poly.is_valid:
            poly = poly.buffer(0)
    except Exception:
        return None
    if poly.is_empty or poly.geom_type not in ("Polygon", "MultiPolygon"):
        return None
    return poly


def _geometry_from_relation(el: dict):
    outers = []
    for member in el.get("members", []):
        if member.get("role") not in ("outer", "", None):
            continue
        geom = member.get("geometry")
        if not geom:
            continue
        coords = [(p["lon"], p["lat"]) for p in geom]
        poly = _polygon_from_coords(coords)
        if poly is not None:
            outers.append(poly)
    if not outers:
        return None
    geom = unary_union(outers)
    if geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
        return None
    return geom


def fetch_overpass() -> dict:
    # Escape hatch: если data/osm/buildings_aoi_raw.json уже лежит (скачан вручную),
    # используем его, сеть не трогаем.
    if RAW.exists() and RAW.stat().st_size > 100:
        print(f"Using cached raw JSON: {RAW}", flush=True)
        return json.loads(RAW.read_text("utf-8"))

    s, w, n, e = AOI_BBOX
    query = f"""
[out:json][timeout:90];
(
  way["building"]({s},{w},{n},{e});
  relation["building"]({s},{w},{n},{e});
);
out geom;
"""
    last_err: Exception | None = None
    for url in OVERPASS_URLS:
        print(f"POST {url}", flush=True)
        try:
            r = requests.post(
                url,
                data={"data": query},
                headers=HEADERS,
                timeout=150,
            )
            r.raise_for_status()
            data = r.json()
            n_el = len(data.get("elements", []))
            if data.get("remark"):
                print(f"remark: {data['remark']}", flush=True)
            if n_el == 0 and data.get("remark"):
                print(f"Empty + remark from {url}, next mirror", flush=True)
                continue
            RAW.parent.mkdir(parents=True, exist_ok=True)
            RAW.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            print(f"OK {url}: elements={n_el}", flush=True)
            return data
        except Exception as exc:
            print(f"FAIL {url}: {type(exc).__name__}: {exc}", flush=True)
            last_err = exc
    raise SystemExit(
        f"All Overpass mirrors failed. Last error: {last_err}. "
        "Проверь сеть / VPN; или сохрани JSON вручную в data/osm/buildings_aoi_raw.json"
    )


def main() -> None:
    data = fetch_overpass()
    rows = []

    for el in data.get("elements", []):
        geom = None
        if el.get("type") == "way" and el.get("geometry"):
            coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
            geom = _polygon_from_coords(coords)
        elif el.get("type") == "relation":
            geom = _geometry_from_relation(el)

        if geom is None:
            continue

        tags = el.get("tags", {})
        rows.append(
            {
                "osm_type": el.get("type"),
                "osm_id": el.get("id"),
                "building": tags.get("building") or "yes",
                "name": tags.get("name"),
                "geometry": geom,
            }
        )

    if not rows:
        raise SystemExit("OSM returned 0 valid buildings; check bbox/Overpass")

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    gdf.to_file(OUT, driver="GPKG", layer="buildings")

    check = gpd.read_file(OUT)
    metric = check.to_crs(32637)
    print(f"Wrote {OUT}: {len(check)} buildings, crs={check.crs}", flush=True)
    print(f"bounds: {check.total_bounds}", flush=True)
    print("areas m2:", flush=True)
    print(metric.area.describe().to_string(), flush=True)

    stats_path = Path("data/cache/nspd/oks_contour_stats.json")
    if stats_path.exists():
        stats = json.loads(stats_path.read_text("utf-8"))
        total = int(stats.get("oks_total") or stats.get("total") or 0)
        if total:
            ratio = len(check) / total * 100
            print(f"NSPD OKS: {total}; OSM/NSPD: {ratio:.1f}%", flush=True)


if __name__ == "__main__":
    main()