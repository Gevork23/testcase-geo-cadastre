"""Устойчивый клиент НСПД: retry, rate limit, файловый кэш, каскад адаптеров.

Публичный вход — RobustCadastreClient. Два адаптера:
  - PynspdAdapter       — основной, async, типизированный;
  - NspdRequestAdapter  — fallback, sync через asyncio.to_thread.

has_contour семантика: контур = Polygon/MultiPolygon. Point приходит для
ОКС без контура (options.no_coords=True) и сохраняется в attrs как
attachment_point_wkt — контуром НЕ считается (см. DECISIONS.md §T7).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

import httpx
import numpy as np
import shapely
from aiolimiter import AsyncLimiter
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.geometry.base import BaseGeometry
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from pynspd import AsyncNspd, NspdFeature
from pynspd.errors import (
    BlockedIP,
    NotFound,
    PynspdResponseError,
    PynspdServerError,
    TooManyRequests,
)

from .nspd_cache import FileCache, cache_key_area, cache_key_point
from .nspd_models import CadastralObject, ObjectType, Source

log = logging.getLogger("nspd_client")
_CACHE_SCHEMA = 2

# AOI в проекте всегда полигональный; алиас для читаемости сигнатур.
AOI = Polygon | MultiPolygon


# --- 1. Что ретраим / что нет ----------------------------------------------
def _is_retryable(exc: BaseException) -> bool:
    # NotFound и BlockedIP — подклассы PynspdResponseError; проверяем их ДО базы.
    if isinstance(exc, (NotFound, BlockedIP)):
        return False
    return isinstance(
        exc,
        (
            TooManyRequests,
            PynspdServerError,
            PynspdResponseError,
            httpx.TimeoutException,
            httpx.ConnectError,
        ),
    )


class CascadeFailed(RuntimeError):
    """И pynspd, и nspd-request упали. Кэш не пишем, ошибку логируем."""


# --- 2. Маппинг слоёв и типов ---------------------------------------------
_LAYER_ALIASES = {
    "land_record": "Земельные участки из ЕГРН",
    "building": "Здания",
    "structure": "Сооружения",
    "unfinished": "Объекты незавершенного строительства",
}

_CATEGORY_TO_TYPE: dict[int, ObjectType] = {
    36048: "parcel", 36049: "building", 36328: "structure", 36329: "unfinished",
    36368: "parcel", 36369: "building", 36383: "structure", 36384: "unfinished",
}

_TYPE_FROM_NSPD: dict[str, ObjectType] = {
    "ЗУ": "parcel",
    "Здание": "building",
    "Сооружение": "structure",
    "Объект незавершенного строительства": "unfinished",
}

_FIELD_TO_TYPE: tuple[tuple[str, ObjectType], ...] = (
    ("land_record_type", "parcel"),
    ("building_record_type", "building"),
    ("construction_record_type", "structure"),
    ("unfinished_record_type", "unfinished"),
)


def _infer_type(
    *candidates: dict[str, Any], category: int | None = None
) -> ObjectType | None:
    if category is not None and category in _CATEGORY_TO_TYPE:
        return _CATEGORY_TO_TYPE[category]
    for dump in candidates:
        for field, mapped in _FIELD_TO_TYPE:
            if dump.get(field):
                return mapped
        ot = str(dump.get("object_type") or "").lower()
        if "участ" in ot:
            return "parcel"
        if "здан" in ot:
            return "building"
        if "сооруж" in ot:
            return "structure"
        if "незавер" in ot:
            return "unfinished"
    return None


def _is_contour(geom: BaseGeometry | None) -> bool:
    """Контур — только Polygon/MultiPolygon. Point/Line — привязка, не контур."""
    if geom is None or geom.is_empty:
        return False
    return geom.geom_type in ("Polygon", "MultiPolygon")


# --- 2a. Мягкий парсинг: whitelist известных полей options ---------------
# Пополняется при добавлении новых полей в парсер. Неизвестные поля
# логируются один раз на имя — это страховка от смены схемы НСПД.
_KNOWN_OPTION_FIELDS: frozenset[str] = frozenset({
    # земельные участки
    "land_record_type", "specified_area", "declared_area",
    # ОКС
    "building_record_type", "construction_record_type",
    "unfinished_record_type", "build_record_area",
    "build_record_registration_date", "build_record_type_value",
    "building_name", "purpose", "floors", "underground_floors",
    "year_built", "year_commisioning",
    # общее
    "area", "cad_num", "cad_number", "quarter_cad_number",
    "cancel_date", "common_data_status", "cost_index", "cost_value",
    "cultural_heritage_object", "cultural_heritage_val",
    "name", "ownership_type", "readable_address",
    "registration_date", "removed_GCU", "status", "type",
    "no_coords", "objdoc_id", "registers_id", "object_type",
})
_UNKNOWN_OPTION_FIELDS_SEEN: set[str] = set()


def _log_unknown_options(opts: dict[str, Any], source: Source) -> None:
    """WARNING один раз на каждое новое имя поля options."""
    unknown = set(opts.keys()) - _KNOWN_OPTION_FIELDS
    new_unknown = unknown - _UNKNOWN_OPTION_FIELDS_SEEN
    if not new_unknown:
        return
    _UNKNOWN_OPTION_FIELDS_SEEN.update(new_unknown)
    log.warning(
        "unknown_option_field",
        extra={
            "fields": sorted(new_unknown)[:20],
            "source": source,
            "count": len(new_unknown),
        },
    )


# --- 3. Мягкий парсинг pynspd-фичи ---------------------------------------
def _dump_model(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    try:
        return (
            obj.model_dump(by_alias=False) if hasattr(obj, "model_dump") else dict(obj)
        )
    except Exception as e:  # noqa: BLE001 — мягкий парсинг
        log.warning("model_dump_failed", extra={"err": str(e), "type": type(obj).__name__})
        return {}


def _to_object(feat: Any, source: Source) -> CadastralObject:
    props = getattr(feat, "properties", None)
    options = getattr(props, "options", None)

    opts = _dump_model(options)
    props_dump = _dump_model(props)
    feat_dump = _dump_model(feat)

    _log_unknown_options(opts, source)

    attrs: dict[str, Any] = {**feat_dump, **props_dump, **opts}

    category = getattr(props, "category", None)
    if category is None:
        category = getattr(getattr(feat, "layer_meta", None), "category_id", None)

    geom: BaseGeometry | None = None
    try:
        raw_geom = getattr(feat, "geometry", None)
        if raw_geom is not None and hasattr(raw_geom, "to_shape"):
            shape_ = raw_geom.to_shape()
            if _is_contour(shape_):
                geom = shape_
            else:
                attrs["attachment_point_wkt"] = shapely.to_wkt(
                    shape_, rounding_precision=8
                )
    except Exception as e:  # noqa: BLE001
        log.warning("geometry_parse_failed", extra={"err": str(e)})

    cad_num = (
        opts.get("cad_num") or attrs.get("cad_num") or attrs.get("cad_number")
    )
    area = (
        opts.get("build_record_area")
        or opts.get("specified_area")
        or opts.get("area")
        or opts.get("declared_area")
    )

    return CadastralObject(
        cad_num=cad_num,
        object_type=_infer_type(opts, props_dump, category=category),
        geometry=geom,
        area_cad_m2=float(area) if area is not None else None,
        attrs=attrs,
        has_contour=geom is not None,
        source=source,
    )


# --- 4. Адаптер pynspd ----------------------------------------------------
class PynspdAdapter:
    def __init__(self, client: AsyncNspd, layers: list[str] | None = None) -> None:
        self.client = client
        self.layers = layers or list(_LAYER_ALIASES)
        self._layer_defs: dict[str, Any] = {}

    def _layer_def(self, name: str) -> Any:
        if name not in self._layer_defs:
            title = _LAYER_ALIASES.get(name, name)
            self._layer_defs[name] = NspdFeature.by_title(title)  # type: ignore[arg-type]
        return self._layer_defs[name]

    async def fetch_parcel(self, cad_num: str) -> CadastralObject | None:
        try:
            feat = await self.client.find(cad_num)
        except NotFound:
            return None
        if feat is None:
            return None
        return _to_object(feat, "pynspd")

    async def fetch_related_parcels(self, cad_num: str) -> list[str]:
        """T9.4: cad_nums родительских участков ОКС через related/tab_land_links."""
        try:
            feat = await self.client.find(cad_num)
        except NotFound:
            return []
        if feat is None:
            return []
        try:
            links = await self.client.tab_land_links(feat)
        except Exception as e:  # noqa: BLE001
            log.warning(
                "tab_land_links_failed",
                extra={"cad_num": cad_num, "err": type(e).__name__},
            )
            return []
        if not links:
            return []
        return [x for x in links if isinstance(x, str) and x.strip()]

    async def get_features(
        self, aoi: AOI, *, layer: str | None = None
    ) -> list[CadastralObject]:
        layers = [layer] if layer else self.layers
        out: list[CadastralObject] = []
        succeeded = 0
        last_err: Exception | None = None
        for ln in layers:
            try:
                layer_def = self._layer_def(ln)
                async for feat in self.client.search_in_contour_iter(aoi, layer_def):
                    out.append(_to_object(feat, "pynspd"))
                succeeded += 1
            except Exception as e:  # noqa: BLE001 — один слой не должен убить обход
                log.warning("layer_iter_failed", extra={"layer": ln, "err": type(e).__name__})
                last_err = e
        # Все слои упали — это не «пустой AOI», а сбой → raise, чтобы сработал каскад.
        if succeeded == 0 and last_err is not None:
            raise last_err
        return out


# --- 5. Адаптер nspd-request (fallback, sync) ----------------------------
_TRANSFORM_3857_TO_4326 = Transformer.from_crs(3857, 4326, always_xy=True)


def _reproject_3857_to_4326(geom: BaseGeometry) -> BaseGeometry:
    """EPSG:3857 → EPSG:4326 через shapely.transform (без deprecated .ops.transform)."""
    def _f(coords: np.ndarray) -> np.ndarray:
        lon, lat = _TRANSFORM_3857_TO_4326.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([lon, lat]).astype("float64")

    return shapely.transform(geom, _f)


def _iter_grid_points(
    aoi: BaseGeometry, step_deg: float
) -> Iterable[tuple[float, float]]:
    """Точки сетки внутри AOI с шагом step_deg (в градусах)."""
    minx, miny, maxx, maxy = aoi.bounds
    x = minx
    while x <= maxx:
        y = miny
        while y <= maxy:
            if aoi.contains(Point(x, y)):
                yield x, y
            y += step_deg
        x += step_deg


def _parse_nspd_request_features(
    payload: Any, source: Source
) -> list[CadastralObject]:
    """Толерантный парсер ответа nspd-request.

    Реальные форматы (по probe 2026-10-25):
      - get_object_info              → {data: {type: FeatureCollection, features: [...]}}
      - get_land_plot_by_coordinates → str cad_num (НЕ объект — пропускаем)
      - get_oks_by_coordinates       → str cad_num | null
      - get_grid_data                → формат проверяется в T8 (TODO)

    Геометрия из nspd-request — в EPSG:3857, конвертируется в EPSG:4326.
    Площадь и cad_num ищутся в нескольких возможных полях options
    (разные слои НСПД кладут разные имена).
    """
    if payload is None:
        return []

    # get_land_plot_by_coordinates / get_oks_by_coordinates отдают строку
    # cad_num без геометрии — бесполезна для матчинга, пропускаем.
    if isinstance(payload, str):
        log.info(
            "nspd_request_cadnum_only",
            extra={"cad_num": payload, "source": source},
        )
        return []

    if isinstance(payload, dict):
        if payload.get("error"):
            return []
        feats = payload.get("features")
        if feats is None:
            data = payload.get("data")
            if isinstance(data, dict):
                feats = data.get("features")
        if feats is None:
            # единичный объект — оборачиваем
            feats = [payload]
    elif isinstance(payload, list):
        feats = payload
    else:
        return []

    out: list[CadastralObject] = []
    for feat in feats:
        if not isinstance(feat, dict):
            continue

        props = feat.get("properties") or {}
        opts = props.get("options") or {}
        if not isinstance(props, dict):
            props = {}
        if not isinstance(opts, dict):
            opts = {}

        # cad_num: реальный ответ nspd-request кладёт его в options.cad_num;
        # externalKey/label — дубли на верхнем уровне properties.
        cad_num = (
            opts.get("cad_num")
            or props.get("cad_num")
            or props.get("externalKey")
            or props.get("label")
        )
        if not cad_num:
            log.debug(
                "nspd_request_feature_without_cad_num",
                extra={"source": source},
            )
            continue

        # geometry: в 3857 → конвертируем в 4326
        geom: BaseGeometry | None = None
        raw_geom = feat.get("geometry")
        if isinstance(raw_geom, dict):
            try:
                candidate = _reproject_3857_to_4326(shape(raw_geom))
                geom = candidate if _is_contour(candidate) else None
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "nspd_request_geometry_failed",
                    extra={"err": type(e).__name__, "cad_num": cad_num},
                )

        # area: разные слои — разные имена полей
        area = (
            opts.get("build_record_area")
            or opts.get("specified_area")
            or opts.get("land_record_area")
            or opts.get("area")
            or opts.get("declared_area")
        )

        # category: в верхнем уровне properties (36368 / 36369 / 36383 / 36384)
        category = props.get("category")

        # attrs: плоский dict со всеми полями (feat + properties + options)
        feat_attrs: dict[str, Any] = {}
        for k, v in feat.items():
            if k not in ("geometry", "properties"):
                feat_attrs[k] = v
        attrs: dict[str, Any] = {**feat_attrs, **props, **opts}

        out.append(
            CadastralObject(
                cad_num=cad_num,
                object_type=_infer_type(opts, props, category=category),
                geometry=geom,
                area_cad_m2=float(area) if area is not None else None,
                attrs=attrs,
                has_contour=geom is not None,
                source=source,
            )
        )
    return out


class NspdRequestAdapter:
    """Fallback-адаптер на синхронной библиотеке nspd-request.

    Точечный fetch_parcel — полноценный, доказан вживую
    (NSPD_LIVE=1 test_live_cascade...): get_object_info → GeoJSON
    с geometry в EPSG:3857, парсер конвертирует в 4326.

    Площадной get_features — сетка точек → get_land_plot_by_coordinates /
    get_oks_by_coordinates (возвращают только cad_num строкой) →
    get_object_info для каждого cad_num (полный GeoJSON).
    ОГРАНИЧЕНИЯ:
      - 2 запроса на точку + 1 на уникальный cad_num;
      - здания меньше шага сетки пропускаются;
      - AOI 3.81 км² при шаге 100 м ≈ 400 точек ≈ 15-20 мин при 1.5 rps;
      - get_grid_data (по кварталу) в nspd-request 2.0.2 зависает на
        плотных кварталах без пагинации — НЕ используется (probe 2026-10-25);
      - fallback_max_points защищает от долгого обхода (WARNING + []).

    Для T8+: либо bbox-API НСПД через requests напрямую, либо get_grid_data
    с явным таймаутом на квартал.
    """

    def __init__(
        self, grid_step_m: float = 100.0, max_points: int = 500
    ) -> None:
        try:
            from nspd_request import NSPD

            self.api = NSPD(timeout=30)
            self._ok = True
        except ImportError:
            self._ok = False
            log.warning("nspd_request_not_installed")
        self.grid_step_m = grid_step_m
        self.max_points = max_points

    async def fetch_parcel(self, cad_num: str) -> CadastralObject | None:
        if not self._ok:
            return None
        try:
            payload = await asyncio.to_thread(
                self.api.get_object_info,
                cad_num,
                include_geom_id=True,
                include_object_type=True,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("nspd_request_failed",
                        extra={"cad_num": cad_num, "err": type(e).__name__})
            return None
        if not payload or payload.get("error"):
            return None

        objects = _parse_nspd_request_features(payload, "nspd_request")
        if not objects:
            return None
        obj = objects[0]
        raw_type = payload.get("object_type")
        if obj.object_type is None and isinstance(raw_type, str):
            obj = obj.model_copy(update={"object_type": _TYPE_FROM_NSPD.get(raw_type)})
        return obj

    async def get_features(
        self, aoi: AOI, *, layer: str | None = None
    ) -> list[CadastralObject]:
        if not self._ok:
            return []

        step_deg = self.grid_step_m / 111_320.0  # ~1° широты = 111.32 км
        xs_ys = list(_iter_grid_points(aoi, step_deg))
        if not xs_ys:
            return []
        if len(xs_ys) > self.max_points:
            log.warning(
                "nspd_request_area_too_big",
                extra={"points": len(xs_ys), "max": self.max_points, "layer": layer},
            )
            return []

        results: dict[str, CadastralObject] = {}
        for x, y in xs_ys:
            try:
                objs = await self._fetch_by_point(x, y, layer)
            except Exception as e:  # noqa: BLE001
                log.warning("nspd_request_point_failed",
                            extra={"x": x, "y": y, "err": type(e).__name__})
                continue
            for obj in objs:
                if obj.cad_num is None:
                    continue
                if obj.cad_num in results:
                    continue
                if obj.geometry is not None and not aoi.intersects(obj.geometry):
                    continue
                results[obj.cad_num] = obj

        log.info(
            "nspd_request_area_done",
            extra={"points": len(xs_ys), "objects": len(results), "layer": layer},
        )
        return list(results.values())

    async def fetch_related_parcels(self, cad_num: str) -> list[str]:
        """T9.6: real fallback через nspd-request get_related_objects.

        Проверено 2026-10-27: get_land_plots_by_oks / get_oks_by_land_plot
        в 2.0.2 возвращают None; get_related_objects отдаёт dict с полем
        "related": ["<parcel cad_num>", ...] — это аналог tab_land_links.
        """
        if not self._ok:
            return []
        try:
            payload = await asyncio.to_thread(
                self.api.get_related_objects, cad_num,
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "nspd_request_related_failed",
                extra={"cad_num": cad_num, "err": type(e).__name__},
            )
            return []
        if not isinstance(payload, dict):
            return []
        related = payload.get("related")
        if not isinstance(related, list):
            return []
        return [x for x in related if isinstance(x, str) and x.strip()]

    async def _fetch_by_point(
        self, lon: float, lat: float, layer: str | None
    ) -> list[CadastralObject]:
        """Один узел сетки: cad_num через get_*_by_coordinates, затем объект
        через get_object_info (реальный формат — см. probe 2026-10-25).

        get_land_plot_by_coordinates / get_oks_by_coordinates возвращают
        ТОЛЬКО cad_num строкой (или null). Геометрии в этих ответах нет —
        поэтому после сбора cad_num вызываем get_object_info для каждого.
        """
        out: list[CadastralObject] = []
        cad_nums: set[str] = set()

        if layer in (None, "land_record"):
            try:
                r = await asyncio.to_thread(
                    self.api.get_land_plot_by_coordinates, lat, lon
                )
                if isinstance(r, str):
                    cad_nums.add(r)
                elif isinstance(r, list):
                    cad_nums.update(x for x in r if isinstance(x, str))
                else:
                    # на случай, если nspd-request в будущем начнёт отдавать
                    # GeoJSON напрямую — парсер уже умеет
                    out.extend(_parse_nspd_request_features(r, "nspd_request"))
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "nspd_request_land_plot_failed",
                    extra={"err": type(e).__name__},
                )

        if layer in (None, "building", "structure", "unfinished"):
            try:
                r = await asyncio.to_thread(
                    self.api.get_oks_by_coordinates, lat, lon
                )
                if isinstance(r, str):
                    cad_nums.add(r)
                elif isinstance(r, list):
                    cad_nums.update(x for x in r if isinstance(x, str))
                else:
                    out.extend(_parse_nspd_request_features(r, "nspd_request"))
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "nspd_request_oks_failed",
                    extra={"err": type(e).__name__},
                )

        # Добираем полные объекты по cad_num. 2 запроса на точку вместо 1,
        # но иначе геометрии не получить.
        for cad in cad_nums:
            try:
                payload = await asyncio.to_thread(
                    self.api.get_object_info,
                    cad,
                    include_geom_id=True,
                    include_object_type=True,
                )
                out.extend(_parse_nspd_request_features(payload, "nspd_request"))
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "nspd_request_object_info_failed",
                    extra={"cad_num": cad, "err": type(e).__name__},
                )

        return out


# --- 6. Сериализация для кэша --------------------------------------------
def _obj_to_payload(obj: CadastralObject) -> dict[str, Any]:
    return {
        "_v": _CACHE_SCHEMA,
        "cad_num": obj.cad_num,
        "object_type": obj.object_type,
        "geometry_wkb": shapely.to_wkb(obj.geometry, hex=True) if obj.geometry else None,
        "area_cad_m2": obj.area_cad_m2,
        "attrs": json.loads(json.dumps(obj.attrs, ensure_ascii=False, default=str)),
        "has_contour": obj.has_contour,
        "source": obj.source,
    }


def _payload_to_obj(p: dict[str, Any]) -> CadastralObject | None:
    """None — если схема кэша устарела или геометрия — не контур (Point и т.п.)."""
    if p.get("_v") != _CACHE_SCHEMA:
        return None
    geom_wkb = p.get("geometry_wkb")
    geom = shapely.from_wkb(geom_wkb) if isinstance(geom_wkb, str) else None
    if not _is_contour(geom):
        geom = None
    obj_type = p.get("object_type")
    return CadastralObject(
        cad_num=p.get("cad_num"),
        object_type=obj_type if obj_type in ("parcel", "building", "structure", "unfinished") else None,
        geometry=geom,
        area_cad_m2=p.get("area_cad_m2"),
        attrs=p.get("attrs") or {},
        has_contour=geom is not None,
        source=p.get("source", "pynspd"),
    )


# --- 7. Оркестратор -----------------------------------------------------
class CachePolicy(str, Enum):
    """Политика работы с файловым кэшем НСПД.

    USE     — читать кэш, писать в кэш (default);
    NO      — не читать, не писать (CI, отладка «что реально отдаёт API»);
    REFRESH — не читать, но писать поверх (инвалидация без rm).
    """

    USE = "use"
    NO = "no"
    REFRESH = "refresh"


class RobustCadastreClient:
    def __init__(
        self,
        primary: PynspdAdapter,
        fallback: NspdRequestAdapter | None,
        cache: FileCache,
        *,
        max_retries: int = 3,
        retry_backoff_sec: float = 1.0,
        retry_backoff_min_sec: float = 2.0,
        rate_limit_rps: float = 1.5,
        cache_policy: CachePolicy = CachePolicy.USE,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.cache = cache
        self.max_retries = max_retries
        self.backoff = retry_backoff_sec
        self.backoff_min = retry_backoff_min_sec
        self._limiter = AsyncLimiter(rate_limit_rps, 1)  # общий на процесс
        self.cache_policy = cache_policy
        self.stats: dict[str, int] = {
            "hits": 0,
            "misses": 0,
            "stale": 0,
            "writes": 0,
            "errors": 0,
        }

    # --- работа с кэшем под политикой ----------------------------------
    def _cached_get(self, key: str) -> Any | None:
        """Читает кэш только при USE; NO/REFRESH — осознанный обход."""
        if self.cache_policy is not CachePolicy.USE:
            return None
        payload = self.cache.get(key)
        if payload is None:
            self.stats["misses"] += 1
        return payload

    def _cached_put(self, key: str, payload: Any) -> None:
        """Пишет кэш при USE и REFRESH; NO — не пишет (безопасно для CI)."""
        if self.cache_policy is CachePolicy.NO:
            return
        self.cache.put(key, payload)
        self.stats["writes"] += 1

    def cache_summary(self) -> dict[str, int]:
        """Снапшот счётчиков для лога в конце пайплайна."""
        return dict(self.stats)

    # --- низкоуровневые хелперы ----------------------------------------
    async def _rate_limited(self, fn: Callable[[], Awaitable[Any]]) -> Any:
        async with self._limiter:
            return await fn()

    async def _call_with_retry(
        self, fn: Callable[[], Awaitable[Any]], *, op: str
    ) -> Any:
        """Ретраит транзиентные ошибки. NotFound/BlockedIP/CascadeFailed — сразу raise."""
        request_id = uuid.uuid4().hex[:8]
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_is_retryable),
            wait=wait_exponential(
                multiplier=self.backoff, min=self.backoff_min, max=30
            ),
            stop=stop_after_attempt(self.max_retries),
            reraise=True,
        ):
            with attempt:
                attempt_no = attempt.retry_state.attempt_number
                t0 = time.monotonic()
                try:
                    result = await self._rate_limited(fn)
                except (NotFound, BlockedIP):
                    raise
                except Exception as e:
                    elapsed = int((time.monotonic() - t0) * 1000)
                    log.warning(
                        "nspd_call",
                        extra={
                            "request_id": request_id,
                            "attempt": attempt_no,
                            "status": "error",
                            "op": op,
                            "source": "pynspd",
                            "elapsed_ms": elapsed,
                            "error": type(e).__name__,
                        },
                    )
                    raise
                elapsed = int((time.monotonic() - t0) * 1000)
                log.info(
                    "nspd_call",
                    extra={
                        "request_id": request_id,
                        "attempt": attempt_no,
                        "status": "ok",
                        "op": op,
                        "source": "pynspd",
                        "elapsed_ms": elapsed,
                    },
                )
                return result
        return None

    async def _try_cascade(
        self, op_name: str, method_name: str, *args: Any, **kwargs: Any
    ) -> Any:
        try:
            return await self._call_with_retry(
                lambda: getattr(self.primary, method_name)(*args, **kwargs),
                op=op_name,
            )
        except (NotFound, BlockedIP):
            raise
        except PynspdResponseError as e:
            log.warning(
                "pynspd_failed_trying_fallback",
                extra={"op": op_name, "err": type(e).__name__, "source": "pynspd"},
            )
            if self.fallback is None:
                raise CascadeFailed(str(e)) from e
            try:
                return await self._rate_limited(
                    lambda: getattr(self.fallback, method_name)(*args, **kwargs)
                )
            except Exception as e2:  # noqa: BLE001
                log.error(
                    "fallback_failed",
                    extra={"op": op_name, "err": type(e2).__name__, "source": "nspd_request"},
                )
                raise CascadeFailed(str(e2)) from e2

    # --- публичный контракт CadastreLoader ----------------------------
    async def fetch_parcel(self, cad_num: str) -> CadastralObject | None:
        key = cache_key_point("parcel", cad_num)
        cached = self._cached_get(key)
        if cached is not None:
            if cached.get("ok") is False:
                self.stats["hits"] += 1
                log.info("cache_hit", extra={"op": "parcel", "cad_num": cad_num})
                return None
            obj = _payload_to_obj(cached["obj"])
            if obj is not None:
                self.stats["hits"] += 1
                log.info("cache_hit", extra={"op": "parcel", "cad_num": cad_num})
                return obj
            self.stats["stale"] += 1
            log.warning("cache_stale_schema",
                        extra={"op": "parcel", "cad_num": cad_num})

        cache_missed = (
            cached is None and self.cache_policy is CachePolicy.USE
        )
        try:
            obj = await self._try_cascade("parcel", "fetch_parcel", cad_num)
        except NotFound:
            obj = None
        except Exception as e:
            self.stats["errors"] += 1
            extra = {"cad_num": cad_num, "err": type(e).__name__,
                     "detail": str(e)[:200]}
            if cache_missed:
                log.error("cache_miss_on_network_error", extra=extra)
            log.exception("parcel_failed_no_cache", extra=extra)
            return None

        if obj is None:
            self._cached_put(key, {"ok": False, "error": "not_found"})
            return None
        self._cached_put(key, {"ok": True, "obj": _obj_to_payload(obj)})
        return obj

    async def get_features(
        self, aoi: AOI, *, layer: str | None = None
    ) -> list[CadastralObject]:
        ln = layer or "all"
        key = cache_key_area(ln, aoi)
        cached = self._cached_get(key)
        if cached is not None:
            objs = [_payload_to_obj(o) for o in cached]
            if all(o is not None for o in objs):
                self.stats["hits"] += 1
                log.info("cache_hit", extra={"op": "area", "layer": ln})
                return objs  # type: ignore[return-value]
            self.stats["stale"] += 1
            log.warning("cache_stale_schema", extra={"op": "area", "layer": ln})

        cache_missed = (
            cached is None and self.cache_policy is CachePolicy.USE
        )
        try:
            objects = await self._try_cascade("area", "get_features", aoi, layer=layer)
        except Exception as e:
            self.stats["errors"] += 1
            extra = {"layer": ln, "err": type(e).__name__,
                     "detail": str(e)[:200]}
            if cache_missed:
                log.error("cache_miss_on_network_error", extra=extra)
            log.exception("area_failed_no_cache", extra=extra)
            return []

        objects = objects or []
        self._cached_put(key, [_obj_to_payload(o) for o in objects])
        return objects

    async def fetch_related_parcels(self, cad_num: str) -> list[str]:
        """T9.4: родительские участки ОКС (pynspd tab_land_links → fallback).

        Не кэшируется: точечный вызов на конкретный ОКС; на Koisug — 0 раз
        (AOI не содержит ОКС без контура, см. §T9).
        """
        try:
            result = await self._rate_limited(
                lambda: self.primary.fetch_related_parcels(cad_num)
            )
        except Exception as e:  # noqa: BLE001
            log.warning(
                "related_primary_failed",
                extra={"cad_num": cad_num, "err": type(e).__name__},
            )
            result = []
        if result:
            return result
        if self.fallback is None:
            return result
        try:
            fb = await self._rate_limited(
                lambda: self.fallback.fetch_related_parcels(cad_num)
            )
            return fb
        except Exception as e:  # noqa: BLE001
            log.warning(
                "related_fallback_failed",
                extra={"cad_num": cad_num, "err": type(e).__name__},
            )
            return result

    async def fetch_oks_in_bbox(
        self,
        bbox: tuple[float, float, float, float],
        *,
        layer: str = "building",
    ) -> list[CadastralObject]:
        """ОКС в прямоугольнике (minx, miny, maxx, maxy) EPSG:4326.

        Тонкая обёртка над get_features(): конвертирует bbox в Polygon и
        запрашивает указанный слой (по умолчанию «Здания»; для сооружений
        передайте layer='structure').
        """
        minx, miny, maxx, maxy = bbox
        if minx >= maxx or miny >= maxy:
            raise ValueError(f"invalid bbox: {bbox}")
        aoi = Polygon([
            (minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny),
        ])
        return await self.get_features(aoi, layer=layer)


# --- 8. CLI для быстрой проверки ----------------------------------------
async def _main_test(  # pragma: no cover
    cfg_path: str, cache_policy: CachePolicy = CachePolicy.USE
) -> None:
    import sys
    # Windows: stdout по умолчанию cp1251, а в логах есть "->".
    # Принудительно utf-8, чтобы print не падал с UnicodeEncodeError.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    import yaml

    cfg = yaml.safe_load(Path(cfg_path).read_text("utf-8"))
    n = cfg.get("nspd", {})
    cache = FileCache(
        Path(n.get("cache_dir", "data/cache/nspd")),
        ttl_days=int(n.get("cache_ttl_days", 7)),
    )
    async with AsyncNspd(trust_env=False) as nspd:
        primary = PynspdAdapter(nspd, layers=n.get("layers"))
        fallback = NspdRequestAdapter(
            grid_step_m=float(n.get("fallback_grid_step_m", 100.0)),
            max_points=int(n.get("fallback_max_points", 500)),
        )
        client = RobustCadastreClient(
            primary=primary,
            fallback=fallback,
            cache=cache,
            max_retries=int(n.get("max_retries", 3)),
            retry_backoff_sec=float(n.get("retry_backoff_sec", 1.0)),
            retry_backoff_min_sec=float(n.get("retry_backoff_min_sec", 2.0)),
            rate_limit_rps=float(n.get("rate_limit_rps", 1.5)),
            cache_policy=cache_policy,
        )
        for cad in ("61:44:0060958:173", "61:44:0000000:0"):
            obj = await client.fetch_parcel(cad)
            print(cad, "->", obj.model_dump(exclude={"geometry"}) if obj else None)
        summary = client.cache_summary()
        log.info("cache_summary %s", summary)
        if client.stats["hits"] > 0:
            log.info(
                "running_on_cache hits=%d misses=%d",
                client.stats["hits"], client.stats["misses"],
            )


if __name__ == "__main__":  # pragma: no cover
    import argparse

    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--test", action="store_true")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--use-cache", dest="cache_policy",
                     action="store_const", const=CachePolicy.USE)
    grp.add_argument("--no-cache", dest="cache_policy",
                     action="store_const", const=CachePolicy.NO)
    grp.add_argument("--refresh-cache", dest="cache_policy",
                     action="store_const", const=CachePolicy.REFRESH)
    ap.set_defaults(cache_policy=CachePolicy.USE)
    args = ap.parse_args()
    if args.test:
        asyncio.run(_main_test(args.config, args.cache_policy))