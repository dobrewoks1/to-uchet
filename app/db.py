"""База данных «Учёт ТО»: подключение SQLite, схема, сиды, миграции, настройки и резервные копии
(SPEC, разделы 2 и 3).

ОГЛАВЛЕНИЕ
    LOCK, KIND_MARK, SCHEMA_VERSION, PROTECTED_KINDS, LIST_NAMES
    open_db(path) -> conn                      открыть/создать базу (pragma, ulower, схема, сиды, миграции)
    close_db(conn)                             wal_checkpoint(TRUNCATE) + close
    transaction(conn, commit=None)             контекст-менеджер транзакции (commit/rollback)
    now_iso() -> str                           метка времени 'YYYY-MM-DDTHH:MM:SS'

    get_settings(conn) -> dict                 set_settings(conn, values, commit=None) -> dict
    get_lists(conn) -> {name: [values]}        set_list(conn, name, values, commit=None) -> list
    get_rules(conn) -> [dict]                  set_rules(conn, rules, commit=None) -> [dict]
    get_regulation(conn) -> [dict]             set_regulation(conn, rows, commit=None) -> [dict]

    backup_now(conn, backups_dir) -> name      создать копию сейчас
    auto_backup(conn, backups_dir) -> name | None   копия при старте (правило 6 часов / 30 штук)
    list_backups(backups_dir) -> [{name, size, mtime}]
    restore_backup(conn, backups_dir, name) -> name страховочной копии
    recover_db(path, backups_dir) -> (conn, info)   база повреждена: отложить файл, поднять свежую исправную копию

Одно общее соединение на всю программу; вызывающий код работает под замком LOCK.
Функции set_* по умолчанию (commit=None) сами фиксируют изменения, если транзакция ещё не открыта,
и ничего не фиксируют, если вызваны внутри уже открытой транзакции (её закроет тот, кто открыл).
"""

import json
import os
import re
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from app.logic import DEFAULT_SETTINGS, KIND_MARK

LOCK = threading.RLock()

SCHEMA_VERSION = 2

BACKUP_NAME_RE = re.compile(r'^to-\d{8}-\d{6}\.db$')
BACKUP_KEEP = 30          # сколько последних копий хранить
BACKUP_MIN_HOURS = 6      # автокопия при старте — не чаще
BACKUP_FUTURE_MINUTES = 10   # копия с датой в имени позже «сейчас» больше чем на столько — «из будущего»

# Виды событий, которые нельзя удалить из списка kinds (на них держится логика).
PROTECTED_KINDS = ['ТО-1', 'ТО-2', 'ТО-3', 'Ремонт', 'Простой', 'Заметка', KIND_MARK]

LIST_NAMES = ['kinds', 'shifts', 'machine_statuses', 'sites', 'mtypes',
              'unit_names', 'tyre_types', 'positions']

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS lists(
  id INTEGER PRIMARY KEY, list TEXT NOT NULL, value TEXT NOT NULL, pos INTEGER NOT NULL DEFAULT 0,
  UNIQUE(list, value));
CREATE TABLE IF NOT EXISTS rules(
  id INTEGER PRIMARY KEY, pos INTEGER, keyword TEXT NOT NULL, unit TEXT NOT NULL,
  interval REAL NOT NULL, warn REAL, daily_norm REAL);
