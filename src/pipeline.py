"""CadastreCheck pipeline entrypoint.

Usage:
    python -m src.pipeline --config config.yaml

Current status (T1-T12): infrastructure ready (AOI, ESRI imagery, NSPD client,
cache, Sentinel-2). Full pipeline (T13-T26) — WIP: model, vectorization,
classification, GPKG export.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import yaml

log = logging.getLogger("pipeline")


async def run(config_path: Path) -> int:
    cfg = yaml.safe_load(config_path.read_text("utf-8"))
    log.info("pipeline_start config=%s", config_path)
    log.info("aoi=%s imagery=%s",
             cfg["paths"]["aoi_geojson"], cfg["paths"]["esri_mosaic_tif"])
    log.info("model=%s tile=%d overlap=%.2f",
             cfg["model"]["name"], cfg["model"]["tile_size"], cfg["model"]["overlap"])
    log.info("thresholds: min_area=%.1f m2, iou_match=%.2f, buffer=%.1f m",
             cfg["detection"]["min_area_m2"],
             cfg["matching"]["iou_threshold_match"],
             cfg["matching"]["buffer_m"])
    log.info("pipeline_stage: T1-T12 done (AOI, imagery, NSPD client, cache, S2).")
    log.info("pipeline_stage: T13-T26 WIP (OSM, model, vectorization, classification, GPKG).")
    log.info("pipeline_stage: no discrepancies.gpkg produced yet.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="config.yaml",
                    help="path to config.yaml (default: config.yaml)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s %(message)s")
    raise SystemExit(asyncio.run(run(Path(args.config))))


if __name__ == "__main__":
    main()
