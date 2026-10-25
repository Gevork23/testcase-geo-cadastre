# DECISIONS

Журнал решений по тестовому заданию CadastreCheck. Фиксирует выбор AOI,
окружение, конфигурацию, доступ к НСПД и ограничения. На текущем этапе
закрыты шаги T1–T6; дальнейшие шаги (T7+) перечислены в roadmap в конце.

---

## T1–T2: Выбор и фиксация AOI

**Выбранный участок:** Батайск, мкр. Койсуг и садовые товарищества.
**Координаты центра (ориентир):** ~47.13, 39.76.
**Тип застройки:** СНТ, плотная малоэтажка (ИЖС).

**Обоснование выбора:**
1. Визуальный анализ подложек (ESRI, Яндекс, Google) подтвердил, что данный
   участок имеет высокую плотность частной жилой застройки и садовых
   товариществ, что соответствует требованиям ТЗ.
2. Полигон имеет форму квадрата со стороной ~1.95 км, выровненного по
   сторонам света. Площадь полигона составляет ~3.81 км² (проверено через
   `pyproj.Geod` на WGS84), что укладывается в требуемый диапазон 2–5 км².
3. Полигон валиден (замкнутый, без самопересечений), сохранён в EPSG:4326
   в файле `aoi.geojson`.

**Пригодность снимка:**
Снимок ESRI World Imagery покрывает весь AOI. Визуально не обнаружено
сплошных облаков или снега. Характер застройки — частные дома с
приусадебными участками. Часть крыш может быть скрыта кронами деревьев
(типично для СНТ), что будет учтено при оценке ограничений.

---

## T3: Форк и история коммитов

Репозиторий — публичный форк исходного проекта, ветка разработки `dev`.
История коммитов пошаговая, по номерам T-пунктов, с осмысленными
сообщениями (не один giant-коммит).

`.gitignore` исключает из репозитория: `data/`, `logs/`, `*.tif`, `*.gpkg`,
`*.pth`, `weights/`, `.env`, `.venv/`, `__pycache__/`.

Файл `uv.lock` **коммитится** (проект — приложение, а не библиотека:
воспроизводимость окружения важнее гибкости версий).

---

## T4: Окружение и воспроизводимость

- Python 3.12; менеджер зависимостей — `uv` (`pyproject.toml` + `uv.lock`).
- Ключевая зависимость для T6: `pynspd == 1.1.15` (зафиксирована в lock-файле).
  Дополнительно: `shapely`, `geopandas`, `pyproj`, `httpx`, `pyyaml`.
- Часть зависимостей в `pyproject.toml` (`streamlit`, `opencv-python-headless`,
  `rasterio`, `tenacity`, `pytest`) добавлена «на будущее» под T7–T30;
  на T1–T6 они не используются, но не мешают запуску.
- `.env.example` содержит заготовку переменных окружения; на T1–T6 переменные
  не требуются.
- Установка и запуск: `uv sync && uv run python scripts/probe_nspd.py --config config.yaml`.

---

## T5: Конфигурация

Всё, что может меняться без правки кода, вынесено в `config.yaml`:
- `paths.*` — AOI, кэш, лог, выходные файлы;
- `imagery.*`, `osm.*`, `model.*`, `detection.*`, `matching.*`, `pipeline.*` —
  заделы под T7–T30 (на T6 не используются);
- `nspd.*` — таймаут, повторы, rate limit, TTL кэша.

**На T6 фактически используются только:**
- `paths.aoi_geojson`, `paths.cache_dir`, `paths.log_dir`;
- `nspd.timeout_sec`, `nspd.max_retries`, `nspd.rate_limit_rps`,
  `nspd.use_cache`, `nspd.cache_ttl_days`.

Поля `nspd.retry_backoff_sec` определены в конфиге, но на T6 не применяются
(`pynspd` не даёт прямого контроля над backoff — реализуется в T7 через
`tenacity`). Пороги для классификации (`matching.*`) не захардкожены в коде
и готовы к использованию в T18–T24.

---

## T6: Доступ к НСПД

**Библиотека: `pynspd 1.1.15`.**
Скрипт: `scripts/probe_nspd.py`. Лог: `logs/probe_nspd.log`.

### Почему pynspd — основной клиент

