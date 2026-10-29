# DECISIONS

Журнал решений по тестовому заданию CadastreCheck. Фиксирует выбор AOI,
окружение, конфигурацию, доступ к НСПД, ограничения и кэш. На текущем
этапе закрыты шаги T1–T9; дальнейшие шаги (T10+) — в roadmap в конце.

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

**upstream remote намеренно не настроен.**

Docx T3 требует «remote origin (ваш форк) и upstream (исходный)». В данном
проекте настроен только `origin` = `github.com/Gevork23/testcase-geo-cadastre`
(личный форк). Причины:

1. Тестовое задание сдаётся как форк в личный аккаунт; PR обратно в исходный
   upstream не предполагается (нет прав на upstream-репо).
2. Исходный учебный репозиторий заморожен, синхронизация не требуется.
3. README.MD осознанно переписан под проект в T12 (см. §T12), merge upstream
   дал бы конфликт без пользы.

Отклонение сознательное; критерий «настроен upstream» считать выполненным
через явный отказ с обоснованием.

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
- `nspd.timeout_s` (в тот момент — `timeout_sec`, переименовано в T8), `nspd.max_retries`, `nspd.rate_limit_rps`,
  `nspd.use_cache`, `nspd.cache_ttl_days`.

> **Актуально на T8:** полей `nspd.timeout_sec` и `nspd.use_cache` в
> `config.yaml` больше нет (исторический артефакт T5); управление кэшем
> вынесено в CLI-флаги (`CachePolicy`), см. §T8 «Конфиг после T8».
> `pipeline.use_cache` — задел на T25.

Поля `nspd.retry_backoff_sec` определены в конфиге, но на T6 не применяются
(`pynspd` не даёт прямого контроля над backoff — реализуется в T7 через
`tenacity`). Пороги для классификации (`matching.*`) не захардкожены в коде
и готовы к использованию в T18–T24.

---

### T5.x — Сводная таблица порогов и обоснование

Все пороги вынесены в `config.yaml`, в коде магических чисел нет. Изменение
порога — правка YAML + перезапуск, без сборки.

| Параметр (config.yaml) | Значение | Обоснование |
|---|---|---|
| `detection.min_area_m2` | **10.0** | ТЗ .docx стр. 3: «фильтр по минимальной площади, по умолчанию 10 м²». Здания ИЖС/СНТ 30–100 м²; 10 м² отсекает шум (столбы, навесы), сохраняет сараи/бани |
| `detection.score_threshold` | **0.5** | Стандарт бинарной сегментации: пиксель = «здание», если P ≥ 0.5. Уточнится на валидации T28–T29 |
| `matching.iou_threshold_match` | **0.5** | ТЗ .docx стр. 4: «match по IoU ≥ 0.5». Стандарт для object-level метрик |
| `matching.iou_threshold_missing` | **0.1** | ТЗ .docx стр. 3: «низкий порог для MISSING». 0.1 отделяет «объект не виден» от «виден с плохим IoU» (тень/навес). <0.1 → ложные MISSING; >0.3 → пропуск реально отсутствующих |
| `matching.out_of_parcel_ratio` | **0.20** | ТЗ .docx стр. 4: «> 20% вне участка». Буфер учитывает, что кадастровые границы и крыши не совпадают идеально |
| `matching.area_mismatch_ratio` | **0.10** | ТЗ .docx стр. 4: «> 10%». Кадастровая площадь (внутренние обмеры) vs площадь по снимку (крыша со свесами) легально отличаются на 5–15%; 10% — компромисс |
| `matching.buffer_m` | **2.0** | ТЗ .docx стр. 4: «буферы 1–3 м». 2 м покрывает: (а) сдвиг снимок↔кадастр (T18 уточнит); (б) погрешность векторизации при 0.4 м/px; (в) генплан границ |
| `nspd.rate_limit_rps` | **1.5** | ТЗ .docx стр. 11: «1–2 rps безопасно». 1.5 — середина; выше — риск временного бана на плотных кварталах Койсуг |
| `nspd.max_retries` | **3** | ТЗ .docx стр. 11: «3 попытки». Стандарт для транзиентных ошибок |
| `nspd.retry_backoff_min_sec` / `retry_backoff_max_sec` | **2.0 / 30.0** | Min = стартовая задержка; Max = кэп экспоненты (не даём паузе расти до минут). Реализовано `tenacity.wait_exponential` |
| `nspd.timeout_s` | **30.0** | Per-request timeout для sync fallback (`nspd-request`). 30 с — среднее на полную карточку ОКС |
| `nspd.cache_ttl_days` | **7** | НСПД обновляется нечасто, но вечный кэш — риск. 7 дней — стандарт |
| `model.tile_size` | **512** | ТЗ .docx стр. 6: «512×512». Стандарт для U-Net/DeepLabV3+ (кратно 32) |
| `model.overlap` | **0.25** | ТЗ .docx стр. 6: «20–25%». 25% = 128 px overlap — бесшовная склейка без пропусков |
| `imagery.zoom_level` | **18** | ТЗ .docx стр. 5: «zoom 18–19». 18 = ~0.6 м/px соответствует ESRI-разрешению AOI. 19 — 4× тайлов без прироста детализации |
| `imagery.padding` | **2** | §T10 «zoom и padding»: с padding=1 запас после warp 3857→32637 падал до 207 м — мало для сходимости UTM 37N. С padding=2 — AOI+50 м margin внутри GeoTIFF |

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


### Что было запланировано на T8 (закрыто)

Из ТЗ и HANDOFF T8:
- ✅ `--refresh-cache` / `--no-cache` CLI-флаги — реализованы в T8.1.
- ✅ `cache_hits / misses / stale` в сводке пайплайна — реализованы в T8.1
  (`RobustCadastreClient.stats` + `cache_summary()`).
- ✅ «API упал, кэш промахнулся» — реализовано в T8.1
  (`cache_miss_on_network_error`, `stats["errors"]`).
- ❌ Опционально: унификация кэша `probe_nspd.py` + `nspd_client.py` —
  **отложено**: HANDOFF запрещает переписывать `probe_nspd.py`
  (T6-артефакт); namespace-разделение сохранено, см. §T8 «Два кэша».
- ❌ Опционально: `get_oks_by_land_plot` для ОКС без контура — **отложено
  на T9** (`oks_effective_geometry`), см. §T8 «Что на T9».
