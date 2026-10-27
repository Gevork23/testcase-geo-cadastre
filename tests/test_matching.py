"""T9: тесты oks_effective_geometry (fallback ОКС без контура → участок)."""
from __future__ import annotations

from shapely.geometry import Point, Polygon

from src.matching import is_detection_unregistered, oks_effective_geometry
from src.nspd_models import CadastralObject


# --- хелперы ---------------------------------------------------------------

def _parcel(cad_num: str, coords: list[tuple[float, float]]) -> CadastralObject:
    """Участок с контуром (has_contour=True)."""
    poly = Polygon(coords)
    return CadastralObject(
        cad_num=cad_num,
        object_type="parcel",
        geometry=poly,
        has_contour=True,
    )


def _oks_with_contour(cad_num: str, coords: list[tuple[float, float]]) -> CadastralObject:
    poly = Polygon(coords)
    return CadastralObject(
        cad_num=cad_num,
        object_type="building",
        geometry=poly,
        has_contour=True,
    )


def _oks_without_contour(
    cad_num: str,
    *,
    parent_cad_num: str | None = None,
    attachment_point_wkt: str | None = None,
    related_cad_nums: list[str] | None = None,
    geometry: object | None = None,
) -> CadastralObject:
    """ОКС без контура: has_contour=False, geometry=None (по умолчанию)."""
    attrs: dict = {}
    if parent_cad_num is not None:
        attrs["options"] = {"parent_cad_num": parent_cad_num}
    if attachment_point_wkt is not None:
        attrs["attachment_point_wkt"] = attachment_point_wkt
    if related_cad_nums is not None:
        attrs["related_cad_nums"] = related_cad_nums
    return CadastralObject(
        cad_num=cad_num,
        object_type="building",
        geometry=geometry,  # type: ignore[arg-type]
        has_contour=False,
        attrs=attrs,
    )


# --- 1) ОКС со своим контуром: inherited=False, геометрия — его ------------------

def test_oks_with_contour_returns_own_geometry():
    oks = _oks_with_contour("61:44:1:1", [(0, 0), (0, 1), (1, 1), (1, 0)])
    parcels = {"61:44:9:9": _parcel("61:44:9:9", [(0, 0), (0, 5), (5, 5), (5, 0)])}

    geom, inherited = oks_effective_geometry(oks, parcels)

    assert geom is oks.geometry
    assert inherited is False


# --- 2) ОКС без контура + явная связь parent_cad_num -----------------------------

def test_oks_without_contour_uses_explicit_parent():
    parcel = _parcel("61:44:2:2", [(0, 0), (0, 10), (10, 10), (10, 0)])
    oks = _oks_without_contour("61:44:3:3", parent_cad_num="61:44:2:2")
    parcels = {"61:44:2:2": parcel}

    geom, inherited = oks_effective_geometry(oks, parcels)

    assert geom is parcel.geometry
    assert inherited is True


# --- 3) ОКС без контура + spatial по attachment_point_wkt -----------------------

def test_oks_without_contour_uses_spatial_attachment_point():
    parcel = _parcel("61:44:4:4", [(0, 0), (0, 10), (10, 10), (10, 0)])
    # точка внутри участка
    oks = _oks_without_contour(
        "61:44:5:5",
        attachment_point_wkt=Point(5, 5).wkt,
    )
    parcels = {"61:44:4:4": parcel}

    geom, inherited = oks_effective_geometry(oks, parcels)

    assert geom is parcel.geometry
    assert inherited is True


# --- 4) ОКС без контура, но участок не найден -----------------------------------

def test_oks_without_contour_no_parent_no_attachment_returns_none():
    oks = _oks_without_contour("61:44:6:6")
    parcels = {"61:44:4:4": _parcel("61:44:4:4", [(0, 0), (0, 1), (1, 1), (1, 0)])}

    geom, inherited = oks_effective_geometry(oks, parcels)

    assert geom is None
    assert inherited is False


def test_oks_without_contour_attachment_point_outside_any_parcel():
    oks = _oks_without_contour(
        "61:44:7:7",
        attachment_point_wkt=Point(100, 100).wkt,
    )
    parcels = {"61:44:4:4": _parcel("61:44:4:4", [(0, 0), (0, 1), (1, 1), (1, 0)])}

    geom, inherited = oks_effective_geometry(oks, parcels)

    assert geom is None
    assert inherited is False