- синхронный и асинхронный API совпадают по возможностям;
- Shapely из коробки (`feat.geometry.to_shape()`) — прямая совместимость
  с гео-стеком (geopandas, shapely);
- Pydantic-валидация полей — смена схемы НСПД проявляется сразу, а не молча
  съедает данные;
- встроенные retry (`client_retries`) на транзиентные ошибки;
- доступ к вкладкам НСПД (`tab_objects_list`, `tab_build_parts` и т.д.);
- типизированные методы `find_in_layer`, `search_in_contour_iter` —
  пригодятся в T7 для явных слоёв (Здания / Сооружения / ОНС).

`nspd-request` — проще, но слабее по типизации и геометрии; держим как
**fallback** для T7 (каскад `pynspd → nspd-request` при `PynspdResponseError`).

### Результаты probe (2026-10-24)

Проверено 5 кадастровых номеров: 3 в AOI (квартал `61:46:0010601`, Батайск)
+ 2 контрольных.

| К/Н | Результат | kind | contour | area_m2 |
|---|---|---|---|---|
| `61:46:0010601:757`  | not_found (404) | — | — | — |
| `61:46:0010601:5789` | OK | Земельный участок | True | 5876 |
| `61:46:0011802:3199` | OK | Земельный участок | True | 362 |
| `61:44:0060958:173`  | OK | Жилой дом | True | 152 |
| `77:05:0001005:19`   | OK | Земельный участок | True | 1434 |

**Интерпретация:** 4 из 5 объектов найдены в НСПД, у всех есть контур
и площадь. Один (`61:46:0010601:757`) отсутствует в НСПД — это валидный
ответ 404, а не сбой клиента; важно, что `pynspd` корректно вернул `None`
и скрипт не упал. Для пайплайна это штатная ситуация: в реальном AOI
будут номера, снятые с учёта.

403, 429 и schema-errors не зафиксированы. `pynspd 1.1.15` функционален
из текущего окружения. Выгрузка ограничена AOI (в T7 — через
`search_in_contour_iter`); массовая выгрузка по региону не производится.

### Кэш

Встроенный кэш Hishel в `pynspd 1.1.15` **не переиспользует** ответы НСПД:
сервер не отдаёт `Cache-Control`/`Expires`/`ETag`/`Last-Modified`, поэтому
Hishel считает ответы «сразу устаревшими» (`metadata.number_of_uses = 1`).
Кроме того, одновременная передача `cache_folder_path` + `cache_ttl`
в конструкторе (даже через `PYNSPD_CACHE_TTL`) вызывает
`ValueError("Допустимо выбрать только один вариант хранилища кэша")`.

**Решение:** в `scripts/probe_nspd.py` реализован собственный файловый кэш
независимо от `pynspd`:
- путь: `data/cache/nspd/{sha1(cad_num)}.json`;
- поля: `created_at`, `cad_num`, `payload` (`ok`, `error`, `kind`,
  `has_contour`, `area_m2`);
- TTL: из `nspd.cache_ttl_days` (по умолчанию 7 дней);
- `not_found` и сетевые ошибки тоже кэшируются (`ok: false`) — повторный
  запуск не долбит НСПД заведомо отсутствующим номером;
- флаг `--no-cache` принудительно идёт в сеть; по умолчанию — `--use-cache`.

Проверено 2026-10-24: второй прогон с кэшем → 0 HTTP-запросов к НСПД,
все строки помечены `[cache]`. `--no-cache` → снова 5 HTTP-запросов.

### Ограничения, выявленные при probe

1. **Кэш Hishel не переиспользуется** (см. выше). Реализован собственный
   файловый кэш с TTL; в T7 он будет перенесён в `src/nspd_client.py`
   и расширен на площадные запросы (`fetch_in_aoi`).

2. **TLS из чистого `httpx`.** Прямой `httpx.get` к `nspd.gov.ru` из текущего
   окружения падает с `SSL: CERTIFICATE_VERIFY_FAILED (self-signed certificate
   in certificate chain)` — вероятно, корпоративный MITM или несовпадение
   системного хранилища Windows и `certifi`. `pynspd` работает корректно
   (свой стек проверки). На пайплайн не влияет.

