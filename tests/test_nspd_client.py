"""T7: retry/404/has_contour + покрытие всех веток клиента НСПД."""
from __future__ import annotations

import asyncio
import builtins
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import shapely
from shapely.geometry import Point, Polygon

from src.nspd_cache import FileCache, cache_key_area, cache_key_point
from src.nspd_client import (
    _CACHE_SCHEMA,
    CachePolicy,
    NspdRequestAdapter,
    PynspdAdapter,
    RobustCadastreClient,
    _dump_model,
    _infer_type,
    _is_contour,
    _is_retryable,
    _obj_to_payload,
    _payload_to_obj,
    _to_object,
)
from src.nspd_models import CadastralObject


# ============================================================
# Хелперы
# ============================================================
def _fake_response(status: int = 500, message: str = "test") -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json={"message": message},
        request=httpx.Request("GET", "https://nspd.gov.ru/test"),
    )


def _make_exc(cls):
    """Pynspd-исключения бывают двух видов: с httpx.Response и без аргументов."""
    try:
        return cls()
    except TypeError:
        pass
    return cls(_fake_response())


class _StubOptions:
    def __init__(self, **kw):
        self._kw = kw

    def model_dump(self, **kwargs):
        return dict(self._kw)


class _StubProps:
    def __init__(self, options, category=None):
        self.options = options
        self.category = category

    def model_dump(self, **kwargs):
        return {}


class _StubGeom:
    def __init__(self, s):
        self._s = s

    def to_shape(self):
        return self._s


class _StubGeomBroken:
    def to_shape(self):
        raise RuntimeError("bad geometry")


class _StubFeat:
    def __init__(self, geometry, properties):
        self.geometry = geometry
        self.properties = properties

    def model_dump(self, **kwargs):
        return {}


def _poly():
    return Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])


def _client(tmp_path, fake, max_retries=3, fallback=None, layers=None):
    return RobustCadastreClient(
        primary=PynspdAdapter(fake, layers=layers),
        fallback=fallback,
        cache=FileCache(tmp_path),
        max_retries=max_retries,
        retry_backoff_sec=0.01,
        retry_backoff_min_sec=0.02,
        rate_limit_rps=100,
    )


# ============================================================
# 1. nspd_cache
# ============================================================
def test_cache_key_point_deterministic():
    k1 = cache_key_point("parcel", "61:44:0060958:173")
    k2 = cache_key_point("parcel", "61:44:0060958:173")
    k3 = cache_key_point("oks", "61:44:0060958:173")
    assert k1 == k2
    assert k1 != k3
    # Человекочитаемый ключ: kind_<sanitized_cad_num>. ':' недопустим на Windows.
    assert k1 == "parcel_61_44_0060958_173"
    assert ":" not in k1
    assert k3 == "oks_61_44_0060958_173"


def test_cache_key_area_precision_insensitive():
    poly1 = Polygon([(0.0000001, 0.0000001), (1, 0), (1, 1)])
    poly2 = Polygon([(0.0000002, 0.0000001), (1, 0), (1, 1)])
    assert cache_key_area("building", poly1) == cache_key_area("building", poly2)
    assert cache_key_area("building", poly1) != cache_key_area("parcel", poly1)


def test_file_cache_put_get_roundtrip(tmp_path):
    cache = FileCache(tmp_path)
    cache.put("k1", {"hello": "world"})
    assert cache.get("k1") == {"hello": "world"}
    assert cache.get("missing") is None


def test_file_cache_ttl_expired(tmp_path):
    cache = FileCache(tmp_path, ttl_days=7)
    cache.put("k1", {"x": 1})
    p = cache._path("k1")
    body = json.loads(p.read_text("utf-8"))
    body["created_at"] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    p.write_text(json.dumps(body), encoding="utf-8")
    assert cache.get("k1") is None


def test_file_cache_corrupted_json(tmp_path):
    cache = FileCache(tmp_path)
    p = cache._path("broken")
    p.write_text("{not json", encoding="utf-8")
    assert cache.get("broken") is None


# ============================================================
# 2. Примитивы
# ============================================================
def test_is_contour_only_polygons():
    assert _is_contour(None) is False
    assert _is_contour(Point(0, 0)) is False
    assert _is_contour(Polygon()) is False
    assert _is_contour(Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])) is True


def test_is_retryable_by_class():
    assert _is_retryable(httpx.TimeoutException("t")) is True
    assert _is_retryable(httpx.ConnectError("c")) is True
    assert _is_retryable(RuntimeError("boom")) is False


def test_is_retryable_pynspd_errors():
    from pynspd import errors as errs

    for cls_name, expected in [
        ("PynspdServerError", True),
        ("PynspdResponseError", True),
        ("TooManyRequests", True),
        ("NotFound", False),
        ("BlockedIP", False),
    ]:
        cls = getattr(errs, cls_name)
        exc = _make_exc(cls)
        assert _is_retryable(exc) is expected, cls_name


def test_infer_type_from_fields():
    assert _infer_type({"land_record_type": "x"}) == "parcel"
    assert _infer_type({"building_record_type": "x"}) == "building"
    assert _infer_type({"construction_record_type": "x"}) == "structure"
    assert _infer_type({"unfinished_record_type": "x"}) == "unfinished"


def test_infer_type_from_object_type_string():
    assert _infer_type({"object_type": "земельный участок"}) == "parcel"
    assert _infer_type({"object_type": "Здание"}) == "building"
    assert _infer_type({"object_type": "сооружение"}) == "structure"
    assert _infer_type({"object_type": "объект незавершенного"}) == "unfinished"
    assert _infer_type({}) is None


