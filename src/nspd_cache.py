"""Файловый кэш НСПД: точечные и площадные ключи, TTL, атомарная запись."""
from __future__ import annotations

import hashlib
import re
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import shapely
from shapely.geometry.base import BaseGeometry

log = logging.getLogger("nspd.cache")


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


_WINDOWS_FORBIDDEN = re.compile(r'[:/\\*?"<>|]')


def _sanitize(s: str) -> str:
    """Заменяет недопустимые в Windows символы в имени файла.

    ТЗ требует `{cad_num}.json`, но cad_num содержит ':' (например,
    `61:44:0060958:173`), а ':' на Windows недопустим в путях — заменяем на '_'.
    """
    return _WINDOWS_FORBIDDEN.sub("_", s)


def cache_key_point(kind: str, query: str) -> str:
    """kind ∈ {parcel, oks}, query = cad_num.

    Читаемый ключ вида `parcel_61_44_0060958_173` — удобно смотреть
    `ls data/cache/nspd/`, а не 40-символьные хэши.
    """
    return f"{kind}_{_sanitize(query)}"


def cache_key_area(layer: str, aoi: BaseGeometry) -> str:
    """Нормализуем геометрию до ~0.1 м, чтобы 4-й знак не плодил новые ключи."""
    wkb_hex = shapely.to_wkb(
        shapely.set_precision(aoi, grid_size=1e-6), hex=True
    )
    return f"area_{_sanitize(layer)}_{_sha1(wkb_hex)}"


class FileCache:
    """Файловый кэш в data/cache/nspd/{sha1}.json.

    Атомарная запись через tempfile + os.replace — защита от corrupted JSON,
    если два процесса пишут в один ключ.
    """

    def __init__(self, root: Path, ttl_days: int = 7) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl = timedelta(days=ttl_days)

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> Any | None:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text("utf-8"))
            created = datetime.fromisoformat(data["created_at"])
        except (ValueError, KeyError, OSError) as e:
            log.warning("cache_read_failed", extra={"key": key, "err": str(e)})
            return None
        age = datetime.now(timezone.utc) - created
        if age > self.ttl:
            log.info("cache_expired", extra={"key": key, "age_days": age.days})
            return None
        return data.get("payload")

    def put(self, key: str, payload: Any) -> None:
        body = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        target = self._path(key)
        fd, tmp_path = tempfile.mkstemp(
            dir=self.root, prefix=f".{key}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(body, f, ensure_ascii=False, default=str)
            os.replace(tmp_path, target)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise