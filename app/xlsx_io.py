"""Импорт из таблицы v8 и экспорт в Excel (SPEC, раздел 6).

    import_v8(conn, file_bytes, dry_run=False) -> dict     перенос данных из таблицы «Журнал учёта техники и ТО v8»
    export_xlsx(conn) -> bytes                             книга xlsx: Парк, Машины, Журнал, Шины, Узлы

Вызывающий (server.py) держит замок db.LOCK; функции сами управляют транзакцией.
Ошибка формата файла -> ValueError с русским текстом (сервер отвечает 400).

Импорт читает файл целиком ДО начала транзакции, затем одной транзакцией пишет:
настройки и справочники -> регламент -> машины -> события (архив, потом журнал) -> шины -> узлы.
dry_run=True выполняет всё то же самое и откатывает транзакцию — остаётся только отчёт.
openpyxl подключается внутри функций (не при старте программы).
"""

import io
import math
import re
from datetime import date, datetime, time, timedelta

from app import db, logic
from app.logic import KIND_MARK

MAX_EMPTY_ROWS = 2000     # после стольких пустых строк подряд чтение листа прекращается
MAX_WARNINGS = 300        # в отчёт попадает не больше стольких предупреждений (остальные — одной строкой-итогом)

NOT_V8 = 'Это не таблица учёта ТО версии 8'
NOT_XLSX = ('Не удалось открыть файл: это не книга Excel (.xlsx). '
            'В Google-таблице: Файл → Скачать → Microsoft Excel (.xlsx), затем выберите скачанный файл.')

MACHINE_STATUSES = ('В работе', 'На консервации', 'Списана')
CHECK_UNIT_WORDS = ('SANY', 'САНИ', 'VOLVO', 'ВОЛЬВО')      # марки, у которых бывают и самосвалы, и экскаваторы
# км-правила из ТЗ (R3), которых может не быть в таблице: VOLVO не было в v8, SANY убран из таблицы v10.
# Если слова нет в столбцах N–P — правило добавляется (экскаваторы SANY/VOLVO остаются на моточасах
# по настройке «Какая техника считается по моточасам»).
TZ_KM_RULES = (('SANY', 15000, 1000), ('САНИ', 15000, 1000), ('VOLVO', 15000, 1000), ('ВОЛЬВО', 15000, 1000))

# Листы таблицы v8: имя -> (первая строка данных, последний нужный столбец,
#                           столбцы (с нуля), по которым строка считается непустой)
SHEETS = {
    'Настройки':     (4, 16, (0, 1, 3, 5, 7, 9, 11, 13, 14, 15)),
    'Регламент ТО':  (5, 7, (0, 1, 2, 3, 4, 5, 6)),
    # статус (K) в «пустоту» строки не входит: в шаблонах он бывает проставлен заранее на все строки.
    # Столбцы правее таблицы v8 (Машины M–Q, Журнал O–P, Шины L) — служебные столбцы экспорта программы
    # (EXTRA_COLUMNS): читаются, только если в шапке стоит их название; в «пустоту» строки не входят.
    'Машины':        (5, 17, (1, 2, 3, 4, 5, 6, 7, 8, 9, 11)),
    'Архив журнала': (5, 16, (0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13)),
    'Журнал':        (5, 16, (0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13)),
    'Шины':          (5, 12, (0, 1, 2, 3, 4, 5, 6, 10)),
    'Узлы':          (5, 9, (0, 1, 2, 3, 8)),
}

# Служебные столбцы экспорта (правее столбцов таблицы v8), чтобы выгрузка импортировалась обратно без потерь:
# лист -> [(столбец (с нуля), поле, заголовок, ширина)]. При импорте столбец учитывается, только если его
# заголовок в 4-й строке совпадает с заголовком отсюда (у «Заказано» — содержит слово «заказано»:
# так этот столбец назван в самой таблице v8, там в нём дата заказа).
ORDERED_HEADER = 'Заказано (дата)'
SERVICE_HEADER = 'Служебное (программа «Учёт ТО»)'
SERVICE_DATE_UNKNOWN = 'без даты'     # метка служебного столбца журнала: дата события не указана
EXTRA_COLUMNS = {
    'Машины': [
        (12, 'unit_override', 'Своя единица учёта (пусто = авто)', 14),
        (13, 'interval_override', 'Свой интервал ТО (пусто = авто)', 14),
        (14, 'warn_override', 'Свой порог «скоро ТО» (пусто = авто)', 14),
        (15, 'norm_override', 'Своя норма в сутки (пусто = авто)', 14),
        (16, 'reg_date', 'Дата показания при регистрации', 14),
    ],
    'Журнал': [
        (14, 'ordered', ORDERED_HEADER, 12),
        (15, 'service', SERVICE_HEADER, 16),
    ],
    'Шины': [
        (11, 'install_reading', 'Показание машины при установке', 14),
    ],
}
EXTRA_COLUMNS['Архив журнала'] = EXTRA_COLUMNS['Журнал']

HEADER_ROW = 4            # шапка листов с данными; данные — с 5-й строки
# Приметы шапки таблицы v8: лист -> {столбец (с нуля): слово, которое должно быть в названии столбца}
HEADERS = {
    'Машины':        {2: 'гараж', 5: 'регистрац', 10: 'статус'},
    'Архив журнала': {0: 'дата', 2: 'машин', 3: 'событ', 6: 'вышла', 9: 'делал'},
    'Журнал':        {0: 'дата', 2: 'машин', 3: 'событ', 6: 'вышла', 9: 'делал'},
    'Шины':          {0: 'шин', 1: 'гараж', 5: 'ресурс'},
    'Узлы':          {0: 'машин', 1: 'узел', 3: 'установк'},
}

# Списки листа «Настройки»: столбец (с нуля) -> имя справочника
SETTINGS_LISTS = ((3, 'kinds'), (5, 'unit_names'), (7, 'tyre_types'), (9, 'positions'), (11, 'sites'))

_EXCEL_ERRORS = {'#N/A', '#REF!', '#VALUE!', '#DIV/0!', '#NAME?', '#NUM!', '#NULL!'}
# латинские буквы, неотличимые на вид от русских: «MИРНЫЙ», «2A/317» и т.п.
_LAT_LOOKALIKE = 'ABCEHKMOPTXY'
_LAT2CYR = str.maketrans(_LAT_LOOKALIKE, 'АВСЕНКМОРТХУ')

_DATE_DMY = re.compile(r'^(\d{1,2})[./\-](\d{1,2})[./\-](\d{4}|\d{2})$')
_DATE_ISO = re.compile(r'^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T\s].*)?$')
_TIME_HM = re.compile(r'^(\d{1,2})\s*([:.,\-]|\s)\s*(\d{1,2})(?::\d{1,2})?$')
_NUMBER = re.compile(r'^([+-]?\d+(?:\.\d+)?)\D*$')        # число и необязательный хвост («125000км»)
_AUTO_WHO = re.compile(r'^авто\s*\+\s*([\d\s.,]+)$', re.IGNORECASE)


# --------------------------------------------------------------------------------------
# Разбор значений ячеек
# --------------------------------------------------------------------------------------