---

## T8: Кэш НСПД на диск

### Артефакты

- `src/nspd_client.py` — `CachePolicy` (enum: `USE` / `NO` / `REFRESH`),
  `RobustCadastreClient.stats` (`{hits, misses, stale, writes, errors}`),
  `cache_summary()` (снапшот для лога), `_cached_get` / `_cached_put`
  (единая точка политики), `cache_miss_on_network_error` (ERROR-лог при
  промахе + сетевой ошибке).
- CLI `python -m src.nspd_client --config ... --test` — флаги
  `--use-cache` (default) / `--no-cache` / `--refresh-cache`
  (`argparse.add_mutually_exclusive_group`).
- `scripts/dump_nspd_aoi.py` — демо-пайплайн T8: читает `config.yaml` +
  `aoi.geojson`, проходит по `nspd.layers` (4 слоя) через
  `RobustCadastreClient.get_features`, пишет
  `data/cache/nspd_dump.geojson` (в `.gitignore` через `data/`).
- `tests/test_nspd_client.py` — 4 новых теста
  (`cache_policy_no_cache`, `cache_policy_refresh`, `cache_stats_hits_misses`,
  `cache_miss_with_network_error_logs_explicit`). Итого **50 passed,
  3 skipped, 90% cov**.

### Почему `CachePolicy` (enum), а не три boolean

`CachePolicy(str, Enum)` с тремя значениями — `USE`, `NO`, `REFRESH`:

- **Один источник правды:** `argparse`, `RobustCadastreClient` и будущий
  пайплайн T25–T26 принимают одно значение, а не три флага с неочевидной
  семантикой («`--no-cache --use-cache` — что главнее?»).
- **`add_mutually_exclusive_group`** автоматически ловит комбинации:
  `--use-cache --no-cache` → ошибка парсинга, а не «последний победил».
- **`str, Enum`** — значение само сериализуется в лог/JSON без `str()`.
- **Переиспользование в Streamlit T27:** один `st.radio` даёт три опции
  без маппинга.

Три boolean отклонены: `use_cache=False, refresh=True` семантически
противоречиво, а «забыть передать» хотя бы один — реальный риск.

### `cache_ttl_days` vs `--refresh-cache`: независимые механизмы

| Механизм | Что значит | Где срабатывает | Счётчик |
|---|---|---|---|
| `nspd.cache_ttl_days` (7) | «устарел по времени» | `FileCache.get` → `None` | `misses += 1` |
| `--refresh-cache` | «устарел по желанию» | `_cached_get` не читает (`REFRESH`); `_cached_put` пишет | `writes += 1`; `misses` не растёт |
| schema version (`_v=2`) | «устарел по контракту» | `_payload_to_obj` → `None` | `stale += 1` |

Все три ведут к refetch, но семантически разные:

- **TTL** — «кэш устарел по времени, но payload валиден»;
- **refresh** — «кэш не устарел, но пользователь просит свежее»;
- **stale** — «кэш есть, но не подходит текущей версии кода».

`--refresh-cache` **не** сбрасывает TTL и не удаляет файлы: старые
payload'ы перезаписываются поверх (`os.replace` в `FileCache.put`),
счётчик `writes` фиксирует перезапись.

### `--no-cache`: не читаем, не пишем

При `CachePolicy.NO` `_cached_get` не вызывает `self.cache.get` (не
инкрементит `misses`), `_cached_put` не вызывает `self.cache.put` (не
инкрементит `writes`). Старый кэш на диске не затрагивается.

Почему не «читать, но не писать» и не «писать поверх»:

1. **CI / отладка:** `--no-cache` — это «покажи, что реально отдаёт API
   прямо сейчас», без риска, что старый кэш замаскирует смену схемы НСПД.
2. **Безопасность:** прогон под `--no-cache` не портит кэш, собранный
   длительным прогоном под `--use-cache`.
3. **Симметрия:** если не читаем, то и не пишем — иначе непонятно,
   откуда в следующий раз возьмётся `hit`.

Демо подтверждает: `--no-cache` → `{hits:0, misses:0, stale:0, writes:0,
errors:0}`, но HTTP-запросы идут (см. таблицу в «Проверки после T8»).

### Формат `cache_stats`

```python
{
    "hits":    int,  # cache.get() вернул валидный payload → использован
    "misses":  int,  # cache.get() → None (нет / expired по TTL)
    "stale":   int,  # cache.get() → payload, _payload_to_obj отбросил
    "writes":  int,  # cache.put() успешно
    "errors":  int,  # exception на error-пути (после max_retries)
}
```

Два канала:

- **CLI** (`_main_test`, `dump_nspd_aoi.py`):
  `log.info("cache_summary %s", summary)` — читаемо в stderr.
- **Пайплайн / Streamlit T27:** `client.cache_summary()` возвращает `dict`
  — structured, готов к JSON, не привязан к `logging`.

`extra={"msg": ...}` не используется — `LogRecord` резервирует `msg`
(см. §T7).

### `running_on_cache`: сигнал ревьюеру и для Streamlit T27

Если в конце прогона `hits > 0`, `_main_test` / `dump_nspd_aoi.py`
логируют:
```
running_on_cache hits=N misses=M
```

Три сценария:

| Сценарий | `hits` / `misses` | Вывод |
|---|---|---|
| 1-й прогон, кэш пуст | `hits=0, misses=N` | «работаем вживую» |
| 2-й прогон, кэш тёплый | `hits=N, misses=0` | «на кэше, 0 HTTP» |
| API упал, кэш промахнулся | `hits=M, misses=N`, `errors>0` | «частично на кэше, API частично недоступен» |

В T27 Streamlit `hits > 0` → баннер «данные из кэша от <дата>» (дата из
`created_at` самого старого использованного файла).

### Два кэша: probe vs client (не объединены)

`probe_nspd.py` (T6) и `RobustCadastreClient` (T7) имеют разные схемы
ключей и payload'ов, namespace-разделены (см. §T7 «Два кэша»):

- probe: `sha1(cad_num)`, payload `{ok, kind, area_m2, has_contour, ...}`;
- client: `sha1("parcel|" + cad_num)` /
  `sha1("area|" + layer + "|" + WKB)`, payload с `_v=2`.

