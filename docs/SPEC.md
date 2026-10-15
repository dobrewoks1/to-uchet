# SPEC — «Учёт ТО», версия 1 (контракт для разработки, ред. 3 — v2, один главный экран, см. docs/КОНЦЕПЦИЯ_v2.md)

Читать вместе с `docs/ТЗ.md` (требования R1–R14). Этот файл — единый контракт: схема БД, правила расчёта, API, экраны.
Ред. 3: интерфейс упрощён до одного главного экрана (раздел 7 переписан), на сервере добавлены `last_note` в MachineView и фото машин (раздел 5).
Разделы 3, 4 (кроме 4.9) и 6 — расчёты, схема БД, импорт/экспорт — от ред. 2 не менялись.
Если код и SPEC расходятся — прав SPEC; если SPEC неполон — решать в духе ТЗ («максимально просто для механика») и исходной таблицы v8 (её листы и столбцы описаны в README, раздел «Формат импорта»).

## 0. Принципы

- Пользователь — механик на вахте, не айтишник. Интерфейс русский, крупно, без лишнего. Никакого интернета: **ничего с CDN**, всё локально.
- Вся логика расчётов — на сервере (Python), фронтенд показывает готовые поля. Расчёты — чистые функции, покрыты тестами.
  Единственное исключение — «живая подсказка» в диалогах (см. `ui.previewTo` в 7.1): простая арифметика из готовых полей MachineView.
- Стек: Python 3.12+ (venv проекта — 3.14), **только стандартная библиотека** (`http.server`, `sqlite3`, `json`) + `openpyxl` (импорт/экспорт xlsx).
  Фронтенд: статический SPA, ванильный JS (ES-модули), без сборки, без фреймворков.
- Запуск: `app/main.py` поднимает HTTP-сервер на `127.0.0.1:<порт>` и открывает окно Edge/Chrome в режиме `--app` (выглядит как обычная программа).
  Поставка — один exe (PyInstaller onefile, без консоли).

## 1. Структура проекта

```
to-uchet/
  app/
    __init__.py
    main.py        # точка входа: аргументы, папка данных, single-instance, сервер, окно
    server.py      # HTTP-сервер, роутинг /api/*, статика, JSON, ошибки
    db.py          # подключение sqlite, схема, миграции, сиды, бэкапы
    logic.py       # ЧИСТЫЕ функции расчёта (без БД и I/O)
    services.py    # операции над БД: CRUD, показания, «накинуть за день», «ТО сделано», сборка представлений, фото машин (файлы в <данные>/photos/)
    xlsx_io.py     # импорт из таблицы v8, экспорт в xlsx
    static/
      index.html
      css/app.css, css/print.css
      icons/*.svg   (Tabler, уже лежат; stroke=currentColor; в т. ч. arrow-left — кнопка «← Все машины»)
      js/app.js (роутер, каркас), js/api.js, js/ui.js (UI-кит), js/icons.js (иконки из icons/), js/dialogs.js (общие диалоги)
      js/pages/*.js  (по модулю на экран: fleet — главная, card, plan, workorder, machines, tyres, units, settings, welcome;
                      _import.js — общий разбор файла импорта для settings и welcome; страниц readings, journal, reports в v2 нет)
      UI_KIT.md      (документация UI-кита для авторов страниц)
  tests/            # unittest (стандартная библиотека): python -m unittest discover -s tests -v
  run_dev.py        # dev-запуск без окна: python run_dev.py [--port 8765] [--data dev-data] [--demo]
  seed_demo.py      # демо-данные: синтетическая таблица v8 (examples/demo-v8.xlsx) → импорт в demo-data/to.db
  build.bat         # сборка exe через PyInstaller (build-compat.bat — 32 бит, Python 3.8, Windows 7)
  docs/             # SPEC, ТЗ, концепция v2, руководство пользователя, скриншоты
```
Python для запуска и тестов: виртуальное окружение проекта `.venv` (там стоят openpyxl и pyinstaller), подойдёт любой Python 3.8+ с openpyxl.

## 2. Запуск, данные, HTTP-слой (`main.py`, `db.py`, `server.py`)

**Пути.** Базовая папка: `Path(sys.executable).parent`, если `getattr(sys, 'frozen', False)`, иначе корень проекта (родитель `app/`).
`__file__`/`sys._MEIPASS` для данных НЕ использовать (в onefile это временная папка). Статика: `Path(sys._MEIPASS)/'app'/'static'` в exe, иначе `Path(__file__).parent/'static'`.
Сборка: `pyinstaller --onefile --noconsole --name TO-Uchet --add-data "app/static;app/static" app/main.py` (точка входа должна работать и как скрипт, и как модуль — абсолютные импорты `from app import ...` с добавлением корня в `sys.path` при необходимости).

**Аргументы:** `--port N` (по умолчанию 8765), `--data DIR`, `--no-window`.
**Папка данных:** `--data`, иначе `<базовая>/data`, если туда можно писать, иначе `%APPDATA%/TO-Uchet`. Внутри: `to.db`, `backups/`, `photos/<machine_id>/` (фото машин, см. раздел 5; в `backups/` не входят), `server.json` (`{"port":..,"pid":..}`), `app.log`.
Профиль браузера — `%LOCALAPPDATA%/TO-Uchet/browser-profile` (не в папке данных: она может жить на флешке).
Если `sys.stdout`/`sys.stderr` равны None (сборка без консоли) — перенаправить в `<data>/app.log` до старта сервера. Необработанные исключения обработчиков → traceback в `app.log` и HTTP 500 `{"error":"Внутренняя ошибка: ..."}`.

**Сервер.** `ThreadingHTTPServer` с `allow_reuse_address = False`, `daemon_threads = True`, только `127.0.0.1`. `log_message` переопределить (в app.log или no-op).
Порядок старта: bind на `127.0.0.1:port`; не удалось → `GET /api/ping` на этом порту: ответ `{"app":"to-uchet"}` → это уже запущенный экземпляр: открыть окно на него и выйти; иначе bind на порт 0 (свободный).
`server.json` пишется после успешного bind и удаляется при выходе. `serve_forever` — в фоновом потоке; главный поток ждёт условия выхода.

**Окно.** Найти `msedge.exe` (`%ProgramFiles(x86)%`/`%ProgramFiles%`\Microsoft\Edge\Application), иначе `chrome.exe`; запустить
`--app=http://127.0.0.1:PORT/ --user-data-dir=<profile> --no-first-run --no-default-browser-check --disable-background-mode --window-size=1400,900`.
Браузеров нет → `webbrowser.open`. Фронт шлёт `GET /api/ping` каждые 20 с.
**Условие выхода (единое):** процесс браузера завершился (или не запускался) И нет пингов дольше 90 с. Сторож считает время своими тиками; если между тиками сторожа прошло
больше 30 с (компьютер спал) — отметку последнего пинга сбросить на «сейчас». При выходе: `server.shutdown()`, `PRAGMA wal_checkpoint(TRUNCATE)`, закрыть базу, удалить `server.json`.
С `--no-window` сервер работает до Ctrl+C (без сторожа).

**HTTP.** `protocol_version` HTTP/1.0; каждый ответ с `Content-Length`. Запросы с `Host` не `127.0.0.1[:порт]`/`localhost[:порт]` → 403. CORS-заголовки не отдавать.
Тело запроса читается по `Content-Length`; `Content-Type` проверяется только если тело непустое: `application/json` для API, `application/octet-stream` для загрузки xlsx (лимит 40 МБ, больше → 400) и фото машины (лимит 25 МБ, больше → 400).
Статика — по собственной таблице типов (НЕ `mimetypes`): `.html` → `text/html; charset=utf-8`, `.js` → `text/javascript; charset=utf-8`, `.css` → `text/css; charset=utf-8`,
`.svg` → `image/svg+xml`, `.md`/`.txt` → `text/plain; charset=utf-8`, `.json` → `application/json`, прочее → `application/octet-stream`; путь нормализуется, выход за `static/` → 404;
`/` → `index.html`; `Cache-Control: no-store` на всё.

**SQLite.** Одно общее соединение `sqlite3.connect(path, check_same_thread=False)` + глобальный `threading.RLock`: каждый обработчик `/api` целиком выполняется под замком.
При открытии: `PRAGMA foreign_keys=ON; journal_mode=WAL; synchronous=NORMAL`; `row_factory = sqlite3.Row`; зарегистрировать `ulower(s)` = `(s or '').lower()` (LIKE в SQLite не понимает регистр кириллицы).
Каждая изменяющая операция — одна транзакция, при ошибке rollback. После каждого успешного POST/PUT/DELETE `/api` — `PRAGMA wal_checkpoint(PASSIVE)`: скопированный отдельно `to.db` (без `-wal`) содержит все записи.
`to.db` не открывается (`sqlite3.DatabaseError`) → файл (с `-wal`/`-shm`) переименовать в `to.db.broken-YYYYMMDD-HHMMSS`, скопировать на его место самую свежую копию из `backups/`, прошедшую `integrity_check` и содержащую `machines`/`events` (нет такой — пустая база); `/api/bootstrap` отдаёт `db_recovered: {broken, restored_from|null, error}`, фронт показывает баннер.
**Бэкапы:** при старте `backups/to-YYYYMMDD-HHMMSS.db` через `Connection.backup`, если в базе есть машины и последний бэкап старше 6 часов; хранить 30 последних.
Копии с датой в имени позже текущего времени (часы ПК сбились назад) последними не считаются (автокопия делается) и при ротации удаляются первыми.
Restore: имя должно соответствовать `^to-\d{8}-\d{6}\.db$` и существовать в `backups/`; сначала бэкап текущей базы, затем `sqlite3.connect(backup).backup(live_conn)` под замком, затем миграции. Файлы не подменять.

## 3. Схема БД

Даты — текст `YYYY-MM-DD`, время — `HH:MM`, метки времени — ISO локальное время. Показания — REAL.