def _cell(v):
    """Значение ячейки для импорта. Принимаются только str/int/float/bool/datetime/date/time/timedelta;
    строка, начинающаяся с «=» (формула), ошибки Excel и любые другие объекты (ArrayFormula…) — пусто (None)."""
    if v is None:
        return None
    if isinstance(v, str):
        if v.startswith('='):
            return None
        v = v.replace('\xa0', ' ').strip()
        return None if not v or v in _EXCEL_ERRORS else v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, (bool, int, datetime, date, time, timedelta)):
        return v
    return None


def _text(v):
    """Значение ячейки как текст (для текстовых полей): числа без «.0», даты как ДД.ММ.ГГГГ."""
    if v is None:
        return ''
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, bool):
        return 'да' if v else 'нет'
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    if isinstance(v, datetime):
        return v.strftime('%d.%m.%Y') if (v.hour, v.minute) == (0, 0) else v.strftime('%d.%m.%Y %H:%M')
    if isinstance(v, date):
        return v.strftime('%d.%m.%Y')
    if isinstance(v, time):
        return v.strftime('%H:%M')
    if isinstance(v, timedelta):
        minutes = int(round(v.total_seconds() / 60))
        return f'{minutes // 60}:{minutes % 60:02d}'
    return str(v).strip()


def _clean(v):
    """Целое значение — как int (10000, а не 10000.0)."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _parse_number(v):
    """Показание/ресурс: число; строка «125 000», «1250,5», «125000 км» -> float. Не число -> None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, str):
        s = re.sub(r'[\s  ]', '', v).replace(',', '.')
        m = _NUMBER.match(s)
        if m:
            return float(m.group(1))
    return None


def _parse_date(v):
    """Дата -> 'YYYY-MM-DD'. Понимает datetime/date, строки ДД.ММ.ГГГГ, ДД.ММ.ГГ и ISO,
    число 20000…80000 (серийная дата Excel от 30.12.1899). Не распознано -> None."""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if 20000 <= v <= 80000:
            return (date(1899, 12, 30) + timedelta(days=int(v))).isoformat()
        return None
    if isinstance(v, str):
        s = v.strip()
        try:
            m = _DATE_DMY.match(s)
            if m:
                year = int(m.group(3))
                if year < 100:
                    year += 2000
                return date(year, int(m.group(2)), int(m.group(1))).isoformat()
            m = _DATE_ISO.match(s)
            if m:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
        if re.fullmatch(r'\d{5}(?:[.,]\d+)?', s):          # серийная дата, записанная текстом
            return _parse_date(float(s.replace(',', '.')))
    return None


def _hhmm(hours, minutes):
    if 0 <= hours <= 23 and 0 <= minutes <= 59:
        return f'{hours:02d}:{minutes:02d}'
    return None


