"""Операции над базой «Учёт ТО» (SPEC, разделы 4.9 и 5): CRUD, показания, «ТО сделано»,
«накинуть за день», план, заказ-наряд, сборка представлений.

Функции принимают соединение conn и простые типы (dict/list/str/числа), возвращают то, что уходит
в JSON. Ошибки — исключение ApiError(status, message, **extra): 400 — неверные данные, 404 — не найдено,
409 — нужно подтверждение (extra: needs_confirm, warnings, items). Замок db.LOCK держит вызывающий
(обработчик сервера); каждая изменяющая операция — одна транзакция.

Параметр now (datetime) нужен тестам; в работе не передаётся — берётся текущее время.

ОГЛАВЛЕНИЕ
    ApiError
    build_views(conn, D, now, machine_ids=None) -> [MachineView]      единственная точка сборки (4.9)
    bootstrap, fleet
    list_machines, machine_view, machine_card, create_machine, update_machine, delete_machine
    list_photos, add_photo, photo_file, delete_photo         фото машин (файлы в <данные>/photos/<id>/)
    list_events, get_event, create_event, update_event, delete_event, set_order_done
    to_done, readings_bulk
    daily_preview, daily_apply, daily_undo
    plan, workorder
    list_tyres, create_tyre, update_tyre, delete_tyre, list_units, create_unit, update_unit, delete_unit
    update_settings, update_list, update_rules, update_regulation
    list_backups, create_backup, restore_backup
"""

import math
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from app import VERSION, db, logic
from app.logic import KIND_MARK

FAR_DATE = '9999-12-31'          # «на все времена»: дата отчёта, при которой учитываются все события
MAX_DAILY_PERIOD = 92            # «накинуть за день»: самый длинный период, суток
CONFIRM_TEXT = 'Проверьте данные: есть сомнения. Сохранить как есть?'


class ApiError(Exception):
    """Ошибка для клиента: HTTP-статус, русский текст и дополнительные поля ответа."""

    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


def _confirm_error(message, warnings, items=None):
    """409 «нужно подтверждение»: клиент покажет вопрос и повторит запрос с force=true."""
    return ApiError(409, message, needs_confirm=True, warnings=list(warnings), items=list(items or []))


# --------------------------------------------------------------------------------------
# Время и разбор входных значений
# --------------------------------------------------------------------------------------

def _now(now):
    return now if now is not None else datetime.now()


def _today(now):
    return now.date().isoformat()


def _stamp(now):
    return now.isoformat(timespec='seconds')


def norm_text(v):
    """Текстовое поле: всегда строка без крайних пробелов; None -> ''."""
    if v is None:
        return ''
    return str(v).strip()


def norm_num(v, label):
    """Число или None. Понимает строки «153 000» и «1250,5»; пустая строка -> None; мусор -> 400."""
    if v is None:
        return None
    if isinstance(v, bool):
        raise ApiError(400, f'{label}: нужно число')
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        s = str(v).replace(' ', '').replace(' ', '').replace(' ', '').replace(',', '.')
        if s == '':
            return None
        try:
            x = float(s)
        except ValueError:
            raise ApiError(400, f'{label}: «{str(v).strip()}» — не число') from None
    if math.isnan(x) or math.isinf(x):
        raise ApiError(400, f'{label}: нужно число')
    return x


def norm_date(v, label):
    """Дата 'YYYY-MM-DD' или None ('' -> None); другой вид -> 400."""
    s = norm_text(v)
    if not s:
        return None
    if logic.parse_date(s) is None:
        raise ApiError(400, f'{label}: дата должна быть в виде ГГГГ-ММ-ДД, получено «{s}»')
    return s


def norm_time(v, label):
    """Время 'HH:MM' или None ('' -> None); другой вид -> 400."""
    s = norm_text(v)
    if not s:
        return None
    t = logic.parse_time(s)
    if t is None:
        raise ApiError(400, f'{label}: время должно быть в виде ЧЧ:ММ, получено «{s}»')
    return f'{t.hour:02d}:{t.minute:02d}'


def norm_bool(v):
    """Признак да/нет: true/1/«да» -> True."""
    if isinstance(v, str):
        return v.strip().lower() in ('1', 'true', 'да', 'yes', 'on')
    return bool(v)


def _int(v, label):
    """Целое число (id, количество); иначе 400."""
    try:
        if isinstance(v, bool) or v is None:
            raise ValueError
        if isinstance(v, float):
            if not v.is_integer():
                raise ValueError
            return int(v)
        return int(str(v).strip())
    except ValueError:
        raise ApiError(400, f'{label}: нужно целое число') from None