def test_infer_type_from_category():
    assert _infer_type(category=36369) == "building"
    assert _infer_type(category=36048) == "parcel"
    assert _infer_type(category=99999) is None


def test_dump_model_none_and_fail():
    assert _dump_model(None) == {}

    class Broken:
        def model_dump(self, **kw):
            raise RuntimeError("boom")

    assert _dump_model(Broken()) == {}


# ============================================================
# 3. _to_object
# ============================================================
def test_to_object_point_geom_is_not_contour():
    feat = _StubFeat(
        geometry=_StubGeom(Point(39.664, 47.204)),
        properties=_StubProps(
            _StubOptions(
                no_coords=True,
                cad_num="61:44:0060958:173",
                area=152,
                build_record_area=152,
            ),
            category=36369,
        ),
    )
    obj = _to_object(feat, "pynspd")
    assert obj.has_contour is False
    assert obj.geometry is None
    assert "attachment_point_wkt" in obj.attrs
    assert obj.cad_num == "61:44:0060958:173"
    assert obj.area_cad_m2 == 152.0
    assert obj.object_type == "building"


def test_to_object_polygon_geom_is_contour():
    poly = _poly()
    feat = _StubFeat(
        geometry=_StubGeom(poly),
        properties=_StubProps(
            _StubOptions(cad_num="x:y:z:1", build_record_area=50.0),
            category=36369,
        ),
    )
    obj = _to_object(feat, "pynspd")
    assert obj.has_contour is True
    assert obj.geometry is not None and obj.geometry.equals(poly)
    assert obj.area_cad_m2 == 50.0
    assert "attachment_point_wkt" not in obj.attrs


def test_to_object_no_geometry():
    feat = _StubFeat(
        geometry=None,
        properties=_StubProps(_StubOptions(cad_num="x:y:z:2")),
    )
    obj = _to_object(feat, "pynspd")
    assert obj.has_contour is False
    assert obj.geometry is None


def test_to_object_broken_geometry_does_not_crash():
    feat = _StubFeat(
        geometry=_StubGeomBroken(),
        properties=_StubProps(_StubOptions(cad_num="x:y:z:3")),
    )
    obj = _to_object(feat, "pynspd")
    assert obj.has_contour is False


# ============================================================
# 4. _obj_to_payload / _payload_to_obj
# ============================================================
def test_obj_payload_roundtrip_polygon():
    obj = CadastralObject(
        cad_num="x:y:z:1", object_type="parcel", geometry=_poly(),
        area_cad_m2=100.0, attrs={"foo": "bar"},
        has_contour=True, source="pynspd",
    )
    payload = _obj_to_payload(obj)
    assert payload["_v"] == _CACHE_SCHEMA
    back = _payload_to_obj(payload)
    assert back is not None
    assert back.has_contour is True
    assert back.cad_num == "x:y:z:1"
    assert back.area_cad_m2 == 100.0
    assert back.attrs["foo"] == "bar"


def test_payload_to_obj_rejects_old_schema():
    old = {"cad_num": "x:y:z:1", "geometry_wkb": None, "has_contour": False}
    assert _payload_to_obj(old) is None


def test_payload_to_obj_point_is_dropped():
    payload = {
        "_v": _CACHE_SCHEMA,
        "cad_num": "x:y:z:1",
        "object_type": "building",
        "geometry_wkb": shapely.to_wkb(Point(39.6, 47.2), hex=True),
        "area_cad_m2": 152.0,
        "attrs": {},
        "has_contour": True,
        "source": "pynspd",
    }
    obj = _payload_to_obj(payload)
    assert obj is not None
    assert obj.geometry is None
    assert obj.has_contour is False


# ============================================================
# 5. PynspdAdapter.get_features
# ============================================================
def test_pynspd_adapter_get_features_happy():
    async def _run():
        fake = MagicMock()

        async def _iter(aoi, layer_def):
            yield _StubFeat(
                geometry=_StubGeom(_poly()),
                properties=_StubProps(_StubOptions(cad_num="f:1:1:1"), category=36369),
            )

        fake.search_in_contour_iter = _iter
        adapter = PynspdAdapter(fake, layers=["building"])
        objs = await adapter.get_features(_poly())
        assert len(objs) == 1
        assert objs[0].cad_num == "f:1:1:1"

    asyncio.run(_run())


def test_pynspd_adapter_get_features_all_layers_fail():
    async def _run():
        fake = MagicMock()

        async def _iter_fail(aoi, layer_def):
            raise RuntimeError("down")
            yield

        fake.search_in_contour_iter = _iter_fail
        # оба alias валидны → _layer_def() пройдёт, упадёт только iter
        adapter = PynspdAdapter(fake, layers=["building", "land_record"])
        with pytest.raises(RuntimeError):
            await adapter.get_features(_poly())

    asyncio.run(_run())


# ============================================================
# 6. NspdRequestAdapter
# ============================================================
def test_nspd_request_adapter_not_installed(monkeypatch):
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "nspd_request" or name.startswith("nspd_request."):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    adapter = NspdRequestAdapter()
    assert adapter._ok is False
    assert asyncio.run(adapter.fetch_parcel("x:y:z:1")) is None
    assert asyncio.run(adapter.get_features(_poly())) == []