def _parse_time(v):
    """Время -> 'HH:MM'. Понимает time/datetime/timedelta, долю суток 0..1,
    строки и числа вида «9:30», «930», «9.30», «9». Не распознано -> None."""
    if isinstance(v, (datetime, time)):
        return _hhmm(v.hour, v.minute)
    if isinstance(v, timedelta):
        minutes = int(round(v.total_seconds() / 60))
        if minutes < 0:
            return None
        minutes %= 1440
        return _hhmm(minutes // 60, minutes % 60)
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if 0 <= v < 1:                                   # доля суток, как хранит Excel
            minutes = int(round(v * 1440)) % 1440
            return _hhmm(minutes // 60, minutes % 60)
        v = _text(v)                                     # 930 -> «930», 9.3 -> «9.3»
    if isinstance(v, str):
        s = v.strip()
        m = _TIME_HM.match(s)
        if m:
            hours, sep, mins = int(m.group(1)), m.group(2), m.group(3)
            # «9.3» — это 9.30, записанное числом (Excel отбросил ноль)
            minutes = int(mins) * 10 if sep in '.,' and len(mins) == 1 else int(mins)
            return _hhmm(hours, minutes)
        if re.fullmatch(r'\d{3,4}', s):
            return _hhmm(int(s[:-2]), int(s[-2:]))
        if re.fullmatch(r'\d{1,2}', s):
            return _hhmm(int(s), 0)
    return None


def _loose_key(s):
    """Ключ «нестрогого» сравнения: без учёта регистра, лишних пробелов и латиницы вместо похожих русских букв."""
    return ' '.join((s or '').upper().translate(_LAT2CYR).split())


# поля события, которые повторный импорт дописывает в базу, если там пусто (ключ дубля их не включает)
_FILL_FIELDS = ('shift', 'time_in', 'date_out', 'time_out', 'parts_used', 'parts_order', 'remarks', 'who')
_FILL_NAMES = {'shift': 'смена', 'time_in': 'время заезда', 'date_out': 'дата выхода', 'time_out': 'время выхода',
               'parts_used': 'запчасти', 'parts_order': 'заказать', 'remarks': 'замечания', 'who': 'кто'}


def _event_key(machine_id, day, kind, reading, work, remarks):
    """Ключ дубля события: машина, дата, вид, показание, что делалось. У записи без «что делалось» и не
    ТО/ремонта/простоя/отметки (напр. «Заметка») её смысл — в замечаниях, они тоже входят в ключ."""
    key = (machine_id, day, kind, reading, work)
    if not work and not (logic.is_to(kind) or logic.is_repair(kind) or logic.is_idle(kind) or kind == KIND_MARK):
        key += (remarks,)
    return key


# --------------------------------------------------------------------------------------
# Чтение книги
# --------------------------------------------------------------------------------------

def _read_rows(ws, min_row, max_col, used):
    """Непустые строки листа одним проходом: [(номер строки, [значения первых max_col столбцов])].
    Строка пуста, если пусты все столбцы из used. После MAX_EMPTY_ROWS пустых строк подряд чтение прекращается."""
    ws.reset_dimensions()
    rows = []
    empty = 0
    for n, row in enumerate(ws.iter_rows(min_row=min_row, max_col=max_col, values_only=True), min_row):
        values = [_cell(v) for v in row[:max_col]]
        if len(values) < max_col:
            values.extend([None] * (max_col - len(values)))
        if all(values[i] is None for i in used):
            empty += 1
            if empty >= MAX_EMPTY_ROWS:
                break
            continue
        empty = 0
        rows.append((n, values))
    return rows


def _read_book(file_bytes):
    """Прочитать нужные листы книги -> (book, headers):
    book    — {имя листа: [(номер строки, значения)] | None (листа нет)};
    headers — {имя листа: значения строки-шапки (4-я строка)} для листов, где шапка заполнена."""
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(file_bytes or b''), read_only=True, data_only=False)
    except Exception:
        raise ValueError(NOT_XLSX) from None
    try:
        names = {name.strip(): name for name in wb.sheetnames}
        if 'Машины' not in names or 'Журнал' not in names:
            raise ValueError(NOT_V8)
        book, headers = {}, {}
        for title, (min_row, max_col, used) in SHEETS.items():
            if title not in names:
                book[title] = None
                continue
            try:
                # лист читается одним проходом, начиная с шапки (если она выше данных)
                rows = _read_rows(wb[names[title]], min(min_row, HEADER_ROW), max_col, used)
            except Exception as e:
                raise ValueError(f'Не удалось прочитать лист «{title}»: файл повреждён ({e})') from None
            if min_row > HEADER_ROW and rows and rows[0][0] == HEADER_ROW:
                headers[title] = rows.pop(0)[1]
            book[title] = rows
        return book, headers
    finally:
        wb.close()


# --------------------------------------------------------------------------------------
# Импорт
# --------------------------------------------------------------------------------------

class _Import:
    """Один импорт: общее состояние (счётчики, предупреждения, найденные машины) и шаги по листам."""

    def __init__(self, conn):
        self.conn = conn
        self.now = db.now_iso()
        self.today = date.today().isoformat()
        self.warnings = []
        self.warnings_total = 0
        self.counts = {'machines_new': 0, 'machines_updated': 0, 'events_new': 0, 'events_skipped': 0,
                       'events_updated': 0, 'tyres_new': 0, 'tyres_skipped': 0, 'units_new': 0,
                       'units_skipped': 0, 'orders_open': 0, 'settings_updated': 0}
        self.canon = {}          # имя справочника -> {нестрогий ключ: написание из справочника}
        self.machine_ids = {}    # гаражный № (strip + upper) -> id
        self.machine_names = {}  # id -> гаражный № как записан
        self.loose_ids = {}      # нестрогий ключ гаражного № -> {id}
        self.next_pos = 1
        self.headers = {}        # имя листа -> значения строки-шапки (для служебных столбцов)

    def warn(self, text):
        self.warnings_total += 1
        if len(self.warnings) < MAX_WARNINGS:
            self.warnings.append(text)

    def result(self, dry_run):
        warnings = list(self.warnings)
        if self.warnings_total > len(warnings):
            warnings.append(f'… и ещё {self.warnings_total - len(warnings)} предупреждений '
                            f'(показаны первые {len(warnings)})')
        result = {'ok': True, 'dry_run': bool(dry_run)}
        result.update(self.counts)
        result['warnings'] = warnings
        result['warnings_total'] = self.warnings_total
        return result

    def check_header(self, title, header):
        """Шапка листа не похожа на таблицу v8 (другая версия, столбцы сдвинуты) — предупредить."""
        if not header:
            return
        for col, word in HEADERS[title].items():
            name = _text(header[col])
            if word not in name.lower():
                self.warn(f'Лист «{title}»: шапка не как в таблице версии 8 — столбец {chr(65 + col)} называется '
                          f'«{name}», а в его названии ожидалось «{word}…». Проверьте порядок столбцов, '
                          f'иначе данные попадут не в те поля')
                return

    def extra(self, title):
        """Служебные столбцы экспорта, найденные в шапке листа: {поле: столбец (с нуля)}."""
        header = self.headers.get(title) or []
        found = {}
        for col, field, name, _ in EXTRA_COLUMNS.get(title, ()):
            text = _text(header[col]) if col < len(header) else ''
            if text == name or (name == ORDERED_HEADER and 'заказано' in text.lower()):
                found[field] = col
        return found

    def number(self, raw, where, what):
        """Число из ячейки; непонятное или отрицательное значение -> предупреждение и None."""
        if raw is None:
            return None
        value = _parse_number(raw)
        if value is None:
            self.warn(f'{where}: {what} «{_text(raw)}» — не число, записано пустым')
        elif value < 0:
            self.warn(f'{where}: {what} «{_text(raw)}» меньше нуля, записано пустым')
            value = None
        return value

    def canon_value(self, list_name, text):
        """Написание из справочника, если значение совпадает с ним без учёта регистра; иначе как есть."""
        if not text:
            return ''
        return self.canon.get(list_name, {}).get(_loose_key(text), text)

    def load_lists(self):
        self.canon = {name: {_loose_key(v): v for v in reversed(values)}
                      for name, values in db.get_lists(self.conn).items()}

    def extend_list(self, name, values):
        """Добавить в справочник недостающие значения (сравнение без учёта регистра). Возвращает, сколько добавлено."""
        current = db.get_lists(self.conn).get(name, [])
        known = {_loose_key(v) for v in current}
        missing = []
        for value in values:
            key = _loose_key(value)
            if value and key not in known:
                known.add(key)
                missing.append(value)
        if missing:
            db.set_list(self.conn, name, current + missing)
        return len(missing)

    # ---- Настройки и регламент ----

    def apply_settings(self, rows):
        """Лист «Настройки»: B4/B5 (интервал и порог м/ч), суточная норма м/ч, списки D/F/H/J/L, км-правила N–P."""
        conn = self.conn
        if rows:
            changed = 0
            current = db.get_settings(conn)
            new = {}
            for n, v in rows:
                label = _text(v[0]).lower()
                if n == 4:
                    key = 'default_interval_mh'
                elif n == 5:
                    key = 'default_warn_mh'
                elif label.startswith('начисление моточасов в день'):
                    key = 'default_norm_mh'
                else:
                    continue
                if v[1] is None:
                    continue
                value = _parse_number(v[1])
                if value is None or value <= 0:
                    self.warn(f'Настройки, строка {n}: значение «{_text(v[1])}» — не положительное число, '
                              f'настройка не изменена')
                    continue
                new[key] = _clean(value)
            to_set = {key: value for key, value in new.items() if current.get(key) != value}
            if to_set:
                db.set_settings(conn, to_set)
                changed += len(to_set)

            for col, name in SETTINGS_LISTS:
                changed += self.extend_list(name, [_text(v[col]) for n, v in rows if v[col] is not None])

            changed += self._apply_km_rules(rows)
            self.counts['settings_updated'] += changed
        self.load_lists()

    def _apply_km_rules(self, rows):
        """Столбцы N–P: если не пусто — заменить км-правила списком из таблицы (суточные нормы совпадающих слов
        сохраняются), добавить правила ТЗ (SANY/САНИ, VOLVO/ВОЛЬВО), если их нет. Правила не в км остаются.
        Возвращает 1, если изменилось."""
        conn = self.conn
        km = []
        seen = set()
        for n, v in rows:
            keyword = _text(v[13])
            if not keyword:
                continue
            interval = _parse_number(v[14])
            if interval is None or interval <= 0:
                self.warn(f'Настройки, строка {n}: у слова «{keyword}» не указан интервал ТО — правило пропущено')
                continue
            if keyword.upper() in seen:
                continue
            seen.add(keyword.upper())
            warn = _parse_number(v[15])
            km.append({'keyword': keyword, 'unit': logic.UNIT_KM, 'interval': _clean(interval),
                       'warn': _clean(warn) if warn and warn > 0 else None})
        if not km:
            return 0
        for keyword, interval, warn in TZ_KM_RULES:
            if keyword not in seen:
                km.append({'keyword': keyword, 'unit': logic.UNIT_KM, 'interval': interval, 'warn': warn})

        old = db.get_rules(conn)
        old_norm = {(r['keyword'] or '').strip().upper(): r['daily_norm'] for r in old if r['unit'] == logic.UNIT_KM}
        for rule in km:
            rule['daily_norm'] = old_norm.get(rule['keyword'].upper())
        new = km + [r for r in old if r['unit'] != logic.UNIT_KM]

        def signature(rules):
            return [(r['keyword'], r['unit'], r['interval'], r['warn'], r['daily_norm']) for r in rules]

        if signature(new) == signature(old):
            return 0
        db.set_rules(conn, new)
        return 1

    def apply_regulation(self, rows):
        """Лист «Регламент ТО» (A–G): заменить регламент, если лист не пуст."""
        fields = ('to_kind', 'periodicity', 'works', 'oil', 'filters', 'parts', 'note')
        new = []
        for n, v in rows or []:
            if not _text(v[0]):
                self.warn(f'Регламент ТО, строка {n}: не указан тип ТО — строка пропущена')
                continue
            new.append({field: _text(v[i]) for i, field in enumerate(fields)})
        if not new:
            return
        old = [{field: (row[field] or '') for field in fields} for row in db.get_regulation(self.conn)]
        if new != old:
            db.set_regulation(self.conn, new)
            self.counts['settings_updated'] += 1

    # ---- Машины ----

    def _remember_machine(self, machine_id, garage_no):
        self.machine_ids.setdefault(garage_no.strip().upper(), machine_id)
        self.machine_names[machine_id] = garage_no
        self.loose_ids.setdefault(_loose_key(garage_no), set()).add(machine_id)

    def _insert_machine(self, garage_no, fields=None):
        fields = fields or {}
        cur = self.conn.execute(
            'INSERT INTO machines(pos, garage_no, model, mtype, plate, vin, site, warranty, photo, note, '
            'status, reg_reading, reg_date, unit_override, interval_override, warn_override, norm_override, '
            'created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (self.next_pos, garage_no, fields.get('model', ''), fields.get('mtype', ''), fields.get('plate', ''),
             fields.get('vin', ''), fields.get('site', ''), fields.get('warranty', ''), fields.get('photo', ''),
             fields.get('note', ''), fields.get('status') or MACHINE_STATUSES[0], fields.get('reg_reading'),
             fields.get('reg_date'), fields.get('unit_override'), fields.get('interval_override'),
             fields.get('warn_override'), fields.get('norm_override'), self.now, self.now))
        self.next_pos += 1
        self.counts['machines_new'] += 1
        self._remember_machine(cur.lastrowid, garage_no)
        return cur.lastrowid

    def find_machine(self, garage_no, where):
        """id машины по гаражному № из журнала/шин/узлов. Машины нет в списке — создаётся заглушка."""
        key = garage_no.upper()
        machine_id = self.machine_ids.get(key)
        if machine_id is not None:
            return machine_id
        similar = self.loose_ids.get(_loose_key(garage_no))
        if similar and len(similar) == 1:
            machine_id = next(iter(similar))
            self.machine_ids[key] = machine_id          # дальше — без повторных предупреждений
            name = self.machine_names[machine_id]
            self.warn(f'{where}: машина «{garage_no}» в списке машин записана как «{name}» (латинские/русские '
                      f'буквы или пробелы) — её записи отнесены к «{name}»')
            return machine_id
        machine_id = self._insert_machine(garage_no)
        self.warn(f'{where}: машины «{garage_no}» нет в списке машин — создана машина-заглушка '
                  f'(заполните её данные в «Машинах»)')
        return machine_id

    def apply_machines(self, rows):
        """Лист «Машины»: upsert по гаражному № (strip + upper). Пустые ячейки таблицы данные в базе не затирают."""
        conn = self.conn
        existing, existing_by_id = {}, {}
        for row in conn.execute('SELECT * FROM machines ORDER BY id'):
            self._remember_machine(row['id'], row['garage_no'])
            existing.setdefault(row['garage_no'].strip().upper(), dict(row))
            existing_by_id[row['id']] = dict(row)
        self.next_pos = conn.execute('SELECT COALESCE(MAX(pos), 0) + 1 FROM machines').fetchone()[0]

        statuses = {_loose_key(s): s for s in MACHINE_STATUSES}
        extra = self.extra('Машины')      # служебные столбцы выгрузки программы (свои параметры ТО, reg_date)
        seen = {}                # ключ -> номер строки листа
        new_sites, new_mtypes = [], []
        check_units = []         # машины листа — для проверки единицы учёта

        for n, v in rows or []:
            where = f'Машины, строка {n}'
            garage_no = _text(v[2])
            if not garage_no:
                self.warn(f'{where}: нет гаражного № — строка пропущена')
                continue
            key = garage_no.upper()
            loose = _loose_key(garage_no)
            if loose in seen:
                self.warn(f'{where}: гаражный № «{garage_no}» повторяется (уже был в строке {seen[loose]}) — '
                          f'строка пропущена')
                continue
            seen[loose] = n

            site = ' '.join(_text(v[6]).split())
            fields = {
                'model': _text(v[1]), 'plate': _text(v[3]), 'vin': _text(v[4]),
                'site': self.canon_value('sites', site),
                'warranty': _text(v[7]), 'photo': _text(v[8]), 'note': _text(v[9]),
                'mtype': self.canon_value('mtypes', _text(v[11])),
                'reg_reading': self.number(v[5], where, 'показание при регистрации'),
            }
            for field, col in extra.items():
                raw = v[col]
                if raw is None:
                    continue
                if field == 'unit_override':
                    unit = {'КМ': logic.UNIT_KM, 'М/Ч': logic.UNIT_MH}.get(_text(raw).upper().replace(' ', ''))
                    if unit is None:
                        self.warn(f'{where}: своя единица учёта «{_text(raw)}» — не «км» и не «м/ч», не записана')
                    else:
                        fields[field] = unit
                elif field == 'reg_date':
                    fields[field] = _parse_date(raw)
                    if fields[field] is None:
                        self.warn(f'{where}: дата показания при регистрации «{_text(raw)}» не распознана')
                else:
                    value = self.number(raw, where, 'свой параметр ТО')
                    if value is not None and value > 0:
                        fields[field] = value
            status = _text(v[10])
            if status:
                fields['status'] = statuses.get(_loose_key(status))
                if fields['status'] is None:
                    self.warn(f'{where}: непонятный статус «{status}» — записано «{MACHINE_STATUSES[0]}»')
                    fields['status'] = MACHINE_STATUSES[0]
            # новое значение запоминается: следующие машины с тем же участком/типом получат то же написание
            if fields['site']:
                new_sites.append(fields['site'])
                self.canon.setdefault('sites', {}).setdefault(_loose_key(fields['site']), fields['site'])
            if fields['mtype']:
                new_mtypes.append(fields['mtype'])
                self.canon.setdefault('mtypes', {}).setdefault(_loose_key(fields['mtype']), fields['mtype'])

            old = existing.get(key)
            if old is None:
                # в программе номер исправлен (латиница -> кириллица, пробелы): та же машина, а не новая
                similar = self.loose_ids.get(loose) or set()
                if len(similar) == 1:
                    old = existing_by_id.get(next(iter(similar)))
                if old is not None:
                    self.machine_ids[key] = old['id']       # журнал/шины/узлы найдут её без повторных предупреждений
                    self.warn(f"{where}: машина «{garage_no}» в программе записана как «{old['garage_no']}» "
                              f"(латинские/русские буквы или пробелы) — данные отнесены к «{old['garage_no']}»")
            if old is None:
                latin = sorted({ch for ch in key if ch in _LAT_LOOKALIKE})
                if latin:
                    self.warn(f'{where}: в гаражном № «{garage_no}» есть латинские буквы ({", ".join(latin)}), '
                              f'похожие на русские — если это русские буквы, исправьте номер в «Машинах» программы')
                machine_id = self._insert_machine(garage_no, fields)
                machine = dict(fields, id=machine_id, garage_no=garage_no)
            else:
                # обновляются только поля, заполненные в таблице и отличающиеся от базы; reg_date не трогаем
                updates = {name: value for name, value in fields.items()
                           if value not in ('', None) and value != old.get(name)}
                if updates:
                    sets = ', '.join(f'{name}=?' for name in updates)
                    conn.execute(f'UPDATE machines SET {sets}, updated_at=? WHERE id=?',
                                 (*updates.values(), self.now, old['id']))
                    self.counts['machines_updated'] += 1
                machine = dict(old, **updates)
            check_units.append(machine)

        # участки и типы машин, которых нет в справочниках, — добавить, чтобы их можно было выбрать в программе
        self.extend_list('sites', new_sites)
        self.extend_list('mtypes', new_mtypes)

        # км-правило сработало по слову SANY/VOLVO, а тип машины не указан — возможно, это экскаватор на моточасах
        rules = db.get_rules(conn)
        settings = db.get_settings(conn)
        for machine in check_units:
            if machine.get('mtype') or machine.get('unit_override'):
                continue
            auto = logic.machine_params(machine, rules, settings)['auto']
            keyword = (auto.get('rule_keyword') or '').upper()
            if auto['unit'] == logic.UNIT_KM and keyword in CHECK_UNIT_WORDS:
                self.warn(f"Машина «{machine['garage_no']}» ({machine.get('model') or 'без модели'}): тип машины "
                          f"не указан, по слову «{auto['rule_keyword']}» ТО считается в км — "
                          f"проверьте единицу учёта (км или моточасы)")

    # ---- Журнал и архив ----

    def kind(self, text):
        """Вид события в написании из справочника; «ремонт…»/«то-…»/«простой…» — с правильным началом."""
        if not text:
            return ''
        known = self.canon.get('kinds', {}).get(_loose_key(text))
        if known:
            return known
        for prefix in ('ТО-', 'Ремонт', 'Простой'):
            if _loose_key(text).startswith(prefix.upper()):
                return prefix + text[len(prefix):]
        return text

    def load_event_keys(self):
        """Ключи уже существующих событий — одним запросом (для пропуска дублей)."""
        self.event_keys = {}         # ключ _event_key -> поля события в базе (для дозаполнения при повторном импорте)
        self.auto_keys = set()       # (машина, дата) накинутых «авто»
        self.mark_keys = set()       # (машина, дата) отметок source='mark' (одна на дату — уникальный индекс)
        self.seen_rows = set()       # полные строки, уже встреченные в этом файле
        cols = ', '.join(('id', 'machine_id', 'date', 'kind', 'reading', 'work', 'source') + _FILL_FIELDS)
        for row in self.conn.execute(f'SELECT {cols} FROM events ORDER BY id'):
            if row['source'] == 'auto':
                self.auto_keys.add((row['machine_id'], row['date']))
            else:
                if row['source'] == 'mark':
                    self.mark_keys.add((row['machine_id'], row['date']))
                key = _event_key(row['machine_id'], row['date'], row['kind'], row['reading'],
                                 (row['work'] or '').strip(), (row['remarks'] or '').strip())
                self.event_keys.setdefault(key, {f: (row[f] or '').strip() if isinstance(row[f], str) else row[f]
                                                 for f in ('id',) + _FILL_FIELDS})

    def fill_event(self, old, new, where):
        """Повторный импорт: строка совпала с событием в базе по ключу. Пустые в базе поля, заполненные в таблице,
        дописываются (events_updated); противоречие — предупреждение, база не меняется; всё совпало — events_skipped."""
        updates, conflicts = {}, []
        for field in _FILL_FIELDS:
            value = new[field] or None
            current = old[field] or None
            if value is None or value == current:
                continue
            if current is None:
                updates[field] = value
            else:
                conflicts.append(f'{_FILL_NAMES[field]}: в программе «{current}», в таблице «{value}»')
        if conflicts:
            self.warn(f'{where}: запись уже есть в программе, но отличается ({"; ".join(conflicts)}) — '
                      f'оставлено как в программе, исправьте вручную')
            self.counts['events_skipped'] += 1
            return
        if not updates:
            self.counts['events_skipped'] += 1
            return
        sets = ', '.join(f'{name}=?' for name in updates)
        self.conn.execute(f'UPDATE events SET {sets}, updated_at=? WHERE id=?',
                          (*updates.values(), self.now, old['id']))
        old.update(updates)
        self.counts['events_updated'] += 1

    def apply_events(self, sheet, rows):
        """Лист «Журнал» / «Архив журнала». Жёсткая валидация не применяется: странные записи
        импортируются как есть и попадают в предупреждения."""
        to_insert = []
        extra = self.extra(sheet)        # «Заказано» (O) и служебный столбец выгрузки программы (P)
        for n, v in rows or []:
            where = f'{sheet}, строка {n}'
            garage_no = _text(v[2])
            kind = self.kind(_text(v[3]))
            missing = [name for name, empty in (('дата', v[0] is None), ('машина', not garage_no),
                                                ('событие', not kind)) if empty]
            if missing:
                self.warn(f"{where}: не заполнено: {', '.join(missing)} — строка пропущена")
                continue
            day = _parse_date(v[0])
            if day is None:
                self.warn(f'{where}: дата «{_text(v[0])}» не распознана — строка пропущена')
                continue

            reading = self.number(v[4], where, 'показание')
            time_in = _parse_time(v[5])
            date_out = _parse_date(v[6])
            time_out = _parse_time(v[7])
            if v[5] is not None and time_in is None:
                self.warn(f'{where}: время заезда «{_text(v[5])}» не распознано — записано пустым')
            if v[6] is not None and date_out is None:
                self.warn(f'{where}: дата выхода «{_text(v[6])}» не распознана — записана пустой')
            if v[7] is not None and time_out is None:
                self.warn(f'{where}: время выхода «{_text(v[7])}» не распознано — записано пустым')

            shift = self.canon_value('shifts', _text(v[1]))
            work, parts_used, parts_order = _text(v[9]), _text(v[10]), _text(v[11])
            remarks, who = _text(v[12]), _text(v[13])

            # обратный импорт экспорта: «Отметка моточасов» без показания и «Кто» = «авто +N» — это накинутое за день
            source, delta = 'import', None
            auto = _AUTO_WHO.match(who) if kind == KIND_MARK and v[4] is None else None
            if auto:
                delta = _parse_number(auto.group(1))
                if delta is not None and delta > 0:
                    source, who = 'auto', 'авто'
                else:
                    delta = None

            if source != 'auto':
                if logic.is_to(kind) and reading is None:
                    self.warn(f'{where}: {kind} без показания — в расчёте ТО не участвует, впишите показание')
                if date_out and date_out < day:
                    self.warn(f'{where}: дата выхода ({logic.fmt_date_ru(date_out)}) раньше даты заезда '
                              f'({logic.fmt_date_ru(day)})')
                elif date_out and date_out == day and time_in and time_out and time_out < time_in:
                    self.warn(f'{where}: время выхода ({time_out}) раньше времени заезда ({time_in})')
                if time_out and not date_out:
                    self.warn(f'{where}: указано время выхода ({time_out}) без даты выхода')
                if day > self.today:
                    self.warn(f'{where}: дата {logic.fmt_date_ru(day)} — в будущем')

            machine_id = self.find_machine(garage_no, where)

            if source == 'auto':
                if (machine_id, day) in self.auto_keys:
                    self.counts['events_skipped'] += 1
                    continue
                self.auto_keys.add((machine_id, day))
            else:
                old = self.event_keys.get(_event_key(machine_id, day, kind, reading, work, remarks))
                if old is not None:
                    self.fill_event(old, {'shift': shift, 'time_in': time_in, 'date_out': date_out,
                                          'time_out': time_out, 'parts_used': parts_used, 'parts_order': parts_order,
                                          'remarks': remarks, 'who': who}, where)
                    continue
                full = (machine_id, day, shift, kind, reading, time_in, date_out, time_out,
                        work, parts_used, parts_order, remarks, who)
                if full in self.seen_rows:           # та же строка дважды в файле (напр. и в журнале, и в архиве)
                    self.counts['events_skipped'] += 1
                    continue
                self.seen_rows.add(full)

            # служебный столбец выгрузки программы: «baseline»/«mark» (источник записи), «≈» (расчётное показание),
            # «без даты» (дата события не указана — в столбце «Дата» дата внесения)
            estimated = date_unknown = 0
            tokens = set()
            if source != 'auto' and 'service' in extra:
                tokens = {t.strip().lower() for t in _text(v[extra['service']]).split(';')}
                if SERVICE_DATE_UNKNOWN in tokens:
                    date_unknown = 1
            if source != 'auto' and 'service' in extra and reading is not None:
                if 'baseline' in tokens and logic.is_to(kind):
                    source = 'baseline'
                elif 'mark' in tokens and kind == KIND_MARK and (machine_id, day) not in self.mark_keys:
                    source = 'mark'
                    self.mark_keys.add((machine_id, day))
                if '≈' in tokens:
                    estimated = 1

            # заявка на запчасти остаётся открытой только у незакрытого ремонта/простоя (как на «Главной» таблицы);
            # заполненный столбец «Заказано» (дата заказа в таблице v8, «да»/«нет» в выгрузке программы) главнее
            still_open = (logic.is_repair(kind) or logic.is_idle(kind)) and not date_out
            order_done = 1 if parts_order and not still_open else 0
            ordered = _text(v[extra['ordered']]).lower() if 'ordered' in extra else ''
            if parts_order and ordered:
                order_done = 0 if ordered in ('нет', 'no', '0') else 1
            if parts_order and not order_done:
                self.counts['orders_open'] += 1

            to_insert.append((machine_id, day, shift, kind, reading, estimated, delta, source, time_in, date_out,
                              time_out, work, parts_used, parts_order, order_done, remarks, who, date_unknown,
                              self.now, self.now))

        self.conn.executemany(
            'INSERT INTO events(machine_id, date, shift, kind, reading, reading_estimated, delta, source, '
            'time_in, date_out, time_out, work, parts_used, parts_order, order_done, remarks, who, date_unknown, '
            'created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', to_insert)
        self.counts['events_new'] += len(to_insert)

    # ---- Шины и узлы ----

    def apply_tyres(self, rows):
        """Лист «Шины». В таблице заказчика в «№ шины» бывает марка («СИТ»), а отличает шины серийный номер
        в примечании, поэтому дубль внутри файла — только строка, совпавшая целиком (пропуск с предупреждением);
        уже есть в базе — та же машина + № + тип + позиция + дата установки + примечание (счёт в tyres_skipped)."""
        conn = self.conn
        known = {(r['machine_id'], (r['tyre_no'] or '').strip(), (r['ttype'] or '').strip(),
                  (r['position'] or '').strip(), r['install_date'], (r['note'] or '').strip())
                 for r in conn.execute('SELECT machine_id, tyre_no, ttype, position, install_date, note FROM tyres')}
        seen = {}                # полная строка файла -> номер строки
        to_insert = []
        extra = self.extra('Шины')       # служебный столбец выгрузки: показание машины при установке
        for n, v in rows or []:
            where = f'Шины, строка {n}'
            garage_no = _text(v[1])
            if not garage_no:
                self.warn(f'{where}: нет гаражного № — строка пропущена')
                continue
            tyre_no = _text(v[0])
            position = self.canon_value('positions', _text(v[3]))
            install_date = _parse_date(v[4])
            if v[4] is not None and install_date is None:
                self.warn(f'{where}: дата установки «{_text(v[4])}» не распознана — записана пустой')
            resource = self.number(v[5], where, 'ресурс')
            run = self.number(v[6], where, 'пробег')
            install_reading = None
            if 'install_reading' in extra:
                install_reading = self.number(v[extra['install_reading']], where, 'показание при установке')
            machine_id = self.find_machine(garage_no, where)
            ttype, note = self.canon_value('tyre_types', _text(v[2])), _text(v[10])
            full = (machine_id, tyre_no, ttype, position, install_date, resource, run, install_reading, note)
            if full in seen:
                self.warn(f'{where}: шина полностью совпадает со строкой {seen[full]} — строка пропущена')
                self.counts['tyres_skipped'] += 1
                continue
            seen[full] = n
            key = (machine_id, tyre_no, ttype, position, install_date, note)
            if key in known:
                self.counts['tyres_skipped'] += 1
                continue
            to_insert.append((machine_id, tyre_no, ttype, position,
                              install_date, resource, run, install_reading, note))
        conn.executemany('INSERT INTO tyres(machine_id, tyre_no, ttype, position, install_date, resource, run, '
                         'install_reading, note) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)', to_insert)
        self.counts['tyres_new'] += len(to_insert)

    def apply_units(self, rows):
        """Лист «Узлы». Дубль внутри файла — только строка, совпавшая целиком (пропуск с предупреждением);
        уже есть в базе — та же машина + узел + показание при установке + ресурс + рекомендация (units_skipped)."""
        conn = self.conn
        known = {(r['machine_id'], (r['unit'] or '').strip(), r['install_reading'], r['resource'],
                  (r['note'] or '').strip())
                 for r in conn.execute('SELECT machine_id, unit, install_reading, resource, note FROM units')}
        seen = {}
        to_insert = []
        for n, v in rows or []:
            where = f'Узлы, строка {n}'
            garage_no = _text(v[0])
            unit = self.canon_value('unit_names', _text(v[1]))
            if not garage_no or not unit:
                self.warn(f"{where}: не указан{'а машина' if not garage_no else ' узел'} — строка пропущена")
                continue
            resource = self.number(v[2], where, 'ресурс')
            install_reading = self.number(v[3], where, 'показание при установке')
            machine_id = self.find_machine(garage_no, where)
            key = (machine_id, unit, install_reading, resource, _text(v[8]))
            if key in seen:
                self.warn(f'{where}: узел полностью совпадает со строкой {seen[key]} — строка пропущена')
                self.counts['units_skipped'] += 1
                continue
            seen[key] = n
            if key in known:
                self.counts['units_skipped'] += 1
                continue
            to_insert.append((machine_id, unit, resource, install_reading, _text(v[8])))
        conn.executemany('INSERT INTO units(machine_id, unit, resource, install_reading, note) '
                         'VALUES(?, ?, ?, ?, ?)', to_insert)
        self.counts['units_new'] += len(to_insert)


