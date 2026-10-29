"""Audit T1-T13 against docx requirements. Read-only, no mutations."""
from __future__ import annotations

import re
from pathlib import Path
from typing import cast

import geopandas as gpd
import rasterio
import yaml
from shapely.geometry.base import BaseGeometry

ROOT = Path(__file__).resolve().parents[1]
ok = []
fail = []
warn = []


def check(name: str, cond: bool, detail: str = "", severity: str = "fail") -> None:
    if cond:
        ok.append(f"OK   {name}: {detail}" if detail else f"OK   {name}")
    else:
        line = f"{severity.upper():4s} {name}: {detail}"
        (fail if severity == "fail" else warn).append(line)


# ── T1: aoi.geojson ─────────────────────────────────────────
aoi_path = ROOT / "aoi.geojson"
check("T1 aoi.geojson exists", aoi_path.exists())
if aoi_path.exists():
    try:
        gdf = gpd.read_file(aoi_path)
        check("T1 aoi CRS EPSG:4326", gdf.crs is not None and gdf.crs.to_epsg() == 4326, str(gdf.crs))
        check("T1 aoi single polygon", len(gdf) == 1, f"{len(gdf)} features")
        area_km2 = gdf.to_crs(32637).area.sum() / 1e6
        check("T1 aoi area 2-5 km²", 2.0 <= area_km2 <= 5.0, f"{area_km2:.3f} km²")
        geom = cast(BaseGeometry, gdf.geometry.iloc[0])
        check("T1 aoi valid geometry", geom.is_valid and not geom.is_empty, geom.geom_type)
    except Exception as e:
        check("T1 aoi readable", False, str(e))

# ── T3: .gitignore ──────────────────────────────────────────
gi = (ROOT / ".gitignore").read_text(encoding="utf-8") if (ROOT / ".gitignore").exists() else ""
for pat in ("data/", "*.tif", "*.gpkg", "*.pth", ".env"):
    check(f"T3 .gitignore has {pat}", pat in gi)

# ── T4: env, lock, pipeline, README ─────────────────────────
check("T4 uv.lock exists", (ROOT / "uv.lock").exists())
check("T4 .env.example exists", (ROOT / ".env.example").exists())
readme = (ROOT / "README.MD").read_text(encoding="utf-8") if (ROOT / "README.MD").exists() else ""
check("T4 README one-command run", "python -m src.pipeline --config" in readme)
check("T4 pipeline module exists", (ROOT / "src/pipeline.py").exists())
check("T4 ORIGINAL_TZ backup", (ROOT / "docs/ORIGINAL_TZ.md").exists())

# ── T5: config.yaml ─────────────────────────────────────────
cfg_path = ROOT / "config.yaml"
check("T5 config.yaml exists", cfg_path.exists())
if cfg_path.exists():
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    paths = cfg.get("paths") or {}
    for k in ("aoi_geojson", "cache_dir", "imagery_dir", "osm_gpkg"):
        check(f"T5 paths.{k}", k in paths, str(paths.get(k)))
    det = cfg.get("detection") or {}
    check("T5 detection.min_area_m2 = 10", det.get("min_area_m2") == 10.0, str(det.get("min_area_m2")))
    mt = cfg.get("matching") or {}
    for k, v in (
        ("iou_threshold_match", 0.5),
        ("iou_threshold_missing", 0.1),
        ("out_of_parcel_ratio", 0.2),
        ("area_mismatch_ratio", 0.1),
    ):
        check(f"T5 matching.{k} = {v}", mt.get(k) == v, str(mt.get(k)))
    bm = mt.get("buffer_m", 0)
    check("T5 matching.buffer_m in [1,3]", 1.0 <= float(bm) <= 3.0, str(bm))
    nspd = cfg.get("nspd") or {}
    for k in ("timeout_s", "max_retries", "retry_backoff_min_sec",
              "retry_backoff_max_sec", "rate_limit_rps", "cache_ttl_days"):
        check(f"T5 nspd.{k}", k in nspd, str(nspd.get(k)))
    mdl = cfg.get("model") or {}
    for k in ("name", "weights_path", "tile_size", "overlap", "batch_size"):
        check(f"T5 model.{k}", k in mdl, str(mdl.get(k)))

# ── T6: probe_nspd ─────────────────────────────────────────
check("T6 scripts/probe_nspd.py", (ROOT / "scripts/probe_nspd.py").exists())