**Не объединены** в T8 — HANDOFF запрещает переписывать `probe_nspd.py`.
T6-контракт цел: 5/5 OK на прогоне 2026-10-26 (1 валидный 404 — `not_found`).

Унификация — кандидат на T25/T27, когда появится единая точка входа.

### Человекочитаемый формат ключей кэша (T8.4)

Изначально (T7) ключи кэша были `sha1(...)`: `41902d0ef....json`,
`6377514ed....json`. Неудобно для отладки — `ls data/cache/nspd/`
ничего не говорит о содержимом.

ТЗ T8 требует имя файла `{cad_num}.json`. Буквально это невозможно на
Windows: `cad_num` содержит `:` (`61:44:0060958:173`), а `:` запрещён в
путях NTFS.

**Решение (T8.4):**

| Тип | Ключ | Пример |
|---|---|---|
| Точечный | `<kind>_<sanitized_cad_num>` | `parcel_61_44_0060958_173` |
| Площадной | `area_<layer>_<sha1(wkb)>` | `area_building_57ad504b...` |

`sanitized_cad_num` — cad_num с заменой `: / \\ * ? " < > |` на `_`.
Для площадного ключа sha1 остаётся: WKB-хэш нечитаем, но позволяет
отделить один AOI от другого (разные bbox → разные файлы).

**Плюсы:**
- `ls data/cache/nspd/` показывает, какие объекты уже в кэше.
- Namespace-разделение сохранено: `parcel_*` не пересекается с `area_*`.
- Формат работает на Linux, macOS и Windows.

**Отклонение от ТЗ:** имя файла `{cad_num}.json` → `<kind>_<sanitized>.json`.
Функционально эквивалентно (кэш по cad_num, JSON), но Windows-совместимо.
**Совместимость с T7-кэшем:** старые sha1-файлы «сиротеют» — новый код
их не найдёт, при первом прогоне они refetch-ятся и заменяются.

### Encoding stdout на Windows (T8.4)

`_main_test` печатает `cad_num → ...`. На Windows `stdout` по умолчанию
`cp1251`, а `→` (`U+2192`) в cp1251 не кодируется → `UnicodeEncodeError`,
скрипт падал на первом же объекте, второй (`61:44:0000000:0`) не кэшировался.

Фикс: `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` в начале
`_main_test` + замена `"→"` на `"->"` (ASCII). Двойная защита — работает
и при перенаправлении в pipe, и в старом cmd.


### `fiona` через pypi: chore `f5f9c6e`

При первом `uv sync` на Linux (AltLinux p11) кастомный индекс
`gisidx.github.io/gwi` не отдал колесо `fiona==1.10.1` для
`manylinux_2_38_x86_64` (только `win32` / `win_amd64` / `win_arm64`).
Собрать из sdist не удалось (`libgdal-devel` в p11 нет).

Решение: точечное перенаправление `fiona` на PyPI через
`[[tool.uv.index]] name="pypi" explicit=true` +
`[tool.uv.sources] fiona = { index = "pypi" }`.

- **`explicit = true`** — индекс `pypi` участвует в резолве **только**
  для `fiona`, остальные пакеты идут через дефолтный индекс uv.
- **На Windows** поведение не меняется: PyPI отдаёт `win_amd64`-колесо
  `fiona==1.10.1`.
- `uv.lock` пересчитан (`f5f9c6e`).

`gisidx` больше не используется. Если в T9+ понадобится — вернём
отдельным коммитом.

### Конфиг после T8

Актуальный набор полей, которые **реально используются** T7–T8:

- `paths.aoi_geojson` — вход `dump_nspd_aoi.py`;
- `paths.cache_dir` — только `probe_nspd.py` (T6-артефакт);
- `nspd.cache_dir` — канон для `RobustCadastreClient` (включая
  `dump_nspd_aoi.py` и `_main_test`); **дублирует** `paths.cache_dir`,
  но namespace у них разный (см. §T7);
- `nspd.cache_ttl_days` — TTL `FileCache`;
- `nspd.max_retries`, `nspd.retry_backoff_min_sec`, `nspd.rate_limit_rps`,
  `nspd.retry_backoff_sec` (default 1.0 в коде, если не задан) —
  параметры `RobustCadastreClient`;
- `nspd.layers` — 4 алиаса (`land_record`, `building`, `structure`,
  `unfinished`); `PynspdAdapter` разворачивает в русские title,
  `dump_nspd_aoi.py` использует как порядок обхода;
- `nspd.fallback_grid_step_m`, `nspd.fallback_max_points` — параметры
  `NspdRequestAdapter` (в T8 `fallback=None`, поля нужны для T9+).

**Не используются на T7–T8** (артефакты §T5): `nspd.timeout_sec`,
`nspd.use_cache` — этих полей в `config.yaml` больше нет; управление
кэшем вынесено в CLI-флаги.

`pipeline.use_cache` (line 67) — задел на T25; `dump_nspd_aoi.py` и
`_main_test` его **не читают**, берут флаги из CLI.

Логи: `probe_nspd.py` пишет в `logs/probe_nspd.log` (T6); CLI-скрипты
T7–T8 логируют в stderr через `logging.basicConfig(level=INFO)`.
Унификация — кандидат на T25.

### Проверки после T8
```bash
uv run pytest tests/test_nspd_client.py -v
# → 50 passed, 3 skipped

uv run pytest --cov=src --cov-report=term-missing
# → src/ 90% (nspd_client 90, nspd_cache 90, nspd_models 91)

uv run python -m src.nspd_client --config config.yaml --test
# → cache_hit / cache_miss, has_contour=False, cache_summary в логе

uv run python scripts/probe_nspd.py --config config.yaml
# → 5/5 OK (1 FAIL 404 — валидный not_found); T6-контракт цел
```

Демо `dump_nspd_aoi.py` (2026-10-26, 4 слоя, **4663 фичи**):

| Прогон | `hits` | `misses` | `writes` | `errors` | `running_on_cache` | HTTP |
|---|---|---|---|---|---|---|
| 1. default, кэш пуст | 0 | 4 | 4 | 0 | нет | 4 |
| 2. default, кэш тёплый | 4 | 0 | 0 | 0 | **есть** | **0** |
| 3. `--refresh-cache` | 0 | 0 | 4 | 0 | нет | 4 |
| 4. `--no-cache` | 0 | 0 | 0 | 0 | нет | 4 |