def import_v8(conn, file_bytes, dry_run=False):
    """Импорт таблицы «Журнал учёта техники и ТО v8» (байты xlsx) в базу.

    Возвращает {ok, dry_run, machines_new, machines_updated, events_new, events_skipped, tyres_new, units_new,
    orders_open, settings_updated, warnings: [строки], warnings_total}.
    machines_new включает машины-заглушки; machines_updated — машины, у которых реально изменились поля;
    events_skipped — дубли; events_updated — совпавшие события, в которые дописаны пустые поля;
    tyres_skipped/units_skipped — уже были в базе или повтор строки в файле; settings_updated — сколько настроек/значений справочников/наборов правил изменено.
    Всё пишется одной транзакцией; dry_run=True — транзакция откатывается, остаётся только отчёт.
    Файл не того формата -> ValueError (сервер отвечает 400).
    """
    book, headers = _read_book(file_bytes)

    if conn.in_transaction:
        conn.commit()
    conn.execute('BEGIN')
    try:
        imp = _Import(conn)
        imp.headers = headers
        for title in HEADERS:
            imp.check_header(title, headers.get(title))
        imp.apply_settings(book['Настройки'])
        imp.apply_regulation(book['Регламент ТО'])
        imp.apply_machines(book['Машины'])
        imp.load_event_keys()
        imp.apply_events('Архив журнала', book['Архив журнала'])      # архив — более старые записи, идут первыми
        imp.apply_events('Журнал', book['Журнал'])
        imp.apply_tyres(book['Шины'])
        imp.apply_units(book['Узлы'])
        result = imp.result(dry_run)
    except BaseException:
        conn.rollback()
        raise
    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    return result


