#!/usr/bin/env python
"""
Точечная проверка доступа к НСПД через pynspd (>=1.1.15).
Запуск: python scripts/probe_nspd.py --config config.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pynspd import Nspd
from pynspd.errors import (
    BlockedIP,
    NotFound,
    PynspdResponseError,
    TooBigContour,
    TooManyRequests,
)

TEST_CAD_NUMS = [
    "61:46:0010601:757",
    "61:46:0010601:5789",
    "61:46:0011802:3199",
    "61:44:0060958:173",
    "77:05:0001005:19",
]

logger = logging.getLogger("probe_nspd")


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# --- собственный файловый кэш (не зависит от Hishel) ---

def _cache_key(cad_num: str) -> str:
    return hashlib.sha1(f"pynspd:find:{cad_num}".encode()).hexdigest()


def _cache_path(cache_dir: Path, cad_num: str) -> Path:
    return cache_dir / f"{_cache_key(cad_num)}.json"


def _read_cache(cache_dir: Path, cad_num: str, ttl_sec: int) -> dict | None:
    p = _cache_path(cache_dir, cad_num)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    created = datetime.fromisoformat(data["created_at"])
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - created).total_seconds()
    if ttl_sec > 0 and age > ttl_sec:
        return None
    return data["payload"]


def _write_cache(cache_dir: Path, cad_num: str, payload: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = _cache_path(cache_dir, cad_num)
    record = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cad_num": cad_num,
        "payload": payload,
    }
    p.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


# --- probe одного объекта ---

def _probe_one(
    nspd: Nspd,
    cad_num: str,
    *,
    cache_dir: Path | None = None,
    cache_ttl_sec: int = 0,
    use_cache: bool = True,
) -> dict[str, Any]:
    res: dict[str, Any] = {
        "cad_num": cad_num,
        "ok": False,
        "error": None,
        "kind": None,
        "has_contour": None,
        "area_m2": None,
        "from_cache": False,
    }
    print(f"\n--- {cad_num} ---")

    # 0. Кэш
    if use_cache and cache_dir is not None:
        cached = _read_cache(cache_dir, cad_num, cache_ttl_sec)
        if cached is not None:
            print("  [cache hit]")
            res.update(cached)
            res["from_cache"] = True
            status = "OK" if res["ok"] else f"FAIL ({res['error']})"
            print(f"  {status}: kind={res['kind']}, contour={res['has_contour']}, area={res['area_m2']}")
            return res

    # 1. Сетевой запрос
    feat = None
    try:
        feat = nspd.find(cad_num)
    except BlockedIP as exc:
        res["error"] = f"BlockedIP (403): {exc}"
    except TooManyRequests as exc:
        res["error"] = f"TooManyRequests (429): {exc}"
    except NotFound:
        res["error"] = "not_found"
    except TooBigContour as exc:
        res["error"] = f"TooBigContour: {exc}"
    except PynspdResponseError as exc:
        res["error"] = f"PynspdResponseError: {exc}"
    except Exception as exc:  # noqa: BLE001
        res["error"] = f"{type(exc).__name__}: {exc}"

    # 2. Ошибка или «не найдено» — кэшируем как ok=False (повторный запуск не пойдёт в сеть)
    if res["error"] or feat is None:
        if not res["error"]:
            res["error"] = "not_found"
        print(f"  {res['error']}")
        if use_cache and cache_dir is not None:
            _write_cache(cache_dir, cad_num, {
                "ok": False,
                "error": res["error"],
                "kind": None,
                "has_contour": None,
                "area_m2": None,
            })
        return res

    # 3. Разбор свойств
    try:
        opts = feat.properties.cast().options
    except Exception:
        opts = feat.properties.options

    try:
        dumped = opts.model_dump()
    except Exception as exc:  # noqa: BLE001
        dumped = {}
        print(f"  не удалось сделать model_dump: {exc}")

    try:
        human = opts.model_dump_human_readable()
    except Exception:
        human = {}

    # 4. Тип
    kind = (
        dumped.get("land_record_type")
        or dumped.get("building_record_type")
        or dumped.get("construction_record_type")
        or dumped.get("unfinished_record_type")
        or dumped.get("object_type")
        or dumped.get("object_type_name")
        or human.get("Вид объекта")
        or human.get("Тип объекта")
        or human.get("Вид объекта капитального строительства")
        or human.get("Назначение")
        or human.get("Тип")
        or "unknown"
    )
    res["kind"] = str(kind)

    # 5. Контур
    no_coords = dumped.get("no_coords")
    if no_coords is not None:
        res["has_contour"] = not bool(no_coords)
    try:
        if feat.geometry is not None:
            shp = feat.geometry.to_shape()
            if shp is not None and not shp.is_empty:
                res["has_contour"] = True
    except Exception as exc:  # noqa: BLE001
        print(f"  warning: to_shape() упал: {exc}")

    # 6. Площадь
    area = (
        dumped.get("specified_area")
        or dumped.get("declared_area")
        or dumped.get("area")
        or human.get("Площадь (кв. м)")
    )
    res["area_m2"] = area

    res["ok"] = True
    print(f"  kind: {res['kind']}")
    print(f"  has_contour: {res['has_contour']}")
    if res["area_m2"] is not None:
        print(f"  area_m2: {res['area_m2']}")

    # 7. Кэш записи (только для успешного разбора)
    if use_cache and cache_dir is not None:
        _write_cache(cache_dir, cad_num, {
            "ok": True,
            "error": None,
            "kind": res["kind"],
            "has_contour": res["has_contour"],
            "area_m2": res["area_m2"],
        })
    return res


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument("--use-cache", dest="use_cache", action="store_true", default=None)
    cache_group.add_argument("--no-cache", dest="use_cache", action="store_false")
    args = parser.parse_args()

    cfg = load_config(args.config)
    paths = cfg.get("paths", {}) or {}
    nspd_cfg = cfg.get("nspd", {}) or {}

    log_dir = Path(paths.get("log_dir", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "probe_nspd.log", encoding="utf-8"),
        ],
    )

    timeout = int(nspd_cfg.get("timeout_sec", 30))
    retries = int(nspd_cfg.get("max_retries", 3))
    rps = float(nspd_cfg.get("rate_limit_rps", 1.5))
    pause = max(1.0 / rps, 0.1) if rps > 0 else 1.0

    use_cache = args.use_cache if args.use_cache is not None else bool(nspd_cfg.get("use_cache", True))
    cache_dir = Path(paths.get("cache_dir", "data/cache/nspd"))
    cache_ttl_sec = int(nspd_cfg.get("cache_ttl_days", 7)) * 86400

    nspd_kwargs: dict = dict(
        client_timeout=timeout,
        client_retries=retries,
        client_retry_on_blocked_ip=False,
    )

    # Клиент pynspd запускаем без cache_folder_path:
    # - Hishel всё равно не переиспользует ответы НСПД (нет Cache-Control),
    # - cache_folder_path + cache_ttl в конструкторе 1.1.15 падает ValueError.
    # Кэш ведём своим слоем (см. _read_cache / _write_cache).
    nspd_kwargs["trust_env"] = False

    print(f"Проверяем {len(TEST_CAD_NUMS)} к/н через pynspd")
    print(
        f"timeout={timeout}s, retries={retries}, pause≈{pause:.2f}s, "
        f"use_cache={use_cache}, cache_dir={cache_dir if use_cache else None}, "
        f"cache_ttl={cache_ttl_sec}s"
    )

    results: list[dict[str, Any]] = []
    with Nspd(**nspd_kwargs) as nspd:
        for cad_num in TEST_CAD_NUMS:
            results.append(
                _probe_one(
                    nspd,
                    cad_num,
                    cache_dir=cache_dir if use_cache else None,
                    cache_ttl_sec=cache_ttl_sec,
                    use_cache=use_cache,
                )
            )
            time.sleep(pause)

    print("\n=== Сводка ===")
    for r in results:
        src = "cache" if r.get("from_cache") else "http "
        if r["ok"]:
            print(
                f"  OK   [{src}] {r['cad_num']}: kind={r['kind']}, "
                f"contour={r['has_contour']}, area={r['area_m2']}"
            )
        else:
            print(f"  FAIL [{src}] {r['cad_num']}: {r['error']}")


if __name__ == "__main__":
    main()