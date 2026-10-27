"""T9.3: статистика ОКС без контура в AOI (для DECISIONS §T9 и отчёта T30).

Читает кэш area_<layer>_<hash>.json — в payload на верхнем уровне есть
has_contour. Если ОКС без контура найдены, прогоняет oks_effective_geometry
против индекса участков и считает, сколько из них реально наследуют геометрию.

Запуск:
  uv run python scripts/stats_oks_contours.py --config config.yaml
  uv run python scripts/stats_oks_contours.py --cache-dir data/cache/nspd \
      --json-out data/cache/nspd/oks_contour_stats.json
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shapely

from src.matching import oks_effective_geometry
from src.nspd_models import CadastralObject

LAYERS_OKS = ("building", "structure", "unfinished")


def _load(path: Path) -> dict:
    with io.open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _payload_items(path: Path) -> list[dict]:
    d = _load(path)
    return d["payload"] if isinstance(d, dict) and "payload" in d else d


def _payload_to_obj(it: dict) -> CadastralObject:
    geom = None
    wkb_hex = it.get("geometry_wkb")
    if wkb_hex:
        try:
            geom = shapely.from_wkb(bytes.fromhex(wkb_hex))
        except Exception:  # noqa: BLE001
            geom = None
    return CadastralObject(
        cad_num=it.get("cad_num"),
        object_type=it.get("object_type"),
        geometry=geom,
        area_cad_m2=it.get("area_cad_m2"),
        attrs=it.get("attrs") or {},
        has_contour=bool(it.get("has_contour")),
        source=it.get("source", "pynspd"),
    )


def _iter_layer(cache_dir: Path, layer: str):
    for p in sorted(cache_dir.glob(f"area_{layer}_*.json")):
        for it in _payload_items(p):
            yield it


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", default="data/cache/nspd",
                    help="директория с area_<layer>_<hash>.json")
    ap.add_argument("--json-out", default=None,
                    help="куда сохранить JSON с результатами (опционально)")
    args = ap.parse_args()

    cache_dir = Path(args.cache_dir)
    if not cache_dir.is_dir():
        print(f"error: cache dir not found: {cache_dir}", file=sys.stderr)
        return 2

    # --- считаем ОКС по слоям ------------------------------------------------
    per_layer: dict[str, dict[str, int]] = {}
    oks_without_contour: list[CadastralObject] = []
    for layer in LAYERS_OKS:
        total = with_contour = without_contour = 0
        for it in _iter_layer(cache_dir, layer):
            total += 1
            if it.get("has_contour"):
                with_contour += 1
            else:
                without_contour += 1
                oks_without_contour.append(_payload_to_obj(it))
        per_layer[layer] = {
            "total": total,
            "with_contour": with_contour,
            "without_contour": without_contour,
        }

    # --- индекс участков -----------------------------------------------------
    parcels: dict[str, CadastralObject] = {}
    for it in _iter_layer(cache_dir, "land_record"):
        cad = it.get("cad_num")
        if isinstance(cad, str) and cad:
            parcels[cad] = _payload_to_obj(it)

    # --- проверяем, кому поможет fallback ------------------------------------
    inheritable = 0
    for oks in oks_without_contour:
        geom, inherited = oks_effective_geometry(oks, parcels)
        if inherited and geom is not None:
            inheritable += 1

    summary = {
        "layers": per_layer,
        "oks_total": sum(v["total"] for v in per_layer.values()),
        "oks_with_contour": sum(v["with_contour"] for v in per_layer.values()),
        "oks_without_contour": len(oks_without_contour),
        "oks_without_contour_inheritable": inheritable,
        "parcels_indexed": len(parcels),
    }

    # --- вывод ---------------------------------------------------------------
    print("=== OKS contour stats ===")
    for layer in LAYERS_OKS:
        v = per_layer[layer]
        print(f"  {layer:12s}  total={v['total']:5d}  "
              f"with_contour={v['with_contour']:5d}  "
              f"without_contour={v['without_contour']:5d}")
    print(f"  {'TOTAL OKS':12s}  total={summary['oks_total']:5d}  "
          f"with_contour={summary['oks_with_contour']:5d}  "
          f"without_contour={summary['oks_without_contour']:5d}")
    print(f"  parcels_indexed={summary['parcels_indexed']}")
    print(f"  oks_without_contour_inheritable={summary['oks_without_contour_inheritable']}")

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with io.open(out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        print(f"  wrote {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