def test_nspd_request_adapter_happy(monkeypatch):
    fake_api = MagicMock()
    x, y = 4420000, 6000000  # Web Mercator, ~Ростовская обл.
    polygon_3857 = {
        "type": "Polygon",
        "coordinates": [[[x, y], [x + 100, y], [x + 100, y + 100],
                         [x, y + 100], [x, y]]],
    }
    fake_api.get_object_info.return_value = {
        "kad_number": "61:44:0060958:173",
        "object_type": "Здание",
        "data": {
            "features": [{
                "geometry": polygon_3857,
                "properties": {"options": {
                    "cad_num": "61:44:0060958:173",
                    "build_record_area": 152,
                }},
            }],
        },
    }
    fake_module = MagicMock()
    fake_module.NSPD.return_value = fake_api
    monkeypatch.setitem(sys.modules, "nspd_request", fake_module)

    adapter = NspdRequestAdapter()
    assert adapter._ok is True
    obj = asyncio.run(adapter.fetch_parcel("61:44:0060958:173"))
    assert obj is not None
    assert obj.cad_num == "61:44:0060958:173"
    assert obj.object_type == "building"
    assert obj.area_cad_m2 == 152.0
    assert obj.has_contour is True
    assert obj.source == "nspd_request"


def test_nspd_request_adapter_no_features(monkeypatch):
    fake_api = MagicMock()
    fake_api.get_object_info.return_value = {"kad_number": "x", "data": {"features": []}}
    fake_module = MagicMock()
    fake_module.NSPD.return_value = fake_api
    monkeypatch.setitem(sys.modules, "nspd_request", fake_module)

    adapter = NspdRequestAdapter()
    assert asyncio.run(adapter.fetch_parcel("x:y:z:1")) is None


# ============================================================
# 7. RobustCadastreClient.fetch_parcel
# ============================================================
def test_timeout_retries_and_no_crash(tmp_path):
    async def _run():
        fake = AsyncMock()
        fake.find = AsyncMock(side_effect=httpx.TimeoutException("boom"))
        client = _client(tmp_path, fake)
        result = await client.fetch_parcel("61:44:0060958:173")
        assert result is None
        assert fake.find.await_count == 3
        assert client.cache.get(cache_key_point("parcel", "61:44:0060958:173")) is None

    asyncio.run(_run())


def test_not_found_is_not_retried_and_is_cached(tmp_path):
    async def _run():
        fake = AsyncMock()
        fake.find = AsyncMock(return_value=None)
        client = _client(tmp_path, fake)
        assert await client.fetch_parcel("61:44:9999999:999") is None
        assert fake.find.await_count == 1
        assert client.cache.get(cache_key_point("parcel", "61:44:9999999:999")) == {
            "ok": False, "error": "not_found",
        }

    asyncio.run(_run())


def test_fetch_parcel_cache_hit_valid(tmp_path):
    async def _run():
        obj = CadastralObject(
            cad_num="a:b:c:1", object_type="parcel", geometry=_poly(),
            area_cad_m2=100.0, has_contour=True, source="pynspd",
        )
        cache = FileCache(tmp_path)
        cache.put(
            cache_key_point("parcel", "a:b:c:1"),
            {"ok": True, "obj": _obj_to_payload(obj)},
        )
        fake = AsyncMock()
        client = RobustCadastreClient(
            primary=PynspdAdapter(fake), fallback=None, cache=cache,
            max_retries=3, retry_backoff_sec=0.01, retry_backoff_min_sec=0.02,
            rate_limit_rps=100,
        )
        got = await client.fetch_parcel("a:b:c:1")
        assert got is not None and got.cad_num == "a:b:c:1"
        assert fake.find.await_count == 0

    asyncio.run(_run())


# ============================================================
# 8. RobustCadastreClient.get_features
# ============================================================
def test_get_features_cache_hit_valid(tmp_path):
    async def _run():
        obj = CadastralObject(
            cad_num="x:y:z:1", object_type="building",
            geometry=Polygon([(0, 0), (1, 0), (1, 1)]),
            area_cad_m2=50.0, has_contour=True, source="pynspd",
        )
        cache = FileCache(tmp_path)
        cache.put(cache_key_area("all", _poly()), [_obj_to_payload(obj)])
        fake = AsyncMock()
        client = RobustCadastreClient(
            primary=PynspdAdapter(fake), fallback=None, cache=cache,
            max_retries=3, retry_backoff_sec=0.01, retry_backoff_min_sec=0.02,
            rate_limit_rps=100,
        )
        objs = await client.get_features(_poly())
        assert len(objs) == 1
        assert objs[0].cad_num == "x:y:z:1"

    asyncio.run(_run())


def test_get_features_cache_stale_schema_refetch(tmp_path):
    async def _run():
        cache = FileCache(tmp_path)
        cache.put(cache_key_area("all", _poly()), [
            {"cad_num": "old", "geometry_wkb": None, "has_contour": False},
        ])

        async def _iter(aoi, layer_def):
            yield _StubFeat(
                geometry=_StubGeom(Polygon([(0, 0), (1, 0), (1, 1)])),
                properties=_StubProps(_StubOptions(cad_num="new:1:1:1"), category=36369),
            )

        fake = MagicMock()
        fake.search_in_contour_iter = _iter
        client = RobustCadastreClient(
            primary=PynspdAdapter(fake, layers=["building"]),
            fallback=None, cache=cache,
            max_retries=3, retry_backoff_sec=0.01, retry_backoff_min_sec=0.02,
            rate_limit_rps=100,
        )
        objs = await client.get_features(_poly(), layer="building")
        assert len(objs) == 1
        assert objs[0].cad_num == "new:1:1:1"

    asyncio.run(_run())


