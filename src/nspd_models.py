"""Внутренняя модель НСПД-объекта и публичный интерфейс загрузчика (T7)."""
from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

ObjectType = Literal["parcel", "building", "structure", "unfinished"]
Source = Literal["pynspd", "nspd_request"]


class CadastralObject(BaseModel):
    """Единый объект НСПД: участок / ОКС / сооружение / ОНС.

    Контракт geometry: Polygon/MultiPolygon или None. Point в НСПД означает
    привязку ОКС без контура (options.no_coords) и сохраняется в
    attrs["attachment_point_wkt"] — контуром не считается.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    cad_num: str | None = Field(
        default=None,
        description="Кадастровый номер (например '61:44:0060958:173') или None.",
    )
    object_type: ObjectType | None = Field(
        default=None,
        description="Нормализованный тип или None (схема НСПД не распознана).",
    )
    geometry: BaseGeometry | None = Field(
        default=None,
        description="Контур (Polygon/MultiPolygon) или None для ОКС без контура.",
    )
    area_cad_m2: float | None = Field(
        default=None,
        description="Площадь по кадастру (м²) или None.",
    )
    attrs: dict[str, Any] = Field(
        default_factory=dict,
        description="Сырые поля ответа (feat + properties + options).",
    )
    has_contour: bool = Field(
        default=False,
        description="True только для Polygon/MultiPolygon; Point → False.",
    )
    source: Source = Field(
        default="pynspd",
        description="Источник ответа: pynspd (primary) или nspd_request (fallback).",
    )


class CadastreLoader(Protocol):
    """Контракт загрузчика. AOI — Polygon/MultiPolygon в EPSG:4326."""

    async def fetch_parcel(self, cad_num: str) -> CadastralObject | None: ...

    async def get_features(
        self, aoi: Polygon | MultiPolygon, *, layer: str | None = None
    ) -> list[CadastralObject]: ...