3. **Тип объекта определяется по расширенному списку полей.** Для
   `61:44:0060958:173` тип `Жилой дом` получен после добавления в
   `_probe_one` полей `building_record_type`, `construction_record_type`,
   `unfinished_record_type` и human-readable вариантов. На чистом
   `model_dump()` без этих полей ранее было `unknown`. В T7 тип будет
   определяться через явные слои `NspdFeature.by_title("Здания" /
   "Сооружения" / "Объекты незавершённого строительства")`.

## T7: Устойчивый клиент НСПД

### Артефакты

- `src/nspd_models.py` — `CadastralObject` (pydantic, `Field(description=...)`),
  `CadastreLoader` (Protocol), `ObjectType`, `Source`.
- `src/nspd_cache.py` — `FileCache` (TTL, sha1-ключи, schema version,
  атомарная запись через `tempfile` + `os.replace`), `cache_key_point`,
  `cache_key_area`.
- `src/nspd_client.py` — `PynspdAdapter`, `NspdRequestAdapter`,
  `RobustCadastreClient`, публичные `fetch_parcel(cad_num)`,
  `fetch_oks_in_bbox(bbox)`, `get_features(aoi, layer=...)`.
- `tests/test_nspd_client.py` — 46 unit-тестов + 3 live-теста
  (gated `NSPD_LIVE=1`).
- Вспомогательные скрипты: `scripts/probe_nspd_request.py`,
  `scripts/probe_grid_data.py` (результаты использованы для правки парсера
  и выбора стратегии площадного fallback).
- Покрытие `src/`: 90%. Ключевая логика (`_call_with_retry`, `_try_cascade`,
  `_parse_nspd_request_features`, `_to_object`, `_payload_to_obj`) покрыта
  полностью; непокрытые строки — защитный код (cleanup при ошибке записи
  кэша, редкие ветки парсера).

### Каскад адаптеров

`RobustCadastreClient` → `PynspdAdapter` → `NspdRequestAdapter`.

Каскад на **уровне метода** (`_try_cascade`), не `try/except` вокруг
пайплайна:
1. Сбой одного объекта не останавливает выгрузку 3.8 км².
2. `CadastralObject.source` (`pynspd` / `nspd_request`) показывает
   происхождение — в метриках можно посчитать долю fallback.
3. Точка отказа локализована в одном методе.

Срабатывает на `PynspdResponseError` (базовый класс смены схемы НСПД).
`NotFound` / `BlockedIP` пробрасываются без каскада.

**Доказательство вживую (2026-10-25, `NSPD_LIVE=1`):**

| Тест | Что проверяет | Результат |
|---|---|---|
| `test_live_no_coords_object_has_no_contour` | `61:44:0060958:173` через pynspd | ✅ `has_contour=False`, `attachment_point_wkt` есть |
| `test_live_cascade_pynspd_falls_back_to_nspd_request` | При `PynspdServerError` каскад уходит в nspd-request | ✅ `61:46:0010601:5789` получен через `get_object_info`, `source="nspd_request"`, `has_contour=True` |
| `test_live_nspd_request_area_small` | Площадной fallback `layer="land_record"` на AOI ~500×500 м (шаг 300 м) | ✅ **3 участка с контуром** за 3.4 с: `61:44:0060957:435`, `61:44:0060954:24`, `61:44:0060958:26` |

### Что ретраится

| Ошибка | Retry | Причина |
|---|---|---|
| `TooManyRequests` (429) | ✅ | временное ограничение |
| `PynspdServerError` (5xx) | ✅ | временный сбой |
| `PynspdResponseError` (база) | ✅ | смена схемы НСПД |
| `httpx.TimeoutException` | ✅ | сеть |
| `httpx.ConnectError` | ✅ | сеть |
| `NotFound` (404) | ❌ | валидный ответ, кэшируется `ok:false` |
| `BlockedIP` (403) | ❌ | ретраи усугубят блокировку |
| `TooBigContour` | ❌ | нужен `search_in_contour_iter` |
| `CascadeFailed` | ❌ | оба адаптера упали |

Предикат `_is_retryable` проверяет `NotFound` и `BlockedIP` **до** базового
класса `PynspdResponseError`. Это **сознательное** отклонение от
`retry_if_exception_type((PynspdResponseError, ...))` из ТЗ: иначе 404 и
403 ретраились бы — ровно та ошибка, которую ТЗ подсвечивает отдельно
(«на 404 retry бессмысленен и только замедлит выгрузку»).