def test_get_features_cache_miss_writes(tmp_path):
    async def _run():
        async def _iter(aoi, layer_def):
            yield _StubFeat(
                geometry=_StubGeom(Polygon([(0, 0), (1, 0), (1, 1)])),
                properties=_StubProps(_StubOptions(cad_num="m:1:1:1"), category=36369),
            )

        fake = MagicMock()
        fake.search_in_contour_iter = _iter
        client = RobustCadastreClient(
            primary=PynspdAdapter(fake, layers=["building"]),
            fallback=None, cache=FileCache(tmp_path),
            max_retries=3, retry_backoff_sec=0.01, retry_backoff_min_sec=0.02,
            rate_limit_rps=100,
        )
        objs = await client.get_features(_poly(), layer="building")
        assert len(objs) == 1
        cached = client.cache.get(cache_key_area("building", _poly()))
        assert cached is not None and len(cached) == 1

    asyncio.run(_run())


def test_get_features_error_returns_empty(tmp_path):
    async def _run():
        async def _iter_fail(aoi, layer_def):
            raise RuntimeError("down")
            yield

        fake = MagicMock()
        fake.search_in_contour_iter = _iter_fail
        client = RobustCadastreClient(
            primary=PynspdAdapter(fake, layers=["building"]),
            fallback=None, cache=FileCache(tmp_path),
            max_retries=1, retry_backoff_sec=0.01, retry_backoff_min_sec=0.02,
            rate_limit_rps=100,
        )
        objs = await client.get_features(_poly(), layer="building")
        assert objs == []

    asyncio.run(_run())


# ============================================================
# 9. Каскад fallback
# ============================================================
def test_cascade_fallback_success(tmp_path):
    async def _run():
        from pynspd.errors import PynspdResponseError

        primary_fake = AsyncMock()
        primary_fake.find = AsyncMock(side_effect=_make_exc(PynspdResponseError))
        fallback_obj = CadastralObject(
            cad_num="x:y:z:1", object_type="building",
            geometry=None, area_cad_m2=50.0, has_contour=False,
            source="nspd_request",
        )
        fallback = AsyncMock()
        fallback.fetch_parcel = AsyncMock(return_value=fallback_obj)

        client = RobustCadastreClient(
            primary=PynspdAdapter(primary_fake), fallback=fallback,
            cache=FileCache(tmp_path),
            max_retries=1, retry_backoff_sec=0.01, retry_backoff_min_sec=0.02,
            rate_limit_rps=100,
        )
        obj = await client.fetch_parcel("x:y:z:1")
        assert obj is not None
        assert obj.source == "nspd_request"
        assert fallback.fetch_parcel.await_count == 1

    asyncio.run(_run())


def test_cascade_fallback_fails(tmp_path):
    async def _run():
        from pynspd.errors import PynspdResponseError

        primary_fake = AsyncMock()
        primary_fake.find = AsyncMock(side_effect=_make_exc(PynspdResponseError))
        fallback = AsyncMock()
        fallback.fetch_parcel = AsyncMock(side_effect=RuntimeError("also down"))

        client = RobustCadastreClient(
            primary=PynspdAdapter(primary_fake), fallback=fallback,
            cache=FileCache(tmp_path),
            max_retries=1, retry_backoff_sec=0.01, retry_backoff_min_sec=0.02,
            rate_limit_rps=100,
        )
        obj = await client.fetch_parcel("x:y:z:1")
        assert obj is None
        assert client.cache.get(cache_key_point("parcel", "x:y:z:1")) is None

    asyncio.run(_run())


# ============================================================
# 10. Live
# ============================================================
@pytest.mark.skipif(
    os.getenv("NSPD_LIVE") != "1", reason="set NSPD_LIVE=1 to hit live NSPD"
)
def test_live_no_coords_object_has_no_contour(tmp_path):
    from pynspd import AsyncNspd

    async def _run():
        async with AsyncNspd(trust_env=False) as nspd:
            client = RobustCadastreClient(
                primary=PynspdAdapter(nspd),
                fallback=None, cache=FileCache(tmp_path),
                max_retries=2, retry_backoff_sec=0.5, retry_backoff_min_sec=0.5,
                rate_limit_rps=1.5,
            )
            obj = await client.fetch_parcel("61:44:0060958:173")
            assert obj is not None
            assert obj.object_type == "building"
            assert obj.area_cad_m2 == 152.0
            assert obj.has_contour is False
            assert obj.geometry is None
            assert "attachment_point_wkt" in obj.attrs

    asyncio.run(_run())


# ============================================================
# 11. Хвосты T7: unknown fields, source в логе, shapely.transform,
#     площадной fallback nspd-request
# ============================================================
import logging


def test_unknown_option_field_logged_once(caplog):
    from src.nspd_client import (
        _UNKNOWN_OPTION_FIELDS_SEEN,
        _log_unknown_options,
    )
    _UNKNOWN_OPTION_FIELDS_SEEN.clear()

    with caplog.at_level(logging.WARNING, logger="nspd_client"):
        _log_unknown_options({"cad_num": "x", "brand_new_field": 1}, "pynspd")
        _log_unknown_options({"cad_num": "y", "brand_new_field": 2}, "pynspd")
        _log_unknown_options({"cad_num": "z", "another_new_field": 3}, "pynspd")

    events = [r for r in caplog.records if r.message == "unknown_option_field"]
    # 2 события: brand_new_field (1-й раз), another_new_field
    assert len(events) == 2
    assert events[0].fields == ["brand_new_field"]
    assert events[1].fields == ["another_new_field"]