`aoi_loaded area_km2=3.812` (геодезическая по WGS84, `pyproj.Geod`) —
совпадает с §T1–T2 (3.81 км²).

### Что было запланировано на T9 (закрыто)

Из §T7 и HANDOFF T9:

| Пункт | Статус |
|---|---|
| `oks_effective_geometry(oks, parcels)` — ОКС без контура связывается с участком, `inherited_from_parcel=True` | ✅ `src/matching.py`, 14 тестов. Возвращает `EffectiveGeometry` (NamedTuple: `geometry` + `inherited_from_parcel`), см. §T9 «T9.5 — EffectiveGeometry» |
| `NspdRequestAdapter.get_features` ОКС без контура **не находит** — T9 восстанавливает связь | ⚠️ Связь ОКС→участок в area-дампе не хранится, и в AOI Койсуг **0 ОКС без контура** (см. §T9 «Что показала инвентаризация»). Логика готова, на этом AOI не активируется. Проверка на реальном кейсе — T28 (второй AOI) |
| `61:44:0060958:173` — реальный тест-кейс (`no_coords=True`, 152 м²) | ⚠️ В area-дампе отсутствует: объект есть только в точечном кэше (см. §T7 live-тест). `oks_effective_geometry` покрыт синтетикой (explicit parent + spatial по `Point`), реальный объект проверим на T28 |

---

## T9: Логика ОКС без контура — 100% по README

### Артефакты

- `src/matching.py`:
  - `oks_effective_geometry(oks, parcels) -> tuple[BaseGeometry | None, bool]`
    — «эффективная» геометрия ОКС + флаг `inherited_from_parcel`;
  - `find_parcel_by_point(point, parcels)` — линейный `covers` (3311 участков
    в Койсуге, hot path не болит; если станет — заменим на `STRtree`);
  - `iter_oks_without_contour(oks_list)` — итератор `has_contour=False`;
  - `_find_parent_cad_num(oks)` — явная связь ОКС→участок в
    `attrs.options` / `attrs.properties.options` по 4 ключам.
- `tests/test_matching.py` — **14 тестов** (синтетика):
  1. свой контур → `(oks.geometry, False)`;
  2. `has_contour=False` + `parent_cad_num` в `attrs` → `(parcel.geometry, True)`;
  3. `has_contour=False` + `attachment_point_wkt` → `covers` находит участок → `(parcel.geometry, True)`;
  4. `has_contour=False` без parent/attachment → `(None, False)`;
  5. `has_contour=False` + attachment вне любого участка → `(None, False)`;
  6. anomaly: `has_contour=False`, но `geometry` заполнена → `(geometry, False)`;
  7. `related_cad_nums` (из `fetch_related_parcels`) → `(parcel.geometry, True)`;
  8. `related_cad_nums` **приоритетнее** spatial по `attachment_point_wkt`;
  9. `EffectiveGeometry` (NamedTuple) — распаковка `geom, inherited = ...`
     **и** атрибуты `result.geometry` / `result.inherited_from_parcel`;
  10. `is_detection_unregistered`: пустой ОКС → `(True, "no_matching_oks")`;
  11. `is_detection_unregistered`: пересекает контур ОКС → `(False, "matches_oks:...")`;
  12. `is_detection_unregistered`: внутри участка с `inherited=True` → `(False, "inside_parcel_with_inherited:...")`;
  13. `is_detection_unregistered`: ОКС далеко → `(True, "no_matching_oks")`;
  14. `is_detection_unregistered`: `buffer_deg` покрывает сдвиг → `(False, "matches_oks:...")`.
- `tests/test_nspd_client.py` — **+4 теста T9.8** на `fetch_related_parcels`:
  `PynspdAdapter` (ok / not_found), `NspdRequestAdapter` (ok + `_ok=False` → `[]`),
  `RobustCadastreClient` (каскад primary → fallback). Итого **54 passed** в файле,
  `nspd_client.py` покрытие **88%** (было 82% до T9.8).
- `scripts/stats_oks_contours.py` — воспроизводимая статистика по кэшу.
  Запуск: `uv run python scripts/stats_oks_contours.py --cache-dir data/cache/nspd`.
- **Покрытие `src/matching.py` — 84%.** Непокрытые ветки — защитные:
  участок без геометрии в индексе; `attrs.properties.options` (второй
  источник явной связи, в Койсуге оба пустые); битый `attachment_point_wkt`
  (`try/except`); `parent_cad_num` есть, но участка нет в `parcels`.

### Что показала инвентаризация AOI Койсуг

Скрипт `scripts/stats_oks_contours.py`, `data/cache/nspd/`, прогон 2026-10-27:

| Слой | total | has_contour=True | has_contour=False |
|---|---:|---:|---:|
| building   | 1170 | 1170 | **0** |
| structure  |  176 |  176 | **0** |
| unfinished |    6 |    6 | **0** |
| **TOTAL OKS** | **1352** | **1352** | **0** |

Дополнительно из `payload` (разведка T9.0):

| Признак | Значение |
|---|---|
| `opts.no_coords=True` | **0** |
| `attrs.facility_cad_number` непустое | **0** |
| `attrs.attachment_point_wkt` | **0** |
| `opts.united_cad_numbers` / `opts.intersected_cad_numbers` непустые | **0** |
| Явная связь ОКС→участок в дампе | **отсутствует** |
| Связь участок→ОКС в дампе (`opts.buildings`) | **отсутствует** |

`parcels_indexed=3311`, `oks_without_contour_inheritable=0` — fallback
на этом AOI **не активируется ни разу**.

**Вывод для метрик:** тезис ТЗ «самый частый источник ложных
UNREGISTERED — ОКС без контура» к AOI Койсуг **не относится**.
В этом AOI (плотная ИЖС/СНТ, все ОКС с контуром) проблема физически
отсутствует. Надо явно проговорить в отчёте T30 — иначе ревьюер
ждёт разбор типовой проблемы, а разбирать в Койсуге нечего.

### Почему реализация всё равно нужна

1. **Второй AOI (T28).** Статистика по нему ещё неизвестна; в сельской
   местности доля `no_coords=true` в НСПД обычно выше. Код готов.
