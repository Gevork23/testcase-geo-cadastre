"""Bounded probe get_grid_data: 10с на вызов, Windows-совместимо.

Запуск: uv run python scripts/probe_grid_data.py
Если завис на строке — Ctrl+C, пришли что успело вывестись.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout
from nspd_request import NSPD


def _call(label, fn, *args, **kwargs):
    """Вызов с таймаутом 10с. Поток не убьём, но главный тред пойдёт дальше."""
    print(f"→ {label} ...", flush=True)
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(fn, *args, **kwargs)
        r = fut.result(timeout=10)
        kind = type(r).__name__
        n = len(r) if hasattr(r, "__len__") else "?"
        print(f"  {label}: {kind}, len={n}", flush=True)
        print(f"  sample: {str(r)[:300]}", flush=True)
        return r
    except FTimeout:
        print(f"  {label}: TIMEOUT 10s (поток продолжает висеть в фоне)", flush=True)
        return None
    except Exception as e:
        print(f"  {label}: FAILED {type(e).__name__}: {e}", flush=True)
        return None
    finally:
        ex.shutdown(wait=False)


def main():
    lat, lon = 47.20460644, 39.66405053
    api = NSPD(timeout=10)

    # 1. Какой квартал содержит точку
    _call(
        "get_cadastral_quarters",
        api.get_cadastral_quarters,
        lat, lon,
    )

    # 2. grid_data для ЗУ в квартале
    _call(
        "get_grid_data(ЗУ, kr)",
        api.get_grid_data,
        lat, lon,
        boundary_type="kr",
        category_id=36368,
        verbose=False,
    )

    # 3. grid_data для ОКС в квартале
    _call(
        "get_grid_data(Здания, kr)",
        api.get_grid_data,
        lat, lon,
        boundary_type="kr",
        category_id=36369,
        verbose=False,
    )


if __name__ == "__main__":
    main()