# ── T7: nspd_client ────────────────────────────────────────
nc = (ROOT / "src/nspd_client.py")
check("T7 nspd_client.py", nc.exists())
if nc.exists():
    text = nc.read_text(encoding="utf-8")
    check("T7 has retry decorator", "@retry" in text or "retry(" in text)
    check("T7 has rate limit", "Semaphore" in text or "RateLimit" in text or "aiolimiter" in text or "rate_limit" in text)
    check("T7 has soft parse .get", ".get(" in text)
    check("T7 has timeout param", "timeout" in text)
    check("T7 has backoff", "backoff" in text.lower() or "wait_exponential" in text)

# ── T8: cache ──────────────────────────────────────────────
check("T8 nspd_cache.py", (ROOT / "src/nspd_cache.py").exists())
check("T8 data/cache/nspd exists", (ROOT / "data/cache/nspd").exists())
check("T8 README mentions cache reset", "кэш" in readme.lower() or "cache" in readme.lower())

# ── T9: OKS without contour ────────────────────────────────
m = ROOT / "src/matching.py"
check("T9 matching.py", m.exists())
if m.exists():
    text = m.read_text(encoding="utf-8")
    check("T9 oks_effective_geometry", "oks_effective_geometry" in text)
    check("T9 EffectiveGeometry", "EffectiveGeometry" in text or "inherited_from_parcel" in text)

# ── T10: imagery ───────────────────────────────────────────
tif_path = ROOT / "data/imagery/aoi_esri_utm37n.tif"
check("T10 imagery GeoTIFF", tif_path.exists())
if tif_path.exists():
    with rasterio.open(tif_path) as src:
        check("T10 CRS EPSG:32637", src.crs.to_epsg() == 32637, str(src.crs))
        check("T10 resolution ~0.3-0.6m", 0.2 <= src.res[0] <= 0.7, f"{src.res[0]:.3f}")
        desc = src.tags().get("TIFFTAG_IMAGEDESCRIPTION") or src.tags().get("IMAGEDESCRIPTION") or ""
        has_attr = "Esri" in desc or "Earthstar" in desc or "Maxar" in desc
        check("T11 TIFFTAG_IMAGEDESCRIPTION has attribution", has_attr, desc[:80] or "(empty)", severity="warn")

# ── T11: attribution ───────────────────────────────────────
check("T11 ATTRIBUTION.md", (ROOT / "ATTRIBUTION.md").exists())
check("T11 README has Esri attribution", "Esri" in readme or "Earthstar" in readme)

# ── T12: Sentinel-2 ────────────────────────────────────────
s2 = ROOT / "data/sentinel2/aoi_s2_l2a_2025_summer.tif"
check("T12 Sentinel-2 GeoTIFF", s2.exists())
if s2.exists():
    with rasterio.open(s2) as src:
        check("T12 S2 CRS EPSG:32637", src.crs.to_epsg() == 32637, str(src.crs))
        check("T12 S2 4 bands", src.count == 4, str(src.count))

# ── T13: OSM ───────────────────────────────────────────────
osm = ROOT / "data/osm/buildings_aoi.gpkg"
check("T13 buildings_aoi.gpkg", osm.exists())
if osm.exists():
    g = gpd.read_file(osm)
    check("T13 osm CRS EPSG:4326", g.crs is not None and g.crs.to_epsg() == 4326, str(g.crs))
    check("T13 osm count >0", len(g) > 0, f"{len(g)} buildings")

# ── DECISIONS sections ─────────────────────────────────────
dec_path = ROOT / "DECISIONS.md"
check("DECISIONS.md exists", dec_path.exists())
if dec_path.exists():
    dec = dec_path.read_text(encoding="utf-8")
    for t in range(1, 14):
        has = bool(re.search(rf"§T{t}\b", dec)) or bool(re.search(rf"\bT{t}\b", dec))
        check(f"DECISIONS has §T{t}", has, severity="fail" if t <= 13 else "warn")
    # markdown fences balanced
    fences = dec.count("```")
    check("DECISIONS.md fences balanced", fences % 2 == 0, f"{fences} fences")

# ── T11-fix: README rewritten ──────────────────────────────
check("README.MD is project doc", "Быстрый старт" in readme or "Quick start" in readme or "Установка" in readme)

# ── Report ─────────────────────────────────────────────────
print("\n".join(ok))
print()
if warn:
    print("WARNINGS:")
    print("\n".join(warn))
    print()
if fail:
    print("FAILURES:")
    print("\n".join(fail))
    print()

print(f"\nSUMMARY: {len(ok)} ok, {len(warn)} warn, {len(fail)} fail")
if fail:
    raise SystemExit(1)