CREATE TABLE IF NOT EXISTS regulation(
  id INTEGER PRIMARY KEY, pos INTEGER, to_kind TEXT NOT NULL, periodicity TEXT, works TEXT,
  oil TEXT, filters TEXT, parts TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS machines(
  id INTEGER PRIMARY KEY, pos INTEGER,
  garage_no TEXT NOT NULL,
  model TEXT DEFAULT '', mtype TEXT DEFAULT '',
  plate TEXT DEFAULT '', vin TEXT DEFAULT '', site TEXT DEFAULT '',
  warranty TEXT DEFAULT '', photo TEXT DEFAULT '', note TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'В работе',
  reg_reading REAL, reg_date TEXT,
  unit_override TEXT, interval_override REAL, warn_override REAL, norm_override REAL,
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY,
  machine_id INTEGER NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
  date TEXT NOT NULL, shift TEXT DEFAULT '', kind TEXT NOT NULL,
  reading REAL,
  reading_estimated INTEGER NOT NULL DEFAULT 0,
  delta REAL,
  source TEXT NOT NULL DEFAULT 'manual',
  time_in TEXT, date_out TEXT, time_out TEXT,
  work TEXT DEFAULT '', parts_used TEXT DEFAULT '', parts_order TEXT DEFAULT '',
  order_done INTEGER NOT NULL DEFAULT 0,
  remarks TEXT DEFAULT '', who TEXT DEFAULT '',
  created_at TEXT, updated_at TEXT,
  date_unknown INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_ev_machine_date ON events(machine_id, date);
CREATE INDEX IF NOT EXISTS ix_ev_date ON events(date);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ev_auto ON events(machine_id, date) WHERE source='auto';
CREATE UNIQUE INDEX IF NOT EXISTS ux_ev_mark ON events(machine_id, date) WHERE source='mark';
CREATE TABLE IF NOT EXISTS tyres(
  id INTEGER PRIMARY KEY,
  machine_id INTEGER NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
  tyre_no TEXT DEFAULT '', ttype TEXT DEFAULT '', position TEXT DEFAULT '',
  install_date TEXT, resource REAL, run REAL, install_reading REAL, note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS units(
  id INTEGER PRIMARY KEY,
  machine_id INTEGER NOT NULL REFERENCES machines(id) ON DELETE CASCADE,
  unit TEXT DEFAULT '', resource REAL, install_reading REAL, note TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_tyres_machine ON tyres(machine_id);
CREATE INDEX IF NOT EXISTS ix_units_machine ON units(machine_id);
"""

# ---- Сиды (SPEC, раздел 3; списки узлов/шин/позиций и регламент — из листа «Настройки» таблицы v8; участки — примеры) ----

SEED_RULES = [
    # (keyword, unit, interval, warn)
    ('SHACMAN', 'км', 10000, 1000),
    ('ШАХМАН', 'км', 10000, 1000),
    ('ШАКМАН', 'км', 10000, 1000),
    ('SITRAK', 'км', 15000, 1000),
    ('СИТРАК', 'км', 15000, 1000),
    ('SANY', 'км', 15000, 1000),
    ('САНИ', 'км', 15000, 1000),
    ('VOLVO', 'км', 15000, 1000),
    ('ВОЛЬВО', 'км', 15000, 1000),
]

SEED_LISTS = {
    'kinds': ['ТО-1', 'ТО-2', 'ТО-3', 'Ремонт', 'Простой', 'Заметка', KIND_MARK],
    'shifts': ['День', 'Ночь'],
    'machine_statuses': ['В работе', 'На консервации', 'Списана'],
    'sites': ['Северный', 'Южный', 'Мирный', 'Восточный', 'Западный', 'Промбаза'],
    'mtypes': ['Самосвал', 'Экскаватор', 'Бульдозер', 'Каток', 'Грейдер', 'Погрузчик', 'Тягач'],
    # лист «Настройки», столбец F
    'unit_names': [
        'Двигатель', 'Турбокомпрессор', 'ТНВД (топл. насос)', 'Система охлаждения / радиатор',
        'Сцепление', 'КПП', 'Гидротрансформатор / ГМП', 'Раздаточная коробка', 'Карданный вал',
        'Передний мост', 'Средний мост', 'Задний мост', 'Главная передача / редуктор',
        'Бортовой редуктор лев.', 'Бортовой редуктор прав.', 'Гидронасос', 'Гидрораспределитель',
        'Гидроцилиндр стрелы', 'Гидроцилиндр ковша/отвала', 'Гидромотор хода', 'Ходовая часть',
        'Гусеница лев.', 'Гусеница прав.', 'Опорные/поддерж. катки', 'Рулевое управление',
        'Тормозная система', 'Пневмосистема / компрессор', 'Электрооборудование', 'Генератор',
        'Стартер', 'АКБ', 'Рабочий орган (отвал/ковш)', 'Лебёдка', 'Кабина / отопитель',
    ],
    # лист «Настройки», столбец H
    'tyre_types': [
        '12.00R20', '11.00R20', '11.00R22.5', '14.00R20', '16.00R20', '300R508', '385/65R22.5',
        '315/80R22.5', '295/80R22.5', '12.00-20', '9.00R20', '23.5-25', '20.5-25', '17.5-25',
        '26.5-25', '29.5-25', '18.4-26', '16.9-28', '405/70-20', '405/70-24', '10.0/75-15.3',
    ],
    # лист «Настройки», столбец J
    'positions': [
        'Перед. левая', 'Перед. правая', 'Средн. левая', 'Средн. правая',
        'Зад. левая наружн.', 'Зад. левая внутр.', 'Зад. правая наружн.', 'Зад. правая внутр.',
        'Зад. левая', 'Зад. правая', '3-я ось лев.', '3-я ось прав.', '4-я ось лев.', '4-я ось прав.',
        'Запасная',
    ],
}

# лист «Регламент ТО»: (вид, периодичность, работы, масло/жидкости, фильтры, прочие запчасти, примечание)
SEED_REGULATION = [
    ('ТО-1', '250',
     'Замена масла ДВС, масляного фильтра. Осмотр узлов, протяжка креплений, смазка точек.',
     'Масло ДВС', 'Масляный фильтр', '—', 'Базовое ТО каждые 250 м/ч'),
    ('ТО-2', '500',
     'Объём ТО-1 + замена топливного фильтра, проверка тормозов, смазка узлов.',
     'Масло ДВС', 'Масляный + топливный фильтры', 'Тормозные элементы (по износу)', 'Каждые 500 м/ч'),
    ('ТО-3', '750',
     'Объём ТО-2 + замена масла КПП, мостов, бортовых редукторов. Замена воздушного фильтра.',
     'Масло ДВС, КПП 80W-90, масло бортовых/мостов', 'Масляный, топливный, воздушный, фильтр КПП',
     'Прокладки, по состоянию', 'Расширенное ТО каждые 750 м/ч, далее цикл с ТО-1'),
]

RULE_FIELDS = ('keyword', 'unit', 'interval', 'warn', 'daily_norm')
REGULATION_FIELDS = ('to_kind', 'periodicity', 'works', 'oil', 'filters', 'parts', 'note')



def _migrate_1_to_2(conn):
    """v2: events.date_unknown — дата события не указана (baseline «последнее ТО» без даты)."""
    cols = {row[1] for row in conn.execute('PRAGMA table_info(events)')}
    if 'date_unknown' not in cols:
        conn.execute('ALTER TABLE events ADD COLUMN date_unknown INTEGER NOT NULL DEFAULT 0')


# Миграции: {с какой версии: функция(conn)}; функция переводит схему на следующую версию.
MIGRATIONS = {1: _migrate_1_to_2}


# --------------------------------------------------------------------------------------
# Подключение
# --------------------------------------------------------------------------------------

def now_iso():
    """Метка времени: локальное время в ISO, до секунд."""
    return datetime.now().isoformat(timespec='seconds')


def _ulower(s):
    """Нижний регистр для поиска: LIKE/lower() в SQLite не понимают кириллицу."""
    return (s or '').lower() if isinstance(s, str) or s is None else str(s).lower()


def open_db(path):
    """Открыть базу (создать, если её нет): одно соединение на всю программу.

    Включает foreign_keys, WAL, synchronous=NORMAL; row_factory = sqlite3.Row; SQL-функция ulower(s);
    создаёт схему и сиды для пустой базы, выполняет миграции по meta.schema_version.
    """
    path = str(path)
    if path != ':memory:':
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        conn.create_function('ulower', 1, _ulower, deterministic=True)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        with LOCK:
            _ensure_schema(conn)
    except BaseException:
        conn.close()            # файл не должен остаться занятым: повреждённую базу нужно переименовать
        raise
    return conn


def _usable_copy(path):
    """Копия базы исправна (integrity_check = ok) и в ней есть таблицы machines и events."""
    try:
        src = sqlite3.connect(str(path))
    except sqlite3.Error:
        return False
    try:
        if src.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            return False
        return src.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                           "AND name IN ('machines', 'events')").fetchone()[0] == 2
    except sqlite3.Error:
        return False
    finally:
        src.close()


def recover_db(path, backups_dir):
    """База не открывается (sqlite3.DatabaseError): повреждённый файл (с -wal/-shm) переименовывается
    в <имя>.broken-YYYYMMDD-HHMMSS, на его место копируется самая свежая исправная резервная копия;
    исправной копии нет — создаётся пустая база. Возвращает (conn, {broken, restored_from | None})."""
    path = Path(path)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    broken = path.with_name(f'{path.name}.broken-{stamp}')
    for suffix in ('', '-wal', '-shm'):
        src = path.with_name(path.name + suffix)
        if src.exists():
            os.replace(src, broken.with_name(broken.name + suffix))
    for name in _backup_names(backups_dir):
        copy = Path(backups_dir) / name
        if not _usable_copy(copy):
            continue
        shutil.copyfile(copy, path)
        try:
            return open_db(path), {'broken': broken.name, 'restored_from': name}
        except sqlite3.DatabaseError:
            path.unlink(missing_ok=True)
    return open_db(path), {'broken': broken.name, 'restored_from': None}


def close_db(conn):
    """Закрыть базу: сбросить WAL в основной файл (wal_checkpoint(TRUNCATE)) и закрыть соединение."""
    with LOCK:
        try:
            if conn.in_transaction:
                conn.commit()
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except sqlite3.Error:
            pass
        conn.close()


@contextmanager
def transaction(conn, commit=None):
    """Транзакция: `with transaction(conn): ...` — commit при успехе, rollback при ошибке.

    commit=None  — если транзакция уже открыта, блок просто выполняется внутри неё
                   (фиксирует или откатывает тот, кто её открыл); иначе открывает и закрывает свою;
    commit=True  — в конце обязательно commit (при ошибке rollback);
    commit=False — ничего не фиксировать и не откатывать (транзакцией управляет вызывающий).
    """
    own = (not conn.in_transaction) if commit is None else bool(commit)
    if own and not conn.in_transaction:
        conn.execute('BEGIN')
    try:
        yield conn
    except BaseException:
        if own:
            conn.rollback()
        raise
    else:
        if own:
            conn.commit()


def _schema_version(conn):
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def _ensure_schema(conn):
    """Создать недостающие таблицы и индексы, засеять пустую базу, выполнить миграции."""
    conn.executescript(SCHEMA_SQL)
    if _schema_version(conn) == 0:
        with transaction(conn, commit=True):
            _seed(conn)
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                         (str(SCHEMA_VERSION),))
    _migrate(conn)


def _seed(conn):
    """Начальные данные пустой базы: настройки, правила по маркам, справочники, регламент."""
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute('INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)',
                     (key, json.dumps(value, ensure_ascii=False)))
    if conn.execute('SELECT COUNT(*) FROM rules').fetchone()[0] == 0:
        conn.executemany(
            'INSERT INTO rules(pos, keyword, unit, interval, warn, daily_norm) VALUES(?, ?, ?, ?, ?, NULL)',
            [(pos, *rule) for pos, rule in enumerate(SEED_RULES, 1)])
    for name, values in SEED_LISTS.items():
        conn.executemany('INSERT OR IGNORE INTO lists(list, value, pos) VALUES(?, ?, ?)',
                         [(name, value, pos) for pos, value in enumerate(values, 1)])
    if conn.execute('SELECT COUNT(*) FROM regulation').fetchone()[0] == 0:
        conn.executemany(
            'INSERT INTO regulation(pos, to_kind, periodicity, works, oil, filters, parts, note) '
            'VALUES(?, ?, ?, ?, ?, ?, ?, ?)',
            [(pos, *row) for pos, row in enumerate(SEED_REGULATION, 1)])


def _migrate(conn):
    """Довести схему до SCHEMA_VERSION (нужно и после восстановления старой копии)."""
    version = _schema_version(conn)
    while version < SCHEMA_VERSION:
        step = MIGRATIONS.get(version)
        with transaction(conn, commit=True):
            if step is not None:
                step(conn)
            version += 1
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                         (str(version),))


# --------------------------------------------------------------------------------------
# Настройки, справочники, правила, регламент
# --------------------------------------------------------------------------------------

def get_settings(conn):
    """Все настройки: {key: значение}; значения распарсены из JSON, отсутствующие ключи — по умолчанию."""
    result = {key: (list(value) if isinstance(value, list) else value)
              for key, value in DEFAULT_SETTINGS.items()}
    for row in conn.execute('SELECT key, value FROM settings'):
        try:
            result[row['key']] = json.loads(row['value'])
        except (TypeError, ValueError):
            pass   # испорченное значение — остаётся умолчание
    return result


def set_settings(conn, values, commit=None):
    """Записать настройки {key: value} (меняются только переданные ключи). Возвращает все настройки."""
    with transaction(conn, commit):
        for key, value in (values or {}).items():
            conn.execute('INSERT INTO settings(key, value) VALUES(?, ?) '
                         'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                         (str(key), json.dumps(value, ensure_ascii=False)))
    return get_settings(conn)


def get_lists(conn):
    """Справочники: {имя списка: [значения по порядку]}; известные списки есть всегда (хотя бы пустые)."""
    result = {name: [] for name in LIST_NAMES}
    for row in conn.execute('SELECT list, value FROM lists ORDER BY list, pos, id'):
        result.setdefault(row['list'], []).append(row['value'])
    return result


def _clean_values(values):
    """Список строк без пустых и без повторов, порядок сохраняется."""
    result = []
    seen = set()
    for value in values or []:
        value = '' if value is None else str(value).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def set_list(conn, name, values, commit=None):
    """Полностью заменить справочник name значениями values (пустые и повторы отбрасываются).
    Возвращает сохранённый список. Защиту обязательных видов событий делает вызывающий (PROTECTED_KINDS)."""
    values = _clean_values(values)
    with transaction(conn, commit):
        conn.execute('DELETE FROM lists WHERE list=?', (name,))
        conn.executemany('INSERT INTO lists(list, value, pos) VALUES(?, ?, ?)',
                         [(name, value, pos) for pos, value in enumerate(values, 1)])
    return values


def get_rules(conn):
    """Правила по маркам по порядку: [{id, pos, keyword, unit, interval, warn, daily_norm}]."""
    return [dict(row) for row in conn.execute(
        'SELECT id, pos, keyword, unit, interval, warn, daily_norm FROM rules ORDER BY pos, id')]


def set_rules(conn, rules, commit=None):
    """Полностью заменить правила списком [{keyword, unit, interval, warn, daily_norm}] (порядок = порядок списка).
    Значения должны быть уже проверены вызывающим. Возвращает сохранённые правила."""
    rows = [(pos, *[rule.get(field) for field in RULE_FIELDS]) for pos, rule in enumerate(rules or [], 1)]
    with transaction(conn, commit):
        conn.execute('DELETE FROM rules')
        conn.executemany(
            'INSERT INTO rules(pos, keyword, unit, interval, warn, daily_norm) VALUES(?, ?, ?, ?, ?, ?)', rows)
    return get_rules(conn)


def get_regulation(conn):
    """Регламент ТО по порядку: [{id, pos, to_kind, periodicity, works, oil, filters, parts, note}]."""
    return [dict(row) for row in conn.execute(
        'SELECT id, pos, to_kind, periodicity, works, oil, filters, parts, note FROM regulation ORDER BY pos, id')]


def set_regulation(conn, rows, commit=None):
    """Полностью заменить регламент списком [{to_kind, periodicity, works, oil, filters, parts, note}].
    Возвращает сохранённый регламент."""
    data = []
    for pos, row in enumerate(rows or [], 1):
        values = ['' if row.get(field) is None else str(row.get(field)) for field in REGULATION_FIELDS]
        data.append((pos, *values))
    with transaction(conn, commit):
        conn.execute('DELETE FROM regulation')
        conn.executemany(
            'INSERT INTO regulation(pos, to_kind, periodicity, works, oil, filters, parts, note) '
            'VALUES(?, ?, ?, ?, ?, ?, ?, ?)', data)
    return get_regulation(conn)


# --------------------------------------------------------------------------------------
# Резервные копии
# --------------------------------------------------------------------------------------

def _backup_stamp(name):
    try:
        return datetime.strptime(name, 'to-%Y%m%d-%H%M%S.db')
    except ValueError:
        return None


def _backup_names(backups_dir):
    """Имена файлов копий в папке, новые сверху (имя содержит дату и время, поэтому сортируем по имени).
    Копии с датой из будущего (часы ПК сбились назад) — в самом конце: они не считаются «последними»
    и при ротации удаляются первыми."""
    folder = Path(backups_dir)
    if not folder.is_dir():
        return []
    names = sorted((p.name for p in folder.iterdir() if p.is_file() and BACKUP_NAME_RE.match(p.name)),
                   reverse=True)
    limit = datetime.now() + timedelta(minutes=BACKUP_FUTURE_MINUTES)
    future = [n for n in names if (_backup_stamp(n) or limit) > limit]
    return [n for n in names if n not in future] + future


def _make_backup(conn, backups_dir):
    """Снять копию базы в backups_dir/to-YYYYMMDD-HHMMSS.db через Connection.backup. Возвращает имя файла."""
    folder = Path(backups_dir)
    folder.mkdir(parents=True, exist_ok=True)
    if conn.in_transaction:
        conn.commit()
    stamp = datetime.now().replace(microsecond=0)
    while True:
        name = stamp.strftime('to-%Y%m%d-%H%M%S.db')
        if not (folder / name).exists():
            break
        stamp += timedelta(seconds=1)   # две копии в одну секунду — вторая получает следующее имя
    tmp = folder / (name + '.tmp')
    if tmp.exists():
        tmp.unlink()
    dst = sqlite3.connect(str(tmp))
    try:
        conn.backup(dst)
        # копия — один самодостаточный файл (без -wal/-shm): её можно унести на флешке
        dst.execute('PRAGMA journal_mode=DELETE')
        dst.commit()
    finally:
        dst.close()
    os.replace(tmp, folder / name)
    return name


def _prune_backups(backups_dir, protect=()):
    """Оставить BACKUP_KEEP последних копий; имена из protect не удаляются. Заодно убрать недописанные .tmp."""
    folder = Path(backups_dir)
    for name in _backup_names(folder)[BACKUP_KEEP:]:
        if name in protect:
            continue
        try:
            (folder / name).unlink()
        except OSError:
            pass
    if folder.is_dir():
        for p in folder.glob('to-*.db.tmp'):
            try:
                p.unlink()
            except OSError:
                pass


def backup_now(conn, backups_dir):
    """Создать резервную копию сейчас. Возвращает имя файла; хранится BACKUP_KEEP последних копий."""
    with LOCK:
        name = _make_backup(conn, backups_dir)
        _prune_backups(backups_dir, protect={name})
    return name


def auto_backup(conn, backups_dir):
    """Копия при старте программы: только если в базе есть машины и последняя копия старше 6 часов.
    Возвращает имя созданной копии или None."""
    with LOCK:
        if conn.execute('SELECT COUNT(*) FROM machines').fetchone()[0] == 0:
            return None
        names = _backup_names(backups_dir)
        if names:
            last = _backup_stamp(names[0])
            now = datetime.now()
            # копия «из будущего» (часы сбились назад) свежей не считается — копию нужно сделать
            if (last is not None and last <= now + timedelta(minutes=BACKUP_FUTURE_MINUTES)
                    and now - last < timedelta(hours=BACKUP_MIN_HOURS)):
                return None
        return backup_now(conn, backups_dir)


def list_backups(backups_dir):
    """Список копий, новые сверху: [{name, size (байт), mtime ('YYYY-MM-DDTHH:MM:SS')}]."""
    folder = Path(backups_dir)
    result = []
    for name in _backup_names(folder):
        try:
            stat = (folder / name).stat()
        except OSError:
            continue
        result.append({
            'name': name,
            'size': stat.st_size,
            'mtime': datetime.fromtimestamp(stat.st_mtime).isoformat(timespec='seconds'),
        })
    return result


def restore_backup(conn, backups_dir, name):
    """Восстановить базу из копии name в ЖИВОЕ соединение (файлы не подменяются).

    Имя проверяется по шаблону to-YYYYMMDD-HHMMSS.db (иначе ValueError), файл должен лежать в backups_dir
    (иначе FileNotFoundError) и быть базой программы (иначе ValueError). Сначала снимается копия текущей
    базы, затем содержимое копии переносится через backup API, затем выполняются миграции.
    Возвращает имя страховочной копии текущей базы.
    """
    if not isinstance(name, str) or not BACKUP_NAME_RE.match(name):
        raise ValueError('Неверное имя резервной копии')
    path = Path(backups_dir) / name
    if not path.is_file():
        raise FileNotFoundError(f'Резервная копия {name} не найдена')

    with LOCK:
        src = sqlite3.connect(str(path))
        try:
            try:
                ok = src.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('machines', 'events')"
                ).fetchone()[0] == 2
            except sqlite3.DatabaseError:
                ok = False
            if not ok:
                raise ValueError(f'Файл {name} повреждён или это не база программы «Учёт ТО»')

            safety = _make_backup(conn, backups_dir)
            src.backup(conn)
        finally:
            src.close()
        conn.execute('PRAGMA foreign_keys=ON')
        _ensure_schema(conn)
        _prune_backups(backups_dir, protect={name, safety})
    return safety