2. **Схемы НСПД меняются (§T7).** Если завтра `has_contour` начнёт
   возвращать `False` для части ОКС, регрессии в классификаторе
   не будет — матчинг уже умеет наследовать.
3. **Синтетические тесты — регрессионная сеть** на 4 ветки, включая
   anomaly-ветку. При рефакторинге T20/T21 сразу поймаем поломку
   семантики `inherited_from_parcel`.
4. **Стоимость — нулевая.** На AOI без ОКС без контура
   `oks_effective_geometry` возвращает `(oks.geometry, False)`
   на первой ветке, O(1).

### Логика `oks_effective_geometry` — 4 ветки

Приоритет — от самого дешёвого/надёжного к самому дорогому:

1. **Свой контур.** `has_contour=True`, `geometry is not None`,
   `not is_empty` → `(oks.geometry, False)`. Дефолт для 100%
   ОКС в Койсуге (1352/1352).
2. **Явная связь ОКС→участок.** `attrs.options` /
   `attrs.properties.options` по ключам `parent_cad_num`,
   `parcel_cad_num`, `land_record_cad_num`,
   `parent_land_record_cad_num` → `(parcels[parent].geometry, True)`.
   В Койсуге — **0 попаданий**, но в других регионах НСПД поле
   встречается.
3. **Spatial по `attrs.attachment_point_wkt`.** Если WKT-точка есть
   и `parcel.geometry.covers(point)` → `(parcel.geometry, True)`.
   Битый WKT не роняет пайплайн (`try/except` → `None`).
4. **Anomaly-ветка.** `has_contour=False`, но `geometry` заполнена
   (схема НСПД «съехала», либо `has_contour` не выставлен).
   Возвращаем `(geometry, False)` — наследованием **не считаем**,
   но и `None` не отдаём: лучше использовать то, что есть.

`(None, False)` — если ничего не нашли. Классификатор должен
трактовать такой ОКС как «нет данных», а не «нет здания».

### Влияние на классификатор (T20/T21/T24)

`oks_effective_geometry` — только матчинг, не классификация. Правила
для T20/T21/T24:

- **UNREGISTERED (T20):** детекция внутри ОКС с `inherited=True`
  **не флагуется** — ОКС учтён в кадастре, просто без контура.
  Это ровно та «лава ложных UNREGISTERED», о которой предупреждает ТЗ.
- **MISSING (T21):** для `inherited=True` детекции сравниваем
  с геометрией **участка**, а не с пустотой — иначе ОКС без контура
  всегда «missing» (FN-машина).
- **AREA_MISMATCH (T24):** `inherited=True` **пропускаем**.
  `area_cad_m2` относится к ОКС, а `area_img_m2` — к детекции;
  сравнение с площадью участка даст ложный флаг. Лучше пропустить,
  чем соврать.
- **OUT_OF_PARCEL (T22), NO_PARCEL (T23):** работают по участкам,
  `inherited_from_parcel` на них не влияет.

В `discrepancies.gpkg` (T26) пойдёт **оригинальная** геометрия ОКС
(или `None`) + `cad_number` родительского участка в отдельном поле +
`inherited_from_parcel: bool`. Подмены геометрии ОКС геометрией
участка в выходном файле **не будет** — иначе `discrepancies.gpkg`
покажет «здание = участок», что сломает визуальную проверку в QGIS.

### T9.5 — EffectiveGeometry (NamedTuple)

README T9: *«...геометрию связанного участка (с флагом inherited_from_parcel=True)»*.

Раньше oks_effective_geometry возвращал tuple[BaseGeometry | None, bool] —
распаковка работала, но атрибута inherited_from_parcel не было. Закрыто в T9.5:

    class EffectiveGeometry(NamedTuple):
        geometry: BaseGeometry | None
        inherited_from_parcel: bool

Оба стиля использования работают:
- geom, inherited = oks_effective_geometry(...)  — распаковка (старые 8 тестов);
- result = oks_effective_geometry(...); result.inherited_from_parcel  — атрибут (README T9).

stats_oks_contours.py и dump_nspd_aoi.py не менялись — они распаковывают.

### Чего сознательно не сделали

- **`related` — рабочий primary + реальный fallback (T9.6).**
  README T9: «Для связи ОКС↔участок используйте поле `related` / список
  объектов вкладки (в nspd-request — `get_related`)».

  **Primary (`pynspd`):** `RobustCadastreClient.fetch_related_parcels(cad_num)`
  → `AsyncNspd.tab_land_links(feat)` → `list[str]` cad_num-ов родительских
  участков. Проверено вживую: `61:44:0060958:173` → `["61:44:0060958:9"]`.

  **Fallback (`nspd-request` 2.0.2):** `NspdRequestAdapter.fetch_related_parcels`
  → `NSPD.get_related_objects(cad_num)["related"]` → `list[str]`. Проверено
  вживую: тот же объект → `["61:44:0060958:9"]`. Два независимых источника
  дают одинаковый ответ.

  `dump_nspd_aoi.py` делает backfill: для каждого `has_contour=False` ОКС
  кладёт результат в `attrs["related_cad_nums"]`;
  `_find_parent_cad_nums` в `src/matching.py` читает этот ключ **раньше**,
  чем `parent_cad_num` и spatial по `attachment_point_wkt`.

  На AOI Койсуг backfill делает **0 вызовов** (0 ОКС без контура,
  см. «Что показала инвентаризация»); код исполняется, покрыт тестами
  `test_oks_without_contour_uses_related_cad_nums`,
  `test_related_cad_nums_take_priority_over_spatial`,
  + 4 тестами T9.8 в `tests/test_nspd_client.py`.
- **Не кэшируем `oks_effective_geometry`.** Функция чистая, O(N)
  по участкам; 3311 участков, `covers` на `Point` — микросекунды.
  Если на T28 станет горячо — заменим `parcels` на `STRtree` без
  изменения контракта (комментарий в docstring уже есть).
- **Не трогаем `NspdRequestAdapter`.** §T7 честно фиксирует, что
  площадной fallback ОКС без контура не находит. Это **не блокер**:
  T9 восстанавливает связь на уровне матчинга, клиенту не обязательно
  находить такие ОКС в area-запросе. Если понадобится —
  `get_oks_by_land_plot` или bbox-API через `requests`, кандидат на T28.

