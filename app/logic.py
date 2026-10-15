"""Чистые функции расчёта «Учёт ТО» (SPEC, раздел 4). Без базы данных и без «сейчас» внутри:
дата отчёта D ('YYYY-MM-DD') и now (datetime) всегда передаются аргументами.

ОГЛАВЛЕНИЕ

Константы
    KIND_MARK, UNIT_KM, UNIT_MH, DEFAULT_SETTINGS, STATE_LABELS, TO_STATUS_LABELS,
    TYRE_LABELS, UNIT_LABELS

Даты, время, числа
    parse_date(s) -> date | None                  'YYYY-MM-DD'
    parse_time(s) -> time | None                  'HH:MM'
    add_days(D, n) -> str                         дата + n суток
    days_between(a, b) -> int                     b − a в сутках
    fmt_date_ru(s) -> str                         '2026-10-02' -> '02.10.2026'
    round_reading(value, unit)                    км — до целого, м/ч — до 0.1
    is_to(kind), is_repair(kind), is_idle(kind)   вид события по началу названия
    kind_title(kind) -> str                       «Отметка моточасов» -> «Отметка показаний»

4.1 Параметры машины
    machine_params(machine, rules, settings) -> dict
4.2 Текущее показание
    current_reading(machine, readings, D, state_events=None) -> dict
4.3 Статус ТО
    next_to_kind(last_kind, to_cycle) -> str
    to_status(readings, reading, params, settings, D) -> dict
4.4 Состояние на дату
    state_on(machine, state_events, D, now=None) -> dict
4.5 Длительности
    event_start(ev) -> datetime | None
    event_end(ev) -> datetime | None
    event_duration(ev, now) -> {duration_seconds, open, duration_error}
    day_window(D, now) -> (datetime, datetime) | None
    day_hours(machine, state_events, D, now) -> {line_hours, repair_hours, idle_hours}
    last_repair_seconds(state_events, D) -> int | None
4.6 Средняя наработка и прогноз
    rate_and_norm(machine, readings, params, settings, D, remaining=None) -> dict
4.7 Шины и узлы
    tyre_view(tyre, machine_reading, settings) -> dict
    unit_view(unit_row, machine_reading, settings) -> dict
4.8 Проверка данных
    data_warnings(machine, readings, state_events, today) -> [{event_id, text}]

Сборка
    build_machine_view(machine, readings, state_events, open_orders, tyres, units,
                       rules, settings, D, now) -> dict        (MachineView из SPEC, раздел 5)
    fleet_summary(views) -> dict                               (summary для /api/fleet)
    days_to_add(machine_view_today, state_events, auto_dates,
                date_from, date_to, today) -> (list[str], skip_reason)   («Накинуть за день»)

ФОРМАТ ВХОДНЫХ ДАННЫХ
    machine      — dict строки таблицы machines (именно dict: функции читают поля через .get;
                   build_machine_view сам приводит sqlite3.Row к dict).
    readings     — «лёгкие» строки событий одной машины (dict или sqlite3.Row) с ключами
                   id, date, kind, reading, reading_estimated, delta, source, updated_at;
                   сюда входят события с reading не null и события source='auto'.
    state_events — полные строки событий Ремонт*/Простой*/ТО-* одной машины (dict или sqlite3.Row);
                   используются id, date, kind, reading, source, time_in, date_out, time_out, work.
    rules        — список правил по порядку: {keyword, unit, interval, warn, daily_norm}.
    settings     — dict настроек с уже распарсенными значениями (db.get_settings).
"""

import math
from datetime import date, datetime, time, timedelta

KIND_MARK = 'Отметка моточасов'
UNIT_KM = 'км'
UNIT_MH = 'м/ч'

# Настройки по умолчанию (SPEC, раздел 3 «Сиды»). db.py берёт их отсюда.
DEFAULT_SETTINGS = {
    'default_interval_mh': 250,
    'default_warn_mh': 50,
    'default_norm_mh': 11,
    'default_interval_km': 10000,
    'default_norm_km': 100,
    'mh_types': ['экскаватор', 'бульдозер', 'каток', 'грейдер', 'погрузчик'],
    'to_cycle': ['ТО-1', 'ТО-2', 'ТО-3'],
    'rate_window_days': 60,
    'norm_use_fact': True,
    'tyre_red': 0.9,
    'tyre_yellow': 0.75,
    'org_contract': 'Приложение № 1 к договору (образец)',
    'org_mechanic_post': 'Ст. механик',
    'org_mechanic_name': 'Иванов И. И.',
}

STATE_LABELS = {
    'conserved': '🅿 Консервация',
    'repair': '🔴 На ремонте',
    'to': '🔧 На ТО',
    'idle': '🟡 Простой',
    'line': '🟢 На линии',
}

TO_STATUS_LABELS = {
    'no_reading': '⚪ Нет показаний',
    'no_to': '⚪ Внесите ТО',
    'overdue': '🔴 Просрочено',
    'soon': '🟡 Скоро ТО',
    'ok': '🟢 В норме',
}