```sql
meta(key TEXT PRIMARY KEY, value TEXT)                -- schema_version
settings(key TEXT PRIMARY KEY, value TEXT)            -- value = JSON
lists(id INTEGER PRIMARY KEY, list TEXT NOT NULL, value TEXT NOT NULL, pos INTEGER NOT NULL DEFAULT 0, UNIQUE(list, value))
rules(id INTEGER PRIMARY KEY, pos INTEGER, keyword TEXT NOT NULL, unit TEXT NOT NULL, interval REAL NOT NULL, warn REAL, daily_norm REAL)
regulation(id INTEGER PRIMARY KEY, pos INTEGER, to_kind TEXT NOT NULL, periodicity TEXT, works TEXT, oil TEXT, filters TEXT, parts TEXT, note TEXT)
machines(
  id INTEGER PRIMARY KEY, pos INTEGER,
  garage_no TEXT NOT NULL,          -- гаражный №; уникален без учёта регистра/крайних пробелов (проверка в коде: strip + upper)
  model TEXT DEFAULT '', mtype TEXT DEFAULT '',      -- «Тип машины» (Самосвал, Экскаватор…)
  plate TEXT DEFAULT '', vin TEXT DEFAULT '', site TEXT DEFAULT '',
  warranty TEXT DEFAULT '', photo TEXT DEFAULT '', note TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'В работе',           -- 'В работе' | 'На консервации' | 'Списана'
  reg_reading REAL, reg_date TEXT,                   -- показание при регистрации (дата может быть пустой)
  unit_override TEXT, interval_override REAL, warn_override REAL, norm_override REAL,  -- NULL = авто
  created_at TEXT, updated_at TEXT)
events(
  id INTEGER PRIMARY KEY,
  machine_id INTEGER NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
  date TEXT NOT NULL, shift TEXT DEFAULT '', kind TEXT NOT NULL,
  reading REAL,                    -- показание счётчика на момент события; для source='auto' ВСЕГДА NULL
  reading_estimated INTEGER NOT NULL DEFAULT 0,  -- 1 = показание взято из расчётного «≈», на счётчик не смотрели
  delta REAL,                      -- только для source='auto': сколько накинуто за этот день
  source TEXT NOT NULL DEFAULT 'manual',   -- 'manual' | 'mark' | 'auto' | 'import' | 'baseline'
  time_in TEXT, date_out TEXT, time_out TEXT,
  work TEXT DEFAULT '', parts_used TEXT DEFAULT '', parts_order TEXT DEFAULT '', order_done INTEGER NOT NULL DEFAULT 0,
  remarks TEXT DEFAULT '', who TEXT DEFAULT '',
  created_at TEXT, updated_at TEXT,
  date_unknown INTEGER NOT NULL DEFAULT 0)  -- 1 = настоящая дата не указана, `date` — дата внесения (расчёты идут по `date`); schema_version 2
CREATE INDEX ix_ev_machine_date ON events(machine_id, date);
CREATE INDEX ix_ev_date ON events(date);
CREATE UNIQUE INDEX ux_ev_auto ON events(machine_id, date) WHERE source='auto';
CREATE UNIQUE INDEX ux_ev_mark ON events(machine_id, date) WHERE source='mark';
tyres(id INTEGER PRIMARY KEY, machine_id INTEGER NOT NULL REFERENCES machines(id) ON DELETE CASCADE, tyre_no TEXT DEFAULT '', ttype TEXT DEFAULT '',
      position TEXT DEFAULT '', install_date TEXT, resource REAL, run REAL, install_reading REAL, note TEXT DEFAULT '')
units(id INTEGER PRIMARY KEY, machine_id INTEGER NOT NULL REFERENCES machines(id) ON DELETE CASCADE, unit TEXT DEFAULT '',
      resource REAL, install_reading REAL, note TEXT DEFAULT '')
```
Константа: `KIND_MARK = 'Отметка моточасов'` (и для км-техники тоже; на экранах показывать как «Отметка показаний»).

### Сиды (при создании пустой базы)

`settings`:
| key | по умолчанию | смысл |
|---|---|---|
| `default_interval_mh` / `default_warn_mh` / `default_norm_mh` | 250 / 50 / 11 | моточасная техника: интервал ТО, порог «скоро», суточная норма |
| `default_interval_km` / `default_norm_km` | 10000 / 100 | км-техника без своего правила (порог = 10% интервала) |
| `mh_types` | ["экскаватор","бульдозер","каток","грейдер","погрузчик"] | слова, по которым техника всегда на моточасах |
| `to_cycle` | ["ТО-1","ТО-2","ТО-3"] | круг видов ТО |
| `rate_window_days` | 60 | окно для фактической средней |
| `norm_use_fact` | true | «накинуть за день» берёт фактическую среднюю, если она есть |
| `tyre_red` / `tyre_yellow` | 0.9 / 0.75 | пороги износа шин и узлов |
| `org_contract` | "Приложение № 1 к договору (образец)" | шапка заказ-наряда |
| `org_mechanic_post` / `org_mechanic_name` | "Ст. механик" / "Иванов И. И." | |

`rules` (км-правила, по порядку): SHACMAN, ШАХМАН, ШАКМАН → км, 10000, 1000; SITRAK, СИТРАК → км, 15000, 1000; SANY, САНИ → км, 15000, 1000; VOLVO, ВОЛЬВО → км, 15000, 1000. `daily_norm` = NULL.
`lists`: `kinds` = ТО-1, ТО-2, ТО-3, Ремонт, Простой, Заметка, Отметка моточасов; `shifts` = День, Ночь;
`machine_statuses` = В работе, На консервации, Списана; `sites` = Северный, Южный, Мирный, Восточный, Западный, Промбаза;
`mtypes` = Самосвал, Экскаватор, Бульдозер, Каток, Грейдер, Погрузчик, Тягач;
`unit_names`, `tyre_types`, `positions` — из листа «Настройки» таблицы v8 (столбцы F, H, J; значения — в `app/db.py`, `SEED_LISTS`).
`regulation`: три строки ТО-1/2/3 из листа «Регламент ТО» таблицы v8 (`app/db.py`, `SEED_REGULATION`).

## 4. Правила расчёта (`logic.py`) — сердце программы

Все функции чистые: на вход — dict'ы/списки и дата отчёта `D` (+ `now`), на выход — dict. Никакого SQL. Все расчёты «на дату D» используют только события с `date <= D`.

### 4.1 Параметры машины (каждый разрешается отдельно)
- `unit`: `unit_override` → если `mtype` или `model` (без учёта регистра) содержит слово из `mh_types` → `'м/ч'` (км-правила не применяются)
  → первое по порядку км-правило, чьё `keyword` — подстрока `model` без учёта регистра → его `unit` → иначе `'м/ч'`.
- `interval`: `interval_override` → интервал сработавшего правила, если его unit совпал с итоговым `unit` → `default_interval_mh` / `default_interval_km` по единице.
- `warn`: `warn_override` → `warn` сработавшего правила (если правило применилось по единице и `warn` > 0) → для км: `round(0.1 × interval)`; для м/ч: `default_warn_mh`.
- `rule_norm`: `daily_norm` правила (если применилось и задано) → `default_norm_mh` / `default_norm_km`.
- `rule_keyword` — сработавшее слово или null. В `auto` отдаются значения без учёта override (для экрана «Машины»).
Тесты: «SANY SY215», mtype «Экскаватор» → м/ч, 250; «SANY», mtype «Самосвал» → км, 15000; «КАМАЗ» + unit_override «км» → км, 10000, порог 1000.

### 4.2 Текущее показание
Понизить показание может только явная **отметка**; событие ТО/ремонта может только поднять (как MAX в таблице). Накинутое — оценка, любой факт её перебивает.
- **Отметки** — события `kind == KIND_MARK`, `source != 'auto'`, `reading` не null, `date <= D`. К ним же относится `reg_reading`: с датой `reg_date` (если `reg_date > D` — не учитывается),
  а без `reg_date` — как отметка «раньше всех» (дата = −∞).
- **Прочие факты** — события остальных видов с `reading` не null, `reading_estimated = 0`, `source != 'auto'`, `date <= D`.
- `M` = отметка с самой поздней датой (при равных датах — изменённая позже: `updated_at`, затем `id`). `P` = наибольшее показание среди прочих фактов с датой ≥ даты `M` (нет отметок → среди всех).
  **База** = `P` (с его датой; при равных значениях — более поздняя дата), если `P > M.reading` или отметок нет; иначе `M`. Нет ни отметок, ни фактов → `reading = null`.
- **Накинутое** = сумма `delta` событий `source='auto'` с датой **строго больше** даты базы и ≤ D (база с датой −∞ → все auto), кроме дней, когда машина по событиям Ремонт*/Простой* в ремонте или простое (R10: ремонт, внесённый после начисления или задним числом, исключает накинутое за свои дни; сами auto-события не удаляются).
- `reading = base + auto_add`; `reading_estimated = auto_add > 0`; отдаются `reading_base`, `reading_base_date` (null для −∞), `reading_auto_add`.
Обязательные тесты: (1) отметка 153000 от 01.10 + ТО 150000 от 02.10 → reading 153000; (2) отметка 152000 от 01.09 + ремонт 148000 от 05.09 → 152000;
(3) отметка 152000 от 01.09 + ремонт 153500 от 05.09 → 153500, база 05.09; (4) отметка 150000 от 01.10, auto +100 от 01.10 и +100 от 02.10 → 150100 (auto того же дня не считается);
(5) затем отметка 149900 от 02.10 → 149900, без «≈»; (6) reg_reading 47415 без даты + ремонт 43520 → 47415; (7) reg 47415 без даты + отметка 43520 → 43520; (8) только auto без базы → null.

### 4.3 Статус ТО
- ТО-события: `kind` начинается с `ТО-`, `reading` не null (оценочные тоже годятся), `date <= D`. `to_count` — их число.
- Последнее ТО = с самой поздней датой (при равных — наибольшее показание, затем больший id). `last_to = {event_id, kind, reading, date}`.
- `next_to_at = last_to.reading + interval`; `remaining = next_to_at − reading`, округлить (км — до целого, м/ч — до 0.1) ДО сравнений.
- `to_status`: `no_reading` («⚪ Нет показаний») → `no_to` («⚪ Внесите ТО») → `overdue`, если `remaining <= 0` («🔴 Просрочено») → `soon`, если `remaining <= warn` («🟡 Скоро ТО») → `ok` («🟢 В норме»).
- При `overdue`: `overdue_by = −remaining`, `missed = floor(overdue_by / interval) + 1` (R5). Иначе оба 0.
- `next_to_kind`: вид сопоставляется с кругом `to_cycle` по префиксу (kind начинается с элемента круга, самое длинное совпадение); следующий по кругу; нет ТО или вид не из круга → первый в круге.

Контрольные примеры (обязательные тесты):
| последнее ТО | сейчас | интервал/порог | ожидается |
|---|---|---|---|
| 150000 | 153000 | 10000/1000 | next_to_at 160000, remaining 7000, ok |
| 125000 | 134200 | 10000/1000 | remaining 800, soon |
| 125000 | 135000 | 10000/1000 | remaining 0, overdue, missed 1 |
| 150000 | 162000 | 10000/1000 | overdue_by 2000, missed 1 |
| 150000 | 172500 | 10000/1000 | overdue_by 12500, missed 2 |
| 1000.3 м/ч | 1250.3 | 250/50 | remaining 0.0, overdue (без плавающей ошибки) |
| ТО-3 последнее | — | — | next_to_kind ТО-1; «ТО-2 сезонное» → ТО-3 |
| нет ТО | 5000 | — | no_to, next_to_kind ТО-1 |