def test_unknown_options_do_not_fail_to_object(caplog):
    """Фича: незнакомое поле options не роняет парсер, а логируется."""
    from src.nspd_client import _UNKNOWN_OPTION_FIELDS_SEEN
    _UNKNOWN_OPTION_FIELDS_SEEN.clear()

    feat = _StubFeat(
        geometry=_StubGeom(_poly()),
        properties=_StubProps(
            _StubOptions(cad_num="x:y:z:1", brand_new_attr="val"),
            category=36369,
        ),
    )
    with caplog.at_level(logging.WARNING, logger="nspd_client"):
        obj = _to_object(feat, "pynspd")

    assert obj.cad_num == "x:y:z:1"
    assert "brand_new_attr" in obj.attrs  # поле сохранено в attrs
    assert any(r.message == "unknown_option_field" for r in caplog.records)


def test_nspd_call_logs_source(caplog):
    """Фича: nspd_call содержит source='pynspd' в extra."""
    async def _run():
        fake = AsyncMock()
        fake.find = AsyncMock(return_value=None)
        client = _client(tmp_path, fake)
        with caplog.at_level(logging.INFO, logger="nspd_client"):
            await client.fetch_parcel("61:44:9999999:999")

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        tmp_path = Path(d)
        asyncio.run(_run())

    nspd_calls = [r for r in caplog.records if r.message == "nspd_call"]
    assert nspd_calls, "nspd_call должен быть в логе"
    for rec in nspd_calls:
        assert getattr(rec, "source", None) == "pynspd"


def test_reproject_3857_to_4326_no_deprecation():
    """Фича: shapely.transform вместо shapely.ops.transform → нет DeprecationWarning."""
    import warnings
    from src.nspd_client import _reproject_3857_to_4326

    x, y = 4420000, 6000000  # Web Mercator
    poly_3857 = Polygon([
        (x, y), (x + 100, y), (x + 100, y + 100), (x, y + 100), (x, y),
    ])
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        result = _reproject_3857_to_4326(poly_3857)

    assert result.geom_type == "Polygon"
    minx, miny, _, _ = result.bounds
    # после пересчёта — градусы, а не миллионы
    assert -180 <= minx <= 180
    assert -90 <= miny <= 90


def test_nspd_request_adapter_area_fallback(monkeypatch, caplog):
    """Площадной fallback: сетка → cad_num → get_object_info → объект."""
    from pyproj import Transformer
    from shapely.geometry import box as shapely_box

    x, y = 4420000, 6000000
    poly_3857 = {
        "type": "Polygon",
        "coordinates": [[[x, y], [x + 100, y], [x + 100, y + 100],
                         [x, y + 100], [x, y]]],
    }
    fake_api = MagicMock()
    # by_coordinates возвращают строку cad_num (как в реальном API)
    fake_api.get_land_plot_by_coordinates.return_value = "61:44:0060958:100"
    fake_api.get_oks_by_coordinates.return_value = "61:44:0060958:200"
    # get_object_info возвращает полный GeoJSON (как в реальном API)
    def _obj_info(cad_num, **kw):
        return {
            "data": {
                "type": "FeatureCollection",
                "features": [{
                    "geometry": poly_3857,
                    "properties": {
                        "category": 36368 if cad_num.endswith("100") else 36369,
                        "options": {
                            "cad_num": cad_num,
                            "area": 1000 if cad_num.endswith("100") else 152,
                        },
                    },
                }],
            }
        }
    fake_api.get_object_info.side_effect = _obj_info

    fake_module = MagicMock()
    fake_module.NSPD.return_value = fake_api
    monkeypatch.setitem(sys.modules, "nspd_request", fake_module)

    from src.nspd_client import NspdRequestAdapter
    adapter = NspdRequestAdapter(grid_step_m=300.0, max_points=50)

    t = Transformer.from_crs(3857, 4326, always_xy=True)
    lon_c, lat_c = t.transform(x + 50, y + 50)
    aoi = shapely_box(lon_c - 0.01, lat_c - 0.01, lon_c + 0.01, lat_c + 0.01)

    with caplog.at_level(logging.INFO, logger="nspd_client"):
        objs = asyncio.run(adapter.get_features(aoi))

    cad_nums = {o.cad_num for o in objs}
    assert cad_nums == {"61:44:0060958:100", "61:44:0060958:200"}
    assert all(o.source == "nspd_request" for o in objs)
    assert all(o.has_contour for o in objs)
    # get_object_info вызывался для каждого уникального cad_num
    calls = {c.args[0] for c in fake_api.get_object_info.call_args_list}
    assert calls == {"61:44:0060958:100", "61:44:0060958:200"}
    assert any(r.message == "nspd_request_area_done" for r in caplog.records)


# ============================================================
# 12. Внутренние ветки площадного fallback (T7.1)
# ============================================================
from src.nspd_client import _parse_nspd_request_features


