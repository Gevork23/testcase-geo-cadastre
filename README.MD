# CadastreCheck — выявление несоответствий застройки данным кадастра

Прототип ML/CV-системы: по открытым спутниковым снимкам находит здания,
сопоставляет их с публичными кадастровыми данными (НСПД) и выдаёт список
несоответствий с геопривязкой и оценкой уверенности.

**Территория:** Ростовская область, кадастровый округ `61`
**AOI:** Батайск, мкр. Койсуг и садовые товарищества (~47.13, 39.76) — **3.812 км²**

---

## Быстрый старт

### Установка

```
# Требуется uv (https://docs.astral.sh/uv/)
uv sync
cp .env.example .env   # опциональные переменные; см. комментарии в файле
```

### Запуск пайплайна (одна команда по конфигу)
```
uv run python -m src.pipeline --config config.yaml
```

Полный цикл по config.yaml: загрузка снимка ESRI и НСПД (с кэшем) → детекция
зданий → векторизация → классификация → экспорт discrepancies.gpkg.

Статус пайплайна: T13–T26 в работе. На текущем этапе (T1–T12) готовы
инфраструктура AOI, ESRI-снимок, устойчивый клиент НСПД, кэш, Sentinel-2;
модель сегментации и классификация — в следующих вехах (см. DECISIONS.md).

### Тесты
```
uv run pytest -q                 # 70 passed, 3 skipped
uv run pytest --cov=src          # с покрытием
```

## Данные

### Спутниковые снимки
Источник	Разрешение	Роль	Доступ
ESRI World Imagery	~0.3–0.6 м	основной снимок для детекции	XYZ-тайлы, атрибуция обязательна
Sentinel-2 L2A	10 м	вспомогательный: сигнал «свежести»	AWS Open Data (анонимно) / CDSE
Maxar Open Data	~0.3–0.5 м	опционально	отдельные территории

Атрибуция снимка ESRI: Esri, Maxar, Earthstar Geographics, and the GIS User Community
(дата съёмки: 2022-10-07; детали — ATTRIBUTION.md, обоснование — DECISIONS §T11).

### Кадастр — НСПД (nspd.gov.ru)
Внутренние HTTP-эндпоинты, не документированы, схемы меняются. Основной клиент —
pynspd, fallback — nspd-request. Ограничения, retry-логика, мягкий парсинг
и обработка ОКС без контура — в DECISIONS §T6–T9.

### OSM (слабая разметка)
Используется как cross-check и для оценки сдвига (T18). Не ground truth —
полнота в сельской местности Ростовской области низкая (20–40% зданий). См. T13.

### Воспроизводимость
Lock-файл: uv.lock (точные версии всех зависимостей)
Конфигурация: все пороги/пути/буферы — в config.yaml; магических чисел в коде нет
Кэш НСПД: data/cache/nspd/ (gitignored), TTL — 7 дней

### Сброс кэша НСПД
Полный сброс: rm -rf data/cache/nspd/
Один объект: удалить data/cache/nspd/<hash>.json
Игнорировать кэш при запуске: --refresh-cache (перезапись) или --no-cache (обход API)
TTL: config.yaml → nspd.cache_ttl_days (по умолчанию 7)

## Структура проекта

aoi.geojson                    # AOI, EPSG:4326, 3.812 км²
config.yaml                    # пороги / пути / буферы / rate limits
.env.example                   # шаблон переменных окружения
DECISIONS.md                   # журнал решений T1–T30
ATTRIBUTION.md                 # атрибуция ESRI + дата снимка
src/
  nspd_client.py               # устойчивый клиент НСПД (retry, rate limit, soft-parse)
  nspd_cache.py                # файловый кэш (TTL, инвалидация)
  nspd_models.py               # CadastralObject
  matching.py                  # oks_effective_geometry, is_detection_unregistered
  pipeline.py                  # entrypoint `python -m src.pipeline --config ...`
scripts/
  probe_nspd.py                # T6: smoke-тест доступа к НСПД
  fetch_esri_aoi.py            # T10: загрузка XYZ-тайлов
  build_aoi_geotiff.py         # T10: сборка GeoTIFF в EPSG:32637
  fetch_s2_cog.py              # T12: Sentinel-2 через STAC (AWS Open Data)
  dump_nspd_aoi.py             # T8: выгрузка слоёв НСПД по AOI
data/                          # gitignored: imagery, sentinel2, cache
tests/                         # pytest: 70 passed, 3 skipped

## Статус по вехам (T1–T30)
Веха	Артефакт	Статус
T1	aoi.geojson (3.812 км², EPSG:4326)	✅
T2	Пригодность AOI (DECISIONS §T1–T2)	✅
T3	Форк + .gitignore + история коммитов	✅
T4	uv.lock, .env.example, src/pipeline.py, README-инструкция	✅
T5	config.yaml (пороги/буферы/rate limit)	✅
T6	scripts/probe_nspd.py (5/5 OK)	✅
T7	src/nspd_client.py (retry, rate limit, soft-parse)	✅
T8	Кэш data/cache/nspd/ + --use-cache / --refresh-cache / --no-cache	✅
T9	oks_effective_geometry()	✅
T10	data/imagery/aoi_esri_utm37n.tif (EPSG:32637)	✅
T11	Атрибуция в README + ATTRIBUTION.md + TIFF-теги + DECISIONS §T11	✅
T12	data/sentinel2/aoi_s2_l2a_2025_summer.tif (4 band, EPSG:32637)	✅
T13–T26	OSM, модель, векторизация, классификация, discrepancies.gpkg	🚧
T27	Streamlit	⏳
T28–T29	Валидация + метрики (IoU/F1/precision)	⏳
T30	Отчёт 2–3 стр.	⏳

### Документация
DECISIONS.md — детальный журнал: AOI, пороги с обоснованием, доступ к НСПД, сдвиг, ОКС без контура, ограничения, план.
ATTRIBUTION.md — атрибуция ESRI + дата + предупреждение об устаревании снимка (2022-10-07).
docs/ORIGINAL_TZ.md — исходный текст ТЗ (бэкап, не редактируется).

### Про ИИ-инструменты
Использование LLM — нормальная часть работы (см. §«Про ИИ-инструменты» в
исходном ТЗ). Ответственность за артефакты, валидацию и финальные решения — за человеком.