### 4.4 Состояние на дату D (как в таблице), по порядку
1. `status == 'На консервации'` → `conserved` «🅿 Консервация».
2. Есть `Ремонт*` с `date <= D` и (`date_out` пуст или `date_out > D`) → `repair` «🔴 На ремонте».
3. Есть `ТО-*` с `date == D` и `source != 'baseline'` → `to` «🔧 На ТО».
4. Есть `Простой*` с `date <= D` и (`date_out` пуст или `date_out > D`) → `idle` «🟡 Простой».
5. Иначе `line` «🟢 На линии».
`state_event_id`, `state_work` (поле `work` определяющего события; если подходящих несколько — с самой поздней датой, затем id), `state_since` (ISO старт события).
Машины `Списана` в парк/отчёты/план/показания не попадают (остаются в справочнике и истории).

### 4.5 Длительности
- Старт события = `date` + `time_in` (нет времени → 00:00). Конец = `date_out` + `time_out` (нет времени → 00:00 дня выхода).
- `duration_seconds` события: конец − старт; `date_out` пуст и событие `Ремонт*`/`Простой*` → до `now`, `open: true`; `date_out` пуст и другой вид → `null`; отрицательная → `duration_error: true`.
- За сутки D: окно `[D 00:00, конец)`, конец = `min(D+1 00:00, now)` если D — сегодня; D в прошлом → D+1 00:00; D в будущем → окно пустое (все часы 0).
  `repair_hours = min(длина окна, длина ОБЪЕДИНЕНИЯ пересечений интервалов Ремонт* с окном)` (открытые — до конца окна);
  `idle_hours = min(длина окна − repair_hours, объединение для Простой*)`; `line_hours = max(0, длина окна − repair − idle)`. Для консервации все три = null.
- `state_seconds` (для repair/idle) = конец определяющего события (если пусто — `now`) минус его старт, не меньше 0.
- `last_repair_seconds` — длительность ремонта с наибольшим (`date_out`, `time_out`, id) среди закрытых с `date_out <= D`; нет → null.

### 4.6 Средняя наработка и прогноз
- Точки = отметки и прочие факты из 4.2 с непустой датой ≤ D, по одной на дату (наибольшее значение дня); `reg_reading` — точка только при заданном `reg_date`.
  Из последовательности выбросить точки, меньшие предыдущей оставленной (немонотонные).
- `rate_source = 'fact'`: позднее = последняя точка; раннее = самая ранняя точка в окне `[D − rate_window_days, D]`; если она совпадает с поздней — последняя точка до окна.
  Условия: разница дат ≥ 3 суток и прирост > 0 → `rate = прирост / сутки`, `rate_days` = сутки. Иначе `rate_source = 'norm'`, `rate` = норма машины (`norm_override` → `rule_norm`).
- `daily_norm`, `daily_norm_source` (для «накинуть за день»): `'override'` (norm_override) → `'fact'` (если `norm_use_fact` и есть fact) → `'rule'` (норма правила задана) → `'default'`.
  Округление: км — до целого, м/ч — до 0.1.
- Прогноз: `remaining > 0` и `rate > 0` → `forecast_days = ceil(remaining / rate)`, `forecast_date = D + forecast_days`; иначе оба null.

### 4.7 Шины и узлы
Пороги из `settings.tyre_red` / `tyre_yellow` (для узлов те же). `resource` пуст или 0 → `left`, `wear` = null, статус `none` (пустая подпись).
- Шина: `run_eff` = `текущее показание машины − install_reading`, если `install_reading` задан, иначе `run`. `left = resource − run_eff`, `wear = run_eff / resource`.
  wear ≥ red → `red` «🔴 Менять»; ≥ yellow → `yellow` «🟡 Скоро менять»; иначе `green` «🟢 Норма»; нет `run_eff` → `none`.
- Узел: `run = текущее показание − install_reading`; `run < 0` → `check` «⚪ Проверьте показание установки»; red «🔴 Скоро замена/ремонт», yellow «🟡 Контроль», green «🟢 Норма».

### 4.8 Проверка данных (`data_warnings`)
По машине (тексты по-русски, с датой и видом события): показание отметки/факта ниже более раннего факта («счётчик пошёл назад») — кроме ТО-событий (ТО «задним числом» — штатно);
`reg_reading` участвует в этой проверке как опора только при заданном `reg_date` (без даты — не сравнивается: его правят как «текущий пробег» в листе «Машины»);
ТО без показания; `date_out` раньше `date`; дата события в будущем (относительно сегодня); время выхода без даты выхода; отрицательная длительность.
Общий список отдаётся в `/api/fleet`: `warnings: [{machine_id, garage_no, event_id, text}]`.

### 4.9 Сборка представлений (производительность)
`services.build_views(conn, D, now, machine_ids=None) -> list[MachineView]` — единственная точка получения MachineView; её используют fleet, plan, card, view, tyres, units, machines, daily, to-done.
Число SQL-запросов фиксировано и не зависит от числа машин: (1) machines; (2) `SELECT id, machine_id, date, kind, reading, reading_estimated, delta, source, updated_at, date_unknown FROM events WHERE reading IS NOT NULL OR source='auto'`;
(3) полные строки событий `Ремонт*`/`Простой*`/`ТО-*`; (4) события с `parts_order<>''` и `order_done=0`; (5) tyres; (6) units;
(7) последняя запись журнала каждой машины для `last_note` (раздел 5) — **одним запросом с оконной функцией**: `ROW_NUMBER() OVER (PARTITION BY machine_id ORDER BY date DESC, id DESC)` по событиям `source <> 'auto'`, `kind <> KIND_MARK`, `date <= D`, берётся `rn = 1`;
settings и rules — один раз. Группировка по `machine_id` в Python, дальше чистые функции. Итого не больше 9 SQL-запросов на любое число машин (7 + настройки + правила) — это проверяет тест в `test_services` (счёт через `set_trace_callback`).
`daily/preview` считает состояния по дням из той же выборки. Ориентир: `/api/fleet` на 250 машин и 100 000 событий ≤ 1.5 с (тест с генерацией).

## 5. API (`server.py` + `services.py`)

Все ответы JSON UTF-8. Ошибка: HTTP 400 `{"error": "текст по-русски"}`; не найдено — 404 `{"error": ...}`; «нужно подтверждение» — HTTP 409
`{"error": "...", "needs_confirm": true, "warnings": ["строка", ...], "items": [{machine_id, garage_no, text}]}` — клиент показывает вопрос и повторяет запрос с `"force": true`.

**Форматы полей (вход и выход едины).** Числа, даты и время (`reading`, `delta`, `reg_reading`, `reg_date`, `time_in`, `date_out`, `time_out`, `install_date`, `resource`, `run`, `install_reading`, `*_override`) —
значение или `null`; на входе `""` равно `null`. Текстовые поля — всегда строка; `null` на входе равно `""`. Числа на входе могут прийти строкой («153 000», «1250,5») — сервер нормализует.
`order_done`, `open`, `reading_estimated`, `duration_error`, `date_unknown` — boolean. **PUT — частичное обновление**: меняются только переданные ключи, валидация — по итоговой записи; проверки исключают само редактируемое событие. Изменён `reading`, а `reading_estimated` не передан — `reading_estimated = 0` (исправленное показание — факт).

### Объект MachineView (расчётная строка машины)
Все поля таблицы `machines` плюс:
```
unit, interval, warn, rule_keyword, auto: {unit, interval, warn, norm, rule_keyword},
reading, reading_base, reading_base_date, reading_estimated, reading_auto_add,
state ('line'|'repair'|'to'|'idle'|'conserved'), state_label, state_event_id, state_work, state_since, state_seconds, last_repair_seconds,
line_hours, repair_hours, idle_hours,
to_status ('overdue'|'soon'|'ok'|'no_to'|'no_reading'), to_status_label, to_count,
last_to ({event_id, kind, reading, date, date_unknown} | null), next_to_kind, next_to_at, remaining, overdue_by, missed,
rate, rate_source ('fact'|'norm'), rate_days, daily_norm, daily_norm_source,
forecast_date, forecast_days,
parts_order (тексты незакрытых заявок «заказать запчасти» по машине с датой события ≤ D через «; », иначе ""),
last_note ({event_id, date, date_unknown, kind, kind_title, text, open} | null),
tyres_red, units_attention (red + yellow), data_warnings ([строки])
```

**`last_note`** — последняя запись журнала машины (колонка «Что делали» на главном экране). Берётся запись с наибольшей (`date`, затем `id`) среди событий машины
с `source != 'auto'`, `kind != KIND_MARK` и `date <= D` (накинутое и отметки показаний не считаются; ТО, в том числе внесённое как последнее с пометкой «дата не указана», считается). Нет такой записи — `null`.
- `event_id`, `date`, `date_unknown` (boolean), `kind` — как в событии; `kind_title` — название вида для экрана (как `logic.kind_title`).
- `text` — первое непустое (после `strip`) из `work`, `remarks`, `parts_used`, не длиннее 300 символов; все три пусты — `""`.
- `open` — boolean: `Ремонт*`/`Простой*` без `date_out`.
Поле есть во всех ответах, где есть MachineView: `/api/fleet`, `/api/machines`, `/api/machines/{id}/view`, `machine` в `/api/machines/{id}/card`, `rows` и `no_to` в `/api/plan`, ответ создания/правки машины, `machine` в ответе `/api/to-done`.
Считается в `services._load`/`_views` одним запросом на все машины (4.9) — на дату отчёта `D`, как и остальные поля.

### Объект EventView
Поля `events` + `garage_no`, `model`, `unit`, `duration_seconds`, `open`, `duration_error`, `is_to` (kind начинается с «ТО-»).

### Эндпоинты