Параметры `tenacity`: `stop_after_attempt(3)`,
`wait_exponential(multiplier=1.0, min=2.0, max=30.0)`, `reraise=True`.

### Rate limit

`aiolimiter.AsyncLimiter(1.5, 1)` — один лимитер на процесс, общий для
всех запросов (и pynspd, и fallback). НСПД не документирует лимиты;
1.5 rps — компромисс между скоростью обхода AOI и риском 429/403. Порог
из `config.yaml: nspd.rate_limit_rps`.

### Кэш

Файловый, `data/cache/nspd/`, TTL `nspd.cache_ttl_days` (7 дней).

- Ключ точечного запроса: `sha1("parcel|" + cad_num)`.
- Ключ площадного: `sha1("area|" + layer + "|" + WKB(geom, 1e-6))`.
  Геометрия нормализуется `shapely.set_precision(grid_size=1e-6)`,
  чтобы 4-й знак после запятой не плодил новые ключи.
- Payload содержит `_v = 2` (schema version). При несовпадении — refetch,
  лог `cache_stale_schema`. Причина введения версии: v1-payload хранил
  `Point` в `geometry_wkb` с `has_contour=True` — все ОКС без контура
  считались «с контуром». Исправление без версии оставило бы старые
  файлы отравленными на весь TTL.
- `Point` в `geometry_wkb` при чтении отбрасывается: `_payload_to_obj`
  прогоняет `_is_contour`, а не доверяет `has_contour` из файла.
- Запись **атомарная**: `tempfile.mkstemp` в той же директории +
  `os.replace`. При ошибке — `os.unlink` временного файла. Защита от
  corrupted JSON при параллельной записи.

### `has_contour` ≠ `geometry is not None`

ОКС без контура приходят в НСПД с `options.no_coords = true` и геометрией
`Point` (точка привязки к участку). Point **не** считается контуром:

- `CadastralObject.geometry = Polygon | MultiPolygon | None`;
- `has_contour = geom_type ∈ {Polygon, MultiPolygon}`;
- точка привязки → `attrs["attachment_point_wkt"]` (WKT, precision=8).

Реальный кейс: `61:44:0060958:173` — `no_coords=True`, площадь 152 м²,
`has_contour=False`. Без явной проверки T9 (`oks_effective_geometry`)
породил бы лавину ложных UNREGISTERED / OUT_OF_PARCEL
(`Point.buffer(3)` — круг радиусом 3 м вокруг привязки).

### Публичный API ловит `Exception` + `log.exception`

`get_features` возвращает `[]`, `fetch_parcel` — `None` при **любой**
ошибке после `max_retries`. Используется `log.exception` — со
стектрейсом, не молча. Причины:
1. Пайплайн (T10–T26) не должен падать из-за одного объекта/AOI.
2. Список исключений пришлось бы поддерживать вручную (`UnknownLayer`
   при смене схемы, `RuntimeError` из адаптера, `ValidationError` из
   pydantic).
3. Кэш **не пишется** на error-пути (только `NotFound` — валидный
   ответ), поэтому временный сбой не «залипает» на TTL 7 дней.

⚠️ `extra={"msg": ...}` **нельзя** — `msg` зарезервирован `LogRecord`,
`makeRecord` бросает `KeyError`. Используется `detail`.

### Площадной fallback: реальность vs ТЗ

`nspd-request` 2.0.2 в текущем окружении (probe 2026-10-25):

| Метод | Формат ответа | Использование |
|---|---|---|
| `get_object_info(cad_num)` | GeoJSON `{data.features}`, EPSG:3857 | ✅ точечный |
| `get_land_plot_by_coordinates` | **str cad_num** | только для получения номера |
| `get_oks_by_coordinates` | **str cad_num \| null** | только для получения номера |
| `get_cadastral_quarters` | GeoJSON FeatureCollection | не используется |
| `get_grid_data` | **зависает > 10 с** на плотных кварталах | ❌ не используется |

**Реализация:** сетка точек → `get_land_plot_by_coordinates` /
`get_oks_by_coordinates` (получаем `cad_num` строкой) → `get_object_info`
(полный GeoJSON). Дедупликация по `cad_num`, фильтр `aoi.intersects`.

Ограничения (честно):
1. **`get_oks_by_coordinates` не находит ОКС без контура** (например,
   `61:44:0060958:173` с `no_coords=True`). Такие ОКС видны только как
   `related` от участка. Обход `land_plot → related_oks` ещё не реализован
   (кандидат на T8/T9).