# --- 5) Аномалия схемы: has_contour=False, но geometry есть ----------------------

def test_oks_has_contour_false_but_geometry_present_falls_back_to_geometry():
    # Например, НСПД вернул Point в geometry — но не как контур.
    # Полигон здесь — искусственная аномалия, чтобы проверить ветку 4.
    poly = Polygon([(0, 0), (0, 2), (2, 2), (2, 0)])
    oks = _oks_without_contour("61:44:8:8", geometry=poly)
    parcels: dict = {}

    geom, inherited = oks_effective_geometry(oks, parcels)

    assert geom is poly
    assert inherited is False


# --- 6) related_cad_nums: наследуем участок через related из tab_land_links ---

def test_oks_without_contour_uses_related_cad_nums():
    parcel = _parcel("61:44:2:2", [(0, 0), (0, 10), (10, 10), (10, 0)])
    oks = _oks_without_contour("61:44:3:3", related_cad_nums=["61:44:2:2"])
    parcels = {"61:44:2:2": parcel}

    geom, inherited = oks_effective_geometry(oks, parcels)

    assert geom is parcel.geometry
    assert inherited is True


# --- 7) related_cad_nums приоритетнее spatial (attachment_point_wkt) --------

def test_related_cad_nums_take_priority_over_spatial():
    # spatial по точке (5,5) нашёл бы parcel_a, но related указывает на parcel_b
    parcel_a = _parcel("61:44:A", [(0, 0), (0, 10), (10, 10), (10, 0)])
    parcel_b = _parcel("61:44:B", [(20, 20), (20, 30), (30, 30), (30, 20)])
    oks = _oks_without_contour(
        "61:44:3:3",
        attachment_point_wkt=Point(5, 5).wkt,
        related_cad_nums=["61:44:B"],
    )
    parcels = {"61:44:A": parcel_a, "61:44:B": parcel_b}

    geom, inherited = oks_effective_geometry(oks, parcels)

    assert geom is parcel_b.geometry
    assert inherited is True

# --- 9) NamedTuple: распаковка + атрибуты (README T9: "с флагом inherited_from_parcel") ---

def test_effective_geometry_has_named_attributes():
    oks = _oks_with_contour("61:44:1:1", [(0, 0), (0, 1), (1, 1), (1, 0)])
    result = oks_effective_geometry(oks, {})

    geom, inherited = result
    assert geom is oks.geometry
    assert inherited is False
    assert result.geometry is oks.geometry
    assert result.inherited_from_parcel is False


# --- 10-14) is_detection_unregistered: 5 кейсов (T9.7) ----------------------

def test_unregistered_when_no_oks_match():
    det = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    unreg, reason = is_detection_unregistered(det, [], {})
    assert unreg is True
    assert reason == "no_matching_oks"


def test_not_unregistered_when_matches_oks_contour():
    det = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    oks = _oks_with_contour("61:44:1:1", [(0, 0), (0, 2), (2, 2), (2, 0)])
    unreg, reason = is_detection_unregistered(det, [oks], {})
    assert unreg is False
    assert reason.startswith("matches_oks")


def test_not_unregistered_when_inside_inherited_parcel():
    """README T9: детекция внутри участка с ОКС без контура не флагуется."""
    parcel = _parcel("61:44:2:2", [(0, 0), (0, 10), (10, 10), (10, 0)])
    oks = _oks_without_contour("61:44:3:3", parent_cad_num="61:44:2:2")
    det = Polygon([(3, 3), (3, 4), (4, 4), (4, 3)])
    unreg, reason = is_detection_unregistered(det, [oks], {"61:44:2:2": parcel})
    assert unreg is False
    assert "inherited" in reason


def test_unregistered_when_oks_far_away():
    det = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    oks = _oks_with_contour("61:44:4:4", [(20, 20), (20, 21), (21, 21), (21, 20)])
    unreg, _ = is_detection_unregistered(det, [oks], {})
    assert unreg is True


def test_not_unregistered_when_buffered_match():
    det = Polygon([(0, 0.00001), (0, 1.00001), (1, 1.00001), (1, 0.00001)])
    oks = _oks_with_contour("61:44:5:5", [(0, 0), (0, 1), (1, 1), (1, 0)])
    unreg, reason = is_detection_unregistered(det, [oks], {}, buffer_deg=0.00002)
    assert unreg is False
    assert reason.startswith("matches_oks")