| Метод и путь | Вход | Выход |
|---|---|---|
| GET `/api/ping` | — | `{"app":"to-uchet","version":"1.0.0"}` |
| GET `/api/bootstrap` | — | `{version, today, now, data_dir, first_run (нет машин), settings:{...}, lists:{name:[values]}, rules:[...], regulation:[...], db_recovered: null | {broken, restored_from, error}}` |
| GET `/api/fleet?date=` | дата отчёта (умолч. сегодня) | `{date, summary:{total, line, repair, to, idle, conserved, overdue, soon, ok, no_to, no_reading, tyres_red, units_attention}, rows:[MachineView], warnings:[...]}` — без списанных, порядок `pos, id` |
| GET `/api/machines` | — | `[machine]` — все, включая списанные: сырые поля + `unit, interval, warn, auto:{...}, reading, reading_estimated, to_status, next_to_kind` |
| GET `/api/machines/{id}/view?date=` | | MachineView (для диалогов) |
| POST `/api/machines` | поля машины | созданная машина (как в GET) |
| PUT `/api/machines/{id}` | поля, `force` | машина. Смена итоговой единицы при наличии событий с показаниями → 409 «показания в журнале останутся в прежних единицах» |
| DELETE `/api/machines/{id}` | `?force=1` | 409 needs_confirm, если есть события/шины/узлы/фото (в `warnings` — «Будет удалено фото: N» и т. п.); с force — каскад и удаление папки `photos/{id}/`. Файлы фото удалить не удалось (открыты в другой программе) → 400, машина остаётся |
| GET `/api/machines/{id}/card?date=` | | `{machine: MachineView, events:[EventView] (все, новые сверху, вкл. auto), tyres:[...], units:[...] (формат как /api/tyres, /api/units), regulation_next: {строка регламента для next_to_kind} | null, photos:[{name, size, mtime, url}]}` — `photos` тот же список, что отдаёт `GET /api/machines/{id}/photos`, чтобы карточка грузилась одним запросом |
| GET `/api/machines/{id}/photos` | — | `[{name, size, mtime, url}]` — старые первыми (по `mtime`, при равенстве по имени). `size` — байты; `mtime` — ISO локальное время `YYYY-MM-DDTHH:MM:SS`; `url` = `/api/machines/{id}/photos/<имя, percent-encoded>`. Нет фото — `[]`; нет машины → 404 «Машина не найдена» |
| POST `/api/machines/{id}/photos?name=<имя файла, percent-encoded>` | тело — байты файла, `Content-Type: application/octet-stream`, не больше 25 МБ | `{name, size, url}` — `name` уже в виде, под которым файл сохранён. Нет машины → 404; иначе ошибки 400 — см. «Фото машин» ниже |
| GET `/api/machines/{id}/photos/{name}` | `{name}` — имя из `url` | файл; `Content-Type` по расширению (jpg/jpeg → `image/jpeg`, `image/png`, `image/webp`, `image/gif`), `Cache-Control: no-store`. Нет машины, недопустимое имя или нет файла → 404 |
| DELETE `/api/machines/{id}/photos/{name}` | | `{ok: true}`. Нет машины, недопустимое имя или нет файла → 404; файл занят другой программой → 400. Удалено последнее фото — пустая папка `photos/{id}/` тоже удаляется |
| GET `/api/events` | `machine_id, kind, from, to, q, include_auto (0/1, умолч. 0), limit (умолч. 500), offset` | `{total, rows:[EventView]}` — `date desc, id desc`. `kind=ТО` — все ТО-*. `q` — подстрока без учёта регистра (через `ulower`) по work/parts_used/parts_order/remarks/who/garage_no |
| POST `/api/events` | поля события (`machine_id` или `garage_no`), `force` | EventView |
| PUT `/api/events/{id}` | поля, `force` | EventView. Для `source='auto'` → 400 «накинутое не редактируется — удалите или отмените период» |
| DELETE `/api/events/{id}` | | `{ok:true}` |
| POST `/api/events/{id}/order-done` | `{done: bool}` | EventView |
| POST `/api/to-done` | `{machine_id, kind, date, reading, reading_estimated?, current_reading?, work, parts_used, remarks, who, shift, force}` | `{event: EventView, machine: MachineView, note}`. Создаёт событие ТО (`source='manual'`). `current_reading` задан и отличается от текущего факта (или текущее показание расчётное «≈» — тогда даже при совпадении) — upsert отметки на СЕГОДНЯ (как в bulk). `current_reading < reading` → 400. Показание ТО ниже текущего — штатно, без 409 (R8); `note` = «ТО внесено на меньшем показании; текущее осталось N» в этом случае |
| POST `/api/readings/bulk` | `{date, items:[{machine_id, reading}], baseline:[{machine_id, kind, reading, date?, work?}], force}` | `{marks_saved, to_saved, errors:[{machine_id, garage_no, error, part: 'baseline'|'item'}]}` (`part` — какая часть строки не сохранена; остальное в строке сохранено). См. «Пакетные показания» ниже |
| GET `/api/daily/preview?from=&to=` | период (умолч. сегодня–сегодня) | `{from, to, last_auto_date, suggested_from, gap_days, rows:[{machine_id, garage_no, model, site, unit, reading, reading_estimated, days_to_add, norm, norm_source, add, checked, state, state_label, skip_reason}]}` |
| POST `/api/daily/apply` | `{from, to, items:[{machine_id, norm}]}` | `{created, skipped, machines}` |
| POST `/api/daily/undo` | `{from, to}` | `{deleted}` — удаляет auto-события периода |
| GET `/api/plan?horizon_days=30` | | `{date, horizon_days, rows:[MachineView + regulation:{works, oil, filters, parts} | null], no_to:[MachineView], materials:[{to_kind, model, count, garage_nos:[...], oil, filters, parts}], parts_orders:[EventView]}` |
| GET `/api/workorder?machine_id=&date=` | | см. ниже |
| GET/POST `/api/tyres`, PUT/DELETE `/api/tyres/{id}` | `?machine_id=`; POST/PUT принимают `machine_id` | строки + `run_eff, left, wear, status, status_label, garage_no, model, unit` |
| GET/POST `/api/units`, PUT/DELETE `/api/units/{id}` | `?machine_id=` | строки + `run, left, wear, status, status_label, garage_no, model, unit_of_measure` |
| PUT `/api/settings` | `{key: value, ...}` | все настройки |
| PUT `/api/lists/{name}` | `["значение", ...]` | список. `machine_statuses` → 400 (не редактируется); в `kinds` значения ТО-1/2/3, Ремонт, Простой, Заметка, Отметка моточасов удалить нельзя (сервер возвращает их на место) |
| PUT `/api/rules` | `[{keyword, unit, interval, warn, daily_norm}]` (полная замена) | правила |
| PUT `/api/regulation` | `[{to_kind, periodicity, works, oil, filters, parts, note}]` (полная замена) | регламент |
| POST `/api/import/xlsx?dry_run=0|1` | тело — байты файла, `Content-Type: application/octet-stream` | `{ok, dry_run, machines_new, machines_updated, events_new, events_skipped, events_updated, tyres_new, tyres_skipped, units_new, units_skipped, orders_open, settings_updated, warnings:[...]}` |
| GET `/api/export/xlsx` | | файл; `Content-Disposition: attachment; filename="TO_export_YYYY-MM-DD.xlsx"; filename*=UTF-8''<percent-encoded «Учёт_ТО_YYYY-MM-DD.xlsx»>` |
| GET `/api/backups` | | `[{name, size, mtime}]` новые сверху |
| POST `/api/backups` | | `{name}` — создать бэкап сейчас |
| POST `/api/backups/restore` | `{name}` | `{ok}` |

**Пакетные показания (`/api/readings/bulk`).** Порядок: сначала `baseline`, затем `items`; проверки учитывают строки этого же пакета. Элементы с пустым `reading` пропускаются.
- `items` → upsert события `kind=KIND_MARK, source='mark'` по ключу (machine_id, date): найдено — UPDATE `reading` (+ `updated_at`), иначе INSERT. События других source не трогаются.
- `baseline` → событие ТО (`source='baseline'`); `date` пуста → общая `date` пакета и `date_unknown=1` (на экранах «дата не указана», расчёты — по дате пакета); дубль (машина, дата, вид, показание) не создаётся.
  Правка события с новой `date` снимает `date_unknown` (если сам признак не передан).
- Жёстко невалидные строки не сохраняются и возвращаются в `errors[]`; остальные сохраняются одной транзакцией (HTTP 200).
- Если хотя бы у одной строки есть мягкое предупреждение и нет `force` — HTTP 409, НИЧЕГО не сохранено, `items:[{machine_id, garage_no, text}]`. С `force` — сохраняется.
- Диалог «Показание» и `current_reading` в to-done используют этот же upsert.

**«Накинуть за день» (`/api/daily/*`).** День `d` начисляется машине, только если: `from <= d <= to`, `d <= сегодня`, у машины есть база показаний и `d >` `reading_base_date` (на сегодня),
состояние на `d` — `line` или `to` (не `repair`/`idle`/`conserved`, не списана), auto-события на `d` ещё нет.
`days_to_add` — число таких дней; `add = norm × days_to_add`; `state`, `state_label` — на дату `to`; `checked = days_to_add > 0`; `skip_reason` — почему 0
(«в ремонте», «простой», «консервация», «нет показаний», «показание внесено сегодня», «уже накинуто»; ремонт/простой на `d` проверяется раньше «уже накинуто»). `apply` создаёт события ровно по тем же правилам:
`kind=KIND_MARK, source='auto', delta=norm, reading=NULL, who='авто'`; одной транзакцией. `from > to` или `to > сегодня` → 400.
`last_auto_date` — максимальная дата auto-событий; `gap_days` — дней с неё до сегодня; `suggested_from` = `last_auto_date + 1`, если `1 < gap_days <= 7`, иначе сегодня.

**План (`/api/plan`).** `rows` — машины с `to_status` overdue/soon и те, у кого `forecast_days <= horizon_days`; сортировка: overdue по убыванию `overdue_by`, затем остальные по возрастанию `remaining`.
`regulation` — строка регламента для `next_to_kind`. `no_to` — машины `no_to`/`no_reading`. `materials` — группировка `rows` по (`next_to_kind`, `model` (trim, без учёта регистра)).
`parts_orders` — события с непустым `parts_order` и `order_done=0`, новые сверху.

**Заказ-наряд (`/api/workorder`).** `{contract, date, machine:{mtype, model, plate, vin, garage_no, reading, unit}, works:[{name, hours}], parts:[{name}], time_start, time_end, repair_in, repair_out, repair_total_seconds, mechanic_post, mechanic_name}`.
`works` — события `ТО-*`/`Ремонт*` машины за дату: `name` = «вид — work», `hours` = длительность/3600 (1 знак) или null; у незакрытого ремонта (нет даты выхода) `hours` = null и в `repair_total_seconds` он не входит (как пустой «Журнал»!I в v8). `parts` — `parts_used`, если пусто — `parts_order`.
`time_start` — минимальное `time_in`, `time_end` — максимальное `time_out` этих событий. `repair_in`/`repair_out` — самая ранняя дата заезда / поздняя дата выхода
с временем `THH:MM:00` только из реально заданных `time_in`/`time_out` этой даты; времени нет — только дата `YYYY-MM-DD` (не «00:00»). Соответствие полей формы — как в таблице v8 (не менять!):
«Заводской номер» ← `plate`, «Инвентарный номер» ← `vin`, «Бортовой номер» ← `garage_no`, «Наработка» ← `reading` с единицей машины.