def test_parse_nspd_features_formats():
    """Все три формата payload: dict с features, dict-единичный, list, мусор."""
    poly_3857 = {
        "type": "Polygon",
        "coordinates": [[[4420000, 6000000], [4420100, 6000000],
                         [4420100, 6000100], [4420000, 6000100],
                         [4420000, 6000000]]],
    }
    feat = {
        "geometry": poly_3857,
        "properties": {"options": {"cad_num": "x:y:z:1", "build_record_area": 50}},
    }

    # 1. dict с "features"
    out = _parse_nspd_request_features({"features": [feat]}, "nspd_request")
    assert len(out) == 1 and out[0].cad_num == "x:y:z:1"

    # 2. dict с "data.features"
    out = _parse_nspd_request_features({"data": {"features": [feat]}}, "nspd_request")
    assert len(out) == 1

    # 3. dict-единичный (без features)
    out = _parse_nspd_request_features(feat, "nspd_request")
    assert len(out) == 1 and out[0].cad_num == "x:y:z:1"

    # 4. list[dict]
    out = _parse_nspd_request_features([feat], "nspd_request")
    assert len(out) == 1

    # 5. мусор
    assert _parse_nspd_request_features(None, "nspd_request") == []
    assert _parse_nspd_request_features("garbage", "nspd_request") == []
    assert _parse_nspd_request_features({"error": "x"}, "nspd_request") == []


def test_nspd_request_fetch_parcel_error_payload(monkeypatch):
    """fetch_parcel: payload с error / пустой → None (без exception)."""
    fake_api = MagicMock()
    fake_api.get_object_info.return_value = {"error": "not found"}
    fake_module = MagicMock()
    fake_module.NSPD.return_value = fake_api
    monkeypatch.setitem(sys.modules, "nspd_request", fake_module)

    from src.nspd_client import NspdRequestAdapter
    adapter = NspdRequestAdapter()
    assert asyncio.run(adapter.fetch_parcel("x:y:z:1")) is None

    fake_api.get_object_info.return_value = None
    assert asyncio.run(adapter.fetch_parcel("x:y:z:1")) is None


def test_nspd_request_area_empty_and_oversized(caplog, monkeypatch):
    """get_features: пустой AOI → []; превышение max_points → [] + WARNING."""
    fake_api = MagicMock()
    fake_module = MagicMock()
    fake_module.NSPD.return_value = fake_api
    monkeypatch.setitem(sys.modules, "nspd_request", fake_module)

    from src.nspd_client import NspdRequestAdapter

    # пустой AOI (нулевой bbox) — _iter_grid_points ничего не даст
    adapter = NspdRequestAdapter(grid_step_m=100.0, max_points=10)
    empty_aoi = Polygon([(39.66, 47.20), (39.66, 47.20), (39.66, 47.20)])
    with caplog.at_level(logging.WARNING, logger="nspd_client"):
        objs = asyncio.run(adapter.get_features(empty_aoi))
    assert objs == []

    # большой AOI > max_points
    big_aoi = Polygon([
        (39.0, 47.0), (40.0, 47.0), (40.0, 48.0), (39.0, 48.0), (39.0, 47.0),
    ])
    with caplog.at_level(logging.WARNING, logger="nspd_client"):
        objs = asyncio.run(adapter.get_features(big_aoi))
    assert objs == []
    assert any(r.message == "nspd_request_area_too_big" for r in caplog.records)


def test_nspd_request_fetch_by_point_layer_filter(monkeypatch):
    """_fetch_by_point: layer='land_record' не дергает get_oks; layer='building' — не get_land_plot."""
    fake_api = MagicMock()
    fake_api.get_land_plot_by_coordinates.return_value = None
    fake_api.get_oks_by_coordinates.return_value = None
    fake_module = MagicMock()
    fake_module.NSPD.return_value = fake_api
    monkeypatch.setitem(sys.modules, "nspd_request", fake_module)

    from src.nspd_client import NspdRequestAdapter
    adapter = NspdRequestAdapter()

    asyncio.run(adapter._fetch_by_point(39.66, 47.20, "land_record"))
    assert fake_api.get_land_plot_by_coordinates.called
    assert not fake_api.get_oks_by_coordinates.called

    fake_api.get_land_plot_by_coordinates.reset_mock()
    fake_api.get_oks_by_coordinates.reset_mock()

    asyncio.run(adapter._fetch_by_point(39.66, 47.20, "building"))
    assert not fake_api.get_land_plot_by_coordinates.called
    assert fake_api.get_oks_by_coordinates.called

def test_fetch_oks_in_bbox_wraps_get_features(tmp_path):
    """fetch_oks_in_bbox: bbox → Polygon → get_features(layer='building')."""
    async def _run():
        async def _iter(aoi, layer_def):
            # один ОКС в bbox
            yield _StubFeat(
                geometry=_StubGeom(Polygon([(0.5, 0.5), (0.6, 0.5), (0.6, 0.6)])),
                properties=_StubProps(_StubOptions(cad_num="x:y:z:1"), category=36369),
            )

        fake = MagicMock()
        fake.search_in_contour_iter = _iter
        client = RobustCadastreClient(
            primary=PynspdAdapter(fake, layers=["building"]),
            fallback=None, cache=FileCache(tmp_path),
            max_retries=1, retry_backoff_sec=0.01, retry_backoff_min_sec=0.02,
            rate_limit_rps=100,
        )
        objs = await client.fetch_oks_in_bbox((0.0, 0.0, 1.0, 1.0))
        assert len(objs) == 1
        assert objs[0].cad_num == "x:y:z:1"
        assert objs[0].object_type == "building"

    asyncio.run(_run())


def test_fetch_oks_in_bbox_invalid(tmp_path):
    async def _run():
        fake = AsyncMock()
        client = _client(tmp_path, fake)
        with pytest.raises(ValueError, match="invalid bbox"):
            await client.fetch_oks_in_bbox((1.0, 0.0, 0.0, 1.0))  # minx >= maxx
        with pytest.raises(ValueError, match="invalid bbox"):
            await client.fetch_oks_in_bbox((0.0, 1.0, 1.0, 0.0))  # miny >= maxy

    asyncio.run(_run())

