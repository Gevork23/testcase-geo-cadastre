"""Сопоставление ОКС с участками (T9): fallback для ОКС без контура.

Контракт CadastralObject (src/nspd_models.py):
  - geometry != None, has_contour=True  → есть свой контур (Polygon/MultiPolygon);
  - geometry is None, has_contour=False → ОКС без контура (привязка к участку).

Правило T9: ОКС без контура не должен давать ложный UNREGISTERED, если
родительский участок известен. «Эффективная геометрия» = геометрия участка,
флаг inherited_from_parcel=True.

В AOI Койсуг (data/cache/nspd_dump.geojson на 2026-10-26) ОКС без контура = 0,
поэтому на этом AOI fallback не активируется. Реализация — для второго AOI (T28)
и для реальных данных, где ОКС без контура встречаются (options.no_coords=true).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

import shapely
from shapely.geometry.base import BaseGeometry

from src.nspd_models import CadastralObject

__all__ = [
    "EffectiveGeometry",
    "find_parcel_by_point",
    "is_detection_unregistered",
    "iter_oks_without_contour",
    "oks_effective_geometry",
]

# Ключи в attrs/options, по которым НСПД может отдавать явную связь ОКС→участок.
# Порядок — приоритет. Реальных значений в AOI Койсуг нет (проверено T9.0),
# но поля оставляем: схемы НСПД меняются (см. DECISIONS §T7).
_PARENT_CAD_NUM_KEYS: tuple[str, ...] = (
    "parent_cad_num",
    "parcel_cad_num",
    "land_record_cad_num",
    "parent_land_record_cad_num",
)


def _find_parent_cad_nums(oks: CadastralObject) -> list[str]:
    """Явная связь ОКС → участок(и) из attrs.

    Приоритет (по порядку добавления в результат):
      0) related_cad_nums — заполняет RobustCadastreClient.fetch_related_parcels
         (pynspd tab_land_links, "related" из ТЗ T9);
      1) parent_cad_num / parcel_cad_num / land_record_cad_num /
         parent_land_record_cad_num в attrs.options и properties.options.
    """
    attrs: dict[str, Any] = oks.attrs or {}
    out: list[str] = []

    def _push(v: Any) -> None:
        if isinstance(v, str) and v.strip() and v.strip() not in out:
            out.append(v.strip())

    related = attrs.get("related_cad_nums")
    if isinstance(related, (list, tuple)):
        for v in related:
            _push(v)

    options = attrs.get("options") or {}
    properties = attrs.get("properties") or {}
    inner_options = properties.get("options") or {}
    for source in (options, inner_options):
        for key in _PARENT_CAD_NUM_KEYS:
            _push(source.get(key))
    return out


def find_parcel_by_point(
    point: BaseGeometry,
    parcels: Mapping[str, CadastralObject],
) -> CadastralObject | None:
    """Первый участок, содержащий point (contains или touches-с-допуском).

    Перебор линейный — для AOI с <10k участков этого достаточно (3311 в Койсуге).
    Если станет горячим — заменить на STRtree (тот же контракт).
    """
    for parcel in parcels.values():
        geom = parcel.geometry
        if geom is None or geom.is_empty:
            continue
        if geom.covers(point):
            return parcel
    return None


def iter_oks_without_contour(
    oks_list: Iterable[CadastralObject],
) -> Iterable[CadastralObject]:
    """Итератор по ОКС без собственного контура (has_contour=False)."""
    for oks in oks_list:
        if not oks.has_contour:
            yield oks


class EffectiveGeometry(NamedTuple):
    """Результат oks_effective_geometry — соответствует README T9.

    README: "...геометрию связанного участка (с флагом inherited_from_parcel=True)".

    NamedTuple: работает и как кортеж (geom, inherited = ...), и как объект
    с атрибутами (result.inherited_from_parcel).
    """

    geometry: BaseGeometry | None
    inherited_from_parcel: bool


def oks_effective_geometry(
    oks: CadastralObject,
    parcels: Mapping[str, CadastralObject],
) -> EffectiveGeometry:
    """Эффективная геометрия ОКС с флагом наследования.

    Возвращает (geometry, inherited_from_parcel):
      - (oks.geometry, False)      — у ОКС есть свой контур;
      - (parcel.geometry, True)    — контура нет, но найден родительский участок
                                     (явная связь или spatial по attachment_point_wkt);
      - (None, False)              — контура нет, участок не найден.

    Семантика для классификатора (T20/T21):
      - UNREGISTERED: если inherited_from_parcel=True — НЕ флагуем детекцию
        внутри родительского участка (ОКС уже учтён, просто без контура);
      - MISSING: если inherited_from_parcel=True — сравниваем с геометрией
        участка, а не с пустотой (иначе получим ложный MISSING);
      - AREA_MISMATCH: у inherited_from_parcel=True площадь — это площадь участка,
        а не здания → для AREA_MISMATCH такие ОКС пропускаем (area_cad_m2 относится
        к ОКС, а не к участку — сравнение некорректно).
    """
    # 1) Свой контур — приоритет. has_contour=True гарантирует Polygon/MultiPolygon.
    if oks.has_contour and oks.geometry is not None and not oks.geometry.is_empty:
        return EffectiveGeometry(oks.geometry, False)

    # 2) Явная связь (related_cad_nums / parent_cad_num / ...) — приоритет
    #    над spatial. Перебираем все кадастровые номера, берём первый
    #    известный участок с геометрией.
    for parent_cad in _find_parent_cad_nums(oks):
        if parent_cad in parcels:
            parcel = parcels[parent_cad]
            if parcel.geometry is not None and not parcel.geometry.is_empty:
                return EffectiveGeometry(parcel.geometry, True)

    # 3) Spatial fallback: ОКС без контура с привязкой к точке.
    wkt = (oks.attrs or {}).get("attachment_point_wkt")
    if isinstance(wkt, str) and wkt.strip():
        try:
            point = shapely.from_wkt(wkt)
        except Exception:  # noqa: BLE001 — битый WKT не должен ронять пайплайн
            point = None
        if point is not None and not point.is_empty:
            parcel = find_parcel_by_point(point, parcels)
            if parcel is not None and parcel.geometry is not None \
                    and not parcel.geometry.is_empty:
                return EffectiveGeometry(parcel.geometry, True)

    # 4) Геометрия есть, но has_contour=False (аномалия схемы) — доверяем
    #    геометрии, но помечаем как inherited=False: это не наследование.
    if oks.geometry is not None and not oks.geometry.is_empty:
        return EffectiveGeometry(oks.geometry, False)

    return EffectiveGeometry(None, False)

def is_detection_unregistered(
    detection_geom: BaseGeometry,
    oks_list: Iterable[CadastralObject],
    parcels: Mapping[str, CadastralObject],
    *,
    buffer_deg: float = 0.00002,
) -> tuple[bool, str]:
    """T9.7: runtime-проверка UNREGISTERED с учётом ОКС без контура.

    README T9: "При классификации UNREGISTERED детекция внутри такого участка
    не должна флагуться как незарегистрированная."

    Логика:
      1. Для каждого ОКС вычисляем эффективную геометрию (свой контур ИЛИ
         геометрия родительского участка с inherited_from_parcel=True).
      2. Если detection (с буфером) пересекает любую эффективную геометрию —
         ОКС учтён → НЕ UNREGISTERED.
      3. Иначе — UNREGISTERED.

    buffer_deg — допуск сдвига снимка в градусах (2e-5 ≈ 2 м на широте 47°).
    Для более точного допуска T20/T22 может передать точное значение из
    matching.* config + расчёт через UTM. Здесь дефолт — безопасный.

    Returns:
        (is_unregistered, reason):
          - (False, "matches_oks:<cad_num>")            — пересекает свой контур;
          - (False, "inside_parcel_with_inherited:<cad>") — внутри участка с ОКС без контура;
          - (True,  "no_matching_oks")                  — нет совпадений → UNREGISTERED.
    """
    det_buffered = detection_geom.buffer(buffer_deg) if buffer_deg > 0 else detection_geom
    for oks in oks_list:
        eff = oks_effective_geometry(oks, parcels)
        if eff.geometry is None or eff.geometry.is_empty:
            continue
        if not eff.geometry.intersects(det_buffered):
            continue
        if eff.inherited_from_parcel:
            return False, f"inside_parcel_with_inherited:{oks.cad_num}"
        return False, f"matches_oks:{oks.cad_num}"
    return True, "no_matching_oks"