TYRE_LABELS = {
    'red': '🔴 Менять',
    'yellow': '🟡 Скоро менять',
    'green': '🟢 Норма',
    'none': '',
}

UNIT_LABELS = {
    'check': '⚪ Проверьте показание установки',
    'red': '🔴 Скоро замена/ремонт',
    'yellow': '🟡 Контроль',
    'green': '🟢 Норма',
    'none': '',
}

STATUS_CONSERVED = 'На консервации'
STATUS_RETIRED = 'Списана'


# --------------------------------------------------------------------------------------
# Даты, время, числа
# --------------------------------------------------------------------------------------

def parse_date(s):
    """'YYYY-MM-DD' -> date; пусто или мусор -> None."""
    if not isinstance(s, str) or len(s) != 10 or s[4] != '-' or s[7] != '-':
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def parse_time(s):
    """'HH:MM' (допускается 'H:MM' и 'HH:MM:SS') -> time; пусто или мусор -> None."""
    if not isinstance(s, str):
        return None
    parts = s.strip().split(':')
    if len(parts) < 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if 0 <= hours <= 23 and 0 <= minutes <= 59:
        return time(hours, minutes)
    return None


def add_days(D, n):
    """Дата 'YYYY-MM-DD' плюс n суток (n может быть отрицательным) -> 'YYYY-MM-DD'."""
    return (parse_date(D) + timedelta(days=n)).isoformat()


def days_between(a, b):
    """Сколько суток от даты a до даты b (b − a)."""
    return (parse_date(b) - parse_date(a)).days


def fmt_date_ru(s):
    """'2026-10-02' -> '02.10.2026'; непонятное значение возвращается как есть."""
    d = parse_date(s)
    return d.strftime('%d.%m.%Y') if d else (s or '')


def round_reading(value, unit):
    """Округление показаний и остатков: км — до целого, м/ч — до 0.1. None -> None."""
    if value is None:
        return None
    if unit == UNIT_KM:
        return int(math.floor(value + 0.5))
    return round(value, 1) + 0.0   # «+ 0.0» превращает −0.0 в 0.0


def is_to(kind):
    """Событие ТО: вид начинается с «ТО-»."""
    return bool(kind) and kind.startswith('ТО-')


def is_repair(kind):
    """Ремонт: вид начинается с «Ремонт»."""
    return bool(kind) and kind.startswith('Ремонт')


def is_idle(kind):
    """Простой: вид начинается с «Простой»."""
    return bool(kind) and kind.startswith('Простой')


def kind_title(kind):
    """Название вида события для экрана: «Отметка моточасов» показывается как «Отметка показаний»."""
    return 'Отметка показаний' if kind == KIND_MARK else (kind or '')


def _num(v):
    """Число или None (пусто, не число). Понимает строки «153 000» и «1250,5»."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(' ', '').replace(' ', '').replace(',', '.'))
    except ValueError:
        return None


def _clean(v):
    """Целое значение отдаём как int (10000, а не 10000.0)."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _fmt_num(v):
    """153000 -> '153 000', 1250.5 -> '1 250,5' (для текстов предупреждений)."""
    v = _clean(round(float(v), 1))
    return f'{v:,}'.replace(',', ' ').replace('.', ',')