@pytest.mark.skipif(os.getenv("NSPD_LIVE") != "1", reason="NSPD_LIVE=1")
def test_live_cascade_pynspd_falls_back_to_nspd_request(tmp_path):
    """pynspd бросает PynspdServerError → nspd-request отдаёт объект."""
    from pynspd import AsyncNspd
    from pynspd.errors import PynspdServerError
    from src.nspd_client import PynspdAdapter, NspdRequestAdapter, RobustCadastreClient

    async def _run():
        async with AsyncNspd(trust_env=False) as nspd:
            primary = PynspdAdapter(nspd)

            async def _fail(*a, **kw):
                raise PynspdServerError(_fake_response(500))

            primary.fetch_parcel = _fail  # type: ignore[method-assign]

            fallback = NspdRequestAdapter()
            assert fallback._ok, "nspd-request должен быть установлен"

            client = RobustCadastreClient(
                primary=primary, fallback=fallback,
                cache=FileCache(tmp_path),
                max_retries=1, retry_backoff_sec=0.1, retry_backoff_min_sec=0.1,
                rate_limit_rps=5.0,
            )
            obj = await client.fetch_parcel("61:46:0010601:5789")
            assert obj is not None, "каскад должен вернуть объект через nspd-request"
            assert obj.source == "nspd_request"
            assert obj.cad_num == "61:46:0010601:5789"
            assert obj.geometry is not None, "должна быть геометрия"
            assert obj.has_contour is True

    asyncio.run(_run())

def test_parse_nspd_request_real_format_from_probe():
    """Реальный ответ get_object_info (probe 2026-10-25) — парсится корректно."""
    from src.nspd_client import _parse_nspd_request_features

    payload = {
        "data": {
            "type": "FeatureCollection",
            "features": [{
                "id": 105366990,
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [4421092.63, 5966225.87],
                        [4421079.80, 5966252.37],
                        [4421067.28, 5966245.93],
                        [4421092.63, 5966225.87],
                    ]],
                    "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
                },
                "properties": {
                    "cadastralDistrictsCode": 61,
                    "category": 36368,
                    "categoryName": "Земельные участки ЕГРН",
                    "descr": "61:46:0010601:5789",
                    "externalKey": "61:46:0010601:5789",
                    "label": "61:46:0010601:5789",
                    "options": {
                        "area": None,
                        "cad_num": "61:46:0010601:5789",
                        "cost_value": 7541082.12,
                    },
                },
            }],
        }
    }
    objs = _parse_nspd_request_features(payload, "nspd_request")
    assert len(objs) == 1
    obj = objs[0]
    assert obj.cad_num == "61:46:0010601:5789"
    assert obj.object_type == "parcel"
    assert obj.source == "nspd_request"
    assert obj.has_contour is True
    assert obj.geometry is not None
    # attrs содержит данные с верхнего уровня properties
    assert obj.attrs["descr"] == "61:46:0010601:5789"
    assert obj.attrs["externalKey"] == "61:46:0010601:5789"
    # геометрия перепроецирована — координаты в градусах, не миллионы
    minx, miny, _, _ = obj.geometry.bounds
    assert -180 <= minx <= 180 and -90 <= miny <= 90


def test_parse_nspd_request_string_payload(caplog):
    """Строка от get_land_plot_by_coordinates → [] + INFO в логе."""
    from src.nspd_client import _parse_nspd_request_features
    with caplog.at_level(logging.INFO, logger="nspd_client"):
        objs = _parse_nspd_request_features("61:44:0060958:9", "nspd_request")
    assert objs == []
    assert any(r.message == "nspd_request_cadnum_only" for r in caplog.records)

@pytest.mark.skipif(os.getenv("NSPD_LIVE") != "1", reason="NSPD_LIVE=1")
def test_live_nspd_request_area_small():
    """Площадной fallback вживую: layer='land_record' на AOI вокруг 61:44:0060958.

    Ожидаем: > 0 участков (у центральной точки точно есть 61:44:0060958:9
    по probe от 2026-10-25). Тест доказывает, что fallback работает
    для объектов С КОНТУРОМ. ОКС без контура в этом AOI не находятся —
    это известное ограничение nspd-request (см. DECISIONS §T7).
    """
    from src.nspd_client import NspdRequestAdapter

    async def _run():
        adapter = NspdRequestAdapter(grid_step_m=300, max_points=20)
        assert adapter._ok

        aoi = Polygon([
            (39.660, 47.202), (39.668, 47.202),
            (39.668, 47.208), (39.660, 47.208),
            (39.660, 47.202),
        ])
        objs = await adapter.get_features(aoi, layer="land_record")
        assert isinstance(objs, list)
        # Доказательство: центральная точка AOI — в участке 61:44:0060958:9
        assert len(objs) > 0, "land_record fallback должен найти хотя бы участок"
        assert any(o.cad_num and o.cad_num.startswith("61:44:0060958") for o in objs)
        print(f"\n[live] land_record fallback: {len(objs)} участков")
        for o in objs[:3]:
            print(f"  {o.cad_num} {o.object_type} contour={o.has_contour}")

    asyncio.run(_run())