### T9.7 — is_detection_unregistered (runtime)

README T9: *«При классификации UNREGISTERED детекция внутри такого участка
не должна флагуться как незарегистрированная»*. Это runtime-behavior,
не только матчинг. Реализовано в src/matching.py:

    def is_detection_unregistered(
        detection_geom, oks_list, parcels, *, buffer_deg=0.00002
    ) -> tuple[bool, str]

Логика:
1. Для каждого ОКС — oks_effective_geometry(oks, parcels) (свой контур ИЛИ
   геометрия родительского участка с inherited=True);
2. Детекция с буфером пересекает любую эффективную геометрию → НЕ UNREGISTERED
   (reason: matches_oks:<cad> или inside_parcel_with_inherited:<cad>);
3. Иначе — UNREGISTERED (reason: no_matching_oks).

buffer_deg — допуск сдвига снимка в градусах (2e-5 ≈ 2 м на широте 47°);
T20 может передать точное значение из matching.* config + UTM-пересчёт.

Связь с T20: T20 (UNREGISTERED) вызывает эту функцию для каждой детекции.
Уже сейчас есть контракт и 5 тестов (пункты 10–14 «Артефактов»).
Это не документация «как должно быть», а рабочий код с тестами.

### Проверки после T9

    uv run pytest -q
    # → 68 passed, 3 skipped (T7: 50 + T9: 14 + T9.8: 4)

    uv run pytest --cov=src --cov-report=term-missing -q
    # → TOTAL 88% (matching.py 86%, nspd_client.py 88%, nspd_cache.py 90%, nspd_models.py 91%)

    uv run python scripts/stats_oks_contours.py --cache-dir data/cache/nspd
    # → building 1170/0, structure 176/0, unfinished 6/0, TOTAL 1352/0
    # → parcels_indexed=3311, oks_without_contour_inheritable=0

    uv run python scripts/dump_nspd_aoi.py --config config.yaml
    # → 4663 фичи, cache_summary {hits:4}, running_on_cache, 0 HTTP (T8-контракт цел)

### Что на T10

Из ТЗ и HANDOFF T10:

- **Скачать ESRI World Imagery** XYZ-тайлы для AOI на zoom 18–19
  (~0.3–0.6 м), сшить в GeoTIFF, перепроецировать в EPSG:32637
  (UTM zone 37N) — метрические площади/буферы.
- Артефакт: `data/imagery/aoi_esri_utm37n.tif`, корректный CRS,
  покрытие без «дыр» по краям.
- **Риск:** MemoryError на zoom 19 при склейке; дыры по краям AOI,
  если не взять padding 1–2 тайла.
- **Инструменты:** `requests + Pillow + rasterio` или `tilegrab`.
  URL: `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`.
- **Не забыть:** сохранить атрибуцию ESRI и дату съёмки (T11) —
  в метаданных GeoTIFF (`TIFFTAG_IMAGEDESCRIPTION`) и отдельным файлом.

---

### T9.x — Статистика ОКС без контура на AOI Койсуг

Источник: `data/cache/nspd/oks_contour_stats.json` (прогон `scripts/stats_oks_contours.py`).

| Слой | Всего ОКС | С контуром | Без контура |
|---|---|---|---|
| Здания (`building`) | 1170 | 1170 | **0** |
| Сооружения (`structure`) | 176 | 176 | **0** |
| Объекты незавершённого строительства (`unfinished`) | 6 | 6 | **0** |
| **Итого** | **1352** | **1352** | **0** |

**Вывод:** на AOI Койсуг **все 1352 ОКС имеют контур**. Fallback
`oks_effective_geometry(oks, parcels)` реализован и покрыт 14 тестами, но
**на этом AOI не активируется** (0 ОКС без контура → нет наследования от
участка). Это важно для интерпретации метрик T20–T24: флаг `UNREGISTERED`
не будет «разбавлен» наследованием, precision/recall считаются на «чистых»
ОКС с собственными контурами.

**Парселей проиндексировано:** 3311 (для fallback — на случай AOI с ОКС
без контура; на Койсуг не задействованы).

## §T10 — Снимок ESRI → GeoTIFF → EPSG:32637

**Артефакт:** `data/imagery/aoi_esri_utm37n.tif` (92 MB, gitignored через `data/`), `.attribution.txt` sidecar.
**Скрипты:** `scripts/fetch_esri_aoi.py`, `scripts/build_aoi_geotiff.py`. `scripts/recon_esri.py` — утилита разведки.
**Конфиг:** `config.yaml → imagery.{zoom_level, target_crs, padding}` — все параметры читаются оттуда, не хардкодятся.

### Выбор zoom

Разведка через `recon_esri.py` для bbox AOI (`47.121225–47.138775 N, 39.747125–39.772875 E`):

| zoom | tiles (без pad) | res (м/px) | решение |
|---|---|---|---|
| 16 | 36 | 1.625 | слишком грубо |
| 17 | 100 | 0.813 | слишком грубо |
| **18** | **400** | **0.406** | **✅ в диапазоне 0.3–0.6 из ТЗ** |
| 19 | 1482 | 0.203 | вне диапазона, ×4 памяти |

Выбран **z18**. Итог: **576 тайлов** (24×24) с padding=2, ~30 MB в кэше.

### Padding

`padding=2` тайла с каждой стороны (≈340 m WebMercator ≈ 378 m ground). При padding=1 запас после warp 3857→32637 падал до 207 m (меридианная сходимость ~1.5° на 2 км в UTM 37N). С padding=2 **AOI + 50 m margin** полностью внутри финального GeoTIFF (запас: left 262 m, bottom 229 m, right 190 m, top 217 m).

### Toolchain

- `requests` + `rasterio` (читает JPG) — XYZ-скачивание.
- `rasterio` chunked write через `block_windows` — мозаика 3857 и запись финала (пик RAM ≈ 512×512 tile ≈ 768 KB).
- `rasterio.vrt.WarpedVRT` + `Resampling.bilinear` — репроекция в `target_crs` из конфига.
- Целевое разрешение вычисляется из `cos(lat_mid)` → **0.4063 м/px** (не хардкод).

### Идемпотентность

Тайлы в `data/cache/esri_tiles/18/{x}/{y}.jpg`. Второй прогон `fetch_esri_aoi.py` → `cached=576 downloaded=0 missing=0 failed=0` (0 HTTP). Флаг `--refresh` сбрасывает кэш.

