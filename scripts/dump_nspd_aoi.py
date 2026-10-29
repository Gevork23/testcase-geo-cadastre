#!/usr/bin/env python3
"""T8.2: выгрузка всех слоёв НСПД по AOI в один GeoJSON.

Демо-пайплайн T8: читает config.yaml + aoi.geojson, проходит по слоям
nspd.layers через RobustCadastreClient.get_features, складывает результат
в data/cache/nspd_dump.geojson.

Второй прогон с default --use-cache → 0 HTTP, 4 hits (см. DECISIONS.md §T8).
Флаги --no-cache / --refresh-cache — та же семантика, что в
src/nspd_client._main_test.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

# Запуск как `python scripts/dump_nspd_aoi.py`: sys.path[0] = scripts/,
# корень репозитория туда не попадает — добавляем вручную, чтобы `import src` работал.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import asyncio
import json
import logging
from typing import Any

import shapely
import yaml
from pynspd import AsyncNspd
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.wkt import loads as wkt_loads

from src.nspd_cache import FileCache
from src.nspd_client import (
    AOI,
    CachePolicy,
    PynspdAdapter,
    RobustCadastreClient,
)
from src.nspd_models import CadastralObject

log = logging.getLogger("nspd.dump")

DEFAULT_OUTPUT = Path("data/cache/nspd_dump.geojson")


def _load_aoi(path: Path) -> BaseGeometry:
    """Feature / FeatureCollection / голая геометрия → единая BaseGeometry."""
    data: dict[str, Any] = json.loads(path.read_text("utf-8"))
    t = data.get("type")
    if t == "FeatureCollection":
        geoms = [shape(f["geometry"]) for f in data["features"]]
        if len(geoms) == 1:
            return geoms[0]
        return shapely.union_all(geoms)
    if t == "Feature":
        return shape(data["geometry"])
    return shape(data)

def _area_km2(aoi: BaseGeometry) -> float:
    """Геодезическая площадь AOI в км² (эллипсоид WGS84)."""
    from pyproj import Geod

    geod = Geod(ellps="WGS84")
    area_m2, _ = geod.geometry_area_perimeter(aoi)
    return abs(area_m2) / 1_000_000.0

def _obj_to_feature(obj: CadastralObject) -> dict[str, Any] | None:
    """CadastralObject → GeoJSON Feature.

    has_contour=True → полигон из obj.geometry;
    has_contour=False + attachment_point_wkt → Point из WKT (ОКС без контура);
    иначе — None (рисовать нечего).
    """
    props: dict[str, Any] = {
        "cad_num": obj.cad_num,
        "object_type": obj.object_type,
        "area_cad_m2": obj.area_cad_m2,
        "has_contour": obj.has_contour,
        "source": obj.source,
    }
    if obj.geometry is not None:
        geom = mapping(obj.geometry)
    elif obj.attrs.get("attachment_point_wkt"):
        geom = mapping(wkt_loads(obj.attrs["attachment_point_wkt"]))
        props["geometry_kind"] = "attachment_point"
    else:
        return None
    return {"type": "Feature", "geometry": geom, "properties": props}


def _write_dump(objs: list[CadastralObject], out: Path) -> dict[str, int]:
    feats: list[dict[str, Any]] = []
    skipped = 0
    for o in objs:
        f = _obj_to_feature(o)
        if f is None:
            skipped += 1
            continue
        feats.append(f)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"type": "FeatureCollection", "features": feats},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"features": len(feats), "skipped": skipped}


async def run(
    cfg_path: str, cache_policy: CachePolicy, output: Path | None
) -> None:
    cfg = yaml.safe_load(Path(cfg_path).read_text("utf-8"))
    n = cfg.get("nspd", {})
    paths = cfg.get("paths", {})
    aoi_path = Path(paths.get("aoi_geojson", "aoi.geojson"))
    out_path = output or DEFAULT_OUTPUT
    layers: list[str] = n.get(
        "layers", ["land_record", "building", "structure", "unfinished"]
    )

    aoi = _load_aoi(aoi_path)
    log.info("aoi_loaded path=%s area_km2=%.3f", aoi_path, _area_km2(aoi))

    cache = FileCache(
        Path(n.get("cache_dir", "data/cache/nspd")),
        ttl_days=int(n.get("cache_ttl_days", 7)),
    )
    async with AsyncNspd(trust_env=False) as nspd:
        primary = PynspdAdapter(nspd, layers=layers)
        client = RobustCadastreClient(
            primary=primary,
            fallback=None,               # T8 не про fallback; он медленный
            cache=cache,
            max_retries=int(n.get("max_retries", 3)),
            retry_backoff_sec=float(n.get("retry_backoff_sec", 1.0)),
            retry_backoff_min_sec=float(n.get("retry_backoff_min_sec", 2.0)),
            rate_limit_rps=float(n.get("rate_limit_rps", 1.5)),
            cache_policy=cache_policy,
        )

        collected: list[CadastralObject] = []
        for ln in layers:
            objs = await client.get_features(cast(AOI, aoi), layer=ln)
            collected.extend(objs)
            log.info("layer_done layer=%s count=%d", ln, len(objs))

        # T9.4: backfill related_cad_nums для ОКС без контура (related из ТЗ T9).
        # На AOI Койсуг — 0 таких ОКС, т.е. 0 HTTP. Если встретятся — тянем.
        n_no_contour = 0
        n_with_related = 0
        for obj in collected:
            if obj.has_contour or not obj.cad_num:
                continue
            n_no_contour += 1
            try:
                related = await client.fetch_related_parcels(obj.cad_num)
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "related_backfill_failed",
                    extra={"cad_num": obj.cad_num, "err": type(e).__name__},
                )
                continue
            if related:
                obj.attrs["related_cad_nums"] = related
                n_with_related += 1
        log.info(
            "related_backfill oks_without_contour=%d with_related=%d",
            n_no_contour, n_with_related,
        )

        stats = _write_dump(collected, out_path)
        log.info(
            "dump_written path=%s features=%d skipped=%d total=%d",
            out_path, stats["features"], stats["skipped"], len(collected),
        )

        summary = client.cache_summary()
        log.info("cache_summary %s", summary)
        if summary["hits"] > 0:
            log.info(
                "running_on_cache hits=%d misses=%d",
                summary["hits"], summary["misses"],
            )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--output", default=None,
                    help=f"куда писать GeoJSON (default: {DEFAULT_OUTPUT})")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--use-cache", dest="cache_policy",
                     action="store_const", const=CachePolicy.USE)
    grp.add_argument("--no-cache", dest="cache_policy",
                     action="store_const", const=CachePolicy.NO)
    grp.add_argument("--refresh-cache", dest="cache_policy",
                     action="store_const", const=CachePolicy.REFRESH)
    ap.set_defaults(cache_policy=CachePolicy.USE)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s %(message)s")
    output = Path(args.output) if args.output else None
    asyncio.run(run(args.config, args.cache_policy, output))


if __name__ == "__main__":
    main()