# ============================================================
# 12. T8.1: CachePolicy + session stats
# ============================================================
def _stub_feat_polygon(cad_num: str, category: int = 36369):
    return _StubFeat(
        geometry=_StubGeom(Polygon([(0, 0), (1, 0), (1, 1), (0, 0)])),
        properties=_StubProps(_StubOptions(cad_num=cad_num), category=category),
    )


def test_cache_policy_no_cache_ignores_cache(tmp_path):
    async def _run():
        obj = CadastralObject(
            cad_num="a:b:c:1", object_type="parcel", geometry=_poly(),
            area_cad_m2=100.0, has_contour=True, source="pynspd",
        )
        cache = FileCache(tmp_path)
        key = cache_key_point("parcel", "a:b:c:1")
        cache.put(key, {"ok": True, "obj": _obj_to_payload(obj)})
        before = cache.get(key)

        fake = AsyncMock()
        fake.find = AsyncMock(return_value=None)  # NotFound → None
        client = RobustCadastreClient(
            primary=PynspdAdapter(fake), fallback=None, cache=cache,
            max_retries=1, retry_backoff_sec=0.01, retry_backoff_min_sec=0.02,
            rate_limit_rps=100, cache_policy=CachePolicy.NO,
        )
        assert await client.fetch_parcel("a:b:c:1") is None
        # HTTP пошёл, несмотря на валидный кэш
        assert fake.find.await_count == 1
        # кэш не пишется (остался прежний payload)
        assert cache.get(key) == before
        assert client.stats == {
            "hits": 0, "misses": 0, "stale": 0, "writes": 0, "errors": 0,
        }

    asyncio.run(_run())


def test_cache_policy_refresh_cache_reads_network_writes(tmp_path):
    async def _run():
        cache = FileCache(tmp_path)
        key = cache_key_point("parcel", "a:b:c:1")
        cache.put(key, {"ok": False, "error": "not_found"})

        fake = AsyncMock()
        fake.find = AsyncMock(return_value=_stub_feat_polygon("a:b:c:1"))
        client = RobustCadastreClient(
            primary=PynspdAdapter(fake), fallback=None, cache=cache,
            max_retries=1, retry_backoff_sec=0.01, retry_backoff_min_sec=0.02,
            rate_limit_rps=100, cache_policy=CachePolicy.REFRESH,
        )
        got = await client.fetch_parcel("a:b:c:1")
        assert got is not None and got.cad_num == "a:b:c:1"
        assert fake.find.await_count == 1
        assert client.stats["hits"] == 0
        assert client.stats["writes"] == 1
        # кэш перезаписан с not_found на ok
        fresh = cache.get(key)
        assert fresh is not None and fresh.get("ok") is True

    asyncio.run(_run())


def test_cache_stats_hits_misses(tmp_path):
    async def _run():
        cache = FileCache(tmp_path)
        fake = AsyncMock()
        fake.find = AsyncMock(return_value=_stub_feat_polygon("a:b:c:1"))
        client = RobustCadastreClient(
            primary=PynspdAdapter(fake), fallback=None, cache=cache,
            max_retries=1, retry_backoff_sec=0.01, retry_backoff_min_sec=0.02,
            rate_limit_rps=100,  # default USE
        )
        # 1) пустой кэш → miss + write
        assert await client.fetch_parcel("a:b:c:1") is not None
        # 2) тёплый кэш → hit
        assert await client.fetch_parcel("a:b:c:1") is not None
        # 3) payload со старой схемой → _payload_to_obj отбрасывает → stale + refetch + write
        cache.put(
            cache_key_point("parcel", "a:b:c:1"),
            {"ok": True, "obj": {"_v": 1, "cad_num": "a:b:c:1"}},
        )
        assert await client.fetch_parcel("a:b:c:1") is not None

        assert client.stats == {
            "hits": 1, "misses": 1, "stale": 1, "writes": 2, "errors": 0,
        }
        assert fake.find.await_count == 2  # 1-й miss + 3-й stale

    asyncio.run(_run())


def test_cache_miss_with_network_error_logs_explicit(tmp_path, caplog):
    async def _run():
        caplog.set_level("ERROR", logger="nspd_client")

        # fetch_parcel: пустой кэш + сеть упала
        fake1 = AsyncMock()
        fake1.find = AsyncMock(side_effect=RuntimeError("down"))
        client1 = RobustCadastreClient(
            primary=PynspdAdapter(fake1), fallback=None,
            cache=FileCache(tmp_path / "p1"),
            max_retries=1, retry_backoff_sec=0.01, retry_backoff_min_sec=0.02,
            rate_limit_rps=100,
        )
        assert await client1.fetch_parcel("a:b:c:1") is None
        assert client1.stats["errors"] == 1

        # get_features: пустой кэш + сеть упала
        async def _iter_fail(aoi, layer_def):
            raise RuntimeError("down")
            yield  # pragma: no cover

        fake2 = MagicMock()
        fake2.search_in_contour_iter = _iter_fail
        client2 = RobustCadastreClient(
            primary=PynspdAdapter(fake2, layers=["building"]), fallback=None,
            cache=FileCache(tmp_path / "p2"),
            max_retries=1, retry_backoff_sec=0.01, retry_backoff_min_sec=0.02,
            rate_limit_rps=100,
        )
        assert await client2.get_features(_poly(), layer="building") == []
        assert client2.stats["errors"] == 1

    asyncio.run(_run())

    # ERROR-лог «cache_miss_on_network_error» сработал в обоих путях
    msgs = [
        r.getMessage() for r in caplog.records if r.levelname == "ERROR"
    ]
    assert msgs.count("cache_miss_on_network_error") == 2