# --------------------------------------------------------------------------------------
# Экспорт
# --------------------------------------------------------------------------------------

class _Book:
    """Книга для экспорта (openpyxl, потоковая запись): заголовок листа в 1-й строке,
    шапка в 4-й (жирная, закреплённая, с автофильтром), данные с 5-й; значения без формул."""

    def __init__(self):
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils import get_column_letter
        self.wb = Workbook(write_only=True)
        self._cell = WriteOnlyCell
        self._illegal = ILLEGAL_CHARACTERS_RE
        self._letter = get_column_letter
        self._title_font = Font(bold=True, size=14)
        self._bold = Font(bold=True)
        self._wrap = Alignment(wrap_text=True, vertical='center')

    def sheet(self, title, caption, hint, columns, n_rows):
        """Новый лист. columns — [(заголовок столбца, ширина)]; n_rows — сколько будет строк данных."""
        ws = self.wb.create_sheet(title)
        ws.freeze_panes = 'A5'
        for i, (_, width) in enumerate(columns, 1):
            ws.column_dimensions[self._letter(i)].width = width
        ws.auto_filter.ref = f'A4:{self._letter(len(columns))}{4 + n_rows}'
        head = self._cell(ws, value=caption)
        head.font = self._title_font
        ws.append([head])
        ws.append([hint])
        ws.append([])
        header = []
        for name, _ in columns:
            cell = self._cell(ws, value=name)
            cell.font = self._bold
            cell.alignment = self._wrap
            header.append(cell)
        ws.append(header)
        return ws

    def text(self, ws, value):
        """Текст без недопустимых для xlsx символов. Строка с «=» в начале пишется с пробелом впереди:
        иначе Excel примет её за формулу, а обратный импорт — за пустую ячейку."""
        value = self._illegal.sub('', '' if value is None else str(value))
        if not value:
            return None
        if value.startswith('='):
            return ' ' + value
        return value

    def date(self, ws, iso):
        """Дата 'YYYY-MM-DD' — настоящей датой Excel (ДД.ММ.ГГГГ); нераспознанное значение — текстом."""
        day = logic.parse_date(iso)
        if day is None:
            return self.text(ws, iso)
        cell = self._cell(ws, value=day)
        cell.number_format = 'DD.MM.YYYY'
        return cell

    def time(self, ws, hhmm):
        """Время 'HH:MM' — настоящим временем Excel; нераспознанное значение — текстом."""
        value = logic.parse_time(hhmm)
        if value is None:
            return self.text(ws, hhmm)
        cell = self._cell(ws, value=value)
        cell.number_format = 'HH:MM'
        return cell

    def save(self):
        buf = io.BytesIO()
        self.wb.save(buf)
        return buf.getvalue()


