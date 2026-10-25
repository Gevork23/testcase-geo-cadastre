"""Живая проверка nspd-request: get_object_info + по координатам.

Запуск: uv run python scripts/probe_nspd_request.py
Вывод — весь сырой ответ, чтобы увидеть реальный формат.
"""
import json
from nspd_request import NSPD


def _dump(label, payload):
    print(f"\n=== {label} ===")
    try:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str)[:3000])
    except Exception as e:
        print(f"<не JSON: {type(e).__name__}: {e}>")
        print(repr(payload)[:1000])


def main():
    api = NSPD(timeout=30)

    # 1. Точечный: известный участок
    print("### Точечный запрос по кадастровому номеру")
    try:
        r = api.get_object_info(
            "61:46:0010601:5789",
            include_geom_id=True,
            include_object_type=True,
        )
        _dump("get_object_info('61:46:0010601:5789')", r)
    except Exception as e:
        print(f"FAILED get_object_info: {type(e).__name__}: {e}")

    # 2. Площадной: по координатам известного ОКС
    #    (39.66405053, 47.20460644 — из живого ответа pynspd на 61:44:0060958:173)
    lat, lon = 47.20460644, 39.66405053
    print(f"\n### Площадной запрос по точке ({lat}, {lon})")

    try:
        r = api.get_land_plot_by_coordinates(lat, lon)
        _dump("get_land_plot_by_coordinates", r)
    except Exception as e:
        print(f"FAILED get_land_plot_by_coordinates: {type(e).__name__}: {e}")

    try:
        r = api.get_oks_by_coordinates(lat, lon)
        _dump("get_oks_by_coordinates", r)
    except Exception as e:
        print(f"FAILED get_oks_by_coordinates: {type(e).__name__}: {e}")

    # 3. get_grid_data по кадастровому кварталу — для площадного fallback
    print(f"\n### get_grid_data по кадастровому кварталу (lat={lat}, lon={lon})")
    for cat_id, label in [(36368, "ЗУ"), (36369, "Здания"), (36383, "Сооружения")]:
        try:
            r = api.get_grid_data(lat, lon, boundary_type="kr", category_id=cat_id)
            if isinstance(r, dict):
                feats = (r.get("data") or {}).get("features") or r.get("features") or []
                print(f"  {label} (cat={cat_id}): {len(feats)} features, тип={type(r).__name__}")
                if feats:
                    f = feats[0]
                    print(f"    geometry: {'есть' if f.get('geometry') else 'нет'}")
                    opts = (f.get("properties") or {}).get("options") or {}
                    print(f"    cad_num: {opts.get('cad_num')!r}")
            elif isinstance(r, list):
                print(f"  {label} (cat={cat_id}): list, {len(r)} элементов")
                if r:
                    print(f"    первый элемент: {str(r[0])[:200]}")
            else:
                print(f"  {label} (cat={cat_id}): {type(r).__name__}, {str(r)[:200]}")
        except Exception as e:
            print(f"  {label} FAILED: {type(e).__name__}: {e}")

if __name__ == "__main__":
    main()