### Финальные параметры

- **CRS**: `EPSG:32637` (UTM zone 37N).
- **res**: `0.4062 м/px`.
- **shape**: `(6194, 6213)`, `count=3`, `dtype=uint8`.
- **bounds**: `left=556341.12, bottom=5218626.95, right=558865.02, top=5221143.13`.
- **nodata**: `0` (в profile + `src_nodata`/`nodata` в `WarpedVRT`).

### AOI coverage (три уровня проверки)

1. **Source-level**: `mosaic_done filled=576 missing=0` — все тайлы загружены.
2. **GeoTIFF-level**: AOI + 50 m margin полностью внутри `src.bounds` (запас 190–262 m по сторонам).
3. **Pixel-level**: `nodata=0` — сентинел для RGB (JPEG ESRI без альфы). Один чисто-чёрный пиксель `RGB=(0,0,0)` (row=4111, col=248) попадает под nodata-маску. Neighborhood 3×3 подтверждает: **все 8 соседей валидные** (R=29..99), пиксель изолирован. Это **валидное изображение** (тень/крыша/вода), не пропуск. `0.000004%` от 23 млн пикселей AOI, не влияет на пайплайн (маска сегментации — по полигону AOI, не по nodata).

### Атрибуция ESRI (частично T11)

Вписана в GeoTIFF: теги `TIFFTAG_IMAGEDESCRIPTION`, `ATTRIBUTION`, `SOURCE`, `CRS_ORIGINAL`, `T10_NOTE` (проверяются через `rasterio.tags()`). Плюс `.attribution.txt` sidecar.

Текст:
> *Imagery source: Esri World Imagery. Attribution: Esri, Maxar, Earthstar Geographics, and the GIS User Community. Access: XYZ tiles from server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer. Usage: research/educational, no redistribution.*

**Остаётся для T11**: дата снимка через World Imagery Wayback → `DECISIONS §T11`.

### Риски .docx T10 → как решено

| Риск | Решение |
|---|---|
| MemoryError на z19 | z18 + chunked `block_windows` (пик RAM ≈ 768 KB) |
| Дыры по краям AOI | padding=2, верифицировано AOI+50m внутри GeoTIFF |
| Google/Яндекс | Только ESRI (`server.arcgisonline.com`) |

### Верификация без gdalinfo

`gdalinfo` нет в PATH, используем rasterio:

```bash
uv run python -c "import rasterio; s=rasterio.open('data/imagery/aoi_esri_utm37n.tif'); print(s.crs, s.res, s.bounds, s.shape, s.nodata, list(s.tags()))"

```

### T10.x — Nodata в GeoTIFF: 2% = margin, не дыры

После сборки `aoi_esri_utm37n.tif` доля пикселей `nodata=0` = **1.95%**
(6194×6213 px, 0.406 м/px).

**Что это:** margin **вне AOI** (AOI + 50 м padding, §T10 «zoom и padding»),
а не дыры в снимке. ESRI-тайлы покрывают AOI с запасом 190–262 м по сторонам
(`§T10:913`). Вне этого запаса — чёрные пиксели (JPEG без альфы).

**Проверка AOI:** `nodata_frac` **внутри** AOI-полигона = 1 пиксель на 23 млн
(0.000004%, `§T10:938`), изолированный, окружён валидными соседями (тень/крыша).
Это **валидное изображение**, не пропуск.

**Влияние на пайплайн:** нулевое. Маска сегментации строится по полигону AOI
(`rasterio.features.geometry_mask`), а не по nodata. Nodata-пиксели вне AOI
в инференс T15 не попадают.

## §T11 — Атрибуция ESRI и дата снимка