2. **Здания меньше шага сетки** (100 м) могут быть пропущены. Для задачи
   с ИЖС/СНТ это существенно — фиксируется как ограничение, влияющее
   на recall fallback'а.
3. **Время:** AOI 3.81 км² при шаге 100 м ≈ 400 точек ≈ 15–20 мин при
   1.5 rps. Для полного AOI — неприемлемо; для контрольных точек — секунды
   (что и подтверждено live-тестом).
4. **`get_grid_data`** (по кварталу) в 2.0.2 **зависает** без пагинации
   и без документированного таймаута. Проверено `scripts/probe_grid_data.py`
   (10 с через `ThreadPoolExecutor`). Не используется.
5. `fallback_max_points` (500) защищает от долгого обхода — WARNING
   `nspd_request_area_too_big` + `[]` (явно, не молча).

**Что это значит для ТЗ:** контракт `fetch_oks_in_bbox(bbox) → list`
выполнен. Основная реализация — через pynspd, работает вживую.
Каскад для точечных запросов доказан вживую. Площадной fallback работает
для объектов **с контуром** (3 участка за 3.4 с на контрольном AOI),
теряет ОКС без контура и мелкие объекты, медленный на полном AOI.
Это **документированный риск**, а не скрытая проблема.

**Почему ОКС без контура — не блокер T7.** ТЗ (T9) прямо предписывает
решать это на уровне матчинга: `oks_effective_geometry(oks, parcels)`
возвращает либо собственный контур, либо геометрию связанного участка
с флагом `inherited_from_parcel=True`. То есть клиенту **не обязательно**
находить ОКС без контура в площадном запросе — T9 свяжет их с участком,
который fallback находит (мы это видели: 3 участка найдены, включая
`61:44:0060958:26` из того же квартала, что и `…:173`). Клиент отдаёт
всё, что можно, T9 восстанавливает связь.

**T8+ оптимизация:** `get_cadastral_quarters` + `search_by_cadastral_number`
по номерам кварталов, либо `get_oks_by_land_plot` для ОКС без контура
(если метод работает — сейчас не проверялось), либо bbox-API через
`requests` напрямую (без nspd-request).

### Два кэша: `probe_nspd.py` vs `nspd_client.py`

`probe_nspd.py` (T6-артефакт) использует `sha1(cad_num)` и плоский JSON
`{ok, kind, area_m2, has_contour, ...}`.
`nspd_client.py` — `sha1("parcel|" + cad_num)` и payload с `_v`.

Namespace-разделены (`sha1("x")` ≠ `sha1("parcel|x")`), файлы не
пересекаются, каждый читает только свой. Это **следствие правила**:
`probe_nspd.py` — рабочий артефакт T6, HANDOFF прямо запрещает его
переписывать. Унификация — кандидат на T8, если решите объединить.
На текущих тестах `probe_nspd.py` даёт 5/5 `[cache hit]` — T6-контракт
не сломан.

### Проверки после T7
uv run pytest tests/test_nspd_client.py -v
→ 46 passed, 3 skipped

NSPD_LIVE=1 uv run pytest -k live -v
→ 3 passed (no_coords, cascade, area fallback)

uv run python -m src.nspd_client --config config.yaml --test
→ cache_hit, has_contour=False, attachment_point_wkt есть

uv run python scripts/probe_nspd.py --config config.yaml
→ 5/5 [cache hit] (T6-контракт цел)

uv run pytest --cov=src --cov-report=term-missing
→ src/ 90%

text

### Что на T8

Из ТЗ и HANDOFF T8:
- `--refresh-cache` / `--no-cache` CLI-флаги (TTL и schema-version уже
  есть, ручной инвалидации нет).
- `cache_hits / misses / stale` в сводке пайплайна — чтобы при падении
  API было явно видно «работаем на кэше».
- «API упал, кэш промахнулся» — сейчас `[]` без отдельного ERROR (на
  error-пути), в T8 нужен явный лог и счётчик.
- Опционально: унификация кэша `probe_nspd.py` + `nspd_client.py`.
- Опционально: `get_oks_by_land_plot` для ОКС без контура в площадном
  fallback (если подтвердится, что метод работает — сейчас не проверялся).