**Фото машин** (VIN-табличка, документы, что угодно). Файлы лежат в `<папка данных>/photos/<machine_id>/<имя>`; в базе ничего не хранится — список строится по содержимому папки
(видны файлы с расширением jpg/jpeg/png/webp/gif, без `..`, `/`, `\`, `:` и управляющих символов в имени, не начинающиеся с точки; файл, положенный в папку вручную, тоже виден). В резервные копии `backups/` фото не входят.
- Загрузка. Расширение — jpg/jpeg/png/webp/gif, иначе (или расширения нет) 400 «Можно загружать только фото: jpg, png, webp, gif». Первые байты проверяются по сигнатуре: JPEG `FF D8`, PNG `89 50 4E 47 0D 0A 1A 0A`, WEBP `RIFF…WEBP`, GIF `GIF8`;
  не совпали с расширением → 400 «Файл «имя.ext» не похож на фото FORMAT: содержимое не совпадает с расширением». Пустое тело → 400 «Файл пустой — выберите фото». Больше 25 МБ → 400 «Фото слишком большое (больше 25 МБ)» (лимит действует по `Content-Length`, файл не сохраняется).
  Тело не `application/octet-stream` → 400 «Файл нужно отправлять как application/octet-stream». Параметр `name` не задан или пуст (вставка из буфера) — расширение определяется по содержимому.
- Имя. Берётся базовое имя без пути (разделители `/` и `\`), остаются буквы (латиница и кириллица), цифры, `. _ -` и пробел; повторы этих знаков схлопываются в один; пробелы и точки по краям основы убираются; расширение — в нижнем регистре;
  имя вместе с расширением — не длиннее 80 символов. Имя устройства Windows (CON, PRN, AUX, NUL, COM1–9, LPT1–9) получает приставку `foto-`. Пустая основа → `foto-YYYYMMDD-HHMMSS.<ext>`.
  Имя занято (без учёта регистра — как в Windows) → `-2`, `-3`… перед расширением. Файл пишется во временный `.<имя>.part` и затем переименовывается: недописанный файл в списке не появляется.
- Скачивание и удаление. `{name}` — один сегмент пути (уже раскодированный). Любая попытка выйти из папки машины (`..`, `/`, `\`, `:`, `%00`), точка в начале имени, чужое расширение → 404 «Фото не найдено».
- Удаление машины с фото — 409 (фото считается историей), с `force` папка `photos/{id}/` удаляется вместе с машиной (иначе номер удалённой машины, доставшийся новой, показывал бы чужие фото).

### Валидация событий (`services.py`)
Жёстко (400): нет машины/даты/вида; дата не `YYYY-MM-DD`; время не `HH:MM`; показание не число или < 0; `date_out < date`; `date_out == date`, оба времени заданы и `time_out < time_in`;
`time_out` без `date_out` (клиент при закрытии «только временем» сам подставляет дату выхода: = дате заезда, или +1 день, если время выхода < времени заезда);
`ТО-*` без показания; правка `source='auto'`.
Мягко (409, проходит с `force`): дата в будущем; для НЕ-ТО событий: показание меньше `reading_base` при дате ≥ `reading_base_date` («меньше последнего факта N от даты — это исправление?»; для отметки-upsert той же даты не спрашивать),
или больше фактического показания с более поздней датой; скачок: прирост от `reading_base` больше `max(3 × interval, 3 × daily_norm × дней от reading_base_date)` (нет базы или её даты — не проверять);
у машины уже есть открытый `Ремонт*`, а создаётся ещё один; для ТО — показание меньше `reading_base` больше чем на интервал ТО при дате ≥ `reading_base_date` («проверьте разрядность»; просто ниже факта — штатно, R8).
Клиент: в полях показаний машин в км «153.000»/«153,000» (один разделитель и ровно 3 цифры) читается как 153 000.
Если `reading` не задан у события не-ТО — остаётся null (текущее показание не меняется).

## 6. Импорт / экспорт (`xlsx_io.py`)

`import_v8(conn, file_bytes, dry_run=False) -> dict`, `export_xlsx(conn) -> bytes`. Вызывающий (`server.py`) держит замок; функции сами управляют транзакцией.

**Чтение (openpyxl `read_only=True, data_only=False`).** Каждый лист — одним проходом: `ws.reset_dimensions()`, затем
`for row in ws.iter_rows(min_row=..., max_col=<последний нужный столбец>, values_only=True)`; `ws.cell`, `ws[...]`, `ws.max_row` НЕ использовать; в конце `wb.close()`.
Значение ячейки принимается, только если его тип str/int/float/bool/datetime/date/time/timedelta; строка, начинающаяся с `=`, и любой другой объект (ArrayFormula и т.п.) считаются пустыми.
Пустая строка — пусты все импортируемые столбцы после нормализации. Прекращать чтение листа после 2000 пустых строк подряд (файлы бывают на 100 000 строк с формулами).
Признак формата: есть листы `Машины` и `Журнал`. Иначе — 400 «Это не таблица учёта ТО версии 8».
Данные с 5-й строки:
- `Машины`: B модель, C гаражный №, D госномер, E VIN, F показание при регистрации, G участок, H гарантия, I фото, J примечание, K статус, L тип машины.
  Upsert по гаражному № (strip + upper); нет точного совпадения, но есть ровно одна машина с тем же номером без учёта латиницы/кириллицы и пробелов — обновляется она (+ предупреждение «в программе записана как …»); новая машина с латинскими буквами в номере — предупреждение. Пустой статус → «В работе». Строка без гаражного № — пропуск с предупреждением. `reg_date` не трогать.
- `Журнал` и `Архив журнала`: A дата, B смена, C машина, D событие, E показание, F заехала-время, G вышла-дата, H вышла-время, J что делалось, K масло/запчасти, L заказать, M замечания, N кто.
  Строки без машины, даты или вида — пропуск с предупреждением. Машина не из списка — создать машину-заглушку (только гаражный №) + предупреждение. `source='import'`.
  Жёсткая валидация НЕ применяется: ТО без показания, `date_out < date`, время без даты импортируются как есть и попадают в `warnings`; нераспознанное время → NULL + предупреждение.
  Дубль (машина, дата, вид, показание, work; у записи без work и не ТО/ремонт/простой/отметка — ещё и remarks) — не вставлять; ключи существующих событий загрузить одним SELECT; вставка `executemany`.
  Совпавшее с событием базы: пустые в базе shift/time_in/date_out/time_out/parts_used/parts_order/remarks/who, заполненные в таблице, дописываются (`events_updated`); противоречие (оба заполнены и различаются) — предупреждение с номером строки, база не меняется; иначе `events_skipped`.
  `order_done = 1` у всех событий с непустым `parts_order`, кроме открытых `Ремонт*`/`Простой*` (`date_out` пуст) — как столбец K «Главной» таблицы; `orders_open` — сколько осталось открытыми.
  Обратный импорт экспорта: строка KIND_MARK с пустым показанием и «Кто» вида «авто +N» → `source='auto'`, `delta=N`.
  Служебный столбец P выгрузки — метки через «;»: `baseline`/`mark` (источник), `≈` (`reading_estimated`), `без даты` (`date_unknown`); на листе «Парк» у такого последнего ТО — «дата не указана».
- `Шины`: A № шины, B гар. №, C тип, D позиция, E дата установки, F ресурс, G пробег, K примечание. `Узлы`: A гар. №, B узел, C ресурс, D показание при установке, I рекомендация.
  Внутри файла дубль — только строка, совпавшая целиком (пропуск + предупреждение с номером строки): в таблице заказчика в «№ шины» бывает марка («СИТ»), а различает шины серийный № в примечании.
  Уже есть в базе (без предупреждения): шина — та же машина + № + тип + позиция + дата установки + примечание; узел — та же машина + узел + показание установки + ресурс + рекомендация. Пропущенные считаются в `tyres_skipped`/`units_skipped`.
- `Настройки`: B4 интервал м/ч, B5 порог м/ч; списки D (события), F (узлы), H (шины), J (позиции), L (участки) — добавить недостающие значения;
  N–P (км-правила) — если не пусто: ЗАМЕНИТЬ км-правила списком из таблицы (порядок, интервалы, пороги; `daily_norm` совпадающих слов сохранить), затем добавить VOLVO/ВОЛЬВО (15000/1000), если таких слов нет.
  `Регламент ТО` A–G — заменить регламент, если лист не пуст.
- Разбор значений. Дата: `datetime`/`date` → ISO; строки `ДД.ММ.ГГГГ`/`ДД.ММ.ГГ`/ISO → ISO; число 20000…80000 → серийная дата от 1899-12-30; иначе предупреждение.
  Время: `time`/`datetime`/`timedelta`/доля суток 0..1/строки «9:30», «930», «9.30» → `HH:MM`. Показание: число; строка → убрать пробелы и неразрывные пробелы, запятую заменить точкой, отбросить хвост из букв; не число → предупреждение, null.
- Участки приводить к написанию из списка `sites`, если совпадают без учёта регистра («СЕВЕРНЫЙ» → «Северный»).
- Если у машины сработало км-правило, а `mtype` пуст и слово — SANY/САНИ/VOLVO/ВОЛЬВО → предупреждение «проверьте единицу учёта (км или моточасы)».
- Всё в одной транзакции; `dry_run` → rollback и только отчёт.

**Экспорт:** книга с листами `Парк` (расчётные поля fleet на сегодня), `Машины`, `Журнал` (все события; auto — с пустым показанием и «Кто» = «авто +<delta>»), `Шины`, `Узлы` — значения без формул,
шапка в 4-й строке (данные с 5-й, заголовок листа в 1-й), жирная, закреплённая, автофильтр. Порядок колонок `Машины`, `Журнал`, `Шины`, `Узлы` — как в таблице v8 (файл импортируется обратно).

## 7. Фронтенд

Дизайн-контракт — три правила в начале `app/static/UI_KIT.md` (цвета только через переменные, статус всегда словом). Главное правило: **всё, что вводит человек, — на кремовом фоне `--input-bg`; всё, что считает программа, — на белом/сером.**
Раздел описывает то, что сделано в коде (ред. 3, v2); замысел и сценарий механика — в `docs/КОНЦЕПЦИЯ_v2.md`. Экранов «Парк», «Показания», «Журнал», «Отчёты» больше нет: они слиты в один главный экран.

### 7.1 Каркас (`index.html`, `app.js`, `api.js`, `ui.js`, `app.css`)
- Hash-роутер (`ROUTES` в `app.js`):

  | Адрес | Заголовок | Модуль страницы | Подсвеченный пункт меню |
  |---|---|---|---|
  | `#/fleet` (по умолчанию) | Главная | `pages/fleet.js` | Главная |
  | `#/machine/{id}` | Карточка машины (в заголовке — «гар. № · модель») | `pages/card.js` | Главная |
  | `#/plan` | Список на ТО | `pages/plan.js` | Список на ТО |
  | `#/workorder` | Заказ-наряд | `pages/workorder.js` | Заказ-наряд |
  | `#/machines` | Машины | `pages/machines.js` | Машины |
  | `#/tyres` | Шины | `pages/tyres.js` | Шины |
  | `#/units` | Узлы | `pages/units.js` | Узлы |
  | `#/settings` | Настройки | `pages/settings.js` | Настройки |
  | `#/welcome` | Первый запуск | `pages/welcome.js` | — (в меню нет) |

  Query-параметры в hash допустимы, роутер отдаёт их в `params` (все значения — строки): `#/workorder?machine_id=5&date=2026-10-02`, `#/machine/5?date=2026-10-01` (карточка на дату), `#/plan?horizon=14`,
  `#/settings?tab=data`, `#/fleet?to_status=overdue` и `#/fleet?state=repair` (включают чип-фильтр главной, см. 7.2). Неизвестный адрес — экран «Экран не найден» с кнопкой «На главную».
- **Старые адреса не ломаются** (закладки, ссылки): `#/readings` и `#/reports` → `#/fleet`; `#/journal?machine_id=N` → `#/machine/N` (история теперь в карточке); `#/journal` без машины → `#/fleet`.
  Перенаправление делается через `history.replaceState`: старый адрес в истории не остаётся, «Назад» не зацикливается.
- Пока в базе нет машин (`first_run`), открываются только `#/welcome`, `#/settings`, `#/machines`; любой другой адрес ведёт на `#/welcome`. `#/welcome` при непустой базе → `#/fleet`.
- Слева тёмная боковая панель с навигацией (иконки из `icons/*.svg`), ниже — версия программы:

  ```
  Главная          #/fleet       truck
  Список на ТО     #/plan        calendar-check
  СЛУЖЕБНОЕ        — подпись-заголовок группы (.nav-caption: 11.5 px, заглавные, цвет --sidebar-caption), не ссылка
  Заказ-наряд      #/workorder   clipboard-text
  Машины           #/machines    list-details
  Шины             #/tyres       wheel
  Узлы             #/units       engine
  ———              разделитель (.nav-sep)
  Настройки        #/settings    settings
  ```
  Сверху строка: название экрана, дата и глобальный поиск машины по гаражному №, госномеру и VIN (выбор из списка → карточка; в списке есть и списанные, с пометкой «списана»).
  При обрыве связи с программой показывается баннер; если база была повреждена и восстановлена из копии — баннер с пояснением (`db_recovered`).
- Модуль страницы: `export async function render(root, params, ctx)`; `ctx = {boot, api, ui, dialogs, navigate(hash), refresh(), setTitle(text), setLeaveGuard(fn)}`.
  `ctx.refresh()` перезагружает `/api/bootstrap` в `ctx.boot` и перерисовывает текущий экран (прокрутка сохраняется). `ctx.setLeaveGuard(fn)` — защита несохранённого ввода: `fn()` возвращает текст вопроса, если уйти без подтверждения нельзя;
  роутер спросит «Данные не сохранены» при смене адреса, а `beforeunload` — при закрытии окна. Если `render` вернул функцию, роутер вызовет её при уходе со страницы (снять таймеры и слушатели).
  Роутер грузит модуль через `import()` и показывает понятную ошибку с кнопкой «Повторить», если страница упала. `ctx.dialogs.<имя>(...)` грузит `dialogs.js` при первом обращении.
- `api.js`: `get(path, params)`, `post(path, body)`, `put`, `del(path, params)`, `upload(path, file, params)` (тело — байты, `application/octet-stream`), `download(path)`; ошибка → исключение `ApiError` с `.message` (русский текст с сервера), `.status` (0 — нет связи), `.needsConfirm`, `.warnings`, `.items`;
  хелпер `withConfirm(fn)` — `fn(force)`; при 409 показать `ui.confirm` со списком предупреждений и повторить с `force=true`. Пинг `/api/ping` каждые 20 с.
- `ui.js` — UI-кит, единственный источник компонентов для страниц (полный справочник — `app/static/UI_KIT.md`): `h(tag, attrs, ...children)`, `icon(name)`, `modal({title, body, buttons, wide})`, `confirm(text, {danger, details})`,
  `toast(text, kind)`, `field(label, control, {required, hint})`, `select`, `combo` (ввод с подсказками), `dateInput`, `timeInput` (понимает 130, 1.30, 9 → 09:00),
  `numInput` (пробелы-разделители тысяч, запятая), `table({columns, rows, sort, onRowClick, empty, rowClass, fill, inputNav})` с сортировкой по клику на шапку, `statusChip(kind, label)`, `tabs`, `emptyState`, `loading`,
  `pickFile(accept, {multiple})` (выбор файла: `File | null`, с `multiple: true` — `File[]`),
  форматтеры `fmtNum(1234567) → "1 234 567"`, `fmtReading(v, unit, estimated) → "≈ 153 000 км"`, `fmtDate("2026-10-02") → "02.10.2026"`, `fmtDuration(seconds) → "1 д 3 ч 20 мин"`, `fmtHours`,
  `today()` → ISO-дата по часам клиента (НЕ `boot.today`: программа бывает открыта сутками), `parseNum`, `printPage(title, {landscape})`,
  `previewTo(machine, {kind, reading, current}, settings) → {next_kind, next_at, remaining, status}` — единственный разрешённый расчёт на фронте:
  `next_at = reading + machine.interval`; `remaining = next_at − (current ?? machine.reading)`; `next_kind` — следующий после `kind` в `settings.to_cycle`; `status` по `machine.warn`.
- Диалоги (`dialogs.js`): `openToDoneDialog(ctx, machineId?)`, `openReadingDialog(ctx, machineId?)`, `openEventDialog(ctx, {machineId?, event?, preset?})`, `openCloseRepairDialog(ctx, eventOrId)`,
  `openMachineDialog(ctx, machine?)`, `openDailyDialog(ctx)`. Каждый возвращает Promise (true, если что-то сохранено) и сам показывает ошибки/подтверждения.
  Данные машины диалоги берут из `GET /api/machines/{id}/view`, список для выбора машины — из `GET /api/machines` (без списанных).
- Минимальная ширина окна 1100px; при 1366×768 всё главное видно без горизонтальной прокрутки страницы (широкие таблицы прокручиваются внутри своего контейнера).
- Печать: `css/print.css` — скрывать панель, кнопки, фильтры (`.toolbar`, `.filter-chips`, `.save-bar`), чёрно-белые таблицы с рамками, A4; `ui.printPage(title, {landscape: true})` — альбомная.
  Класс `.print-blank` на таблице: кремовые поля печатаются пустыми рамками (бланк для обхода, куда вписывают от руки), а не подчёркнутым текстом.

### 7.2 Экраны
1. **Главная** (`fleet.js`; `#/fleet`). Одна таблица на весь парк: строка = машина. Данные — `GET /api/fleet` на сегодня (параметр `?date=` в API остаётся для карточки и тестов). Плиток-счётчиков, выбора даты отчёта и пресета «сначала просроченные» чекбоксом больше нет.
   - **Верх, строка 1 (toolbar):** `[Накинуть за день]` (icon calendar-plus → диалог) · `[Список на ТО]` (primary, icon calendar-check → `#/plan`) · `[Печать]` (icon printer) · распорка · поиск «Гар. №, модель, госномер» (по гаражному №, модели, типу, госномеру, VIN, участку; несколько слов — должны встретиться все) · select «Все участки»
     (участки, которые есть в парке, в порядке справочника; «Без участка» — если у части машин участок пуст).
   - **Строка 2 — чипы-фильтры** (`.filter-chips`; кнопки-переключатели, включён один или ни одного; повторный клик снимает; цвет — как у статус-чипа, включённый чип залит цветом; число в чипе — сколько машин подходит среди прошедших поиск и участок):
     `Все · 93` · `🔴 Просрочено ТО` (`overdue`) · `🟡 Скоро ТО` (`soon`) · `⚪ Нет данных о ТО` (`no_to` + `no_reading`) · `🔧 Не на линии` (`state != 'line'`: ремонт, простой, ТО, консервация). Чип с нулём — без цвета.
     Справа: «N из M» (виден при любом отборе) и `[Сбросить]` (виден при отборе или выбранной сортировке; сбрасывает поиск, участок, чип и сортировку — таблица возвращается в порядок «сначала просроченные»).
     Параметры адреса `?to_status=overdue|soon|no_to|no_reading` (последние два и `nodata` включают чип «Нет данных о ТО») и `?state=repair|idle|to|conserved` (включает «Не на линии») выбирают чип при открытии.
     Отбор и сортировка хранятся в памяти модуля, пока открыта программа: вернулись из карточки — список в том же виде.
   - Ниже, если есть `warnings`, — свёрнутый `<details>` «Проверьте записи: N замечаний» (гар. № со ссылкой на карточку, текст, «Исправить запись» → `openEventDialog`); на печать не идёт.
   - **Таблица** (`ui.table({fill: true, inputNav: true})`, класс `print-blank`). Порядок по умолчанию — «сначала просроченные»: `overdue` (чем больше доля интервала, тем выше) → `soon` → `ok` (чем меньше осталось, тем выше) → `no_to` → `no_reading`; равные — в порядке парка.
     Клик по шапке — обычная сортировка по колонке (по возрастанию → по убыванию → порядок по умолчанию); колонка «До ТО» сортируется именно по порядку по умолчанию. Клик по строке → карточка машины; клики по полям ввода, кнопкам, select'ам и по ячейкам «Новое показание», «Последнее ТО» (когда в них поля) и действий карточку не открывают.
     Цвет строки: `overdue` → `row-red`, `soon` → `row-amber`. Колонки «Машина» (слева) и действий (справа) липкие — видны и в узком окне. Модель, участок и текст записи обрезаются с «…», полный текст — во всплывающей подсказке (`title`).

   | # | Колонка | Содержимое |
   |---|---|---|
   | 1 | **Машина** | гар. № (strong); muted small «модель · участок» (`title` — модель · тип · госномер); если `state != 'line'` — чип состояния, у ремонта и простоя с длительностью: «🔴 В ремонте 3 д 4 ч» («🟡 Простой …»; при ≥ суток без минут; `title` — «с ДД.ММ.ГГГГ ЧЧ:ММ»). У машин на линии чипа нет |
   | 2 | **Показание** | `fmtReading(reading, unit, estimated)`; строкой ниже muted small — дата последнего факта (`ДД.ММ`, в другом году `ДД.ММ.ГГГГ`) или «при регистрации»; `title` — «Последний факт: … от …; накинуто: +…». Нет показания → «—» |
   | 3 | **Новое показание** (кремовое) | пустой `numInput` (км — целые и «153.000» = 153 000, м/ч — до 0,1). Под полем пометка; пока человек набирает число, она молчит и появляется после выхода из поля: красная «Не число» / «Меньше нуля»; янтарная «Меньше последнего факта N»; серая «Расчёт был ≈N» (показание было расчётным, а введённое меньше него) |
   | 4 | **До ТО** | `overdue`: `strong text-red` «Просрочено на 2 000 км» + «ТО-2 был на 55 000» (+ « · пропущено ТО: 2», если `missed > 1`); `soon`: `strong text-amber` «Осталось 800 км» + «ТО-2 на 55 000 · ≈ 12.11»; `ok`: `strong` «Осталось 7 000 км» + то же; `no_to`: янтарный чип «Внесите последнее ТО» + «впишите справа →»; `no_reading`: серый чип «Нет показаний» + «впишите показание ←». У `soon`/`ok` в `title` — прогноз и средняя наработка |
   | 5 | **Последнее ТО** | есть `last_to`: «ТО-1 · 45 000» и ниже muted small дата (`fmtEventDate`, «дата не указана»). Нет `last_to`: **кремовые поля в ячейке** — select вида ТО (виды `ТО-*` из справочника, по умолчанию `next_to_kind`) и `numInput` с подсказкой «на каком показании». Дата не спрашивается: сервер запишет дату пакета с пометкой «дата не указана» (поправить можно в карточке). Пометки: янтарная «Больше текущего показания» (сравнивается с введённым «Новым показанием», иначе с текущим) и «Меньше текущего больше чем на интервал ТО — проверьте разрядность». Enter/↓/↑ в этом поле ведут к такому же полю ближайшей строки, где оно есть |
   | 6 | **Что делали** (140–220 px, не выше двух строк) | из `last_note`: строка 1 muted small — дата `ДД.ММ` (или «дата не указана») и, если вид записи не «Заметка», « · » + название вида («Ремонт», «Простой», «ТО-2»); строка 2 — текст. Нет записи → «—». В `title` — всё целиком |
   | 7 | действия (no-print) | `[ТО сделано]` (small accent) → `openToDoneDialog`; кнопка `plus` «Запись: ремонт, простой, заметка» → `openEventDialog(ctx, {machineId})`; кнопка `dots` → меню «Ввести показание» (`openReadingDialog`), «Карточка машины», «Заказ-наряд» (`#/workorder?machine_id=N&date=сегодня`). Если `state` repair/idle и есть `state_event_id` — вторым рядом `[Закрыть ремонт]`/`[Закрыть простой]` → `openCloseRepairDialog` |

     После любого диалога, который что-то сохранил, парк перечитывается. Поля «Новое показание» и «Последнее ТО» создаются один раз на строку и переживают отбор, сортировку и перечитывание данных — введённое не теряется. Enter и ↓ ведут на строку ниже в той же колонке, Shift+Enter и ↑ — выше.
   - **Панель сохранения** (`.save-bar`, внизу, липкая, не печатается; появляется, когда вписано хоть одно значение): «Показаний: 3 · последних ТО: 2» (и красное « · с ошибкой: N») · «Дата показаний» (`dateInput`, сегодня, не позже сегодня) · распорка · `[Очистить]` (ghost; спрашивает «Стереть вписанное в N строках? Ничего не сохранится.») · `[Сохранить всё]` (accent, big, icon check). Ctrl+S — то же, что «Сохранить всё» (не при открытом диалоге или меню).
     Сохранение — `POST /api/readings/bulk {date, items, baseline, force}`:
     строки с ошибкой ввода («Не число», «Меньше нуля») не отправляются, пакет не уходит частично — тост «Исправьте … — ничего не сохранено», фокус на первую такую строку;
     409 → `ui.confirm` «Проверьте показания» со списком предупреждений (одинаковый текст у более чем 5 машин сворачивается в «текст — у N машин»), кнопки «Всё равно сохранить» / «Вернуться и исправить» → повтор с `force: true`;
     `errors[]` ответа — строка остаётся с введённым и текстом ошибки под полем (по `part`: `item` — под «Новым показанием», `baseline` — под «Последним ТО»; сохранённая часть строки очищается), тост «Не сохранено строк: N»;
     успех — тост «Сохранено: показаний N, последних ТО M. Сейчас: просрочено X, скоро Y», поля очищаются, парк перечитывается, строки перекрашиваются и встают в порядок «сначала просроченные».
     `ctx.setLeaveGuard` — пока есть вписанное, уход с экрана требует подтверждения («Вписанное в таблицу не сохранено: N строк. Уйти без сохранения?»).
     Тихое обновление данных — раз в 5 минут и при возврате в окно (если данные старше минуты или сменились сутки; при смене суток «Дата показаний» сама становится сегодняшней, если её не меняли) — только когда ничего не вписано, нет открытых диалогов и меню и курсор не в поле таблицы.
   - **Печать:** `[Печать]` → `ui.printPage('Машины и ТО на ДД.ММ.ГГГГ', {landscape: true})`. Печатается таблица как есть (то, что отобрано на экране; пустые кремовые поля — пустые рамки, бланк для обхода), без кнопок, чипов, панели сохранения и колонки действий. Это заменяет «Отчёт за день» из ред. 2.
   - **Пустые состояния:** нет машин — `emptyState` «В парке пока нет машин» с кнопкой «Добавить машину» (диалог, затем `ctx.refresh()`); `first_run` → `#/welcome`. Нет совпадений с отбором — `emptyState` «Ничего не найдено» с кнопкой «Сбросить».
2. **Карточка машины** (`card.js`; `#/machine/{id}`). Данные — `GET /api/machines/{id}/card` (включая `photos`); нет машины (404) → «Машина не найдена» с кнопкой «Открыть список машин». Все числа и статусы приходят с сервера.
   - **Toolbar:** `[← Все машины]` (big, icon `arrow-left` → `#/fleet`; фильтры главной хранятся в памяти её модуля, список откроется в том же виде) · `[ТО сделано]` · `[Показание]` · `[Запись]` · `[Закрыть ремонт]`/`[Закрыть простой]` (если открыт) · распорка · `[Заказ-наряд]` `[Изменить машину]` `[Печать]`.
     У списанной машины кнопок «ТО сделано», «Показание», «Запись» нет, показывается пояснение «Машина списана…». Под toolbar — предупреждения по данным машины («открыть запись»), а при `?date=` — пояснение «Карточка показана на …» с кнопкой «Показать на сегодня».
   - **Три блока в ряд** (в окне шириной от 1280 px; уже — блок «Сегодня» отдельной строкой ниже):
     - *Машина:* гар. № крупно, статус-чип (если не «В работе»), модель; список «подпись — значение»: Тип, Госномер, **VIN**, Участок, Гарантия, Примечание (если есть), «Ссылка на фото» (старое текстовое поле `photo` — только если заполнено); под ним блок **«Фото»** (см. ниже).
     - *Техобслуживание:* чип статуса ТО; крупно «ТО просрочено на N» (+ «пропущено ТО подряд: N») / «До ТО осталось N» / «Последнее ТО не внесено» / «Нет показаний счётчика»; «Следующее ТО», «Прогноз», «Последнее ТО» (+ «всего ТО в журнале: N»);
       «Что делать на ТО-N — по регламенту» (работы, масло, фильтры, запчасти, примечание; нет строки регламента — ссылка на настройки). В конце блока свёрнутый `<details>` **«Как считается»**: «Средняя наработка» (откуда взята), «Норма за день» (сколько добавляет «Накинуть за день» и откуда норма), «Интервал ТО» (и порог «скоро ТО», откуда заданы).
     - *Сегодня* (или «Состояние на ДД.ММ.ГГГГ»): чип состояния, что делается (`state_work`), «с … — длительность» для ремонта/простоя; показание счётчика крупно и откуда оно («расчётное: по счётчику … + накинуто …»); «За сутки» (ч на линии / в ремонте / простой), «Последний ремонт», «Заказать запчасти», шины и узлы, требующие внимания.
   - **Фото** (блок в карточке «Машина»). Заголовок «Фото», число «N шт.» и кнопка `[Добавить фото]` (`ui.pickFile('image/*', {multiple: true})` — можно выбрать несколько). Файлы можно также перетащить на блок (пунктирная рамка) или вставить снимок из буфера **Ctrl+V** (не при открытом окне и не когда курсор в поле ввода;
     снимку экрана, у которого браузер даёт имя «image.png», присваивается имя `foto-ГГГГММДД-ЧЧММСС.<ext>`, файл, скопированный в Проводнике, сохраняет своё). Файл, брошенный мимо блока, не открывается браузером вместо программы. Фото уходят на сервер по одному (`POST …/photos?name=`); пока идёт загрузка, кнопка отключена и подписана «Загружаю…» / «Загружаю 2 из 5…».
     Перед отправкой проверяется расширение (jpg, jpeg, png, webp, gif; без расширения, но с типом изображения — дописывается) и размер (до 25 МБ); отклонённые файлы и ответы сервера с ошибкой — тостом. После загрузки и удаления карточка перечитывается.
     Миниатюры 96×96 (`object-fit: cover`, рамка 1 px), подпись — имя файла (с «…»); клик → окно с картинкой (до 90vw × 90vh): имя в заголовке, размер и «добавлено ДД.ММ.ГГГГ ЧЧ:ММ», ссылка «открыть в отдельной вкладке», кнопки `[Удалить]` (с подтверждением: файл удалится из папки данных, вернуть нельзя) и `[Закрыть]`.
     Нет фото — «Фото пока нет — например, табличка с VIN или документы.» и подсказка про перетаскивание и Ctrl+V; на печать пустой блок не идёт.
   - **История:** заголовок с числом записей (без накинутых), галочка «Показывать накинутое за день (N)» (по умолчанию выключена), кнопка «Добавить запись»; вкладки-фильтры «Все / ТО / Ремонт / Простой / Заметки / Отметки показаний» (+ «Прочее» для видов, добавленных в настройках) со счётчиками.
     Колонки: Дата (или «дата не указана» + «внесено …»), Событие (для накинутого — «накинуто за день», для незакрытого ремонта/простоя — чип «не закрыт»), Показание, В ремонте / простое (длительность, заезд, выезд), Что делалось, Масло / запчасти, Заказать («заказано»/«ещё не заказано»), Замечания, Кто, действия (Закрыть, изменить, удалить).
     Колонки «Смена» нет; колонка «Замечания» показывается, только если хотя бы у одной записи поле `remarks` непустое. Кнопки «Открыть в журнале» нет — журнал и есть эта история. Клик по строке открывает правку записи (накинутое не правится, только удаляется).
   - **Шины** и **Узлы и агрегаты** машины — таблицы с кнопками «Добавить шину» / «Добавить узел», правка и удаление в тех же диалогах, что на экранах «Шины» и «Узлы».
3. **Список на ТО** (`plan.js`; `#/plan`; бывший «План ТО и заказ», R6). Горизонт 7/14/30/60 дней (кнопки «Показать ТО на ближайшие:», выбор запоминается в браузере; можно задать `?horizon=`). Данные — `GET /api/plan`; сверху плитки: просрочено, скоро, по прогнозу, нет данных о ТО, заявки на запчасти. Блоки:
   «Машины, которым пора на ТО» (Гар. №, Модель, Госномер и VIN, Участок, Вид ТО, Осталось, Прогноз, По регламенту — масло/фильтры/работы, кнопка «ТО сделано»);
   «Нет данных о последнем ТО (Z машин)» — текст «Внести последнее ТО можно на главном экране — в колонке «Последнее ТО» (кремовые поля), показание счётчика — в колонке «Новое показание»» и кнопка «На главную» (`#/fleet`), под ней гаражные № со ссылками на карточки, отдельно «Не внесено последнее ТО» и «Нет показаний счётчика»;
   «Что заказать» — сводка вид ТО × модель: количество, гаражные №, масло/фильтры/запчасти;
   «Заявки на запчасти из записей по машинам» с галочкой «заказано». Кнопка «Печать» (альбомная) печатает всё перечисленное.
4. **Заказ-наряд** (`workorder.js`): как ред. 2 — выбор машины и даты → форма по образцу листа «Заказ-наряд» таблицы v8, все поля редактируемы перед печатью, строки работ/запчастей можно добавлять/удалять; ИТОГО н/час считается; печать A4 книжная, на печати только форма. Открывается и из строки главной («⋯»), и из карточки.
5. **Шины** (`tyres.js`), **Узлы** (`units.js`): как ред. 2 — таблицы с расчётными колонками и статусами, фильтр по машине и статусу, CRUD через модальные формы; гар. № в строке — ссылка на карточку.
6. **Машины** (`machines.js`): как ред. 2 — справочник (включая списанные — серым), CRUD, колонка итоговой единицы и интервала; в форме — поля машины + «свои параметры ТО» (единица, интервал, порог, суточная норма; пусто = авто, рядом показано авто-значение из `auto`). Фото в форме нет — они в карточке.
7. **Настройки** (`settings.js`): как ред. 2 — разделы-вкладки: «ТО и нормы» (интервалы, пороги, суточные нормы, круг ТО, слова моточасной техники, правила по маркам — таблица), «Регламент ТО», «Справочники» (события, участки, смены, типы машин, узлы, типы шин, позиции),
   «Заказ-наряд» (реквизиты), «Данные» (импорт из Excel с предварительным отчётом dry_run → подтверждение; экспорт в Excel; резервные копии: создать / список / восстановить; путь к папке данных; версия).
   После сохранения настроек, импорта и восстановления — `ctx.refresh()` (после restore/импорта — `location.reload()`).
8. **Первый запуск** (`welcome.js`): как ред. 2 — «База пустая» → «Загрузить из таблицы Excel (xlsx)» (выбор файла → dry-run отчёт → подтвердить → кнопка «Открыть главную») с подсказкой
   «В Google-таблице: Файл → Скачать → Microsoft Excel (.xlsx), затем выберите скачанный файл» / «Начать с пустой базы — добавить первую машину» (после сохранения — на главный экран).

### 7.3 Ключевые диалоги
- **«ТО сделано»** (R7, R8, R9): машина (если не задана — выбор), вид ТО (подставлен `next_to_kind`, можно сменить), дата ТО (сегодня),
  два поля рядом, оба всегда видимы и подставлены текущим показанием: **«Показание на момент ТО»** и **«Показание сейчас»**;
  «Что делали» (подставить `works` из регламента для выбранного вида — редактируемо), масло/запчасти, кто (по умолчанию последнее введённое, хранить в localStorage).
  Под полями — живая подсказка через `ui.previewTo`: «Следующее: ТО-3 на 160 000 км, останется 7 000». `reading_estimated: true`, если текущее показание машины расчётное и пользователь не менял поле «на момент ТО».
- **«Показание»** (заголовок окна «Показание счётчика»): дата (сегодня), новое показание; показать последний факт, текущее и что изменится (остаток до ТО). Сохраняет через `/api/readings/bulk` с одним item.
- **«Накинуть за день»** (R10): период (по умолчанию `suggested_from`…сегодня; если подставлено больше одного дня — подпись «не накидывали N дн.»; если `gap_days > 7` или auto ещё не было —
  подсказка «Если вы только заехали на вахту — сначала введите показания со счётчиков на главном экране, в колонке «Новое показание»» с кнопкой «Ввести показания» → `#/fleet`), таблица машин: галочка, гар. №, модель, состояние, норма (редактируемая) и её источник, дней, «будет добавлено».
  Строки с `days_to_add = 0` — disabled с причиной (`skip_reason`). Итог «будет добавлено N машинам». После сохранения — фактические `created/skipped`. Кнопка «Отменить накинутое за период» (с подтверждением).
- **Запись журнала** (`openEventDialog`; «Новая запись» / «Запись — гар. №»). Поля: Машина · Дата · Событие (select из справочника `kinds`) · Показание счётчика (необязательно; обязательно для ТО и отметки) ·
  для «Ремонт»/«Простой» (и для старой записи, где время или выход уже заполнены) — «Заехала — время», «Вышла — дата», «Вышла — время» (ввели только время — дата выхода подставляется сама, см. валидацию) ·
  **«Что делали»** (textarea на 3 строки, крупно; когда машина уже задана, фокус стоит здесь, а после выбора машины из списка — переходит сюда) · «Масло / запчасти (использовано)» · «Заказать запчасти» · «Кто».
  Для ТО-* — подсказка воспользоваться «ТО сделано» (при создании записи в ней есть ссылка «Открыть «ТО сделано»», она переносит введённое в тот диалог; сохранять запись можно и здесь).
  **Полей «Смена» и «Замечания» в форме нет**: в базе они остаются, но диалог их не отправляет (PUT частичный — не затирает). При правке записи, где они заполнены, под формой показывается приглушённая строка «Смена: День · Замечания: …».
  Кнопки: «Отмена», «Сохранить» и (только при создании) «Сохранить и добавить ещё» (форма остаётся открытой; дата, вид события и «кто» сохраняются, остальное очищается, фокус на «Машина»); при правке — «Удалить запись». Накинутое (`source='auto'`) не правится — диалог объясняет, где его удалить.
- **«Закрыть ремонт/простой»**: показывает событие (машина, начало), поля «Вышла — дата» (сегодня) и «время»; при вводе только времени дата подставляется сама (см. валидацию); можно дополнить «что делалось» и «масло/запчасти».
- **Машина** (`openMachineDialog`; «Новая машина» / «Машина гар. №»): Гаражный №, Модель, Тип машины, Госномер, VIN, Участок, Статус, Показание при регистрации и его дата, Гарантийный срок, Примечание; ниже «Свои параметры ТО» (единица, интервал, порог, суточная норма — пусто = авто).
  **Поля «Фото (ссылка)» в форме больше нет** — под полями подпись «Фото машины добавляются в её карточке»; ключ `photo` диалог не отправляет, старое значение в базе не затирается.

## 8. Тесты (обязательный минимум)
- `tests/test_logic.py`: все примеры из 4.1, 4.2, 4.3; состояние на дату (включая baseline-ТО сегодня → line); часы за сутки (ремонт через полночь, открытый ремонт, пересекающиеся ремонты, сегодня/прошлая/будущая дата);
  средняя и прогноз (единственный факт + reg без даты → norm); шины/узлы (resource 0).
- `tests/test_services.py` (временная БД): CRUD; bulk (upsert, повторный ввод меньшего значения той же датой заменяет показание, errors, 409/force, baseline без даты);
  `to-done` (R8: отметка 153000 → ТО 150000 сегодня → reading 153000, remaining 7000; `current_reading`; оценочное показание не становится базой);
  daily preview/apply/undo (повтор не дублирует; ремонт пропускается; на ТО начисляется; дни ≤ даты базы не начисляются; правка отметки после auto того же дня → reading = факт без «≈»);
  валидации; план (no_to, materials по модели); защита списков.
  **`last_note`** (раздел 5): выбор последней записи по (`date`, `id`) — в том числе запись с большим `id`, но более ранней датой не побеждает; запасной текст из `remarks`, затем `parts_used`, пустые строки → `""`; обрезка до 300 символов;
  накинутое (`source='auto'`) и отметки показаний не считаются (машина с одними отметками → `null`); записи позже даты отчёта не считаются; ТО без даты (`date_unknown`) и `open` у ремонта/простоя без `date_out`; поле есть в ответах fleet, card, machines, plan, create-machine, to-done.
  **Число запросов** `build_views` не зависит от числа машин и не больше 9 (3 и 30 машин дают одинаковое число; 7 запросов + настройки + правила).
  **Фото (чистые функции):** `photo_name_parts` (путь и лишние знаки отбрасываются, регистр расширения, кириллица, повторы знаков, имена устройств Windows, длина 80) и `photo_format` (сигнатуры JPEG, PNG, WEBP, GIF; чужие данные и пустые байты → `None`).
- `tests/test_api.py`: поднять сервер на свободном порту во временной папке, пройти основные эндпоинты через `urllib` (включая статику с правильным Content-Type, 403 на чужой Host, экспорт с кириллическим именем).
  **Фото машин:** загрузка (валидный PNG из байтов), список (старые первыми, `mtime` в ISO), получение файла с правильным `Content-Type` и `Cache-Control: no-store`, `photos` в ответе card совпадает со списком;
  имя с кириллицей и пробелами (в `url` — percent-encoding), дубликат имени → `-2`, `-3` (без учёта регистра), путь и лишние знаки в имени, пустая основа и отсутствие имени → `foto-YYYYMMDD-HHMMSS.<ext>`;
  плохое расширение → 400, подделка расширения (содержимое не совпадает с ним) → 400, пустой файл, тело не `application/octet-stream`, больше 25 МБ → 400 (ничего не сохранено);
  `..`, `\`, `%00`, `:` и прочие попытки выйти из папки при GET/DELETE → 404, файлы вне папки машины не затронуты; удаление (повторное → 404); неизвестная машина → 404 на всех четырёх эндпоинтах;
  удаление машины с фото → 409 (`Будет удалено фото: N`), с `?force=1` — папка `photos/{id}/` исчезает, машина без фото и истории удаляется без вопросов.
- `tests/test_xlsx.py`: сгенерировать openpyxl-книгу в формате v8 (минимальную, с формулой в столбце даты, временем как timedelta, показанием строкой «125 000», закрытым и открытым ремонтом с «Заказать»),
  импортировать, проверить счётчики, `order_done`, повторный импорт без дублей; экспорт открывается openpyxl и импортируется обратно (auto восстанавливаются).
- `tests/test_perf.py`: 250 машин × 100 000 событий → `/api/fleet` ≤ 1.5 с (можно помечать как медленный).

## 9. Вопросы заказчику (не блокируют разработку)
1. В заказ-наряде таблицы «Заводской номер» = госномер, «Инвентарный номер» = VIN — так и задумано? (В программе оставлено как в таблице.)
2. Суточные нормы по умолчанию: 100 км и 11 м/ч — на машину в сутки (одна смена). Если техника работает в две смены — поправить в настройках.