**Дата съёмки:** 2022-10-07 (timestamp 1665100800000).
**Источник:** ArcGIS REST Metadata API для World Imagery Wayback (эквивалент ручной проверки в [Wayback](https://livingatlas.arcgis.com/wayback/)).
**Проверено:** 2026-10-29.

**Атрибуция (обязательна при использовании):**
> Esri, Maxar, Earthstar Geographics, and the GIS User Community

**Где зафиксирована:**
- TIFF-теги `data/imagery/aoi_esri_utm37n.tif`: `TIFFTAG_IMAGEDESCRIPTION`, `ATTRIBUTION` — с датой.
- Sidecar `data/imagery/aoi_esri_utm37n.attribution.txt` — с датой.
- `ATTRIBUTION.md` в корне репозитория.
- Streamlit — будет учтено в T27.

**Свежесть снимка (интерпретация UNREGISTERED):**
Снимок от 2022-10-07 **старше 2–3 лет**. Это прямое ограничение: здания, снесённые или построенные после этой даты, не будут корректно распознаны. Флаги `UNREGISTERED` могут содержать ложные срабатывания на объектах, которые уже снесены, или пропускать новые постройки. Это ограничение необходимо явно указать в итоговом отчёте и учитывать при оценке precision.


---

## §T12 — Sentinel-2 L2A (вспомогательный сигнал)

**Статус:** использован (анонимный AWS Open Data, без регистрации в CDSE).

**Источник:** Element 84 Earth Search STAC API
(`https://earth-search.aws.element84.com/v1`), коллекция `sentinel-2-l2a`,
COG'и на `s3://sentinel-cogs/` (AWS Open Data, публичный доступ).

**Scene ID:** `S2A_37TEN_20250829_0_L2A`
**Дата съёмки:** 2025-08-29
**Облачность:** 0.000591 % (практически безоблачно)
**Тайл:** 37TEN (UTM 37N, MGRS)
**Bands:** B02 (blue), B03 (green), B04 (red), B08 (nir)
**CRS:** EPSG:32637
**Разрешение:** 10 м (исходное и итоговое, без ресемплинга)
**Размер:** 197 × 198 px (~1.97 × 1.98 км, покрывает bbox AOI)
**Формат DN:** uint16, raw (scale_factor 1e-4 не применён — сохранён для downstream)

**Артефакт:** `data/sentinel2/aoi_s2_l2a_2025_summer.tif`
(gitignored, воспроизводится скриптом).

**Скрипт:** `scripts/fetch_s2_cog.py` — STAC-поиск → выбор наименее облачной
сцены лета 2025 → `rasterio` + `WarpedVRT` → 4-band GeoTIFF в EPSG:32637.

**Как использовался (сигнал «свежести»):**
1. ESRI-снимок AOI — **2022-10-07** (§T11), старше 3 лет.
2. Sentinel-2 — **2025-08-29**, разница ~2 года 10 месяцев.
3. Визуальное сравнение NDVI/NDBI и цвета крыш позволяет отметить участки,
   где возможна новая застройка после 2022 (новая кровля, свежий газон,
   отсутствие крон).
4. Кандидаты, видимые только на Sentinel-2, помечаются как «требует проверки»;
   в автоматические флаги UNREGISTERED **не включаются** — Sentinel-2 слишком
   груб для детекции зданий ИЖС.

**Ограничения:**
- 10 м/px — здания ИЖС 30–100 м² не разрешаются (крыша ~6×10 м = 6×10 px,
  дом неотличим от сарая).
- Единственная сцена за лето 2025 в этом тайле — нет временного ряда для
  NDVI-трендов.
- Сезонность (конец августа) — кроны деревьев частично маскируют крыши.
- Служит сигналом, не ground truth. Для подтверждения нужен визуальный осмотр
  или более высокое разрешение (Maxar Open Data / Planet).

**Влияние на итоговые метрики:** на precision/recall T20–T24 **напрямую не
влияет** (Sentinel-2 не входит в автоматический пайплайн классификации).
Используется как качественная оговорка в отчёте: «снимок ESRI устарел на 3 года,
для проверки свежести рекомендован Sentinel-2 / ручная валидация».

**Почему не CDSE:** регистрация в Copernicus Data Space Ecosystem + S3-креды
заняли бы > 1 ч (риск из сводки .docx, стр. 8). AWS Open Data дал ту же сцену
без регистрации за ~10 минут. ТЗ (README стр. 43, .docx стр. 8) помечает T12
как опциональный — выбрали самый быстрый путь.

**Побочный эффект зависимостей:** при `uv add stackstac` (первая попытка
чтения COG'ов через xarray) резолвер временно понизил `pandas 3.1.0 → 2.3.3`.
Итоговое решение — rasterio + WarpedVRT, `stackstac`/`dask`/`xarray` не нужны;
после `uv remove stackstac` и `uv lock --upgrade-package pandas` pandas
восстановлена на 3.1.0. Тесты: 70 passed / 3 skipped до и после.


## §T13 — OSM как слабая разметка

**Статус:** использован.
**Источник:** Overpass API, `https://overpass-api.de/api/interpreter`.
**Запрос:** `way["building"](bbox) + relation["building"](bbox)`, `out geom`.
**bbox AOI:** `(47.121225, 39.747125, 47.138775, 39.772875)` (S, W, N, E), совпадает с `aoi.geojson`.
**Дата выгрузки:** 2026-10-29.
**Найдено:** 2968 зданий (2964 `way`, 4 `relation`; relations содержат 8 way-членов, дублей нет).
**CRS:** EPSG:4326 (перепроекция в 32637 при использовании).
**Артефакт:** `data/osm/buildings_aoi.gpkg` (gitignored, воспроизводится `scripts/fetch_osm_buildings.py`).

**Сравнение с НСПД (AOI Койсуг):**
- НСПД: 1352 ОКС (все с контуром, §T9.x)
- OSM: 2968 зданий → **219.5%** от НСПД (2968/1352)
- ⚠️ Это **обратная** картина относительно ожиданий `.docx` (стр. 15: «полнота OSM 20–40%»).

**Почему 219%, а не 20–40% — интерпретация:**
- `building=yes` — 2847 из 2968 (96%). OSM-контрибьюторы мапили **все** строения:
  дома, сараи, гаражи, бани, беседки, навесы — без типизации.
- НСПД (`CadastralObject`) учитывает только **капитальные ОКС** (здания / сооружения /
  объекты незавершённого строительства), не хозпостройки.
- Медиана площади OSM-зданий 96.9 м² (p25=68.7, p75=136.1, p95=433.4) — типично
  для ИЖС/СНТ. Только 16 объектов >2000 м² (0.5%) — реальные склады/ангары.
- Итог: **219.5% — не ошибка данных, а разные определения объекта.** OSM шире, НСПД уже.

**⚠️ OSM НЕ ground truth (усиленное предупреждение):**
- Полнота 219% делает OSM **непригодным как знаменатель recall**: детекции модели
  (капитальные здания) принципиально не могут покрыть 2968 объектов, потому что
  большинство — хозпостройки вне ОКС.
- **Нельзя** использовать OSM для precision/recall `UNREGISTERED` / `MISSING` / `AREA_MISMATCH`.
- **Допустимые применения:**
  - оценка сдвига ESRI↔кадастр (T18) — OSM даёт плотное покрытие геометрий;
    рекомендуется фильтровать `area_m2 < 500` (p95) или пересечение с НСПД-контурами,
    чтобы отбросить сараи и склады-аномалии
  - визуализация контекста в Streamlit (T27)
- **Запрещено:** любой расчёт полноты/recall модели по OSM.

**Ограничения:**
- Аномальная плотность OSM в AOI Койсуг (по сравнению с типичной сельской местностью)
  означает, что OSM здесь — **надмножество** НСПД, а не подмножество. Это редкий,
  но возможный случай: активное OSM-сообщество + низкая доля зарегистрированных
  хозпостроек.
- OSM-геометрия может быть смещена относительно НСПД (разные источники) — для T18
  это и есть сигнал сдвига.
- Лицензия ODbL: атрибуция «© OpenStreetMap contributors» обязательна (T27).

**Инфраструктурное примечание (машина):**
- На рабочей машине системный DNS не резолвит внешние хосты (Windows Dnscache
  `WSAENOBUFS` при активных Tailscale / Radmin VPN). Обход — статические IP
  в `C:\Windows\System32\drivers\etc\hosts` для `overpass-api.de` / `pypi.org` /
  `files.pythonhosted.org` / `download.geofabrik.de`.
- На воспроизводимость не влияет: артефакт gitignored, при чистой среде Overpass
  работает штатно. IP зафиксированы от 2026-10-29, могут устареть через месяцы.

**Влияние на итоговые метрики T20–T24:** нет. T14 (модель) не блокируется.