def _fmt_duration(seconds):
    """Длительность словами: 98400 -> «1 д 3 ч 20 мин»."""
    minutes = int(seconds) // 60
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f'{days} д')
    if hours:
        parts.append(f'{hours} ч')
    if mins or not parts:
        parts.append(f'{mins} мин')
    return ' '.join(parts)


def _fleet_views(conn, today, now):
    """MachineView всех машин на сегодня через services.build_views; None, если services недоступен."""
    try:
        from app import services
    except ImportError:
        return None
    build_views = getattr(services, 'build_views', None)
    if build_views is None:
        return None
    return list(build_views(conn, today, now))


FLEET_COLUMNS = [
    ('№', 5), ('Гаражный №', 12), ('Модель', 28), ('Участок', 16), ('Сегодня', 16),
    ('Что делается / причина', 34), ('Показание', 12), ('Расчётное (≈)', 11), ('Ед.', 6),
    ('Последнее ТО', 12), ('Показание на ТО', 12), ('Дата ТО', 12), ('След. ТО', 10),
    ('След. ТО на показании', 13), ('Осталось до ТО', 12), ('Пропущено ТО', 11), ('Статус ТО', 16),
    ('Прогноз ТО (дата)', 13), ('В сутки (средняя / норма)', 12), ('Заказать запчасти', 34),
]
MACHINE_COLUMNS = [
    ('№', 5), ('Модель / тип техники', 30), ('Гаражный №', 12), ('Госномер', 14), ('VIN', 22),
    ('Моточасы / пробег при регистрации', 16), ('Участок / объект', 16), ('Гарантийный срок', 14),
    ('Фото таблички (ссылка)', 18), ('Примечание', 30), ('Статус машины', 16),
    ('Тип машины (для заказ-наряда)', 18),
]
JOURNAL_COLUMNS = [
    ('ДАТА *', 12), ('Смена', 8), ('МАШИНА *', 12), ('СОБЫТИЕ *', 20), ('МОТОЧАСЫ / КМ *', 12),
    ('Заехала — ВРЕМЯ', 10), ('Вышла — ДАТА', 12), ('Вышла — ВРЕМЯ', 10), ('В ремонте ВСЕГО (само)', 16),
    ('Что делалось / что сломалось', 44), ('Масло / запчасти (использовано)', 30),
    ('Заказать запчасти (в закуп)', 30), ('Замечания (стружка и т.п.)', 30), ('Кто', 14),
]
TYRE_COLUMNS = [
    ('№ шины', 12), ('Гаражный №', 12), ('Тип / размер шины', 16), ('Позиция', 20), ('Дата установки', 12),
    ('Ресурс, км / м·ч', 12), ('Пробег / наработка, км / м·ч', 14), ('Остаток, км / м·ч', 12), ('Износ', 8),
    ('Статус', 16), ('Примечание', 30),
]
MACHINE_COLUMNS += [(name, width) for _, _, name, width in EXTRA_COLUMNS['Машины']]
JOURNAL_COLUMNS += [(name, width) for _, _, name, width in EXTRA_COLUMNS['Журнал']]
TYRE_COLUMNS += [(name, width) for _, _, name, width in EXTRA_COLUMNS['Шины']]
UNIT_COLUMNS = [
    ('Машина (гар. №)', 12), ('Узел', 28), ('Ресурс / гарантия, м/ч', 12), ('Моточасы машины при установке', 14),
    ('Наработка узла, м/ч', 12), ('Остаток, м/ч', 12), ('Износ', 8), ('Статус', 26),
    ('Рекомендация / запчасти / стоимость', 40),
]