def _g(row, key):
    """Значение поля строки (dict или sqlite3.Row); нет такого поля -> None."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _setting(settings, key):
    """Настройка или её значение по умолчанию."""
    value = settings.get(key) if settings else None
    return DEFAULT_SETTINGS[key] if value is None else value


def _setting_num(settings, key):
    value = _num(_setting(settings, key))
    return float(DEFAULT_SETTINGS[key]) if value is None else value


def _norm_unit(u):
    """Единица учёта: 'км' или 'м/ч' (всё, что не км, считается моточасами); пусто -> None."""
    u = (u or '').strip().lower()
    if not u:
        return None
    return UNIT_KM if u == UNIT_KM else UNIT_MH


# --------------------------------------------------------------------------------------
# 4.1 Параметры машины
# --------------------------------------------------------------------------------------

def _resolve_params(unit, rule, interval_override, warn_override, settings):
    """Интервал, порог и норма для заданной единицы и сработавшего правила (может быть None)."""
    km = unit == UNIT_KM
    rule_interval = _num(rule.get('interval')) if rule else None
    rule_warn = _num(rule.get('warn')) if rule else None
    rule_norm = _num(rule.get('daily_norm')) if rule else None

    if interval_override is not None and interval_override > 0:
        interval = interval_override
    elif rule_interval is not None and rule_interval > 0:
        interval = rule_interval
    else:
        interval = _setting_num(settings, 'default_interval_km' if km else 'default_interval_mh')

    if warn_override is not None and warn_override >= 0:
        warn = warn_override
    elif rule_warn is not None and rule_warn > 0:
        warn = rule_warn
    elif km:
        warn = float(math.floor(0.1 * interval + 0.5))
    else:
        warn = _setting_num(settings, 'default_warn_mh')

    if rule_norm is not None and rule_norm > 0:
        norm, norm_source = rule_norm, 'rule'
    else:
        norm, norm_source = _setting_num(settings, 'default_norm_km' if km else 'default_norm_mh'), 'default'

    return {
        'unit': unit,
        'interval': _clean(interval),
        'warn': _clean(warn),
        'norm': _clean(norm),
        'norm_source': norm_source,
        'rule_keyword': rule.get('keyword') if rule else None,
    }


def machine_params(machine, rules, settings):
    """Единица учёта, интервал ТО, порог «скоро» и норма машины (SPEC 4.1).

    Каждый параметр разрешается отдельно: своё значение машины (override) -> правило по марке -> умолчание.
    Возвращает {unit, interval, warn, rule_norm, rule_norm_source ('rule'|'default'), rule_keyword,
    auto: {unit, interval, warn, norm, rule_keyword}}; в auto — значения без учёта override.
    """
    model = (machine.get('model') or '').lower()
    mtype = (machine.get('mtype') or '').lower()

    mh_words = [str(w).strip().lower() for w in (_setting(settings, 'mh_types') or [])]
    always_mh = any(w and (w in mtype or w in model) for w in mh_words)

    # Правила, чьё слово есть в модели, по порядку.
    matched = []
    for rule in rules or []:
        keyword = (rule.get('keyword') or '').strip().lower()
        if keyword and keyword in model:
            matched.append(rule)

    def first_rule(unit=None):
        for rule in matched:
            if unit is None or _norm_unit(rule.get('unit')) == unit:
                return rule
        return None

    if always_mh:
        # Техника из списка «всегда на моточасах»: км-правила не применяются.
        auto_unit = UNIT_MH
        auto_rule = first_rule(UNIT_MH)
    else:
        auto_rule = first_rule()
        auto_unit = (_norm_unit(auto_rule.get('unit')) if auto_rule else None) or UNIT_MH

    auto = _resolve_params(auto_unit, auto_rule, None, None, settings)

    unit = _norm_unit(machine.get('unit_override')) or auto_unit
    # Правило применяется, только если его единица совпала с итоговой.
    rule = auto_rule if unit == auto_unit else first_rule(unit)
    final = _resolve_params(unit, rule, _num(machine.get('interval_override')),
                            _num(machine.get('warn_override')), settings)

    return {
        'unit': final['unit'],
        'interval': final['interval'],
        'warn': final['warn'],
        'rule_norm': final['norm'],
        'rule_norm_source': final['norm_source'],
        'rule_keyword': final['rule_keyword'],
        'auto': {
            'unit': auto['unit'],
            'interval': auto['interval'],
            'warn': auto['warn'],
            'norm': auto['norm'],
            'rule_keyword': auto['rule_keyword'],
        },
    }


# --------------------------------------------------------------------------------------
# 4.2 Текущее показание
# --------------------------------------------------------------------------------------

def current_reading(machine, readings, D, state_events=None):
    """Текущее показание машины на дату D (SPEC 4.2).

    Понизить показание может только явная отметка; событие ТО/ремонта может только поднять.
    Накинутое («авто») — оценка, считается только после даты базы и (если переданы state_events —
    события Ремонт*/Простой*/ТО-*) только за дни, когда машина не в ремонте и не в простое (R10):
    ремонт, внесённый после начисления или задним числом, исключает накинутое за свои дни.
    Возвращает {reading, reading_base, reading_base_date, reading_base_event_id,
    reading_estimated, reading_auto_add}; нет ни отметок, ни фактов -> reading = None.
    """
    mark_key = None      # (дата, updated_at, id) самой поздней отметки; дата '' = «раньше всех»
    mark_value = None
    mark_id = None
    facts = []           # (показание, дата, id) прочих фактов
    autos = []           # (дата, delta)

    for r in readings:
        d = r['date']
        if not d or d > D:
            continue
        if r['source'] == 'auto':
            if r['delta']:
                autos.append((d, r['delta']))
            continue
        value = r['reading']
        if value is None:
            continue
        if r['kind'] == KIND_MARK:
            key = (d, r['updated_at'] or '', r['id'] or 0)
            if mark_key is None or key > mark_key:
                mark_key, mark_value, mark_id = key, value, r['id']
        elif not r['reading_estimated']:
            facts.append((value, d, r['id'] or 0))

    # Показание при регистрации — тоже отметка: с датой reg_date, а без даты — «раньше всех».
    reg = _num(machine.get('reg_reading'))
    if reg is not None:
        reg_date = machine.get('reg_date') or ''
        if reg_date <= D:
            key = (reg_date, '', -1)
            if mark_key is None or key > mark_key:
                mark_key, mark_value, mark_id = key, reg, None

    mark_date = mark_key[0] if mark_key else ''
    later_facts = [f for f in facts if f[1] >= mark_date]
    top = max(later_facts) if later_facts else None   # наибольшее показание, при равных — более поздняя дата

    if mark_key is None and top is None:
        return {'reading': None, 'reading_base': None, 'reading_base_date': None,
                'reading_base_event_id': None, 'reading_estimated': False, 'reading_auto_add': 0}

    if top is not None and (mark_key is None or top[0] > mark_value):
        base, base_date, base_id = top[0], top[1], top[2] or None
    else:
        base, base_date, base_id = mark_value, mark_date, mark_id

    autos = [(d, delta) for d, delta in autos if d > base_date]
    if autos and state_events:
        stops = [ev for ev in state_events if is_repair(ev['kind'] or '') or is_idle(ev['kind'] or '')]
        if stops:
            autos = [(d, delta) for d, delta in autos if _state_pick({}, stops, d)[0] not in ('repair', 'idle')]
    auto_add = sum(delta for d, delta in autos)
    auto_add = _clean(round(float(auto_add), 3))

    return {
        'reading': _clean(round(float(base) + auto_add, 3)),
        'reading_base': _clean(float(base)),
        'reading_base_date': base_date or None,
        'reading_base_event_id': base_id,
        'reading_estimated': auto_add > 0,
        'reading_auto_add': auto_add,
    }


# --------------------------------------------------------------------------------------
# 4.3 Статус ТО
# --------------------------------------------------------------------------------------

def next_to_kind(last_kind, to_cycle):
    """Следующий вид ТО по кругу. Вид сопоставляется с кругом по началу названия
    (самое длинное совпадение): «ТО-2 сезонное» -> после него ТО-3. Нет ТО или вид не из круга -> первый."""
    cycle = [str(k) for k in (to_cycle or []) if k] or list(DEFAULT_SETTINGS['to_cycle'])
    if last_kind:
        best = None
        for i, kind in enumerate(cycle):
            if last_kind.startswith(kind) and (best is None or len(kind) > len(cycle[best])):
                best = i
        if best is not None:
            return cycle[(best + 1) % len(cycle)]
    return cycle[0]


def to_status(readings, reading, params, settings, D):
    """Статус ТО на дату D (SPEC 4.3).

    reading — текущее показание машины (или None), params — результат machine_params.
    Возвращает {to_status, to_status_label, to_count, last_to, next_to_kind, next_to_at,
    remaining, overdue_by, missed}.
    """
    unit, interval, warn = params['unit'], params['interval'], params['warn']
    cycle = _setting(settings, 'to_cycle')

    last = None
    last_key = None
    count = 0
    for r in readings:
        value = r['reading']
        d = r['date']
        if value is None or not d or d > D or r['source'] == 'auto':
            continue
        if not is_to(r['kind']):
            continue
        count += 1
        key = (d, value, r['id'] or 0)   # самая поздняя дата, затем наибольшее показание, затем id
        if last_key is None or key > last_key:
            last, last_key = r, key

    result = {
        'to_status': None, 'to_status_label': '', 'to_count': count,
        'last_to': None, 'next_to_kind': next_to_kind(None, cycle), 'next_to_at': None,
        'remaining': None, 'overdue_by': 0, 'missed': 0,
    }
    if last is not None:
        result['last_to'] = {'event_id': last['id'], 'kind': last['kind'],
                             'reading': _clean(float(last['reading'])), 'date': last['date'],
                             'date_unknown': bool(_g(last, 'date_unknown'))}
        result['next_to_kind'] = next_to_kind(last['kind'], cycle)
        result['next_to_at'] = round_reading(last['reading'] + interval, unit)

    if reading is None:
        status = 'no_reading'
    elif last is None:
        status = 'no_to'
    else:
        remaining = round_reading(last['reading'] + interval - reading, unit)   # округляем ДО сравнений
        result['remaining'] = remaining
        if remaining <= 0:
            status = 'overdue'
            overdue_by = -remaining + 0   # «+ 0»: не отдавать −0
            result['overdue_by'] = overdue_by
            result['missed'] = (int(math.floor(overdue_by / interval + 1e-9)) if interval > 0 else 0) + 1
        elif remaining <= warn:
            status = 'soon'
        else:
            status = 'ok'

    result['to_status'] = status
    result['to_status_label'] = TO_STATUS_LABELS[status]
    return result


# --------------------------------------------------------------------------------------
# 4.4 Состояние на дату
# --------------------------------------------------------------------------------------

def _active_on(ev, D):
    """Ремонт/простой «идёт» на дату D: начался не позже D и к D не закрыт (вышла позже D или не вышла)."""
    d = ev['date']
    if not d or d > D:
        return False
    out = _g(ev, 'date_out')
    return (not out) or out > D


def _state_pick(machine, state_events, D):
    """Состояние машины на дату D и определяющее событие: (state, ev | None)."""
    if machine.get('status') == STATUS_CONSERVED:
        return 'conserved', None
    best = {}
    for ev in state_events:
        kind = ev['kind'] or ''
        if is_repair(kind):
            state = 'repair' if _active_on(ev, D) else None
        elif is_to(kind):
            state = 'to' if ev['date'] == D and _g(ev, 'source') != 'baseline' else None
        elif is_idle(kind):
            state = 'idle' if _active_on(ev, D) else None
        else:
            state = None
        if state is None:
            continue
        cur = best.get(state)
        # если подходящих несколько — с самой поздней датой, затем с большим id
        if cur is None or (ev['date'], _g(ev, 'id') or 0) > (cur['date'], _g(cur, 'id') or 0):
            best[state] = ev
    for state in ('repair', 'to', 'idle'):
        if state in best:
            return state, best[state]
    return 'line', None


def state_on(machine, state_events, D, now=None):
    """Состояние машины на дату D (SPEC 4.4): консервация -> ремонт -> ТО сегодня -> простой -> на линии.

    Возвращает {state, state_label, state_event_id, state_work, state_since, state_seconds}.
    state_seconds считается только для ремонта/простоя и только если передан now.
    """
    state, ev = _state_pick(machine, state_events, D)
    result = {'state': state, 'state_label': STATE_LABELS[state], 'state_event_id': None,
              'state_work': '', 'state_since': None, 'state_seconds': None}
    if ev is None:
        return result
    result['state_event_id'] = _g(ev, 'id')
    result['state_work'] = _g(ev, 'work') or ''
    start = event_start(ev)
    if start is not None:
        result['state_since'] = start.isoformat()
        if state in ('repair', 'idle') and now is not None:
            end = event_end(ev) or now
            result['state_seconds'] = max(0, int((end - start).total_seconds()))
    return result


# --------------------------------------------------------------------------------------
# 4.5 Длительности
# --------------------------------------------------------------------------------------

def event_start(ev):
    """Старт события: date + time_in (нет времени -> 00:00). Нет даты -> None."""
    d = parse_date(_g(ev, 'date'))
    if d is None:
        return None
    return datetime.combine(d, parse_time(_g(ev, 'time_in')) or time(0, 0))


def event_end(ev):
    """Конец события: date_out + time_out (нет времени -> 00:00 дня выхода). Нет даты выхода -> None."""
    d = parse_date(_g(ev, 'date_out'))
    if d is None:
        return None
    return datetime.combine(d, parse_time(_g(ev, 'time_out')) or time(0, 0))


def event_duration(ev, now):
    """Длительность события (SPEC 4.5) -> {duration_seconds, open, duration_error}.

    Открытый ремонт/простой считается до now (open=True); у прочих видов без даты выхода длительности нет.
    Отрицательная длительность (вышла раньше, чем заехала) -> duration_seconds=None, duration_error=True.
    """
    result = {'duration_seconds': None, 'open': False, 'duration_error': False}
    start = event_start(ev)
    if start is None:
        return result
    end = event_end(ev)
    if end is None:
        kind = _g(ev, 'kind') or ''
        if not _g(ev, 'date_out') and (is_repair(kind) or is_idle(kind)):
            result['open'] = True
            result['duration_seconds'] = max(0, int((now - start).total_seconds()))
        return result
    seconds = int((end - start).total_seconds())
    if seconds < 0:
        result['duration_error'] = True
    else:
        result['duration_seconds'] = seconds
    return result


def day_window(D, now):
    """Окно суток D: (начало, конец). Сегодня — до now, прошлая дата — полные сутки, будущая — None."""
    d = parse_date(D)
    if d is None:
        return None
    start = datetime.combine(d, time(0, 0))
    today = now.date()
    if d > today:
        return None
    end = start + timedelta(days=1)
    if d == today:
        end = min(end, now)
    return start, end


def _union_seconds(state_events, kind_test, w_start, w_end):
    """Длина объединения пересечений интервалов событий с окном, секунд (открытые — до конца окна)."""
    parts = []
    for ev in state_events:
        if not kind_test(ev['kind']):
            continue
        start = event_start(ev)
        if start is None or start >= w_end:
            continue
        end = event_end(ev)
        if end is None:
            if _g(ev, 'date_out'):
                continue          # дата выхода не читается — интервал неизвестен
            end = w_end           # открытое событие
        a, b = max(start, w_start), min(end, w_end)
        if b > a:
            parts.append((a, b))
    parts.sort()
    total = 0.0
    cur_a = cur_b = None
    for a, b in parts:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += (cur_b - cur_a).total_seconds()
            cur_a, cur_b = a, b
        elif b > cur_b:
            cur_b = b
    if cur_b is not None:
        total += (cur_b - cur_a).total_seconds()
    return total


def day_hours(machine, state_events, D, now):
    """Часы за сутки D: на линии, в ремонте, в простое (SPEC 4.5). Для консервации все три = None."""
    if machine.get('status') == STATUS_CONSERVED:
        return {'line_hours': None, 'repair_hours': None, 'idle_hours': None}
    window = day_window(D, now)
    if window is None:
        return {'line_hours': 0.0, 'repair_hours': 0.0, 'idle_hours': 0.0}
    w_start, w_end = window
    length = max(0.0, (w_end - w_start).total_seconds())
    repair = min(length, _union_seconds(state_events, is_repair, w_start, w_end))
    idle = min(length - repair, _union_seconds(state_events, is_idle, w_start, w_end))
    line = max(0.0, length - repair - idle)
    return {
        'line_hours': round(line / 3600, 2),
        'repair_hours': round(repair / 3600, 2),
        'idle_hours': round(idle / 3600, 2),
    }


def last_repair_seconds(state_events, D):
    """Длительность последнего закрытого ремонта (с наибольшими date_out, time_out, id; date_out <= D)."""
    best = None
    best_key = None
    for ev in state_events:
        if not is_repair(ev['kind']):
            continue
        out = _g(ev, 'date_out')
        if not out or out > D:
            continue
        key = (out, _g(ev, 'time_out') or '', _g(ev, 'id') or 0)
        if best_key is None or key > best_key:
            best, best_key = ev, key
    if best is None:
        return None
    start, end = event_start(best), event_end(best)
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds()))


# --------------------------------------------------------------------------------------
# 4.6 Средняя наработка и прогноз
# --------------------------------------------------------------------------------------

def _fact_points(machine, readings, D):
    """Фактические точки (дата, показание) до D: по одной на дату (наибольшая), немонотонные выброшены."""
    by_date = {}
    for r in readings:
        value = r['reading']
        d = r['date']
        if value is None or not d or d > D or r['source'] == 'auto':
            continue
        if r['kind'] != KIND_MARK and r['reading_estimated']:
            continue
        if d not in by_date or value > by_date[d]:
            by_date[d] = value
    reg = _num(machine.get('reg_reading'))
    reg_date = machine.get('reg_date')
    if reg is not None and reg_date and reg_date <= D:
        if reg_date not in by_date or reg > by_date[reg_date]:
            by_date[reg_date] = reg
    points = []
    for d in sorted(by_date):
        if not points or by_date[d] >= points[-1][1]:
            points.append((d, by_date[d]))
    return points


def rate_and_norm(machine, readings, params, settings, D, remaining=None):
    """Средняя наработка в сутки, суточная норма и прогноз даты ТО (SPEC 4.6).

    remaining — остаток до ТО из to_status (None — прогноза нет).
    Возвращает {rate, rate_source ('fact'|'norm'), rate_days, daily_norm, daily_norm_source
    ('override'|'fact'|'rule'|'default'), forecast_days, forecast_date}.
    """
    unit = params['unit']
    points = _fact_points(machine, readings, D)

    fact_rate = None
    fact_days = None
    if points:
        window_days = int(_setting_num(settings, 'rate_window_days'))
        window_start = add_days(D, -window_days)
        late = points[-1]
        in_window = [p for p in points if p[0] >= window_start]
        early = None
        if in_window:
            early = in_window[0]
            if early == late:
                before = [p for p in points if p[0] < window_start]
                early = before[-1] if before else None
        if early is not None:
            days = days_between(early[0], late[0])
            gain = late[1] - early[1]
            if days >= 3 and gain > 0:
                rate = round_reading(gain / days, unit)
                if rate > 0:
                    fact_rate, fact_days = rate, days

    norm_override = _num(machine.get('norm_override'))
    rule_norm = round_reading(float(params['rule_norm']), unit)
    machine_norm = round_reading(norm_override, unit) if norm_override is not None else rule_norm

    if fact_rate is not None:
        rate, rate_source, rate_days = fact_rate, 'fact', fact_days
    else:
        rate, rate_source, rate_days = machine_norm, 'norm', None

    if norm_override is not None:
        daily_norm, daily_source = machine_norm, 'override'
    elif fact_rate is not None and _setting(settings, 'norm_use_fact'):
        daily_norm, daily_source = fact_rate, 'fact'
    else:
        daily_norm, daily_source = rule_norm, params.get('rule_norm_source') or 'default'

    forecast_days = None
    forecast_date = None
    if remaining is not None and remaining > 0 and rate and rate > 0:
        days = int(math.ceil(remaining / rate - 1e-9))
        if days <= 36500:   # дальше ста лет прогноз не имеет смысла
            forecast_days = days
            forecast_date = add_days(D, days)

    return {
        'rate': _clean(rate), 'rate_source': rate_source, 'rate_days': rate_days,
        'daily_norm': _clean(daily_norm), 'daily_norm_source': daily_source,
        'forecast_days': forecast_days, 'forecast_date': forecast_date,
    }


# --------------------------------------------------------------------------------------
# 4.7 Шины и узлы
# --------------------------------------------------------------------------------------

def _wear_level(wear, settings):
    """'red' | 'yellow' | 'green' по порогам износа из настроек."""
    if wear >= _setting_num(settings, 'tyre_red'):
        return 'red'
    if wear >= _setting_num(settings, 'tyre_yellow'):
        return 'yellow'
    return 'green'


def tyre_view(tyre, machine_reading, settings):
    """Шина с расчётными полями (SPEC 4.7): поля строки + run_eff, left, wear, status, status_label.

    Пробег: текущее показание машины − install_reading, если install_reading задан, иначе поле run.
    """
    view = dict(tyre)
    resource = _num(_g(tyre, 'resource'))
    install = _num(_g(tyre, 'install_reading'))
    if install is not None:
        run_eff = None if machine_reading is None else machine_reading - install
    else:
        run_eff = _num(_g(tyre, 'run'))

    left = wear = None
    status = 'none'
    if resource and run_eff is not None:
        left = resource - run_eff
        wear = run_eff / resource
        status = _wear_level(wear, settings)

    view.update({
        'run_eff': None if run_eff is None else _clean(round(run_eff, 1)),
        'left': None if left is None else _clean(round(left, 1)),
        'wear': None if wear is None else round(wear, 4),
        'status': status,
        'status_label': TYRE_LABELS[status],
    })
    return view


def unit_view(unit_row, machine_reading, settings):
    """Узел с расчётными полями (SPEC 4.7): поля строки + run, left, wear, status, status_label.

    Наработка: текущее показание машины − install_reading; отрицательная -> статус 'check'.
    """
    view = dict(unit_row)
    resource = _num(_g(unit_row, 'resource'))
    install = _num(_g(unit_row, 'install_reading'))
    run = None
    if install is not None and machine_reading is not None:
        run = machine_reading - install

    left = wear = None
    status = 'none'
    if resource and run is not None:
        left = resource - run
        wear = run / resource
        status = 'check' if run < 0 else _wear_level(wear, settings)

    view.update({
        'run': None if run is None else _clean(round(run, 1)),
        'left': None if left is None else _clean(round(left, 1)),
        'wear': None if wear is None else round(wear, 4),
        'status': status,
        'status_label': UNIT_LABELS[status],
    })
    return view


# --------------------------------------------------------------------------------------
# 4.8 Проверка данных
# --------------------------------------------------------------------------------------

def data_warnings(machine, readings, state_events, today):
    """Предупреждения о подозрительных записях машины (SPEC 4.8) -> [{event_id, text}].

    today — сегодняшняя дата 'YYYY-MM-DD' (для проверки «дата в будущем»).
    """
    items = []

    def add(ev, text):
        items.append({'event_id': _g(ev, 'id'),
                      'text': f"{fmt_date_ru(ev['date'])}, {kind_title(ev['kind'])}: {text}"})

    # 1. Счётчик пошёл назад: показание отметки/факта ниже того, что было на более раннюю дату.
    #    ТО-события не проверяются (ТО «задним числом» — штатно). Опора меняется как в 4.2:
    #    отметка задаёт новое значение, прочие события могут только поднять.
    by_date = {}
    for r in readings:
        d = r['date']
        if r['reading'] is None or not d or r['source'] == 'auto':
            continue
        if r['kind'] != KIND_MARK and r['reading_estimated']:
            continue
        by_date.setdefault(d, []).append(r)

    # Показание при регистрации участвует как отметка, только если у него есть дата (reg_date); само не
    # проверяется. Без даты оно не сравнивается: в исходной таблице текущий пробег правят прямо в листе
    # «Машины», и старые записи журнала с меньшим показанием давали ложное «счётчик пошёл назад».
    reg_row = None
    reg = _num(machine.get('reg_reading'))
    if reg is not None and machine.get('reg_date'):
        reg_row = {'id': None, 'date': machine['reg_date'], 'kind': KIND_MARK, 'reading': reg}
        by_date.setdefault(reg_row['date'], []).append(reg_row)

    ref = None       # опорное показание на более раннюю дату
    ref_at = ''      # его дата
    ref_reg = False  # опора — показание при регистрации
    for d in sorted(by_date):
        rows = by_date[d]
        if ref is not None:
            for r in rows:
                if r is reg_row or is_to(r['kind']) or r['reading'] >= ref:
                    continue
                since = f' от {fmt_date_ru(ref_at)}' + (' (при регистрации)' if ref_reg else '')
                add(r, f"показание {_fmt_num(r['reading'])} меньше более раннего "
                       f"{_fmt_num(ref)}{since} — счётчик пошёл назад?")
        day_max = max(r['reading'] for r in rows)
        has_mark = any(r['kind'] == KIND_MARK for r in rows)
        if has_mark or ref is None or day_max > ref:
            ref, ref_at = day_max, d
            ref_reg = any(r is reg_row and r['reading'] == day_max for r in rows)

    # 2. Проверки полных строк событий (ТО, ремонты, простои).
    for ev in state_events:
        kind = ev['kind'] or ''
        d = ev['date']
        out = _g(ev, 'date_out')
        if is_to(kind) and _g(ev, 'reading') is None:
            add(ev, 'не указано показание — это ТО не учитывается в расчёте')
        if out and d and out < d:
            add(ev, f'дата выхода ({fmt_date_ru(out)}) раньше даты заезда')
        elif out and out == d:
            t_in, t_out = parse_time(_g(ev, 'time_in')), parse_time(_g(ev, 'time_out'))
            if t_in is not None and t_out is not None and t_out < t_in:
                add(ev, 'время выхода раньше времени заезда (отрицательная длительность)')
        if _g(ev, 'time_out') and not out:
            add(ev, 'указано время выхода без даты выхода')

    # 3. Дата события в будущем.
    seen = set()
    for ev in list(state_events) + list(readings):
        d = ev['date']
        if d and d > today and ev['id'] not in seen:
            seen.add(ev['id'])
            add(ev, 'дата в будущем')

    return items


# --------------------------------------------------------------------------------------
# Сборка представления машины
# --------------------------------------------------------------------------------------

def build_machine_view(machine, readings, state_events, open_orders, tyres, units,
                       rules, settings, D, now):
    """Расчётная строка машины — MachineView (SPEC, раздел 5) на дату отчёта D.

    machine      — dict строки machines;
    readings     — лёгкие строки событий машины (reading не null или source='auto');
    state_events — полные строки событий Ремонт*/Простой*/ТО-* машины;
    open_orders  — события машины с непустым parts_order и order_done=0 (нужны id, date, parts_order);
    tyres, units — строки шин и узлов машины;
    rules, settings — правила по маркам и настройки; now — текущее время (datetime).

    Кроме полей SPEC отдаёт: rule_norm, reading_base_event_id и
    data_warning_items [{event_id, text}] (для общего списка warnings в /api/fleet).
    """
    machine = dict(machine)          # допускается и sqlite3.Row
    view = dict(machine)

    params = machine_params(machine, rules, settings)
    view.update({
        'unit': params['unit'], 'interval': params['interval'], 'warn': params['warn'],
        'rule_keyword': params['rule_keyword'], 'rule_norm': params['rule_norm'], 'auto': params['auto'],
    })

    cur = current_reading(machine, readings, D, state_events)
    view.update(cur)

    view.update(state_on(machine, state_events, D, now))
    view['last_repair_seconds'] = last_repair_seconds(state_events, D)
    view.update(day_hours(machine, state_events, D, now))

    to = to_status(readings, cur['reading'], params, settings, D)
    view.update(to)

    view.update(rate_and_norm(machine, readings, params, settings, D, to['remaining']))

    # только заявки, внесённые на дату отчёта или раньше (SPEC 4: расчёт на D видит события с date <= D);
    # order_done — текущий флаг, его истории нет
    orders = sorted((ev for ev in open_orders or [] if ev['date'] and ev['date'] <= D),
                    key=lambda ev: (ev['date'], ev['id'] or 0))
    texts = [(ev['parts_order'] or '').strip() for ev in orders]
    view['parts_order'] = '; '.join(t for t in texts if t)

    reading = cur['reading']
    view['tyres_red'] = sum(1 for t in tyres or [] if tyre_view(t, reading, settings)['status'] == 'red')
    view['units_attention'] = sum(1 for u in units or []
                                  if unit_view(u, reading, settings)['status'] in ('red', 'yellow'))

    warnings = data_warnings(machine, readings, state_events, now.date().isoformat())
    view['data_warnings'] = [w['text'] for w in warnings]
    view['data_warning_items'] = warnings
    return view


def fleet_summary(views):
    """Сводка по парку для /api/fleet из списка MachineView (без списанных)."""
    summary = {key: 0 for key in ('total', 'line', 'repair', 'to', 'idle', 'conserved',
                                  'overdue', 'soon', 'ok', 'no_to', 'no_reading',
                                  'tyres_red', 'units_attention')}
    for v in views:
        summary['total'] += 1
        summary[v['state']] += 1
        summary[v['to_status']] += 1
        summary['tyres_red'] += v.get('tyres_red') or 0
        summary['units_attention'] += v.get('units_attention') or 0
    return summary


# --------------------------------------------------------------------------------------
# «Накинуть за день»
# --------------------------------------------------------------------------------------

def days_to_add(machine_view_today, state_events, auto_dates, date_from, date_to, today):
    """Дни периода, за которые машине нужно начислить суточную норму (SPEC «Накинуть за день»).

    machine_view_today — MachineView машины НА СЕГОДНЯ (нужны status, reading_base, reading_base_date);
    state_events — полные строки её событий Ремонт*/Простой*/ТО-*; auto_dates — множество дат,
    на которые у машины уже есть auto-событие; today — сегодняшняя дата 'YYYY-MM-DD'.

    День d начисляется, если: date_from <= d <= date_to, d <= today, у машины есть база показаний и
    d позже даты базы, состояние на d — «на линии» или «на ТО», auto-события на d ещё нет.
    Возвращает (список дат, skip_reason); skip_reason — почему список пуст ('' если дни есть).
    """
    mv = machine_view_today
    status = mv.get('status')
    if status == STATUS_RETIRED:
        return [], 'списана'
    if status == STATUS_CONSERVED:
        return [], 'консервация'
    if mv.get('reading_base') is None:
        return [], 'нет показаний'

    base_date = mv.get('reading_base_date') or ''
    last = min(date_to, today)
    days = []
    reason = ''
    d = date_from
    while d <= last:
        if d <= base_date:
            reason = ('показание внесено сегодня' if base_date == today
                      else f'показание внесено {fmt_date_ru(base_date)}')
        else:
            # ремонт/простой проверяется раньше «уже накинуто»: если ремонт внесли после начисления,
            # накинутое за этот день в показании не учитывается (current_reading), и причина — ремонт
            state, _ = _state_pick(mv, state_events, d)
            if state == 'repair':
                reason = 'в ремонте'
            elif state == 'idle':
                reason = 'простой'
            elif d in auto_dates:
                reason = 'уже накинуто'
            else:
                days.append(d)
        d = add_days(d, 1)

    if days:
        return days, ''
    return [], reason or 'нет дней для начисления'