def _num_out(v):
    """Число для ответа: целое значение отдаётся как int (153000, а не 153000.0)."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _fmt(v):
    """153000 -> '153 000', 1250.5 -> '1 250,5' (для текстов сообщений)."""
    v = _num_out(round(float(v), 1))
    return f'{v:,}'.replace(',', ' ').replace('.', ',')


def _ru(d):
    return logic.fmt_date_ru(d)


def _date_param(value, now, label='Дата'):
    """Дата из параметра запроса; пусто -> сегодня."""
    return norm_date(value, label) or _today(now)


def _gkey(garage_no):
    """Ключ сравнения гаражных номеров: без крайних пробелов и без учёта регистра."""
    return (garage_no or '').strip().upper()


def _require_dict(data, what='Данные'):
    if not isinstance(data, dict):
        raise ApiError(400, f'{what}: ожидается объект с полями')
    return data


# --------------------------------------------------------------------------------------
# 4.9 Сборка представлений машин
# --------------------------------------------------------------------------------------

_READING_COLS = 'id, machine_id, date, kind, reading, reading_estimated, delta, source, updated_at, date_unknown'
_STATE_WHERE = "(kind LIKE 'Ремонт%' OR kind LIKE 'Простой%' OR kind LIKE 'ТО-%')"
_MACHINE_ORDER = 'pos IS NULL, pos, id'
_MACHINE_NUMS = ('reg_reading', 'interval_override', 'warn_override', 'norm_override')
_MACHINE_TEXTS = ('garage_no', 'model', 'mtype', 'plate', 'vin', 'site', 'warranty', 'photo', 'note')
NOTE_TEXT_MAX = 300              # last_note.text: не длиннее, символов
_NOTE_TEXT_COLS = ('work', 'remarks', 'parts_used')      # откуда берётся текст last_note — первое непустое

# Последняя запись журнала каждой машины на дату D (для колонки «Что делали»): без накинутого
# и без отметок показаний; одна строка на машину — оконной функцией, а не запросом на каждую машину.
_NOTES_SQL = (
    'SELECT id, machine_id, date, date_unknown, kind, work, remarks, parts_used, date_out FROM ('
    ' SELECT id, machine_id, date, date_unknown, kind, work, remarks, parts_used, date_out,'
    '  ROW_NUMBER() OVER (PARTITION BY machine_id ORDER BY date DESC, id DESC) AS rn'
    " FROM events WHERE source <> 'auto' AND kind <> ? AND date <= ?{e_and}"
    ') WHERE rn = 1')


class _Loaded:
    """Всё, что нужно для расчётов по машинам, одной выборкой (число запросов не зависит от числа машин)."""
    __slots__ = ('machines', 'readings', 'states', 'orders', 'tyres', 'units', 'notes', 'settings', 'rules')


def _group(rows):
    result = {}
    for row in rows:
        result.setdefault(row['machine_id'], []).append(row)
    return result


def _load(conn, D, machine_ids=None):
    """Семь запросов (machines, показания, события состояний, заявки, шины, узлы, последние записи
    на дату D) + настройки и правила."""
    if machine_ids is None:
        m_where = e_and = t_where = ''
        args = ()
    else:
        ids = [int(i) for i in machine_ids]
        marks = ','.join('?' * len(ids)) or 'NULL'
        m_where = f' WHERE id IN ({marks})'
        e_and = f' AND machine_id IN ({marks})'
        t_where = f' WHERE machine_id IN ({marks})'
        args = tuple(ids)

    data = _Loaded()
    data.settings = db.get_settings(conn)
    data.rules = db.get_rules(conn)
    data.machines = [dict(r) for r in conn.execute(
        f'SELECT * FROM machines{m_where} ORDER BY {_MACHINE_ORDER}', args)]
    data.readings = _group(conn.execute(
        f"SELECT {_READING_COLS} FROM events WHERE (reading IS NOT NULL OR source='auto'){e_and}", args))
    data.states = _group(conn.execute(f'SELECT * FROM events WHERE {_STATE_WHERE}{e_and}', args))
    data.orders = _group(conn.execute(
        f"SELECT id, machine_id, date, parts_order FROM events WHERE parts_order<>'' AND order_done=0{e_and}",
        args))
    data.tyres = _group(conn.execute(f'SELECT * FROM tyres{t_where} ORDER BY id', args))
    data.units = _group(conn.execute(f'SELECT * FROM units{t_where} ORDER BY id', args))
    data.notes = {row['machine_id']: row for row in conn.execute(
        _NOTES_SQL.format(e_and=e_and), (KIND_MARK, D) + args)}
    return data


def _last_note(row):
    """last_note для MachineView (КОНЦЕПЦИЯ_v2, 9.1): {event_id, date, date_unknown, kind, kind_title, text, open}
    или None. text — первое непустое из work, remarks, parts_used (не длиннее NOTE_TEXT_MAX);
    open — ремонт или простой без даты выхода."""
    if row is None:
        return None
    kind = row['kind'] or ''
    text = next((t for t in ((row[col] or '').strip() for col in _NOTE_TEXT_COLS) if t), '')
    return {
        'event_id': row['id'],
        'date': row['date'],
        'date_unknown': bool(row['date_unknown']),
        'kind': kind,
        'kind_title': logic.kind_title(kind),
        'text': text[:NOTE_TEXT_MAX],
        'open': not row['date_out'] and (logic.is_repair(kind) or logic.is_idle(kind)),
    }


def _clean_view(view):
    """Привести сырые поля машины к формату ответа: числа без «.0», текст — строка."""
    for key in _MACHINE_NUMS:
        view[key] = _num_out(view.get(key))
    for key in _MACHINE_TEXTS:
        if view.get(key) is None:
            view[key] = ''
    for key in ('reg_date', 'unit_override'):
        if view.get(key) == '':
            view[key] = None
    return view


def _views(data, D, now):
    """MachineView по всем загруженным машинам (включая списанные), порядок pos, id.
    data — выборка _load на ту же дату D."""
    result = []
    for m in data.machines:
        mid = m['id']
        view = logic.build_machine_view(
            m, data.readings.get(mid, ()), data.states.get(mid, ()), data.orders.get(mid, ()),
            data.tyres.get(mid, ()), data.units.get(mid, ()), data.rules, data.settings, D, now)
        view = _clean_view(view)
        view['last_note'] = _last_note(data.notes.get(mid))
        result.append(view)
    return result


def build_views(conn, D, now, machine_ids=None):
    """Расчётные строки машин (MachineView) на дату D — единственная точка их получения (SPEC 4.9).

    Возвращает все машины (в том числе списанные; кому не нужны — отфильтрует), порядок pos, id.
    machine_ids — ограничить список машин (число запросов от этого не меняется).
    """
    return _views(_load(conn, D, machine_ids), D, now)


def _one_view(conn, machine_id, D, now):
    views = build_views(conn, D, now, [machine_id])
    if not views:
        raise ApiError(404, 'Машина не найдена')
    return views[0]


def _active(views):
    """Без списанных: в парк, отчёты, план и показания они не попадают."""
    return [v for v in views if v['status'] != logic.STATUS_RETIRED]


# --------------------------------------------------------------------------------------
# Старт и парк
# --------------------------------------------------------------------------------------

def bootstrap(conn, data_dir='', now=None):
    """Всё, что нужно фронтенду при старте: версия, дата, настройки, справочники, правила, регламент."""
    now = _now(now)
    count = conn.execute('SELECT COUNT(*) FROM machines').fetchone()[0]
    return {
        'version': VERSION,
        'today': _today(now),
        'now': _stamp(now),
        'data_dir': str(data_dir),
        'first_run': count == 0,
        'settings': db.get_settings(conn),
        'lists': db.get_lists(conn),
        'rules': db.get_rules(conn),
        'regulation': db.get_regulation(conn),
    }


def fleet(conn, date=None, now=None):
    """Парк на дату отчёта: сводка, строки машин (без списанных) и общий список предупреждений по данным."""
    now = _now(now)
    D = _date_param(date, now, 'Дата отчёта')
    views = _active(build_views(conn, D, now))
    warnings = []
    for v in views:
        for item in v.get('data_warning_items') or []:
            warnings.append({'machine_id': v['id'], 'garage_no': v['garage_no'],
                             'event_id': item['event_id'], 'text': item['text']})
    return {'date': D, 'summary': logic.fleet_summary(views), 'rows': views, 'warnings': warnings}


# --------------------------------------------------------------------------------------
# Машины
# --------------------------------------------------------------------------------------

def _machine_row(conn, machine_id):
    row = conn.execute('SELECT * FROM machines WHERE id=?', (machine_id,)).fetchone()
    if row is None:
        raise ApiError(404, 'Машина не найдена')
    return dict(row)


def _machine_by_garage(conn, garage_no):
    key = _gkey(garage_no)
    for row in conn.execute('SELECT * FROM machines'):
        if _gkey(row['garage_no']) == key:
            return dict(row)
    return None


def _machine_ref(conn, data):
    """id машины из входных данных: machine_id или garage_no. Нет/не найдена -> 400."""
    mid = data.get('machine_id')
    if mid not in (None, ''):
        mid = _int(mid, 'Машина')
        if conn.execute('SELECT 1 FROM machines WHERE id=?', (mid,)).fetchone() is None:
            raise ApiError(400, 'Машина не найдена')
        return mid
    garage_no = norm_text(data.get('garage_no'))
    if garage_no:
        machine = _machine_by_garage(conn, garage_no)
        if machine is None:
            raise ApiError(400, f'Машина с гаражным № «{garage_no}» не найдена')
        return machine['id']
    raise ApiError(400, 'Не выбрана машина')


def _norm_unit(v, label='Единица учёта'):
    s = norm_text(v).lower()
    if not s:
        return None
    if s == 'км':
        return logic.UNIT_KM
    if s in ('м/ч', 'мч', 'м.ч', 'м.ч.', 'моточасы'):
        return logic.UNIT_MH
    raise ApiError(400, f'{label}: допустимо «км» или «м/ч»')


def _apply_machine_input(conn, rec, data):
    """Перенести переданные ключи в запись машины rec (нормализуя) и проверить итог."""
    for key in _MACHINE_TEXTS:
        if key in data:
            rec[key] = norm_text(data[key])
    if 'status' in data:
        rec['status'] = norm_text(data['status']) or 'В работе'
    if 'reg_reading' in data:
        rec['reg_reading'] = norm_num(data['reg_reading'], 'Показание при регистрации')
    if 'reg_date' in data:
        rec['reg_date'] = norm_date(data['reg_date'], 'Дата регистрации показания')
    if 'unit_override' in data:
        rec['unit_override'] = _norm_unit(data['unit_override'])
    if 'interval_override' in data:
        rec['interval_override'] = norm_num(data['interval_override'], 'Интервал ТО')
    if 'warn_override' in data:
        rec['warn_override'] = norm_num(data['warn_override'], 'Порог «скоро ТО»')
    if 'norm_override' in data:
        rec['norm_override'] = norm_num(data['norm_override'], 'Суточная норма')
    if 'pos' in data and data['pos'] not in (None, ''):
        rec['pos'] = _int(data['pos'], 'Порядок')

    if not rec.get('garage_no'):
        raise ApiError(400, 'Укажите гаражный номер')
    key = _gkey(rec['garage_no'])
    for row in conn.execute('SELECT id, garage_no FROM machines'):
        if row['id'] != rec.get('id') and _gkey(row['garage_no']) == key:
            raise ApiError(400, f'Машина с гаражным № «{row["garage_no"]}» уже есть')
    statuses = db.get_lists(conn).get('machine_statuses') or []
    if rec.get('status') not in statuses:
        raise ApiError(400, 'Статус машины: допустимо ' + ', '.join(f'«{s}»' for s in statuses))
    if rec.get('reg_reading') is not None and rec['reg_reading'] < 0:
        raise ApiError(400, 'Показание при регистрации не может быть отрицательным')
    if rec.get('interval_override') is not None and rec['interval_override'] <= 0:
        raise ApiError(400, 'Интервал ТО должен быть больше нуля')
    if rec.get('warn_override') is not None and rec['warn_override'] < 0:
        raise ApiError(400, 'Порог «скоро ТО» не может быть отрицательным')
    if rec.get('norm_override') is not None and rec['norm_override'] <= 0:
        raise ApiError(400, 'Суточная норма должна быть больше нуля')
    return rec


_MACHINE_COLS = _MACHINE_TEXTS + ('pos', 'status', 'reg_reading', 'reg_date', 'unit_override',
                                  'interval_override', 'warn_override', 'norm_override')


def list_machines(conn, now=None):
    """Справочник машин — все, включая списанные; каждая строка — полный MachineView на сегодня
    (сырые поля + unit, interval, warn, auto, reading, reading_estimated, to_status, next_to_kind и остальное)."""
    now = _now(now)
    return build_views(conn, _today(now), now)


def machine_view(conn, machine_id, date=None, now=None):
    """MachineView одной машины на дату (для диалогов)."""
    now = _now(now)
    return _one_view(conn, machine_id, _date_param(date, now), now)


def create_machine(conn, data, now=None):
    """Создать машину. Возвращает её MachineView."""
    now = _now(now)
    data = _require_dict(data)
    rec = {key: '' for key in _MACHINE_TEXTS}
    rec.update({'status': 'В работе', 'reg_reading': None, 'reg_date': None, 'unit_override': None,
                'interval_override': None, 'warn_override': None, 'norm_override': None, 'pos': None})
    _apply_machine_input(conn, rec, data)
    stamp = _stamp(now)
    with db.transaction(conn):
        if rec['pos'] is None:
            rec['pos'] = conn.execute('SELECT COALESCE(MAX(pos), 0) + 1 FROM machines').fetchone()[0]
        cols = _MACHINE_COLS + ('created_at', 'updated_at')
        cur = conn.execute(
            f'INSERT INTO machines({", ".join(cols)}) VALUES({", ".join("?" * len(cols))})',
            [rec.get(c) for c in _MACHINE_COLS] + [stamp, stamp])
        machine_id = cur.lastrowid
    return _one_view(conn, machine_id, _today(now), now)


def update_machine(conn, machine_id, data, force=False, now=None):
    """Изменить машину (только переданные ключи). Смена итоговой единицы учёта при наличии
    показаний в журнале требует подтверждения (409)."""
    now = _now(now)
    data = _require_dict(data)
    old = _machine_row(conn, machine_id)
    rec = _apply_machine_input(conn, dict(old), data)

    settings, rules = db.get_settings(conn), db.get_rules(conn)
    old_unit = logic.machine_params(old, rules, settings)['unit']
    new_unit = logic.machine_params(rec, rules, settings)['unit']
    if old_unit != new_unit and not force:
        count = conn.execute(
            'SELECT COUNT(*) FROM events WHERE machine_id=? AND (reading IS NOT NULL OR delta IS NOT NULL)',
            (machine_id,)).fetchone()[0]
        if count:
            raise _confirm_error(
                f'Единица учёта машины меняется с «{old_unit}» на «{new_unit}»: '
                'показания в журнале останутся в прежних единицах. Продолжить?',
                [f'Записей с показаниями в журнале: {count}'])

    with db.transaction(conn):
        sets = ', '.join(f'{c}=?' for c in _MACHINE_COLS)
        conn.execute(f'UPDATE machines SET {sets}, updated_at=? WHERE id=?',
                     [rec.get(c) for c in _MACHINE_COLS] + [_stamp(now), machine_id])
    return _one_view(conn, machine_id, _today(now), now)


def delete_machine(conn, machine_id, force=False, photos_root=None):
    """Удалить машину. Есть события/шины/узлы/фото -> 409; с force удаляется вместе с ними.

    photos_root — папка фото (<данные>/photos); папка фото машины удаляется вместе с машиной
    (номер удалённой машины может достаться новой — чужие фото ей не нужны). Не удалось удалить
    файлы (заняты другой программой) -> 400, машина остаётся.
    """
    machine = _machine_row(conn, machine_id)
    folder = _photo_dir(photos_root, machine_id) if photos_root is not None else None
    counts = [
        ('записей в журнале', conn.execute('SELECT COUNT(*) FROM events WHERE machine_id=?', (machine_id,)).fetchone()[0]),
        ('шин', conn.execute('SELECT COUNT(*) FROM tyres WHERE machine_id=?', (machine_id,)).fetchone()[0]),
        ('узлов', conn.execute('SELECT COUNT(*) FROM units WHERE machine_id=?', (machine_id,)).fetchone()[0]),
        ('фото', len(_photo_entries(folder)) if folder is not None else 0),
    ]
    if any(n for _, n in counts) and not force:
        raise _confirm_error(
            f'У машины {machine["garage_no"]} есть история. Удалить машину вместе со всеми её записями? '
            'Вернуть будет нельзя (только из резервной копии).',
            [f'Будет удалено {what}: {n}' for what, n in counts if n])
    with db.transaction(conn):
        # явное удаление — на случай базы без включённых внешних ключей; обычно хватает каскада
        conn.execute('DELETE FROM events WHERE machine_id=?', (machine_id,))
        conn.execute('DELETE FROM tyres WHERE machine_id=?', (machine_id,))
        conn.execute('DELETE FROM units WHERE machine_id=?', (machine_id,))
        conn.execute('DELETE FROM machines WHERE id=?', (machine_id,))
        if folder is not None and folder.exists():
            # внутри транзакции: файлы не удалились -> откат, машина остаётся
            try:
                shutil.rmtree(folder)
            except OSError as err:
                raise ApiError(400, 'Не удалось удалить фото машины (файл открыт в другой программе?): '
                                    f'{err.strerror or err}') from None
    return {'ok': True}


def _regulation_for(regulation, kind):
    """Строка регламента для вида ТО: точное совпадение названия, иначе по началу («ТО-2 сезонное» -> «ТО-2»)."""
    key = (kind or '').strip().lower()
    if not key:
        return None
    best = None
    for row in regulation:
        row_key = (row.get('to_kind') or '').strip().lower()
        if not row_key:
            continue
        if row_key == key:
            return row
        if key.startswith(row_key) and (best is None or len(row_key) > len(best[0])):
            best = (row_key, row)
    return best[1] if best else None


def machine_card(conn, machine_id, date=None, now=None, photos_root=None):
    """Карточка машины: расчётная строка, вся история (новые сверху, включая накинутое), шины, узлы,
    строка регламента для следующего ТО и фото (как list_photos; photos_root не задан -> [])."""
    now = _now(now)
    D = _date_param(date, now)
    data = _load(conn, D, [machine_id])
    views = _views(data, D, now)
    if not views:
        raise ApiError(404, 'Машина не найдена')
    view = views[0]
    rows = conn.execute('SELECT * FROM events WHERE machine_id=? ORDER BY date DESC, id DESC',
                        (machine_id,)).fetchall()
    info = {view['id']: (view['garage_no'], view['model'], view['unit'])}
    photos = [] if photos_root is None else _photos_out(view['id'], _photo_dir(photos_root, view['id']))
    return {
        'machine': view,
        'events': [_event_view(r, info, now) for r in rows],
        'tyres': [_tyre_out(t, view, data.settings) for t in data.tyres.get(view['id'], ())],
        'units': [_unit_out(u, view, data.settings) for u in data.units.get(view['id'], ())],
        'regulation_next': _regulation_for(db.get_regulation(conn), view['next_to_kind']),
        'photos': photos,
    }


# --------------------------------------------------------------------------------------
# Фото машин (КОНЦЕПЦИЯ_v2, 9.2): файлы <папка данных>/photos/<machine_id>/<имя>; в базе ничего не хранится
# --------------------------------------------------------------------------------------

PHOTO_MAX_BYTES = 25 * 1024 * 1024          # лимит одного фото, байт
PHOTO_TOO_BIG = 'Фото слишком большое (больше 25 МБ)'
PHOTO_NAME_MAX = 80                         # длина имени файла вместе с расширением, символов
PHOTO_ONLY_TEXT = 'Можно загружать только фото: jpg, png, webp, gif'
PHOTO_MIME = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp',
              'gif': 'image/gif'}
_PHOTO_FORMAT = {'jpg': 'JPEG', 'jpeg': 'JPEG', 'png': 'PNG', 'webp': 'WEBP', 'gif': 'GIF'}   # расширение -> формат
_PHOTO_FORMAT_EXT = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp', 'GIF': 'gif'}              # формат -> расширение
# при загрузке в имени остаются буквы (латиница и вся кириллица), цифры, «. _ -» и пробел
_PHOTO_BAD = re.compile(r'[^0-9A-Za-z\u0400-\u04ff._\- ]+')
_PHOTO_REPEATS = re.compile(r'([._\- ])\1+')                  # «..», «  », «--», «__» -> один знак
# имена устройств Windows: файл «con.png» в папке создать нельзя (или он уйдёт в устройство)
_WIN_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}


def _photo_dir(photos_root, machine_id):
    """Папка фото машины: <photos_root>/<machine_id>."""
    return Path(photos_root) / str(int(machine_id))


def _photo_url(machine_id, name):
    return f'/api/machines/{int(machine_id)}/photos/{quote(name, safe="")}'


def _photo_name_ok(name):
    """Имя файла в папке фото, которое можно показать, отдать и удалить: без «..», «/», «\\», «:»
    и управляющих символов, не начинается с точки (так названы недописанные файлы), без пробелов по краям,
    расширение — фото. Иначе -> 404 (так не выйти за папку фото машины). Мягче правил загрузки:
    фото, положенное в папку вручную («IMG (1).jpg»), тоже видно."""
    if not isinstance(name, str) or not name or name != name.strip() or name.startswith('.'):
        return False
    if '..' in name or any(ch in '/\\:' or ord(ch) < 32 for ch in name):
        return False
    stem, dot, ext = name.rpartition('.')
    return bool(dot and stem and ext.lower() in PHOTO_MIME)


def _photo_entries(folder):
    """Фото в папке: [(mtime, name, size)] по mtime (старые первыми), при равенстве — по имени.
    Посторонние файлы (недописанные, с чужим расширением) не показываются."""
    if folder is None or not folder.is_dir():
        return []
    result = []
    for path in folder.iterdir():
        if not _photo_name_ok(path.name):
            continue
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        result.append((stat.st_mtime, path.name, stat.st_size))
    result.sort()
    return result


def _photos_out(machine_id, folder):
    """Список фото для ответа: [{name, size, mtime, url}] — старые первыми."""
    return [{'name': name, 'size': size,
             'mtime': datetime.fromtimestamp(mtime).isoformat(timespec='seconds'),
             'url': _photo_url(machine_id, name)}
            for mtime, name, size in _photo_entries(folder)]


def photo_format(data):
    """Формат картинки по первым байтам: 'JPEG' | 'PNG' | 'WEBP' | 'GIF' | None."""
    if data[:2] == b'\xff\xd8':
        return 'JPEG'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'PNG'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'WEBP'
    if data[:4] == b'GIF8':
        return 'GIF'
    return None


def photo_name_parts(raw_name):
    """Имя файла от клиента -> (основа, расширение в нижнем регистре).

    Берётся базовое имя без пути (разделители / и \\), остаются буквы (латиница и кириллица), цифры,
    «. _ -» и пробел; повторы этих знаков схлопываются; по краям основы убираются пробелы и точки;
    основа обрезается так, чтобы имя целиком было не длиннее PHOTO_NAME_MAX. Расширения нет -> ''.
    """
    base = re.split(r'[\\/]', str(raw_name or ''))[-1]
    base = _PHOTO_REPEATS.sub(r'\1', _PHOTO_BAD.sub('', base))
    stem, dot, ext = base.rpartition('.')
    if not dot:
        stem, ext = base, ''
    ext = ext.strip().lower()
    stem = stem.strip(' .')[:max(1, PHOTO_NAME_MAX - len(ext) - 1)].rstrip(' .')
    if stem and stem.split('.')[0].strip().upper() in _WIN_RESERVED:
        stem = f'foto-{stem}'[:max(1, PHOTO_NAME_MAX - len(ext) - 1)]
    return stem, ext


def _photo_free_name(folder, stem, ext):
    """Свободное имя в папке: «имя.ext», занято -> «имя-2.ext», «имя-3.ext»… (без учёта регистра:
    в Windows «A.png» и «a.png» — один файл)."""
    taken = {p.name.casefold() for p in folder.iterdir()} if folder.is_dir() else set()
    limit = PHOTO_NAME_MAX - len(ext) - 1
    name = f'{stem}.{ext}'
    n = 2
    while name.casefold() in taken:
        suffix = f'-{n}'
        name = f'{stem[:limit - len(suffix)].rstrip(" .")}{suffix}.{ext}'
        n += 1
    return name


def list_photos(conn, photos_root, machine_id):
    """Фото машины: [{name, size, mtime, url}], старые первыми. Нет машины -> 404."""
    machine_id = _machine_row(conn, machine_id)['id']
    return _photos_out(machine_id, _photo_dir(photos_root, machine_id))


def add_photo(conn, photos_root, machine_id, raw_name, data, now=None):
    """Сохранить фото машины (байты data, имя файла от клиента raw_name) -> {name, size, url}.

    Нет машины -> 404. Пустой файл, больше 25 МБ, расширение не jpg/jpeg/png/webp/gif, первые байты
    не совпадают с расширением -> 400. Имя приводится к безопасному (photo_name_parts); пустая основа ->
    «foto-YYYYMMDD-HHMMSS.<ext>» (имени нет совсем — расширение по содержимому); занято -> «-2», «-3»…
    """
    now = _now(now)
    machine_id = _machine_row(conn, machine_id)['id']
    data = bytes(data or b'')
    if not data:
        raise ApiError(400, 'Файл пустой — выберите фото')
    if len(data) > PHOTO_MAX_BYTES:
        raise ApiError(400, PHOTO_TOO_BIG)
    actual = photo_format(data)
    stem, ext = photo_name_parts(raw_name)
    if not norm_text(raw_name) and actual:
        ext = _PHOTO_FORMAT_EXT[actual]           # имени нет (вставка из буфера) — расширение по содержимому
    if ext not in PHOTO_MIME:
        raise ApiError(400, PHOTO_ONLY_TEXT)
    if actual != _PHOTO_FORMAT[ext]:
        raise ApiError(400, f'Файл «{stem}.{ext}» не похож на фото {_PHOTO_FORMAT[ext]}: '
                            'содержимое не совпадает с расширением')
    if not stem:
        stem = now.strftime('foto-%Y%m%d-%H%M%S')

    folder = _photo_dir(photos_root, machine_id)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        name = _photo_free_name(folder, stem, ext)
        tmp = folder / f'.{name}.part'                # недописанный файл в список не попадёт
        try:
            tmp.write_bytes(data)
            os.replace(tmp, folder / name)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    except OSError as err:
        raise ApiError(400, f'Не удалось сохранить фото: {err.strerror or err}') from None
    return {'name': name, 'size': len(data), 'url': _photo_url(machine_id, name)}


def _photo_path(conn, photos_root, machine_id, name):
    """Путь к существующему фото машины. Нет машины, недопустимое имя («..», «/», «\\» и т.п.)
    или нет файла -> 404."""
    machine_id = _machine_row(conn, machine_id)['id']
    if not _photo_name_ok(name):
        raise ApiError(404, 'Фото не найдено')
    folder = _photo_dir(photos_root, machine_id)
    path = folder / name
    try:
        if not path.is_file() or path.resolve().parent != folder.resolve():
            raise ApiError(404, 'Фото не найдено')
    except OSError:
        raise ApiError(404, 'Фото не найдено') from None
    return path


def photo_file(conn, photos_root, machine_id, name):
    """Файл фото -> (байты, Content-Type по расширению). Нет машины/файла, недопустимое имя -> 404."""
    path = _photo_path(conn, photos_root, machine_id, name)
    try:
        data = path.read_bytes()
    except OSError:
        raise ApiError(404, 'Фото не найдено') from None
    return data, PHOTO_MIME[path.suffix[1:].lower()]


def delete_photo(conn, photos_root, machine_id, name):
    """Удалить фото машины -> {ok: true}. Нет машины/файла, недопустимое имя -> 404; файл занят -> 400."""
    path = _photo_path(conn, photos_root, machine_id, name)
    try:
        path.unlink()
    except FileNotFoundError:
        raise ApiError(404, 'Фото не найдено') from None
    except OSError as err:
        raise ApiError(400, f'Не удалось удалить фото (файл открыт в другой программе?): {err.strerror or err}') from None
    try:
        path.parent.rmdir()                             # последнее фото — пустая папка не нужна
    except OSError:
        pass
    return {'ok': True}


# --------------------------------------------------------------------------------------
# События журнала
# --------------------------------------------------------------------------------------

_EVENT_TEXTS = ('shift', 'kind', 'work', 'parts_used', 'parts_order', 'remarks', 'who')
_EVENT_COLS = ('machine_id', 'date', 'shift', 'kind', 'reading', 'reading_estimated', 'delta', 'source',
               'time_in', 'date_out', 'time_out', 'work', 'parts_used', 'parts_order', 'order_done',
               'remarks', 'who', 'date_unknown')


def _machine_info(conn):
    """{machine_id: (гаражный №, модель, единица учёта)} по всем машинам — для EventView."""
    settings, rules = db.get_settings(conn), db.get_rules(conn)
    info = {}
    for row in conn.execute('SELECT * FROM machines'):
        m = dict(row)
        info[m['id']] = (m['garage_no'] or '', m['model'] or '',
                         logic.machine_params(m, rules, settings)['unit'])
    return info


def _event_view(row, info, now):
    """EventView: поля события + garage_no, model, unit, duration_seconds, open, duration_error, is_to."""
    ev = dict(row)
    for key in _EVENT_TEXTS:
        if ev.get(key) is None:
            ev[key] = ''
    for key in ('time_in', 'date_out', 'time_out'):
        if ev.get(key) == '':
            ev[key] = None
    ev['reading'] = _num_out(ev.get('reading'))
    ev['delta'] = _num_out(ev.get('delta'))
    ev['reading_estimated'] = bool(ev.get('reading_estimated'))
    ev['order_done'] = bool(ev.get('order_done'))
    ev['date_unknown'] = bool(ev.get('date_unknown'))     # дата не указана (date — дата внесения)
    garage_no, model, unit = info.get(ev['machine_id'], ('', '', ''))
    ev['garage_no'], ev['model'], ev['unit'] = garage_no, model, unit
    ev.update(logic.event_duration(ev, now))
    ev['is_to'] = logic.is_to(ev['kind'])
    ev['kind_title'] = logic.kind_title(ev['kind'])
    return ev


def _event_row(conn, event_id):
    row = conn.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
    if row is None:
        raise ApiError(404, 'Запись журнала не найдена')
    return dict(row)


def get_event(conn, event_id, now=None):
    """Одна запись журнала (EventView)."""
    return _event_view(_event_row(conn, event_id), _machine_info(conn), _now(now))


def list_events(conn, machine_id=None, kind=None, date_from=None, date_to=None, q=None,
                include_auto=False, limit=500, offset=0, now=None):
    """Журнал: {total, rows:[EventView]}, новые сверху (date desc, id desc).

    kind='ТО' — все виды «ТО-…»; иначе — по началу названия вида. q — подстрока без учёта регистра
    по «что делалось», запчастям, заявке, замечаниям, «кто» и гаражному №. Накинутое (auto) — только
    с include_auto.
    """
    now = _now(now)
    where, args = [], []
    if machine_id not in (None, ''):
        where.append('e.machine_id=?')
        args.append(_int(machine_id, 'Машина'))
    kind = norm_text(kind)
    if kind:
        prefix = 'ТО-' if kind == 'ТО' else kind
        where.append('substr(e.kind, 1, ?) = ?')
        args += [len(prefix), prefix]
    date_from = norm_date(date_from, 'Период: с')
    date_to = norm_date(date_to, 'Период: по')
    if date_from:
        where.append('e.date >= ?')
        args.append(date_from)
    if date_to:
        where.append('e.date <= ?')
        args.append(date_to)
    q = norm_text(q).lower()
    if q:
        cols = ('e.work', 'e.parts_used', 'e.parts_order', 'e.remarks', 'e.who', 'm.garage_no')
        where.append('(' + ' OR '.join(f'instr(ulower({c}), ?) > 0' for c in cols) + ')')
        args += [q] * len(cols)
    if not norm_bool(include_auto):
        where.append("e.source <> 'auto'")
    limit = min(max(_int(limit if limit not in (None, '') else 500, 'limit'), 1), 10000)
    offset = max(_int(offset if offset not in (None, '') else 0, 'offset'), 0)

    sql_where = (' WHERE ' + ' AND '.join(where)) if where else ''
    base = f'FROM events e JOIN machines m ON m.id = e.machine_id{sql_where}'
    total = conn.execute(f'SELECT COUNT(*) {base}', args).fetchone()[0]
    rows = conn.execute(f'SELECT e.* {base} ORDER BY e.date DESC, e.id DESC LIMIT ? OFFSET ?',
                        args + [limit, offset]).fetchall()
    info = _machine_info(conn)
    return {'total': total, 'rows': [_event_view(r, info, now) for r in rows]}


def _apply_event_input(conn, rec, data):
    """Перенести переданные ключи в запись события rec, нормализуя значения."""
    if data.get('machine_id') not in (None, '') or norm_text(data.get('garage_no')):
        rec['machine_id'] = _machine_ref(conn, data)
    for key in _EVENT_TEXTS:
        if key in data:
            rec[key] = norm_text(data[key])
    if 'date' in data:
        rec['date'] = norm_date(data['date'], 'Дата')
    if 'reading' in data:
        rec['reading'] = norm_num(data['reading'], 'Показание')
    if 'reading_estimated' in data:
        rec['reading_estimated'] = 1 if norm_bool(data['reading_estimated']) else 0
    if 'time_in' in data:
        rec['time_in'] = norm_time(data['time_in'], 'Заехала (время)')
    if 'date_out' in data:
        rec['date_out'] = norm_date(data['date_out'], 'Вышла (дата)')
    if 'time_out' in data:
        rec['time_out'] = norm_time(data['time_out'], 'Вышла (время)')
    if 'order_done' in data:
        rec['order_done'] = 1 if norm_bool(data['order_done']) else 0
    if 'date_unknown' in data:
        rec['date_unknown'] = 1 if norm_bool(data['date_unknown']) else 0
    return rec


def _validate_event(rec):
    """Жёсткие проверки итоговой записи события (400)."""
    if not rec.get('machine_id'):
        raise ApiError(400, 'Не выбрана машина')
    if not rec.get('date'):
        raise ApiError(400, 'Не указана дата')
    kind = rec.get('kind') or ''
    if not kind:
        raise ApiError(400, 'Не указан вид события')
    reading = rec.get('reading')
    if reading is not None and reading < 0:
        raise ApiError(400, 'Показание не может быть отрицательным')
    date_out, time_in, time_out = rec.get('date_out'), rec.get('time_in'), rec.get('time_out')
    if time_out and not date_out:
        raise ApiError(400, 'Указано время выхода без даты выхода')
    if date_out:
        if date_out < rec['date']:
            raise ApiError(400, 'Дата выхода раньше даты заезда')
        if date_out == rec['date'] and time_in and time_out and time_out < time_in:
            raise ApiError(400, 'Время выхода раньше времени заезда')
    if logic.is_to(kind) and reading is None:
        raise ApiError(400, 'Для ТО укажите показание счётчика, на котором оно сделано')
    if kind == KIND_MARK and reading is None:
        raise ApiError(400, 'Для отметки показаний укажите показание счётчика')


def _machine_readings(conn, machine_id, exclude_id=None):
    """Лёгкие строки событий одной машины (показания и накинутое), без события exclude_id."""
    rows = conn.execute(
        f"SELECT {_READING_COLS} FROM events WHERE machine_id=? AND (reading IS NOT NULL OR source='auto')",
        (machine_id,)).fetchall()
    return [r for r in rows if r['id'] != exclude_id]


def _soft_warnings(conn, machine, rec, exclude_id, today, settings, rules, check_open_repair=True):
    """Мягкие предупреждения по событию rec (SPEC «Валидация событий») — список строк.

    Проверки не видят само редактируемое событие (exclude_id). Сравнение идёт с последним фактом
    машины по всем датам (reading_base).
    """
    warns = []
    kind = rec.get('kind') or ''
    date = rec['date']
    if date > today:
        warns.append(f'дата {_ru(date)} — в будущем')

    reading = rec.get('reading')
    if reading is not None:
        readings = _machine_readings(conn, machine['id'], exclude_id)
        params = logic.machine_params(machine, rules, settings)
        unit = params['unit']
        cur = logic.current_reading(machine, readings, FAR_DATE)
        base, base_date = cur['reading_base'], cur['reading_base_date']

        if not logic.is_to(kind):
            if base is not None and reading < base and (base_date is None or date >= base_date):
                since = f' от {_ru(base_date)}' if base_date else ' (при регистрации)'
                warns.append(f'показание {_fmt(reading)} {unit} меньше последнего факта '
                             f'{_fmt(base)} {unit}{since} — это исправление?')
            later = [r for r in readings
                     if r['source'] != 'auto' and r['reading'] is not None and r['date'] and r['date'] > date
                     and not logic.is_to(r['kind'])
                     and (r['kind'] == KIND_MARK or not r['reading_estimated'])
                     and r['reading'] < reading]
            if later:
                r = min(later, key=lambda x: (x['date'], x['id']))
                warns.append(f'показание {_fmt(reading)} {unit} больше показания {_fmt(r["reading"])} {unit} '
                             f'от более поздней даты {_ru(r["date"])}')

        elif (base is not None and (base_date is None or date >= base_date)
              and reading < base - params['interval']):
            # ТО ниже факта — штатно (R8), но ниже больше чем на интервал при дате не раньше факта —
            # похоже на ошибку разрядности («153.000» вместо 153 000)
            warns.append(f'ТО на {_fmt(reading)} {unit} меньше последнего факта {_fmt(base)} {unit} больше чем на '
                         f'интервал ТО — проверьте разрядность (не пропущены ли нули)')

        if base is not None and base_date and date >= base_date and reading > base:
            days = logic.days_between(base_date, date)
            norm = logic.rate_and_norm(machine, readings, params, settings, today)['daily_norm'] or 0
            limit = max(3 * params['interval'], 3 * norm * days)
            if reading - base > limit:
                warns.append(f'показание выросло на {_fmt(reading - base)} {unit} с {_ru(base_date)} '
                             f'(было {_fmt(base)}) — проверьте, нет ли лишней цифры')

    if check_open_repair and logic.is_repair(kind) and not rec.get('date_out'):
        row = conn.execute(
            "SELECT date FROM events WHERE machine_id=? AND kind LIKE 'Ремонт%' "
            "AND (date_out IS NULL OR date_out='') AND id<>? ORDER BY date DESC, id DESC LIMIT 1",
            (machine['id'], exclude_id or 0)).fetchone()
        if row is not None:
            warns.append(f'у машины уже есть незакрытый ремонт от {_ru(row["date"])}')
    return warns


def _col_value(rec, col):
    """Значение столбца events для INSERT/UPDATE: флаги — строго 0/1 (NOT NULL)."""
    if col in ('date_unknown', 'order_done', 'reading_estimated'):
        return 1 if rec.get(col) else 0
    return rec.get(col)


def _insert_event(conn, rec, stamp):
    cols = _EVENT_COLS + ('created_at', 'updated_at')
    try:
        cur = conn.execute(
            f'INSERT INTO events({", ".join(cols)}) VALUES({", ".join("?" * len(cols))})',
            [_col_value(rec, c) for c in _EVENT_COLS] + [stamp, stamp])
    except sqlite3.IntegrityError:
        raise ApiError(400, 'На эту дату у машины уже есть такая отметка') from None
    return cur.lastrowid


def _new_event_rec(source='manual'):
    return {'machine_id': None, 'date': None, 'shift': '', 'kind': '', 'reading': None,
            'reading_estimated': 0, 'delta': None, 'source': source, 'time_in': None, 'date_out': None,
            'time_out': None, 'work': '', 'parts_used': '', 'parts_order': '', 'order_done': 0,
            'remarks': '', 'who': '', 'date_unknown': 0}


def create_event(conn, data, force=False, now=None):
    """Новая запись журнала (source='manual'). Возвращает EventView."""
    now = _now(now)
    data = _require_dict(data)
    rec = _apply_event_input(conn, _new_event_rec(), data)
    _validate_event(rec)
    machine = _machine_row(conn, rec['machine_id'])
    settings, rules = db.get_settings(conn), db.get_rules(conn)
    warns = _soft_warnings(conn, machine, rec, None, _today(now), settings, rules)
    if warns and not force:
        raise _confirm_error(CONFIRM_TEXT, warns)
    with db.transaction(conn):
        event_id = _insert_event(conn, rec, _stamp(now))
    return get_event(conn, event_id, now)


def update_event(conn, event_id, data, force=False, now=None):
    """Изменить запись журнала: меняются только переданные ключи, проверяется итоговая запись.
    Накинутое (source='auto') не редактируется."""
    now = _now(now)
    data = _require_dict(data)
    old = _event_row(conn, event_id)
    if old['source'] == 'auto':
        raise ApiError(400, 'Накинутое не редактируется — удалите запись или отмените период')
    rec = _apply_event_input(conn, dict(old), data)
    for key in _EVENT_TEXTS:
        if rec.get(key) is None:
            rec[key] = ''
    # показание исправлено, а признак «≈» не передан: введённое число — со счётчика, это факт (ТЗ п.45)
    if 'reading' in data and 'reading_estimated' not in data and rec.get('reading') != old['reading']:
        rec['reading_estimated'] = 0
    # дату указали явно — она больше не «не указана»
    if 'date' in data and 'date_unknown' not in data and rec.get('date') != old['date']:
        rec['date_unknown'] = 0
    _validate_event(rec)
    if rec['source'] == 'mark' and rec['kind'] != KIND_MARK:
        rec['source'] = 'manual'
    machine = _machine_row(conn, rec['machine_id'])
    settings, rules = db.get_settings(conn), db.get_rules(conn)
    was_open_repair = logic.is_repair(old['kind']) and not old['date_out']
    warns = _soft_warnings(conn, machine, rec, event_id, _today(now), settings, rules,
                           check_open_repair=not was_open_repair)
    if warns and not force:
        raise _confirm_error(CONFIRM_TEXT, warns)
    with db.transaction(conn):
        sets = ', '.join(f'{c}=?' for c in _EVENT_COLS)
        try:
            conn.execute(f'UPDATE events SET {sets}, updated_at=? WHERE id=?',
                         [_col_value(rec, c) for c in _EVENT_COLS] + [_stamp(now), event_id])
        except sqlite3.IntegrityError:
            raise ApiError(400, 'На эту дату у машины уже есть отметка показаний') from None
    return get_event(conn, event_id, now)


def delete_event(conn, event_id):
    """Удалить запись журнала (накинутое тоже можно)."""
    _event_row(conn, event_id)
    with db.transaction(conn):
        conn.execute('DELETE FROM events WHERE id=?', (event_id,))
    return {'ok': True}


def set_order_done(conn, event_id, done, now=None):
    """Отметить заявку на запчасти «заказано» / снять отметку."""
    now = _now(now)
    _event_row(conn, event_id)
    with db.transaction(conn):
        conn.execute('UPDATE events SET order_done=?, updated_at=? WHERE id=?',
                     (1 if norm_bool(done) else 0, _stamp(now), event_id))
    return get_event(conn, event_id, now)


# --------------------------------------------------------------------------------------
# Показания: отметка (upsert), «ТО сделано», пакетный ввод
# --------------------------------------------------------------------------------------

def _existing_mark_id(conn, machine_id, date):
    row = conn.execute("SELECT id FROM events WHERE machine_id=? AND date=? AND source='mark'",
                       (machine_id, date)).fetchone()
    return row['id'] if row else None


def _upsert_mark(conn, machine_id, date, reading, stamp, mark_id=None):
    """Отметка показаний машины на дату: есть (source='mark') — заменить показание, нет — создать."""
    if mark_id is None:
        mark_id = _existing_mark_id(conn, machine_id, date)
    if mark_id is not None:
        conn.execute('UPDATE events SET reading=?, reading_estimated=0, updated_at=? WHERE id=?',
                     (reading, stamp, mark_id))
        return mark_id
    cur = conn.execute(
        "INSERT INTO events(machine_id, date, kind, reading, source, created_at, updated_at) "
        "VALUES(?, ?, ?, ?, 'mark', ?, ?)", (machine_id, date, KIND_MARK, reading, stamp, stamp))
    return cur.lastrowid


def _mark_warnings(conn, machine, date, reading, mark_id, today, settings, rules):
    rec = {'date': date, 'kind': KIND_MARK, 'reading': reading}
    return _soft_warnings(conn, machine, rec, mark_id, today, settings, rules, check_open_repair=False)


def to_done(conn, data, force=False, now=None):
    """«ТО сделано» (R7, R8): событие ТО на указанном показании + при необходимости отметка
    текущего показания на сегодня. Возвращает {event, machine, note}."""
    now = _now(now)
    today = _today(now)
    data = _require_dict(data)
    machine = _machine_row(conn, _machine_ref(conn, data))
    mid = machine['id']

    rec = _new_event_rec()
    rec['machine_id'] = mid
    rec['kind'] = norm_text(data.get('kind'))
    if not logic.is_to(rec['kind']):
        raise ApiError(400, 'Выберите вид ТО (ТО-1, ТО-2, …)')
    rec['date'] = norm_date(data.get('date'), 'Дата ТО') or today
    rec['reading'] = norm_num(data.get('reading'), 'Показание на момент ТО')
    rec['reading_estimated'] = 1 if norm_bool(data.get('reading_estimated')) else 0
    for key in ('work', 'parts_used', 'parts_order', 'remarks', 'who', 'shift'):
        rec[key] = norm_text(data.get(key))
    _validate_event(rec)
    current = norm_num(data.get('current_reading'), 'Показание сейчас')
    if current is not None and current < rec['reading']:
        raise ApiError(400, 'Показание сейчас не может быть меньше показания на момент ТО')

    settings, rules = db.get_settings(conn), db.get_rules(conn)
    stamp = _stamp(now)
    warns = []
    with db.transaction(conn):
        before = _one_view(conn, mid, today, now)
        # расчётное «≈» — не факт: введённое «показание сейчас», даже совпавшее с ним, становится отметкой (R8)
        if current is not None and (before['reading'] is None or before['reading_estimated']
                                    or abs(current - before['reading']) > 1e-6):
            mark_id = _existing_mark_id(conn, mid, today)
            warns += _mark_warnings(conn, machine, today, current, mark_id, today, settings, rules)
            _upsert_mark(conn, mid, today, current, stamp, mark_id)
        warns += _soft_warnings(conn, machine, rec, None, today, settings, rules, check_open_repair=False)
        event_id = _insert_event(conn, rec, stamp)
        if warns and not force:
            raise _confirm_error(CONFIRM_TEXT, list(dict.fromkeys(warns)))   # откатит всё

    view = _one_view(conn, mid, today, now)
    note = ''
    if view['reading'] is not None and rec['reading'] < view['reading']:
        note = f'ТО внесено на меньшем показании; текущее осталось {_fmt(view["reading"])} {view["unit"]}'
    return {'event': get_event(conn, event_id, now), 'machine': view, 'note': note}


def readings_bulk(conn, data, force=False, now=None):
    """Пакетный ввод показаний («заезд на вахту»): baseline (последние ТО) и items (отметки).

    Сначала baseline, затем items; проверки видят строки этого же пакета. Жёстко неверные строки
    не сохраняются и возвращаются в errors; при мягких предупреждениях без force — 409 и ничего не сохранено.
    """
    now = _now(now)
    today = _today(now)
    data = _require_dict(data)
    date = norm_date(data.get('date'), 'Дата показаний') or today
    items = data.get('items') or []
    baseline = data.get('baseline') or []
    if not isinstance(items, list) or not isinstance(baseline, list):
        raise ApiError(400, 'items и baseline должны быть списками')

    settings, rules = db.get_settings(conn), db.get_rules(conn)
    machines = {m['id']: dict(m) for m in conn.execute('SELECT * FROM machines')}
    stamp = _stamp(now)
    errors, soft = [], []
    marks_saved = to_saved = 0

    def machine_of(row):
        if not isinstance(row, dict):
            raise ApiError(400, 'строка должна быть объектом')
        machine = machines.get(_int(row.get('machine_id'), 'Машина'))
        if machine is None:
            raise ApiError(400, 'Машина не найдена')
        return machine

    def fail(row, err, part):
        """part — 'baseline' (последнее ТО) или 'item' (новое показание): клиент очищает сохранённую часть строки."""
        mid = row.get('machine_id') if isinstance(row, dict) else None
        try:
            machine = machines.get(int(mid))
        except (TypeError, ValueError):
            machine = None
        errors.append({'machine_id': mid, 'garage_no': machine['garage_no'] if machine else '',
                       'error': err.message, 'part': part})

    def warn(machine, texts):
        for text in texts:
            soft.append({'machine_id': machine['id'], 'garage_no': machine['garage_no'], 'text': text})

    with db.transaction(conn):
        for row in baseline:
            try:
                if isinstance(row, dict) and norm_text(row.get('reading')) == '':
                    continue                                   # пустое показание — строка пропускается
                machine = machine_of(row)
                rec = _new_event_rec('baseline')
                rec['machine_id'] = machine['id']
                rec['kind'] = norm_text(row.get('kind'))
                if not rec['kind']:
                    raise ApiError(400, 'Не указан вид последнего ТО')
                if not logic.is_to(rec['kind']):
                    raise ApiError(400, f'«{rec["kind"]}» — не вид ТО')
                rec['date'] = norm_date(row.get('date'), 'Дата ТО')
                if not rec['date']:
                    # дату последнего ТО часто не знают: ставим дату пакета, но помечаем «дата не указана»
                    rec['date'], rec['date_unknown'] = date, 1
                rec['reading'] = norm_num(row.get('reading'), 'Показание ТО')
                rec['work'] = norm_text(row.get('work'))
                _validate_event(rec)
                duplicate = conn.execute(
                    'SELECT 1 FROM events WHERE machine_id=? AND date=? AND kind=? AND reading=?',
                    (machine['id'], rec['date'], rec['kind'], rec['reading'])).fetchone()
                if duplicate:
                    continue
                warn(machine, _soft_warnings(conn, machine, rec, None, today, settings, rules,
                                             check_open_repair=False))
                _insert_event(conn, rec, stamp)
                to_saved += 1
            except ApiError as err:
                fail(row, err, 'baseline')

        for row in items:
            try:
                if isinstance(row, dict) and norm_text(row.get('reading')) == '':
                    continue
                machine = machine_of(row)
                reading = norm_num(row.get('reading'), 'Показание')
                if reading < 0:
                    raise ApiError(400, 'Показание не может быть отрицательным')
                mark_id = _existing_mark_id(conn, machine['id'], date)
                warn(machine, _mark_warnings(conn, machine, date, reading, mark_id, today, settings, rules))
                _upsert_mark(conn, machine['id'], date, reading, stamp, mark_id)
                marks_saved += 1
            except ApiError as err:
                fail(row, err, 'item')

        if soft and not force:
            lines = list(dict.fromkeys(f'{it["garage_no"]}: {it["text"]}' for it in soft))
            raise _confirm_error(
                f'Есть сомнения в показаниях ({len(soft)}). Сохранить как есть?', lines, soft)   # откатит всё

    return {'marks_saved': marks_saved, 'to_saved': to_saved, 'errors': errors}


# --------------------------------------------------------------------------------------
# «Накинуть за день»
# --------------------------------------------------------------------------------------

def _period(date_from, date_to, today, limit_today=True):
    """Период «с … по …» (пусто -> сегодня). Проверки: from <= to, to <= сегодня, длина."""
    date_to = norm_date(date_to, 'Период: по') or today
    date_from = norm_date(date_from, 'Период: с') or min(today, date_to)
    if date_from > date_to:
        raise ApiError(400, 'Начало периода позже его конца')
    if limit_today:
        if date_to > today:
            raise ApiError(400, 'Нельзя накинуть наработку за будущие дни')
        if logic.days_between(date_from, date_to) >= MAX_DAILY_PERIOD:
            raise ApiError(400, f'Период слишком длинный (больше {MAX_DAILY_PERIOD} дней) — '
                                'после долгого перерыва введите показания вручную')
    return date_from, date_to


def _auto_dates(data, machine_id):
    return {r['date'] for r in data.readings.get(machine_id, ()) if r['source'] == 'auto'}


def daily_preview(conn, date_from=None, date_to=None, now=None):
    """Что будет начислено за период: по каждой машине (без списанных) — дни, норма, сумма, причина пропуска."""
    now = _now(now)
    today = _today(now)
    date_from, date_to = _period(date_from, date_to, today)
    data = _load(conn, today)
    views = _views(data, today, now)

    last_auto = None
    for rows in data.readings.values():
        for r in rows:
            if r['source'] == 'auto' and r['date'] and (last_auto is None or r['date'] > last_auto):
                last_auto = r['date']
    gap_days = logic.days_between(last_auto, today) if last_auto and logic.parse_date(last_auto) else None
    suggested = logic.add_days(last_auto, 1) if gap_days is not None and 1 < gap_days <= 7 else today

    rows = []
    for v in _active(views):
        mid = v['id']
        states = data.states.get(mid, ())
        days, reason = logic.days_to_add(v, states, _auto_dates(data, mid), date_from, date_to, today)
        state = logic.state_on(v, states, date_to, now)
        norm = v['daily_norm']
        add = logic.round_reading((norm or 0) * len(days), v['unit'])
        rows.append({
            'machine_id': mid, 'garage_no': v['garage_no'], 'model': v['model'], 'site': v['site'],
            'unit': v['unit'], 'reading': v['reading'], 'reading_estimated': v['reading_estimated'],
            'days_to_add': len(days), 'norm': norm, 'norm_source': v['daily_norm_source'],
            'add': _num_out(add), 'checked': len(days) > 0,
            'state': state['state'], 'state_label': state['state_label'], 'skip_reason': reason,
        })
    return {'from': date_from, 'to': date_to, 'last_auto_date': last_auto, 'suggested_from': suggested,
            'gap_days': gap_days, 'rows': rows}


def daily_apply(conn, data, now=None):
    """Начислить суточную норму выбранным машинам за период — по тем же правилам, что в preview.

    Возвращает {created — создано записей (машино-дней), skipped — машин, которым нечего начислять,
    machines — машин, которым начислено}.
    """
    now = _now(now)
    today = _today(now)
    data = _require_dict(data)
    date_from, date_to = _period(data.get('from'), data.get('to'), today)
    items = data.get('items')
    if not isinstance(items, list):
        raise ApiError(400, 'Не выбраны машины (items)')

    loaded = _load(conn, today)
    views = {v['id']: v for v in _views(loaded, today, now)}
    stamp = _stamp(now)
    rows = []
    seen = set()
    created = machines = skipped = 0
    for item in items:
        if not isinstance(item, dict):
            raise ApiError(400, 'items: строка должна быть объектом')
        mid = _int(item.get('machine_id'), 'Машина')
        if mid in seen:
            continue
        seen.add(mid)
        view = views.get(mid)
        if view is None:
            skipped += 1
            continue
        norm = norm_num(item.get('norm'), f'Норма ({view["garage_no"]})')
        if norm is None:
            norm = view['daily_norm']
        norm = logic.round_reading(norm, view['unit'])
        if not norm or norm <= 0:
            skipped += 1
            continue
        days, _ = logic.days_to_add(view, loaded.states.get(mid, ()), _auto_dates(loaded, mid),
                                    date_from, date_to, today)
        if not days:
            skipped += 1
            continue
        rows += [(mid, d, KIND_MARK, norm, 'авто', stamp, stamp) for d in days]
        created += len(days)
        machines += 1

    with db.transaction(conn):
        conn.executemany(
            "INSERT INTO events(machine_id, date, kind, delta, source, who, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, 'auto', ?, ?, ?)", rows)
    return {'created': created, 'skipped': skipped, 'machines': machines}


def daily_undo(conn, data, now=None):
    """Отменить накинутое за период: удалить auto-события с датами from…to."""
    now = _now(now)
    data = _require_dict(data)
    date_from, date_to = _period(data.get('from'), data.get('to'), _today(now), limit_today=False)
    with db.transaction(conn):
        cur = conn.execute("DELETE FROM events WHERE source='auto' AND date >= ? AND date <= ?",
                           (date_from, date_to))
        deleted = cur.rowcount
    return {'deleted': deleted}


# --------------------------------------------------------------------------------------
# План ТО и заказ-наряд
# --------------------------------------------------------------------------------------

def plan(conn, horizon_days=30, now=None):
    """План ТО: машины с просроченным/скорым ТО и те, у кого ТО по прогнозу в пределах горизонта;
    отдельно — машины без данных о ТО, сводка «что заказать» и открытые заявки на запчасти."""
    now = _now(now)
    today = _today(now)
    horizon = 30 if horizon_days in (None, '') else _int(horizon_days, 'Горизонт')
    horizon = min(max(horizon, 1), 3650)
    views = _active(build_views(conn, today, now))
    regulation = db.get_regulation(conn)

    def reg_of(kind):
        row = _regulation_for(regulation, kind)
        if row is None:
            return None
        return {key: row.get(key) or '' for key in ('to_kind', 'periodicity', 'works', 'oil', 'filters', 'parts', 'note')}

    rows, no_to = [], []
    for v in views:
        if v['to_status'] in ('no_to', 'no_reading'):
            no_to.append(v)
        elif v['to_status'] in ('overdue', 'soon') or (
                v['forecast_days'] is not None and v['forecast_days'] <= horizon):
            row = dict(v)
            row['regulation'] = reg_of(v['next_to_kind'])
            rows.append(row)
    rows.sort(key=lambda r: (0, -r['overdue_by']) if r['to_status'] == 'overdue' else (1, r['remaining']))

    groups = {}
    for r in rows:
        key = (r['next_to_kind'], (r['model'] or '').strip().lower())
        g = groups.get(key)
        if g is None:
            reg = r['regulation'] or {}
            g = groups[key] = {'to_kind': r['next_to_kind'], 'model': (r['model'] or '').strip(), 'count': 0,
                               'garage_nos': [], 'oil': reg.get('oil', ''), 'filters': reg.get('filters', ''),
                               'parts': reg.get('parts', '')}
        g['count'] += 1
        g['garage_nos'].append(r['garage_no'])

    order_rows = conn.execute(
        "SELECT e.* FROM events e JOIN machines m ON m.id = e.machine_id "
        "WHERE e.parts_order <> '' AND e.order_done = 0 AND m.status <> ? "
        "ORDER BY e.date DESC, e.id DESC", (logic.STATUS_RETIRED,)).fetchall()
    info = {v['id']: (v['garage_no'], v['model'], v['unit']) for v in views}
    return {
        'date': today, 'horizon_days': horizon, 'rows': rows, 'no_to': no_to,
        'materials': list(groups.values()),
        'parts_orders': [_event_view(r, info, now) for r in order_rows],
    }


def _date_time_iso(pairs, pick):
    """[(дата, время | None)] -> 'YYYY-MM-DDTHH:MM:00' или 'YYYY-MM-DD' (времени у этой даты нет) или None.
    pick (min/max) выбирает дату, затем время среди событий этой даты, у которых оно задано."""
    if not pairs:
        return None
    day = pick(d for d, _ in pairs)
    times = [t for d, t in pairs if d == day and t]
    return f'{day}T{pick(times)}:00' if times else day


def workorder(conn, machine_id, date=None, now=None):
    """Данные для заказ-наряда: машина, работы и запчасти из событий ТО/ремонта за дату, времена.
    Соответствие полей формы — как в таблице v8: «Заводской номер» <- plate, «Инвентарный» <- vin,
    «Бортовой» <- garage_no, «Наработка» <- reading."""
    now = _now(now)
    D = _date_param(date, now)
    if machine_id in (None, ''):
        raise ApiError(400, 'Не выбрана машина')
    view = _one_view(conn, _int(machine_id, 'Машина'), D, now)
    settings = db.get_settings(conn)
    events = conn.execute(
        "SELECT * FROM events WHERE machine_id=? AND date=? AND (kind LIKE 'ТО-%' OR kind LIKE 'Ремонт%') "
        "ORDER BY COALESCE(time_in, ''), id", (view['id'], D)).fetchall()

    works, parts = [], []
    starts, ends, times_in, times_out = [], [], [], []
    total = None
    for ev in events:
        ev = dict(ev)
        work = (ev.get('work') or '').strip()
        dur = logic.event_duration(ev, now)
        # незакрытый ремонт: часов нет (как в таблице v8 — «Журнал»!I пуст без даты выхода), иначе число
        # в печатной форме росло бы с каждой печатью
        duration = None if dur['open'] else dur['duration_seconds']
        works.append({'name': f'{ev["kind"]} — {work}' if work else ev['kind'],
                      'hours': None if duration is None else round(duration / 3600, 1),
                      'event_id': ev['id']})
        name = (ev.get('parts_used') or '').strip() or (ev.get('parts_order') or '').strip()
        if name:
            parts.append({'name': name})
        if duration is not None:
            total = (total or 0) + duration
        # время — только реально заданное: событие без времени даёт лишь дату (не «00:00»)
        if ev.get('date'):
            starts.append((ev['date'], ev.get('time_in') or None))
        if ev.get('date_out'):
            ends.append((ev['date_out'], ev.get('time_out') or None))
        if ev.get('time_in'):
            times_in.append(ev['time_in'])
        if ev.get('time_out'):
            times_out.append(ev['time_out'])

    return {
        'contract': settings.get('org_contract') or '',
        'date': D,
        'machine': {'id': view['id'], 'mtype': view['mtype'], 'model': view['model'], 'plate': view['plate'],
                    'vin': view['vin'], 'garage_no': view['garage_no'], 'reading': view['reading'],
                    'reading_estimated': view['reading_estimated'], 'unit': view['unit']},
        'works': works,
        'parts': parts,
        'time_start': min(times_in) if times_in else None,
        'time_end': max(times_out) if times_out else None,
        'repair_in': _date_time_iso(starts, min),
        'repair_out': _date_time_iso(ends, max),
        'repair_total_seconds': total,
        'mechanic_post': settings.get('org_mechanic_post') or '',
        'mechanic_name': settings.get('org_mechanic_name') or '',
    }


# --------------------------------------------------------------------------------------
# Шины и узлы
# --------------------------------------------------------------------------------------

def _tyre_out(tyre, view, settings):
    out = logic.tyre_view(dict(tyre), view['reading'], settings)
    for key in ('tyre_no', 'ttype', 'position', 'note'):
        if out.get(key) is None:
            out[key] = ''
    for key in ('resource', 'run', 'install_reading'):
        out[key] = _num_out(out.get(key))
    if out.get('install_date') == '':
        out['install_date'] = None
    out.update({'garage_no': view['garage_no'], 'model': view['model'], 'unit': view['unit']})
    return out


def _unit_out(unit_row, view, settings):
    out = logic.unit_view(dict(unit_row), view['reading'], settings)
    for key in ('unit', 'note'):
        if out.get(key) is None:
            out[key] = ''
    for key in ('resource', 'install_reading'):
        out[key] = _num_out(out.get(key))
    out.update({'garage_no': view['garage_no'], 'model': view['model'], 'unit_of_measure': view['unit']})
    return out


def _list_parts(conn, machine_id, now, attr, make):
    """Общий список шин/узлов: по одной машине или по всем, кроме списанных."""
    now = _now(now)
    ids = None if machine_id in (None, '') else [_int(machine_id, 'Машина')]
    today = _today(now)
    data = _load(conn, today, ids)
    result = []
    for view in _views(data, today, now):
        if ids is None and view['status'] == logic.STATUS_RETIRED:
            continue
        for row in getattr(data, attr).get(view['id'], ()):
            result.append(make(row, view, data.settings))
    return result


def list_tyres(conn, machine_id=None, now=None):
    """Шины с расчётными полями. Без machine_id — по всем машинам, кроме списанных."""
    return _list_parts(conn, machine_id, now, 'tyres', _tyre_out)


def list_units(conn, machine_id=None, now=None):
    """Узлы с расчётными полями. Без machine_id — по всем машинам, кроме списанных."""
    return _list_parts(conn, machine_id, now, 'units', _unit_out)


def _apply_tyre_input(conn, rec, data):
    if data.get('machine_id') not in (None, '') or norm_text(data.get('garage_no')):
        rec['machine_id'] = _machine_ref(conn, data)
    for key in ('tyre_no', 'ttype', 'position', 'note'):
        if key in data:
            rec[key] = norm_text(data[key])
    if 'install_date' in data:
        rec['install_date'] = norm_date(data['install_date'], 'Дата установки')
    for key, label in (('resource', 'Ресурс'), ('run', 'Пробег'), ('install_reading', 'Показание при установке')):
        if key in data:
            rec[key] = norm_num(data[key], label)
            if rec[key] is not None and rec[key] < 0:
                raise ApiError(400, f'{label}: не может быть отрицательным')
    if not rec.get('machine_id'):
        raise ApiError(400, 'Не выбрана машина')
    return rec


def _apply_unit_input(conn, rec, data):
    if data.get('machine_id') not in (None, '') or norm_text(data.get('garage_no')):
        rec['machine_id'] = _machine_ref(conn, data)
    for key in ('unit', 'note'):
        if key in data:
            rec[key] = norm_text(data[key])
    for key, label in (('resource', 'Ресурс'), ('install_reading', 'Показание при установке')):
        if key in data:
            rec[key] = norm_num(data[key], label)
            if rec[key] is not None and rec[key] < 0:
                raise ApiError(400, f'{label}: не может быть отрицательным')
    if not rec.get('machine_id'):
        raise ApiError(400, 'Не выбрана машина')
    if not rec.get('unit'):
        raise ApiError(400, 'Укажите узел')
    return rec


_TYRE_COLS = ('machine_id', 'tyre_no', 'ttype', 'position', 'install_date', 'resource', 'run',
              'install_reading', 'note')
_UNIT_COLS = ('machine_id', 'unit', 'resource', 'install_reading', 'note')


def _part_row(conn, table, row_id, what):
    row = conn.execute(f'SELECT * FROM {table} WHERE id=?', (row_id,)).fetchone()
    if row is None:
        raise ApiError(404, f'{what} не найден{"а" if table == "tyres" else ""}')
    return dict(row)


def _part_out(conn, table, row_id, now):
    row = _part_row(conn, table, row_id, 'Шина' if table == 'tyres' else 'Узел')
    view = _one_view(conn, row['machine_id'], _today(now), now)
    make = _tyre_out if table == 'tyres' else _unit_out
    return make(row, view, db.get_settings(conn))


def _save_part(conn, table, cols, rec, row_id):
    with db.transaction(conn):
        if row_id is None:
            cur = conn.execute(
                f'INSERT INTO {table}({", ".join(cols)}) VALUES({", ".join("?" * len(cols))})',
                [rec.get(c) for c in cols])
            return cur.lastrowid
        sets = ', '.join(f'{c}=?' for c in cols)
        conn.execute(f'UPDATE {table} SET {sets} WHERE id=?', [rec.get(c) for c in cols] + [row_id])
        return row_id


def create_tyre(conn, data, now=None):
    """Добавить шину. Возвращает строку как в list_tyres."""
    now = _now(now)
    rec = {'machine_id': None, 'tyre_no': '', 'ttype': '', 'position': '', 'install_date': None,
           'resource': None, 'run': None, 'install_reading': None, 'note': ''}
    _apply_tyre_input(conn, rec, _require_dict(data))
    return _part_out(conn, 'tyres', _save_part(conn, 'tyres', _TYRE_COLS, rec, None), now)


def update_tyre(conn, tyre_id, data, now=None):
    """Изменить шину (только переданные ключи)."""
    now = _now(now)
    rec = _apply_tyre_input(conn, _part_row(conn, 'tyres', tyre_id, 'Шина'), _require_dict(data))
    return _part_out(conn, 'tyres', _save_part(conn, 'tyres', _TYRE_COLS, rec, tyre_id), now)


def delete_tyre(conn, tyre_id):
    _part_row(conn, 'tyres', tyre_id, 'Шина')
    with db.transaction(conn):
        conn.execute('DELETE FROM tyres WHERE id=?', (tyre_id,))
    return {'ok': True}


def create_unit(conn, data, now=None):
    """Добавить узел. Возвращает строку как в list_units."""
    now = _now(now)
    rec = {'machine_id': None, 'unit': '', 'resource': None, 'install_reading': None, 'note': ''}
    _apply_unit_input(conn, rec, _require_dict(data))
    return _part_out(conn, 'units', _save_part(conn, 'units', _UNIT_COLS, rec, None), now)


def update_unit(conn, unit_id, data, now=None):
    """Изменить узел (только переданные ключи)."""
    now = _now(now)
    rec = _apply_unit_input(conn, _part_row(conn, 'units', unit_id, 'Узел'), _require_dict(data))
    return _part_out(conn, 'units', _save_part(conn, 'units', _UNIT_COLS, rec, unit_id), now)


def delete_unit(conn, unit_id):
    _part_row(conn, 'units', unit_id, 'Узел')
    with db.transaction(conn):
        conn.execute('DELETE FROM units WHERE id=?', (unit_id,))
    return {'ok': True}


# --------------------------------------------------------------------------------------
# Настройки, справочники, правила, регламент
# --------------------------------------------------------------------------------------

_SETTING_LABELS = {
    'default_interval_mh': 'Интервал ТО, м/ч',
    'default_warn_mh': 'Порог «скоро ТО», м/ч',
    'default_norm_mh': 'Суточная норма, м/ч',
    'default_interval_km': 'Интервал ТО, км',
    'default_norm_km': 'Суточная норма, км',
    'mh_types': 'Слова моточасной техники',
    'to_cycle': 'Круг видов ТО',
    'rate_window_days': 'Окно для средней наработки, дней',
    'norm_use_fact': 'Брать фактическую среднюю',
    'tyre_red': 'Порог износа «менять»',
    'tyre_yellow': 'Порог износа «скоро менять»',
    'org_contract': 'Шапка заказ-наряда',
    'org_mechanic_post': 'Должность механика',
    'org_mechanic_name': 'ФИО механика',
}


def _str_list(value, label):
    if isinstance(value, str):
        value = value.replace(';', ',').split(',')
    if not isinstance(value, list):
        raise ApiError(400, f'{label}: ожидается список')
    result = []
    for item in value:
        item = norm_text(item)
        if item and item not in result:
            result.append(item)
    return result


def _setting_value(key, value):
    label = _SETTING_LABELS[key]
    if key in ('default_interval_mh', 'default_interval_km', 'default_norm_mh', 'default_norm_km'):
        x = norm_num(value, label)
        if x is None or x <= 0:
            raise ApiError(400, f'{label}: нужно число больше нуля')
        return _num_out(x)
    if key == 'default_warn_mh':
        x = norm_num(value, label)
        if x is None or x < 0:
            raise ApiError(400, f'{label}: нужно неотрицательное число')
        return _num_out(x)
    if key == 'rate_window_days':
        x = norm_num(value, label)
        if x is None or x < 3 or x > 3650 or not float(x).is_integer():
            raise ApiError(400, f'{label}: нужно целое число от 3 до 3650')
        return int(x)
    if key in ('tyre_red', 'tyre_yellow'):
        x = norm_num(value, label)
        if x is None or not 0 < x <= 1:
            raise ApiError(400, f'{label}: нужна доля от 0 до 1 (например, 0,9)')
        return x
    if key == 'mh_types':
        return [w.lower() for w in _str_list(value, label)]
    if key == 'to_cycle':
        cycle = _str_list(value, label)
        if not cycle:
            raise ApiError(400, f'{label}: нужен хотя бы один вид ТО')
        return cycle
    if key == 'norm_use_fact':
        return norm_bool(value)
    return norm_text(value)


def update_settings(conn, values):
    """Изменить настройки (только переданные ключи). Возвращает все настройки."""
    values = _require_dict(values, 'Настройки')
    clean = {}
    for key, value in values.items():
        if key not in _SETTING_LABELS:
            raise ApiError(400, f'Неизвестная настройка: {key}')
        clean[key] = _setting_value(key, value)
    merged = dict(db.get_settings(conn))
    merged.update(clean)
    if merged['tyre_yellow'] > merged['tyre_red']:
        raise ApiError(400, 'Порог «скоро менять» не может быть больше порога «менять»')
    return db.set_settings(conn, clean)


def update_list(conn, name, values):
    """Заменить справочник. Статусы машин не редактируются; обязательные виды событий возвращаются на место."""
    if name not in db.LIST_NAMES:
        raise ApiError(404, f'Справочник «{name}» не найден')
    if name == 'machine_statuses':
        raise ApiError(400, 'Статусы машин не редактируются')
    if isinstance(values, dict):
        values = values.get('values')
    if not isinstance(values, list):
        raise ApiError(400, 'Справочник: ожидается список значений')
    values = _str_list(values, 'Справочник')
    if name == 'kinds':
        values = [k for k in db.PROTECTED_KINDS if k not in values] + values
    return db.set_list(conn, name, values)


def update_rules(conn, rules):
    """Заменить правила по маркам списком [{keyword, unit, interval, warn, daily_norm}]."""
    if not isinstance(rules, list):
        raise ApiError(400, 'Правила: ожидается список')
    clean = []
    for n, rule in enumerate(rules, 1):
        where = f'Правило {n}'
        if not isinstance(rule, dict):
            raise ApiError(400, f'{where}: ожидается объект')
        keyword = norm_text(rule.get('keyword'))
        if not keyword:
            raise ApiError(400, f'{where}: не указано слово (марка)')
        unit = _norm_unit(rule.get('unit'), f'{where}, единица')
        if unit is None:
            raise ApiError(400, f'{where}: не указана единица («км» или «м/ч»)')
        interval = norm_num(rule.get('interval'), f'{where}, интервал')
        if interval is None or interval <= 0:
            raise ApiError(400, f'{where}: интервал должен быть больше нуля')
        warn = norm_num(rule.get('warn'), f'{where}, порог')
        if warn is not None and warn < 0:
            raise ApiError(400, f'{where}: порог не может быть отрицательным')
        daily_norm = norm_num(rule.get('daily_norm'), f'{where}, суточная норма')
        if daily_norm is not None and daily_norm <= 0:
            raise ApiError(400, f'{where}: суточная норма должна быть больше нуля (или пусто)')
        clean.append({'keyword': keyword, 'unit': unit, 'interval': interval, 'warn': warn,
                      'daily_norm': daily_norm})
    return _rules_out(db.set_rules(conn, clean))


def _rules_out(rules):
    for rule in rules:
        for key in ('interval', 'warn', 'daily_norm'):
            rule[key] = _num_out(rule.get(key))
    return rules


def update_regulation(conn, rows):
    """Заменить регламент ТО списком [{to_kind, periodicity, works, oil, filters, parts, note}]."""
    if not isinstance(rows, list):
        raise ApiError(400, 'Регламент: ожидается список')
    clean = []
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ApiError(400, f'Регламент, строка {n}: ожидается объект')
        item = {key: norm_text(row.get(key)) for key in db.REGULATION_FIELDS}
        if not item['to_kind']:
            raise ApiError(400, f'Регламент, строка {n}: не указан вид ТО')
        clean.append(item)
    return db.set_regulation(conn, clean)


# --------------------------------------------------------------------------------------
# Резервные копии
# --------------------------------------------------------------------------------------

def list_backups(backups_dir):
    """Список резервных копий, новые сверху."""
    return db.list_backups(backups_dir)


def create_backup(conn, backups_dir):
    """Создать резервную копию сейчас."""
    return {'name': db.backup_now(conn, backups_dir)}


def restore_backup(conn, backups_dir, name):
    """Восстановить базу из копии (перед этим снимается страховочная копия текущей базы)."""
    try:
        safety = db.restore_backup(conn, backups_dir, name)
    except FileNotFoundError as err:
        raise ApiError(404, str(err)) from None
    except ValueError as err:
        raise ApiError(400, str(err)) from None
    return {'ok': True, 'safety': safety}