def export_xlsx(conn):
    """Книга xlsx (байты): «Парк» (расчётные поля на сегодня), «Машины», «Журнал» (все события; накинутое —
    с пустым показанием и «Кто» = «авто +N»), «Шины», «Узлы». Порядок столбцов «Машины», «Журнал», «Шины»,
    «Узлы» — как в таблице v8, поэтому файл импортируется обратно через import_v8.
    Лист «Парк» строится через services.build_views; если модуля services нет — лист пропускается."""
    now = datetime.now()
    today = now.date().isoformat()
    settings = db.get_settings(conn)
    hint = f'Выгружено из программы «Учёт ТО» {now:%d.%m.%Y %H:%M}. Значения без формул.'
    book = _Book()

    views = _fleet_views(conn, today, now)
    readings = {v['id']: v.get('reading') for v in views or []}    # текущие показания машин — для шин и узлов

    # ---- Парк ----
    if views is not None:
        fleet = [v for v in views if v.get('status') != logic.STATUS_RETIRED]
        ws = book.sheet('Парк', f'Парк — состояние на {now:%d.%m.%Y}', hint, FLEET_COLUMNS, len(fleet))
        for i, v in enumerate(fleet, 1):
            last_to = v.get('last_to') or {}
            ws.append([
                i, book.text(ws, v.get('garage_no')), book.text(ws, v.get('model')), book.text(ws, v.get('site')),
                book.text(ws, v.get('state_label')), book.text(ws, v.get('state_work')),
                v.get('reading'), '≈' if v.get('reading_estimated') else None, v.get('unit'),
                book.text(ws, last_to.get('kind')), last_to.get('reading'),
                'дата не указана' if last_to.get('date_unknown') else book.date(ws, last_to.get('date')),
                book.text(ws, v.get('next_to_kind')), v.get('next_to_at'), v.get('remaining'),
                v.get('missed') or None, book.text(ws, v.get('to_status_label')),
                book.date(ws, v.get('forecast_date')), v.get('rate'), book.text(ws, v.get('parts_order')),
            ])

    # ---- Машины (все, включая списанные) ----
    machines = conn.execute('SELECT * FROM machines ORDER BY pos, id').fetchall()
    ws = book.sheet('Машины', 'Машины', hint, MACHINE_COLUMNS, len(machines))
    for i, m in enumerate(machines, 1):
        ws.append([
            i, book.text(ws, m['model']), book.text(ws, m['garage_no']), book.text(ws, m['plate']),
            book.text(ws, m['vin']), _clean(m['reg_reading']), book.text(ws, m['site']),
            book.text(ws, m['warranty']), book.text(ws, m['photo']), book.text(ws, m['note']),
            book.text(ws, m['status']), book.text(ws, m['mtype']),
            # служебные столбцы: свои параметры ТО и дата показания при регистрации
            book.text(ws, m['unit_override']), _clean(m['interval_override']), _clean(m['warn_override']),
            _clean(m['norm_override']), book.date(ws, m['reg_date']) if m['reg_date'] else None,
        ])

    # ---- Журнал (все события по порядку дат) ----
    total = conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
    ws = book.sheet('Журнал', 'Журнал', hint, JOURNAL_COLUMNS, total)
    cursor = conn.execute('SELECT e.*, m.garage_no FROM events e JOIN machines m ON m.id = e.machine_id '
                          'ORDER BY e.date, e.id')
    for ev in cursor:
        auto = ev['source'] == 'auto'
        total_text = None                      # «В ремонте всего» — только у закрытых событий, как в таблице
        if ev['date_out']:
            duration = logic.event_duration(ev, now)
            if duration['duration_error']:
                total_text = '⚠ время'
            elif duration['duration_seconds'] is not None:
                total_text = _fmt_duration(duration['duration_seconds'])
        ordered = None                         # «Заказано»: да/нет — только у строк с заявкой на запчасти
        if ev['parts_order']:
            ordered = 'да' if ev['order_done'] else 'нет'
        service = []                           # служебный столбец: источник записи, «≈», «без даты»
        if ev['source'] in ('baseline', 'mark'):
            service.append(ev['source'])
        if ev['reading_estimated'] and not auto:
            service.append('≈')
        if ev['date_unknown'] and not auto:
            service.append(SERVICE_DATE_UNKNOWN)
        ws.append([
            book.date(ws, ev['date']), book.text(ws, ev['shift']), book.text(ws, ev['garage_no']),
            book.text(ws, ev['kind']), None if auto else _clean(ev['reading']),
            book.time(ws, ev['time_in']), book.date(ws, ev['date_out']), book.time(ws, ev['time_out']),
            total_text, book.text(ws, ev['work']), book.text(ws, ev['parts_used']),
            book.text(ws, ev['parts_order']), book.text(ws, ev['remarks']),
            f"авто +{_clean(ev['delta'] or 0)}" if auto else book.text(ws, ev['who']),
            ordered, ';'.join(service) or None,
        ])

    # ---- Шины ----
    tyres = conn.execute('SELECT t.*, m.garage_no FROM tyres t JOIN machines m ON m.id = t.machine_id '
                         'ORDER BY m.pos, m.id, t.id').fetchall()
    ws = book.sheet('Шины', 'Шины — учёт и износ', hint, TYRE_COLUMNS, len(tyres))
    for t in tyres:
        view = logic.tyre_view(t, readings.get(t['machine_id']), settings)
        # в таблице v8 нет «показания при установке»: в «Пробег» идёт расчётный пробег, если он есть
        run = view['run_eff'] if view['run_eff'] is not None else _clean(t['run'])
        ws.append([
            book.text(ws, t['tyre_no']), book.text(ws, t['garage_no']), book.text(ws, t['ttype']),
            book.text(ws, t['position']), book.date(ws, t['install_date']), _clean(t['resource']), run,
            view['left'], view['wear'], book.text(ws, view['status_label']), book.text(ws, t['note']),
            _clean(t['install_reading']),
        ])

    # ---- Узлы ----
    units = conn.execute('SELECT u.*, m.garage_no FROM units u JOIN machines m ON m.id = u.machine_id '
                         'ORDER BY m.pos, m.id, u.id').fetchall()
    ws = book.sheet('Узлы', 'Узлы — ресурс, гарантия, износ', hint, UNIT_COLUMNS, len(units))
    for u in units:
        view = logic.unit_view(u, readings.get(u['machine_id']), settings)
        ws.append([
            book.text(ws, u['garage_no']), book.text(ws, u['unit']), _clean(u['resource']),
            _clean(u['install_reading']), view['run'], view['left'], view['wear'],
            book.text(ws, view['status_label']), book.text(ws, u['note']),
        ])

    return book.save()
