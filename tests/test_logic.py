"""Тесты чистых функций расчёта app/logic.py (SPEC, раздел 4: все контрольные примеры 4.1–4.7).

Запуск из корня проекта:  python -m unittest discover -s tests -v
"""

import sys
import unittest
from datetime import date, datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import logic  # noqa: E402

MARK = logic.KIND_MARK
D = '2026-10-02'                       # дата отчёта во всех тестах («сегодня»)
NOW = datetime(2026, 10, 2, 14, 30)    # «сейчас»

SETTINGS = dict(logic.DEFAULT_SETTINGS)

RULES = [
    {'keyword': 'SHACMAN', 'unit': 'км', 'interval': 10000, 'warn': 1000, 'daily_norm': None},
    {'keyword': 'ШАХМАН', 'unit': 'км', 'interval': 10000, 'warn': 1000, 'daily_norm': None},
    {'keyword': 'ШАКМАН', 'unit': 'км', 'interval': 10000, 'warn': 1000, 'daily_norm': None},
    {'keyword': 'SITRAK', 'unit': 'км', 'interval': 15000, 'warn': 1000, 'daily_norm': None},
    {'keyword': 'СИТРАК', 'unit': 'км', 'interval': 15000, 'warn': 1000, 'daily_norm': None},
    {'keyword': 'SANY', 'unit': 'км', 'interval': 15000, 'warn': 1000, 'daily_norm': None},
    {'keyword': 'САНИ', 'unit': 'км', 'interval': 15000, 'warn': 1000, 'daily_norm': None},
    {'keyword': 'VOLVO', 'unit': 'км', 'interval': 15000, 'warn': 1000, 'daily_norm': None},
    {'keyword': 'ВОЛЬВО', 'unit': 'км', 'interval': 15000, 'warn': 1000, 'daily_norm': None},
]

KM = {'unit': 'км', 'interval': 10000, 'warn': 1000, 'rule_norm': 100, 'rule_norm_source': 'default'}
MH = {'unit': 'м/ч', 'interval': 250, 'warn': 50, 'rule_norm': 11, 'rule_norm_source': 'default'}


def machine(**kw):
    """Строка таблицы machines с умолчаниями."""
    m = {
        'id': 1, 'pos': 1, 'garage_no': '101', 'model': 'SHACMAN X3000', 'mtype': 'Самосвал',
        'plate': '', 'vin': '', 'site': '', 'warranty': '', 'photo': '', 'note': '',
        'status': 'В работе', 'reg_reading': None, 'reg_date': None,
        'unit_override': None, 'interval_override': None, 'warn_override': None, 'norm_override': None,
        'created_at': None, 'updated_at': None,
    }
    m.update(kw)
    return m


def rd(id, date, kind, reading, estimated=0, source='manual', updated_at=None):
    """Лёгкая строка события с показанием."""
    return {'id': id, 'date': date, 'kind': kind, 'reading': reading, 'reading_estimated': estimated,
            'delta': None, 'source': source, 'updated_at': updated_at or date + 'T12:00:00'}


def mark(id, date, reading, updated_at=None):
    """Отметка показаний."""
    return rd(id, date, MARK, reading, source='mark', updated_at=updated_at)


def auto(id, date, delta):
    """Накинутое за день."""
    return {'id': id, 'date': date, 'kind': MARK, 'reading': None, 'reading_estimated': 0,
            'delta': delta, 'source': 'auto', 'updated_at': date + 'T20:00:00'}


def ev(id, date, kind, **kw):
    """Полная строка события (ремонт, простой, ТО)."""
    e = {
        'id': id, 'machine_id': 1, 'date': date, 'shift': '', 'kind': kind, 'reading': None,
        'reading_estimated': 0, 'delta': None, 'source': 'manual',
        'time_in': None, 'date_out': None, 'time_out': None,
        'work': '', 'parts_used': '', 'parts_order': '', 'order_done': 0, 'remarks': '', 'who': '',
        'created_at': None, 'updated_at': date + 'T12:00:00',
    }
    e.update(kw)
    return e


class HelpersTest(unittest.TestCase):
    def test_kind_mark_same_in_db(self):
        """Константа вида «отметка» одна и та же в logic и db."""
        from app import db
        self.assertEqual(db.KIND_MARK, logic.KIND_MARK)
        self.assertEqual(logic.KIND_MARK, 'Отметка моточасов')

    def test_parse_date(self):
        self.assertEqual(logic.parse_date('2026-10-02'), date(2026, 10, 2))
        for bad in (None, '', '02.10.2026', '2026-13-01', '20261002', '2026-10-2', 20261002):
            self.assertIsNone(logic.parse_date(bad), bad)

    def test_parse_time(self):
        self.assertEqual(logic.parse_time('09:30'), time(9, 30))
        self.assertEqual(logic.parse_time('9:05'), time(9, 5))
        self.assertEqual(logic.parse_time('23:59:00'), time(23, 59))
        for bad in (None, '', '930', '24:00', '12:60', 'аб:вг'):
            self.assertIsNone(logic.parse_time(bad), bad)

    def test_add_days_and_between(self):
        self.assertEqual(logic.add_days('2026-10-02', 70), '2026-12-11')
        self.assertEqual(logic.add_days('2026-03-01', -1), '2026-02-28')
        self.assertEqual(logic.days_between('2026-09-02', '2026-10-02'), 30)
        self.assertEqual(logic.fmt_date_ru('2026-10-02'), '02.10.2026')

    def test_round_reading(self):
        """км — до целого, м/ч — до 0.1, без «минус нуля»."""
        self.assertEqual(logic.round_reading(7000.4, 'км'), 7000)
        self.assertEqual(logic.round_reading(7000.5, 'км'), 7001)
        self.assertEqual(logic.round_reading(-2000.2, 'км'), -2000)
        self.assertEqual(logic.round_reading(12.34, 'м/ч'), 12.3)
        self.assertEqual(logic.round_reading(1000.3 + 250 - 1250.3, 'м/ч'), 0.0)
        self.assertEqual(str(logic.round_reading(-0.00001, 'м/ч')), '0.0')
        self.assertIsNone(logic.round_reading(None, 'км'))

    def test_kind_helpers(self):
        self.assertTrue(logic.is_to('ТО-2 сезонное'))
        self.assertFalse(logic.is_to('Регл. 2000'))
        self.assertTrue(logic.is_repair('Ремонт двигателя'))
        self.assertTrue(logic.is_idle('Простой'))
        self.assertFalse(logic.is_repair(None))
        self.assertEqual(logic.kind_title(MARK), 'Отметка показаний')
        self.assertEqual(logic.kind_title('Ремонт'), 'Ремонт')


class MachineParamsTest(unittest.TestCase):
    """SPEC 4.1."""

    def test_sany_excavator_is_motohours(self):
        """«SANY SY215», тип «Экскаватор» -> м/ч, 250 (км-правило SANY не применяется)."""
        p = logic.machine_params(machine(model='SANY SY215', mtype='Экскаватор'), RULES, SETTINGS)
        self.assertEqual(p['unit'], 'м/ч')
        self.assertEqual(p['interval'], 250)
        self.assertEqual(p['warn'], 50)
        self.assertIsNone(p['rule_keyword'])
        self.assertEqual(p['rule_norm'], 11)

    def test_sany_dump_truck_is_km(self):
        """«SANY», тип «Самосвал» -> км, 15000."""
        p = logic.machine_params(machine(model='SANY', mtype='Самосвал'), RULES, SETTINGS)
        self.assertEqual((p['unit'], p['interval'], p['warn']), ('км', 15000, 1000))
        self.assertEqual(p['rule_keyword'], 'SANY')
        self.assertEqual(p['rule_norm'], 100)

    def test_kamaz_with_unit_override(self):
        """«КАМАЗ» + unit_override «км» -> км, 10000, порог 1000; auto остаётся без override."""
        p = logic.machine_params(machine(model='КАМАЗ', mtype='', unit_override='км'), RULES, SETTINGS)
        self.assertEqual((p['unit'], p['interval'], p['warn']), ('км', 10000, 1000))
        self.assertIsNone(p['rule_keyword'])
        self.assertEqual(p['auto'], {'unit': 'м/ч', 'interval': 250, 'warn': 50, 'norm': 11, 'rule_keyword': None})

    def test_keyword_case_insensitive_and_first_rule_wins(self):
        p = logic.machine_params(machine(model='Самосвал шахман x3000', mtype=''), RULES, SETTINGS)
        self.assertEqual((p['unit'], p['interval'], p['rule_keyword']), ('км', 10000, 'ШАХМАН'))
        p = logic.machine_params(machine(model='SITRAK C7H (кабина VOLVO)', mtype='Тягач'), RULES, SETTINGS)
        self.assertEqual(p['rule_keyword'], 'SITRAK')

    def test_mh_word_in_model(self):
        """Слово моточасной техники в модели (тип пуст) тоже переводит на м/ч."""
        p = logic.machine_params(machine(model='Экскаватор SANY SY365', mtype=''), RULES, SETTINGS)
        self.assertEqual((p['unit'], p['interval']), ('м/ч', 250))

    def test_no_rule_defaults_to_motohours(self):
        p = logic.machine_params(machine(model='ДЗ-98', mtype=''), RULES, SETTINGS)
        self.assertEqual((p['unit'], p['interval'], p['warn'], p['rule_norm']), ('м/ч', 250, 50, 11))

    def test_overrides_resolved_separately(self):
        """Каждый параметр разрешается отдельно: свой интервал не мешает порогу правила и наоборот."""
        p = logic.machine_params(machine(model='SANY', interval_override=20000), RULES, SETTINGS)
        self.assertEqual((p['unit'], p['interval'], p['warn']), ('км', 20000, 1000))
        p = logic.machine_params(machine(model='SANY', warn_override=500), RULES, SETTINGS)
        self.assertEqual((p['interval'], p['warn']), (15000, 500))
        self.assertEqual(p['auto']['warn'], 1000)

    def test_km_warn_is_ten_percent_without_rule_warn(self):
        """км без порога в правиле: порог = round(0.1 × интервал)."""
        p = logic.machine_params(machine(model='КАМАЗ', unit_override='км', interval_override=12000),
                                 RULES, SETTINGS)
        self.assertEqual(p['warn'], 1200)
        rules = [{'keyword': 'MAN', 'unit': 'км', 'interval': 25000, 'warn': None, 'daily_norm': 180}]
        p = logic.machine_params(machine(model='MAN TGS'), rules, SETTINGS)
        self.assertEqual((p['interval'], p['warn'], p['rule_norm'], p['rule_norm_source']),
                         (25000, 2500, 180, 'rule'))

    def test_override_to_km_on_excavator_uses_km_rule(self):
        """Экскаватор SANY, вручную переведённый на км: интервал берётся из км-правила SANY."""
        p = logic.machine_params(machine(model='SANY SY215', mtype='Экскаватор', unit_override='км'),
                                 RULES, SETTINGS)
        self.assertEqual((p['unit'], p['interval'], p['warn']), ('км', 15000, 1000))
        self.assertEqual(p['auto']['unit'], 'м/ч')

    def test_override_to_motohours_ignores_km_rule(self):
        """Самосвал SANY, вручную переведённый на м/ч: км-правило не подходит по единице -> умолчания м/ч."""
        p = logic.machine_params(machine(model='SANY', unit_override='м/ч'), RULES, SETTINGS)
        self.assertEqual((p['unit'], p['interval'], p['warn'], p['rule_norm']), ('м/ч', 250, 50, 11))
        self.assertIsNone(p['rule_keyword'])
        self.assertEqual(p['auto']['unit'], 'км')
        self.assertEqual(p['auto']['rule_keyword'], 'SANY')


class CurrentReadingTest(unittest.TestCase):
    """SPEC 4.2 — восемь обязательных примеров и крайние случаи."""

    def test_1_mark_then_lower_to(self):
        """(1) отметка 153000 от 01.10 + ТО 150000 от 02.10 -> 153000."""
        r = logic.current_reading(machine(), [mark(1, '2026-10-01', 153000),
                                              rd(2, '2026-10-02', 'ТО-2', 150000)], D)
        self.assertEqual(r['reading'], 153000)
        self.assertEqual(r['reading_base_date'], '2026-10-01')
        self.assertFalse(r['reading_estimated'])

    def test_2_mark_then_lower_repair(self):
        """(2) отметка 152000 от 01.09 + ремонт 148000 от 05.09 -> 152000."""
        r = logic.current_reading(machine(), [mark(1, '2026-09-01', 152000),
                                              rd(2, '2026-09-05', 'Ремонт', 148000)], D)
        self.assertEqual(r['reading'], 152000)
        self.assertEqual(r['reading_base_date'], '2026-09-01')

    def test_3_mark_then_higher_repair(self):
        """(3) отметка 152000 от 01.09 + ремонт 153500 от 05.09 -> 153500, база 05.09."""
        r = logic.current_reading(machine(), [mark(1, '2026-09-01', 152000),
                                              rd(2, '2026-09-05', 'Ремонт', 153500)], D)
        self.assertEqual(r['reading'], 153500)
        self.assertEqual(r['reading_base'], 153500)
        self.assertEqual(r['reading_base_date'], '2026-09-05')
        self.assertEqual(r['reading_base_event_id'], 2)

    def test_4_auto_of_same_day_not_counted(self):
        """(4) отметка 150000 от 01.10, auto +100 от 01.10 и +100 от 02.10 -> 150100."""
        rows = [mark(1, '2026-10-01', 150000), auto(2, '2026-10-01', 100), auto(3, '2026-10-02', 100)]
        r = logic.current_reading(machine(), rows, D)
        self.assertEqual(r['reading'], 150100)
        self.assertEqual(r['reading_base'], 150000)
        self.assertEqual(r['reading_auto_add'], 100)
        self.assertTrue(r['reading_estimated'])

    def test_5_later_mark_overrides_estimate(self):
        """(5) затем отметка 149900 от 02.10 -> 149900, без «≈»."""
        rows = [mark(1, '2026-10-01', 150000), auto(2, '2026-10-01', 100), auto(3, '2026-10-02', 100),
                mark(4, '2026-10-02', 149900)]
        r = logic.current_reading(machine(), rows, D)
        self.assertEqual(r['reading'], 149900)
        self.assertFalse(r['reading_estimated'])
        self.assertEqual(r['reading_auto_add'], 0)
        self.assertEqual(r['reading_base_date'], '2026-10-02')

    def test_6_reg_without_date_and_lower_repair(self):
        """(6) reg_reading 47415 без даты + ремонт 43520 -> 47415."""
        r = logic.current_reading(machine(reg_reading=47415), [rd(1, '2026-09-05', 'Ремонт', 43520)], D)
        self.assertEqual(r['reading'], 47415)
        self.assertIsNone(r['reading_base_date'])
        self.assertIsNone(r['reading_base_event_id'])

    def test_7_reg_without_date_and_mark(self):
        """(7) reg 47415 без даты + отметка 43520 -> 43520."""
        r = logic.current_reading(machine(reg_reading=47415), [mark(1, '2026-09-05', 43520)], D)
        self.assertEqual(r['reading'], 43520)
        self.assertEqual(r['reading_base_date'], '2026-09-05')

    def test_8_only_auto_gives_none(self):
        """(8) только auto без базы -> null."""
        r = logic.current_reading(machine(), [auto(1, '2026-10-01', 100), auto(2, '2026-10-02', 100)], D)
        self.assertIsNone(r['reading'])
        self.assertIsNone(r['reading_base'])
        self.assertFalse(r['reading_estimated'])

    def test_reg_without_date_counts_all_auto(self):
        """База с датой «раньше всех» — считается всё накинутое."""
        rows = [auto(1, '2026-10-01', 11), auto(2, '2026-10-02', 11)]
        r = logic.current_reading(machine(reg_reading=1000.3), rows, D)
        self.assertEqual(r['reading'], 1022.3)
        self.assertTrue(r['reading_estimated'])
        self.assertIsNone(r['reading_base_date'])

    def test_reg_with_date(self):
        """reg_reading с датой — отметка на эту дату; reg_date позже D не учитывается."""
        m = machine(reg_reading=5000, reg_date='2026-09-20')
        r = logic.current_reading(m, [mark(1, '2026-09-10', 4000), auto(2, '2026-09-25', 100)], D)
        self.assertEqual((r['reading'], r['reading_base'], r['reading_base_date']), (5100, 5000, '2026-09-20'))
        r = logic.current_reading(m, [mark(1, '2026-09-10', 4000)], '2026-09-15')
        self.assertEqual(r['reading'], 4000)
        r = logic.current_reading(machine(reg_reading=5000, reg_date='2026-12-01'), [], D)
        self.assertIsNone(r['reading'])

    def test_events_after_report_date_ignored(self):
        """На дату D используются только события с date <= D."""
        rows = [mark(1, '2026-09-01', 150000), mark(2, '2026-10-01', 153000), auto(3, '2026-10-02', 100)]
        r = logic.current_reading(machine(), rows, '2026-09-15')
        self.assertEqual(r['reading'], 150000)

    def test_same_date_marks_latest_update_wins(self):
        """При равных датах отметок берётся изменённая позже (updated_at), затем больший id."""
        rows = [mark(1, '2026-10-01', 153000, updated_at='2026-10-01T18:00:00'),
                mark(2, '2026-10-01', 152000, updated_at='2026-10-01T09:00:00')]
        self.assertEqual(logic.current_reading(machine(), rows, D)['reading'], 153000)
        rows = [mark(1, '2026-10-01', 153000, updated_at='2026-10-01T09:00:00'),
                mark(2, '2026-10-01', 152000, updated_at='2026-10-01T09:00:00')]
        self.assertEqual(logic.current_reading(machine(), rows, D)['reading'], 152000)

    def test_estimated_fact_is_not_base(self):
        """Оценочное показание события (reading_estimated=1) базой не становится."""
        rows = [mark(1, '2026-09-01', 150000), rd(2, '2026-09-20', 'ТО-1', 152000, estimated=1)]
        r = logic.current_reading(machine(), rows, D)
        self.assertEqual((r['reading'], r['reading_base_date']), (150000, '2026-09-01'))
        r = logic.current_reading(machine(), [rd(2, '2026-09-20', 'ТО-1', 152000, estimated=1)], D)
        self.assertIsNone(r['reading'])

    def test_facts_only_without_marks(self):
        """Нет отметок — база = наибольший факт; при равных значениях — более поздняя дата."""
        rows = [rd(1, '2026-09-01', 'Ремонт', 1500), rd(2, '2026-09-10', 'ТО-1', 1500),
                rd(3, '2026-09-05', 'Ремонт', 1400), auto(4, '2026-09-08', 11), auto(5, '2026-09-12', 11)]
        r = logic.current_reading(machine(), rows, D)
        self.assertEqual((r['reading_base'], r['reading_base_date'], r['reading']), (1500, '2026-09-10', 1511))

    def test_fact_before_mark_does_not_override(self):
        """Факт с датой раньше отметки отметку не перебивает (отметка — явное исправление вниз)."""
        rows = [rd(1, '2026-09-01', 'Ремонт', 160000), mark(2, '2026-09-10', 153000)]
        self.assertEqual(logic.current_reading(machine(), rows, D)['reading'], 153000)


class ToStatusTest(unittest.TestCase):
    """SPEC 4.3 — вся таблица контрольных примеров."""

    def status(self, last_to, now_reading, params=KM, kind='ТО-1'):
        rows = [rd(1, '2026-09-01', kind, last_to)] if last_to is not None else []
        return logic.to_status(rows, now_reading, params, SETTINGS, D)

    def test_ok(self):
        """ТО 150000, сейчас 153000, 10000/1000 -> следующее на 160000, осталось 7000, в норме."""
        s = self.status(150000, 153000)
        self.assertEqual((s['next_to_at'], s['remaining'], s['to_status']), (160000, 7000, 'ok'))
        self.assertEqual((s['overdue_by'], s['missed']), (0, 0))
        self.assertEqual(s['to_count'], 1)
        self.assertEqual(s['last_to'], {'event_id': 1, 'kind': 'ТО-1', 'reading': 150000,
                                          'date': '2026-09-01', 'date_unknown': False})
        self.assertEqual(s['to_status_label'], logic.TO_STATUS_LABELS['ok'])

    def test_last_to_date_unknown(self):
        """«Последнее ТО» без даты: last_to.date_unknown = True, расчёт не меняется."""
        row = dict(rd(1, '2026-09-01', 'ТО-1', 150000), date_unknown=1)
        s = logic.to_status([row], 153000, KM, SETTINGS, D)
        self.assertIs(s['last_to']['date_unknown'], True)
        self.assertEqual((s['next_to_at'], s['remaining'], s['to_status']), (160000, 7000, 'ok'))

    def test_soon(self):
        """ТО 125000, сейчас 134200 -> осталось 800, скоро."""
        s = self.status(125000, 134200)
        self.assertEqual((s['remaining'], s['to_status']), (800, 'soon'))
        self.assertEqual(s['missed'], 0)

    def test_soon_boundary(self):
        """Остаток ровно равен порогу -> «скоро»; на единицу больше -> «в норме»."""
        self.assertEqual(self.status(125000, 134000)['to_status'], 'soon')
        self.assertEqual(self.status(125000, 133999)['to_status'], 'ok')

    def test_overdue_at_zero(self):
        """ТО 125000, сейчас 135000 -> осталось 0, просрочено, пропущено 1."""
        s = self.status(125000, 135000)
        self.assertEqual((s['remaining'], s['to_status'], s['missed'], s['overdue_by']), (0, 'overdue', 1, 0))

    def test_overdue_by_2000(self):
        """ТО 150000, сейчас 162000 -> просрочено на 2000, пропущено 1."""
        s = self.status(150000, 162000)
        self.assertEqual((s['to_status'], s['overdue_by'], s['missed'], s['remaining']),
                         ('overdue', 2000, 1, -2000))

    def test_overdue_by_12500_missed_two(self):
        """ТО 150000, сейчас 172500 -> просрочено на 12500, пропущено 2."""
        s = self.status(150000, 172500)
        self.assertEqual((s['overdue_by'], s['missed']), (12500, 2))

    def test_motohours_no_float_error(self):
        """ТО 1000.3 м/ч, сейчас 1250.3, 250/50 -> осталось 0.0, просрочено (без плавающей ошибки)."""
        s = self.status(1000.3, 1250.3, MH)
        self.assertEqual(s['remaining'], 0.0)
        self.assertEqual(str(s['remaining']), '0.0')
        self.assertEqual((s['to_status'], s['missed'], s['next_to_at']), ('overdue', 1, 1250.3))
        self.assertEqual(str(s['overdue_by']), '0.0')
        s = self.status(1000.3, 1250.2, MH)
        self.assertEqual((s['remaining'], s['to_status']), (0.1, 'soon'))

    def test_next_kind_cycle(self):
        """Последнее ТО-3 -> следующее ТО-1; «ТО-2 сезонное» -> ТО-3."""
        self.assertEqual(self.status(150000, 153000, kind='ТО-3')['next_to_kind'], 'ТО-1')
        self.assertEqual(self.status(150000, 153000, kind='ТО-2 сезонное')['next_to_kind'], 'ТО-3')
        self.assertEqual(self.status(150000, 153000, kind='ТО-1')['next_to_kind'], 'ТО-2')

    def test_no_to(self):
        """Нет ТО, показание 5000 -> «внесите ТО», следующее ТО-1."""
        s = self.status(None, 5000)
        self.assertEqual((s['to_status'], s['next_to_kind'], s['to_count']), ('no_to', 'ТО-1', 0))
        self.assertIsNone(s['last_to'])
        self.assertIsNone(s['remaining'])
        self.assertIsNone(s['next_to_at'])
        self.assertEqual((s['overdue_by'], s['missed']), (0, 0))

    def test_no_reading_comes_first(self):
        """Нет показаний — статус «нет показаний», даже если ТО не внесено."""
        self.assertEqual(self.status(None, None)['to_status'], 'no_reading')
        s = logic.to_status([rd(1, '2026-09-01', 'ТО-1', 150000, estimated=1)], None, KM, SETTINGS, D)
        self.assertEqual((s['to_status'], s['to_count'], s['next_to_at']), ('no_reading', 1, 160000))
        self.assertIsNone(s['remaining'])

    def test_last_to_is_latest_by_date(self):
        """Последнее ТО — с самой поздней датой (не с наибольшим показанием); при равных датах — большее показание."""
        rows = [rd(1, '2026-08-01', 'ТО-1', 140000), rd(2, '2026-09-01', 'ТО-2', 150000),
                rd(3, '2026-07-01', 'ТО-3', 155000)]
        s = logic.to_status(rows, 153000, KM, SETTINGS, D)
        self.assertEqual((s['last_to']['event_id'], s['next_to_kind'], s['to_count']), (2, 'ТО-3', 3))
        rows = [rd(1, '2026-09-01', 'ТО-1', 140000), rd(2, '2026-09-01', 'ТО-2', 150000)]
        self.assertEqual(logic.to_status(rows, 153000, KM, SETTINGS, D)['last_to']['event_id'], 2)

    def test_to_without_reading_and_future_ignored(self):
        """ТО без показания и ТО с датой позже D в расчёт не идут; оценочное показание ТО годится."""
        rows = [rd(1, '2026-09-01', 'ТО-1', None), rd(2, '2026-11-01', 'ТО-2', 150000)]
        self.assertEqual(logic.to_status(rows, 153000, KM, SETTINGS, D)['to_status'], 'no_to')
        rows = [rd(1, '2026-09-01', 'ТО-1', 150000, estimated=1)]
        s = logic.to_status(rows, 153000, KM, SETTINGS, D)
        self.assertEqual((s['to_status'], s['remaining']), ('ok', 7000))

    def test_next_to_kind_function(self):
        cycle = ['ТО-1', 'ТО-2', 'ТО-3']
        self.assertEqual(logic.next_to_kind('ТО-3', cycle), 'ТО-1')
        self.assertEqual(logic.next_to_kind('ТО-2 сезонное', cycle), 'ТО-3')
        self.assertEqual(logic.next_to_kind(None, cycle), 'ТО-1')
        self.assertEqual(logic.next_to_kind('ТО-9', cycle), 'ТО-1')
        self.assertEqual(logic.next_to_kind('ТО-10', ['ТО-1', 'ТО-10', 'ТО-2']), 'ТО-2')   # самое длинное совпадение
        self.assertEqual(logic.next_to_kind('ТО-1', ['ТО-1']), 'ТО-1')
        self.assertEqual(logic.next_to_kind('ТО-1', []), 'ТО-2')                          # пустой круг -> умолчание


class StateTest(unittest.TestCase):
    """SPEC 4.4."""

    def state(self, events, d=D, **mkw):
        return logic.state_on(machine(**mkw), events, d, NOW)

    def test_line_by_default(self):
        s = self.state([])
        self.assertEqual((s['state'], s['state_label']), ('line', logic.STATE_LABELS['line']))
        self.assertIsNone(s['state_event_id'])
        self.assertEqual(s['state_work'], '')
        self.assertIsNone(s['state_since'])
        self.assertIsNone(s['state_seconds'])

    def test_conserved_first(self):
        """Консервация важнее открытого ремонта."""
        s = self.state([ev(1, '2026-10-01', 'Ремонт')], status='На консервации')
        self.assertEqual(s['state'], 'conserved')
        self.assertIsNone(s['state_event_id'])

    def test_open_repair(self):
        s = self.state([ev(5, '2026-10-02', 'Ремонт', time_in='08:00', work='Замена КПП')])
        self.assertEqual((s['state'], s['state_event_id'], s['state_work']), ('repair', 5, 'Замена КПП'))
        self.assertEqual(s['state_since'], '2026-10-02T08:00:00')
        self.assertEqual(s['state_seconds'], 6 * 3600 + 30 * 60)

    def test_repair_closed_today_is_line(self):
        """Ремонт с date_out == D уже не «на ремонте» (как в таблице: вышла позже D)."""
        e = ev(1, '2026-10-01', 'Ремонт', date_out='2026-10-02', time_out='10:00')
        self.assertEqual(self.state([e])['state'], 'line')

    def test_repair_closed_later_is_repair_on_earlier_date(self):
        """На прошлую дату машина была в ремонте; время — полная длительность закрытого ремонта."""
        e = ev(1, '2026-09-30', 'Ремонт', time_in='20:00', date_out='2026-10-01', time_out='06:00')
        s = self.state([e], d='2026-09-30')
        self.assertEqual((s['state'], s['state_seconds']), ('repair', 10 * 3600))
        self.assertEqual(self.state([e], d='2026-10-01')['state'], 'line')
        self.assertEqual(self.state([e], d='2026-09-29')['state'], 'line')    # ремонт ещё не начался

    def test_to_today(self):
        s = self.state([ev(3, D, 'ТО-2', reading=150000, work='Масло, фильтры')])
        self.assertEqual((s['state'], s['state_event_id'], s['state_work']), ('to', 3, 'Масло, фильтры'))
        self.assertEqual(s['state_since'], '2026-10-02T00:00:00')
        self.assertIsNone(s['state_seconds'])

    def test_baseline_to_today_is_line(self):
        """ТО, внесённое как база (source='baseline') сегодняшней датой, машину «на ТО» не ставит."""
        self.assertEqual(self.state([ev(3, D, 'ТО-2', reading=150000, source='baseline')])['state'], 'line')

    def test_to_yesterday_is_line(self):
        self.assertEqual(self.state([ev(3, '2026-10-01', 'ТО-2', reading=150000)])['state'], 'line')

    def test_idle(self):
        s = self.state([ev(4, '2026-09-28', 'Простой', work='Нет водителя')])
        self.assertEqual((s['state'], s['state_work'], s['state_since']),
                         ('idle', 'Нет водителя', '2026-09-28T00:00:00'))
        self.assertEqual(s['state_seconds'], 4 * 86400 + 14 * 3600 + 30 * 60)

    def test_priority_repair_to_idle(self):
        events = [ev(1, '2026-09-28', 'Простой'), ev(2, D, 'ТО-1', reading=1000), ev(3, '2026-10-01', 'Ремонт')]
        self.assertEqual(self.state(events)['state'], 'repair')
        self.assertEqual(self.state(events[:2])['state'], 'to')
        self.assertEqual(self.state(events[:1])['state'], 'idle')

    def test_several_repairs_latest_date_then_id(self):
        events = [ev(1, '2026-09-28', 'Ремонт', work='старый'), ev(2, '2026-10-01', 'Ремонт', work='новый'),
                  ev(3, '2026-10-01', 'Ремонт двигателя', work='самый новый')]
        s = self.state(events)
        self.assertEqual((s['state_event_id'], s['state_work']), (3, 'самый новый'))

    def test_future_repair_not_counted(self):
        self.assertEqual(self.state([ev(1, '2026-10-05', 'Ремонт')])['state'], 'line')


class DurationTest(unittest.TestCase):
    """SPEC 4.5."""

    def hours(self, events, d, now=NOW, **mkw):
        h = logic.day_hours(machine(**mkw), events, d, now)
        return h['line_hours'], h['repair_hours'], h['idle_hours']

    # --- длительность события ---

    def test_event_duration_closed(self):
        e = ev(1, '2026-09-30', 'Ремонт', time_in='20:00', date_out='2026-10-01', time_out='06:00')
        self.assertEqual(logic.event_duration(e, NOW),
                         {'duration_seconds': 36000, 'open': False, 'duration_error': False})

    def test_event_duration_without_times(self):
        """Нет времени — 00:00 дня заезда и 00:00 дня выхода."""
        e = ev(1, '2026-10-01', 'Ремонт', date_out='2026-10-02')
        self.assertEqual(logic.event_duration(e, NOW)['duration_seconds'], 86400)

    def test_event_duration_open_repair(self):
        e = ev(1, '2026-10-02', 'Ремонт', time_in='08:00')
        self.assertEqual(logic.event_duration(e, NOW),
                         {'duration_seconds': 23400, 'open': True, 'duration_error': False})
        e = ev(1, '2026-10-01', 'Простой')
        self.assertEqual(logic.event_duration(e, NOW)['duration_seconds'], 86400 + 14 * 3600 + 1800)

    def test_event_duration_other_kind_without_out(self):
        for kind in ('Заметка', 'ТО-1', MARK):
            self.assertEqual(logic.event_duration(ev(1, '2026-10-01', kind), NOW),
                             {'duration_seconds': None, 'open': False, 'duration_error': False})

    def test_event_duration_negative(self):
        e = ev(1, '2026-10-01', 'Ремонт', time_in='09:00', date_out='2026-10-01', time_out='07:00')
        d = logic.event_duration(e, NOW)
        self.assertTrue(d['duration_error'])
        self.assertFalse(d['open'])
        self.assertIsNone(d['duration_seconds'])

    # --- часы за сутки ---

    def test_repair_across_midnight(self):
        """Ремонт через полночь: 30.09 20:00 -> 01.10 06:00."""
        e = [ev(1, '2026-09-30', 'Ремонт', time_in='20:00', date_out='2026-10-01', time_out='06:00')]
        self.assertEqual(self.hours(e, '2026-09-30'), (20.0, 4.0, 0.0))
        self.assertEqual(self.hours(e, '2026-10-01'), (18.0, 6.0, 0.0))
        self.assertEqual(self.hours(e, '2026-10-02'), (14.5, 0.0, 0.0))

    def test_open_repair_today(self):
        """Открытый ремонт сегодня с 08:00, сейчас 14:30 -> окно 14.5 ч, ремонт 6.5 ч, на линии 8 ч."""
        e = [ev(1, '2026-10-02', 'Ремонт', time_in='08:00')]
        self.assertEqual(self.hours(e, D), (8.0, 6.5, 0.0))

    def test_open_repair_past_days(self):
        """Открытый ремонт с 30.09 12:00: 30.09 — 12 ч, 01.10 — все сутки, сегодня — всё окно."""
        e = [ev(1, '2026-09-30', 'Ремонт', time_in='12:00')]
        self.assertEqual(self.hours(e, '2026-09-30'), (12.0, 12.0, 0.0))
        self.assertEqual(self.hours(e, '2026-10-01'), (0.0, 24.0, 0.0))
        self.assertEqual(self.hours(e, D), (0.0, 14.5, 0.0))
        self.assertEqual(self.hours(e, '2026-09-29'), (24.0, 0.0, 0.0))

    def test_overlapping_repairs_are_united(self):
        """Пересекающиеся ремонты 08–12 и 10–14 дают 6 ч, а не 8."""
        e = [ev(1, '2026-10-01', 'Ремонт', time_in='08:00', date_out='2026-10-01', time_out='12:00'),
             ev(2, '2026-10-01', 'Ремонт', time_in='10:00', date_out='2026-10-01', time_out='14:00')]
        self.assertEqual(self.hours(e, '2026-10-01'), (18.0, 6.0, 0.0))

    def test_separate_repairs_are_summed(self):
        e = [ev(1, '2026-10-01', 'Ремонт', time_in='08:00', date_out='2026-10-01', time_out='10:00'),
             ev(2, '2026-10-01', 'Ремонт двигателя', time_in='15:00', date_out='2026-10-01', time_out='18:30')]
        self.assertEqual(self.hours(e, '2026-10-01'), (18.5, 5.5, 0.0))

    def test_today_past_future_without_events(self):
        """Сегодня — окно до «сейчас», прошлая дата — 24 ч, будущая — пустое окно."""
        self.assertEqual(self.hours([], D), (14.5, 0.0, 0.0))
        self.assertEqual(self.hours([], '2026-10-01'), (24.0, 0.0, 0.0))
        self.assertEqual(self.hours([], '2026-10-03'), (0.0, 0.0, 0.0))
        e = [ev(1, '2026-10-01', 'Ремонт')]
        self.assertEqual(self.hours(e, '2026-10-03'), (0.0, 0.0, 0.0))

    def test_idle_hours(self):
        """Простой ограничен остатком окна после ремонта."""
        e = [ev(1, '2026-10-01', 'Ремонт', time_in='08:00', date_out='2026-10-01', time_out='12:00'),
             ev(2, '2026-10-01', 'Простой', time_in='14:00', date_out='2026-10-01', time_out='20:00')]
        self.assertEqual(self.hours(e, '2026-10-01'), (14.0, 4.0, 6.0))
        e = [ev(1, '2026-10-01', 'Ремонт', time_in='08:00', date_out='2026-10-01', time_out='12:00'),
             ev(2, '2026-09-29', 'Простой')]
        self.assertEqual(self.hours(e, '2026-10-01'), (0.0, 4.0, 20.0))

    def test_conserved_hours_are_none(self):
        e = [ev(1, '2026-10-01', 'Ремонт')]
        self.assertEqual(self.hours(e, D, status='На консервации'), (None, None, None))

    def test_bad_interval_ignored(self):
        """Событие с отрицательной длительностью часов не даёт."""
        e = [ev(1, '2026-10-01', 'Ремонт', time_in='09:00', date_out='2026-10-01', time_out='07:00')]
        self.assertEqual(self.hours(e, '2026-10-01'), (24.0, 0.0, 0.0))

    def test_day_window(self):
        self.assertEqual(logic.day_window(D, NOW), (datetime(2026, 10, 2), NOW))
        self.assertEqual(logic.day_window('2026-10-01', NOW), (datetime(2026, 10, 1), datetime(2026, 10, 2)))
        self.assertIsNone(logic.day_window('2026-10-03', NOW))

    # --- последний ремонт ---

    def test_last_repair_seconds(self):
        events = [
            ev(1, '2026-09-10', 'Ремонт', time_in='08:00', date_out='2026-09-10', time_out='10:00'),
            ev(2, '2026-09-20', 'Ремонт', time_in='08:00', date_out='2026-09-21', time_out='09:00'),
            ev(3, '2026-10-01', 'Ремонт', time_in='08:00'),                                  # открыт
            ev(4, '2026-09-25', 'Простой', date_out='2026-09-30'),                           # не ремонт
        ]
        self.assertEqual(logic.last_repair_seconds(events, D), 25 * 3600)
        self.assertEqual(logic.last_repair_seconds(events, '2026-09-15'), 2 * 3600)
        self.assertIsNone(logic.last_repair_seconds(events, '2026-09-01'))
        self.assertIsNone(logic.last_repair_seconds([], D))


class RateTest(unittest.TestCase):
    """SPEC 4.6."""

    def rate(self, rows, params=KM, remaining=None, settings=SETTINGS, **mkw):
        return logic.rate_and_norm(machine(**mkw), rows, params, settings, D, remaining)

    def test_fact_rate_and_forecast(self):
        """Два факта за 30 суток, +3000 км -> 100 км/сут; остаток 7000 -> 70 дней, 11.12.2026."""
        rows = [mark(1, '2026-09-02', 150000), mark(2, '2026-10-02', 153000)]
        r = self.rate(rows, remaining=7000)
        self.assertEqual((r['rate'], r['rate_source'], r['rate_days']), (100, 'fact', 30))
        self.assertEqual((r['daily_norm'], r['daily_norm_source']), (100, 'fact'))
        self.assertEqual((r['forecast_days'], r['forecast_date']), (70, '2026-12-11'))

    def test_single_fact_and_reg_without_date_gives_norm(self):
        """Единственный факт + reg без даты -> средней нет, берётся норма."""
        r = self.rate([mark(1, '2026-10-01', 153000)], remaining=7000, reg_reading=100000)
        self.assertEqual((r['rate'], r['rate_source'], r['rate_days']), (100, 'norm', None))
        self.assertEqual((r['daily_norm'], r['daily_norm_source']), (100, 'default'))
        self.assertEqual((r['forecast_days'], r['forecast_date']), (70, '2026-12-11'))

    def test_reg_with_date_is_a_point(self):
        r = self.rate([mark(1, '2026-10-02', 153000)], reg_reading=150600, reg_date='2026-09-02')
        self.assertEqual((r['rate'], r['rate_source'], r['rate_days']), (80, 'fact', 30))

    def test_less_than_three_days_gives_norm(self):
        rows = [mark(1, '2026-09-30', 150000), mark(2, '2026-10-02', 150500)]
        self.assertEqual(self.rate(rows)['rate_source'], 'norm')
        rows = [mark(1, '2026-09-29', 150000), mark(2, '2026-10-02', 150600)]
        r = self.rate(rows)
        self.assertEqual((r['rate'], r['rate_source'], r['rate_days']), (200, 'fact', 3))

    def test_zero_gain_gives_norm(self):
        rows = [mark(1, '2026-09-02', 150000), mark(2, '2026-10-02', 150000)]
        self.assertEqual(self.rate(rows)['rate_source'], 'norm')

    def test_early_point_before_window(self):
        """В окне только последняя точка -> ранней становится последняя точка до окна."""
        rows = [mark(1, '2026-06-24', 100000), mark(2, '2026-09-22', 109000)]
        r = self.rate(rows)
        self.assertEqual((r['rate'], r['rate_source'], r['rate_days']), (100, 'fact', 90))

    def test_earliest_point_in_window_is_used(self):
        """Ранняя точка — самая ранняя В ОКНЕ (старые показания на среднюю не влияют)."""
        rows = [mark(1, '2026-01-01', 100000), mark(2, '2026-09-02', 150000),
                mark(3, '2026-09-17', 151000), mark(4, '2026-10-02', 153000)]
        r = self.rate(rows)
        self.assertEqual((r['rate'], r['rate_days']), (100, 30))

    def test_no_points_in_window_gives_norm(self):
        rows = [mark(1, '2026-06-01', 100000), mark(2, '2026-06-20', 105000)]
        self.assertEqual(self.rate(rows)['rate_source'], 'norm')

    def test_one_point_per_date_max_and_non_monotonic_dropped(self):
        """На дату берётся наибольшее значение; точки меньше предыдущей оставленной выбрасываются."""
        rows = [mark(1, '2026-09-02', 150000), rd(2, '2026-09-02', 'Ремонт', 150300),
                mark(3, '2026-09-20', 149000),                      # счётчик «назад» — выброшена
                rd(4, '2026-10-02', 'ТО-1', 153300)]
        r = self.rate(rows)
        self.assertEqual((r['rate'], r['rate_days']), (100, 30))
        # последняя точка ниже предыдущей — выброшена, прирост считается до 20.09
        rows = [mark(1, '2026-09-02', 150000), mark(2, '2026-09-20', 151800), mark(3, '2026-10-02', 151000)]
        r = self.rate(rows)
        self.assertEqual((r['rate'], r['rate_days']), (100, 18))

    def test_auto_and_estimated_are_not_points(self):
        rows = [mark(1, '2026-09-02', 150000), auto(2, '2026-09-10', 100),
                rd(3, '2026-10-02', 'ТО-1', 153000, estimated=1)]
        self.assertEqual(self.rate(rows)['rate_source'], 'norm')

    def test_norm_sources(self):
        """Источник суточной нормы: override -> fact -> rule -> default."""
        rows = [mark(1, '2026-09-02', 150000), mark(2, '2026-10-02', 153000)]
        r = self.rate(rows, norm_override=250)
        self.assertEqual((r['daily_norm'], r['daily_norm_source']), (250, 'override'))
        self.assertEqual((r['rate'], r['rate_source']), (100, 'fact'))      # средняя всё равно фактическая

        r = self.rate([], norm_override=250, remaining=1000)
        self.assertEqual((r['rate'], r['rate_source'], r['daily_norm'], r['daily_norm_source']),
                         (250, 'norm', 250, 'override'))
        self.assertEqual(r['forecast_days'], 4)

        no_fact = dict(SETTINGS, norm_use_fact=False)
        r = self.rate(rows, settings=no_fact)
        self.assertEqual((r['daily_norm'], r['daily_norm_source'], r['rate_source']), (100, 'default', 'fact'))

        rule_params = dict(KM, rule_norm=180, rule_norm_source='rule')
        r = self.rate([], params=rule_params)
        self.assertEqual((r['daily_norm'], r['daily_norm_source'], r['rate']), (180, 'rule', 180))

    def test_motohours_rounding(self):
        """м/ч: средняя округляется до 0.1."""
        rows = [mark(1, '2026-09-02', 1000), mark(2, '2026-10-02', 1310)]
        r = self.rate(rows, params=MH, remaining=100)
        self.assertEqual((r['rate'], r['rate_source']), (10.3, 'fact'))
        self.assertEqual(r['forecast_days'], 10)
        r = self.rate([], params=MH)
        self.assertEqual((r['rate'], r['daily_norm'], r['daily_norm_source']), (11, 11, 'default'))

    def test_no_forecast_when_overdue_or_unknown(self):
        rows = [mark(1, '2026-09-02', 150000), mark(2, '2026-10-02', 153000)]
        for remaining in (0, -2000, None):
            r = self.rate(rows, remaining=remaining)
            self.assertIsNone(r['forecast_days'])
            self.assertIsNone(r['forecast_date'])
        r = self.rate([], remaining=1000, norm_override=0)
        self.assertIsNone(r['forecast_days'])

    def test_forecast_rounds_up(self):
        r = self.rate([], remaining=7001)
        self.assertEqual(r['forecast_days'], 71)
        r = self.rate([], remaining=1)
        self.assertEqual((r['forecast_days'], r['forecast_date']), (1, '2026-10-03'))


class TyresUnitsTest(unittest.TestCase):
    """SPEC 4.7."""

    def tyre(self, **kw):
        t = {'id': 1, 'machine_id': 1, 'tyre_no': 'Ш-1', 'ttype': '12.00R20', 'position': 'Перед. левая',
             'install_date': None, 'resource': None, 'run': None, 'install_reading': None, 'note': ''}
        t.update(kw)
        return t

    def unit(self, **kw):
        u = {'id': 1, 'machine_id': 1, 'unit': 'КПП', 'resource': None, 'install_reading': None, 'note': ''}
        u.update(kw)
        return u

    def test_tyre_resource_zero_or_empty(self):
        """resource пуст или 0 -> left и wear = null, статус none с пустой подписью."""
        for resource in (0, None):
            v = logic.tyre_view(self.tyre(resource=resource, run=50000), 153000, SETTINGS)
            self.assertEqual((v['left'], v['wear'], v['status'], v['status_label']), (None, None, 'none', ''))
            self.assertEqual(v['run_eff'], 50000)

    def test_tyre_by_run_field(self):
        v = logic.tyre_view(self.tyre(resource=100000, run=50000), 153000, SETTINGS)
        self.assertEqual((v['run_eff'], v['left'], v['wear'], v['status']), (50000, 50000, 0.5, 'green'))
        self.assertEqual(v['status_label'], logic.TYRE_LABELS['green'])
        self.assertEqual(v['tyre_no'], 'Ш-1')            # поля строки сохраняются

    def test_tyre_by_install_reading(self):
        """install_reading задан -> пробег = текущее показание машины − показание при установке (run не важен)."""
        v = logic.tyre_view(self.tyre(resource=100000, run=1, install_reading=73000), 153000, SETTINGS)
        self.assertEqual((v['run_eff'], v['left'], v['wear'], v['status']), (80000, 20000, 0.8, 'yellow'))

    def test_tyre_thresholds(self):
        cases = [(74999, 'green'), (75000, 'yellow'), (89999, 'yellow'), (90000, 'red'), (120000, 'red')]
        for run, status in cases:
            v = logic.tyre_view(self.tyre(resource=100000, run=run), None, SETTINGS)
            self.assertEqual(v['status'], status, run)
        self.assertEqual(logic.tyre_view(self.tyre(resource=100000, run=90000), None, SETTINGS)['status_label'],
                         logic.TYRE_LABELS['red'])
        custom = dict(SETTINGS, tyre_red=0.8, tyre_yellow=0.5)
        self.assertEqual(logic.tyre_view(self.tyre(resource=100000, run=80000), None, custom)['status'], 'red')
        self.assertEqual(logic.tyre_view(self.tyre(resource=100000, run=50000), None, custom)['status'], 'yellow')

    def test_tyre_without_run(self):
        """Нет пробега (run пуст; или есть install_reading, но у машины нет показаний) -> none."""
        v = logic.tyre_view(self.tyre(resource=100000), 153000, SETTINGS)
        self.assertEqual((v['run_eff'], v['left'], v['wear'], v['status']), (None, None, None, 'none'))
        v = logic.tyre_view(self.tyre(resource=100000, install_reading=73000), None, SETTINGS)
        self.assertEqual((v['run_eff'], v['status']), (None, 'none'))

    def test_unit_statuses(self):
        cases = [(2000, 500, 'green'), (2000, 1500, 'yellow'), (2000, 1800, 'red'), (2000, 2500, 'red')]
        for resource, run, status in cases:
            v = logic.unit_view(self.unit(resource=resource, install_reading=1000), 1000 + run, SETTINGS)
            self.assertEqual((v['run'], v['left'], v['status']), (run, resource - run, status))
            self.assertEqual(v['status_label'], logic.UNIT_LABELS[status])
        self.assertEqual(logic.unit_view(self.unit(resource=2000, install_reading=0), 500, SETTINGS)['wear'], 0.25)

    def test_unit_negative_run_is_check(self):
        """Показание установки больше текущего -> «проверьте показание установки»."""
        v = logic.unit_view(self.unit(resource=2000, install_reading=5000), 4000, SETTINGS)
        self.assertEqual((v['run'], v['status'], v['status_label']), (-1000, 'check', logic.UNIT_LABELS['check']))

    def test_unit_resource_zero_or_no_data(self):
        for resource in (0, None):
            v = logic.unit_view(self.unit(resource=resource, install_reading=1000), 3000, SETTINGS)
            self.assertEqual((v['run'], v['left'], v['wear'], v['status'], v['status_label']),
                             (2000, None, None, 'none', ''))
        v = logic.unit_view(self.unit(resource=2000), 3000, SETTINGS)           # нет показания установки
        self.assertEqual((v['run'], v['status']), (None, 'none'))
        v = logic.unit_view(self.unit(resource=2000, install_reading=1000), None, SETTINGS)   # нет показаний машины
        self.assertEqual((v['run'], v['status']), (None, 'none'))


class DataWarningsTest(unittest.TestCase):
    """SPEC 4.8."""

    def warn(self, readings=(), events=(), **mkw):
        return logic.data_warnings(machine(**mkw), list(readings), list(events), D)

    def test_clean_data(self):
        rows = [mark(1, '2026-09-01', 150000), rd(2, '2026-09-10', 'Ремонт', 151000), mark(3, '2026-10-01', 153000)]
        events = [ev(2, '2026-09-10', 'Ремонт', reading=151000, time_in='08:00', date_out='2026-09-11', time_out='09:00')]
        self.assertEqual(self.warn(rows, events), [])

    def test_counter_went_back(self):
        rows = [mark(1, '2026-09-01', 153000), rd(2, '2026-09-05', 'Ремонт', 150000)]
        w = self.warn(rows)
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]['event_id'], 2)
        self.assertIn('05.09.2026', w[0]['text'])
        self.assertIn('Ремонт', w[0]['text'])
        self.assertIn('150 000', w[0]['text'])
        self.assertIn('153 000', w[0]['text'])
        self.assertIn('назад', w[0]['text'])

    def test_to_with_lower_reading_is_fine(self):
        """ТО «задним числом» на меньшем показании — штатно (R8)."""
        rows = [mark(1, '2026-10-01', 153000), rd(2, '2026-10-02', 'ТО-2', 150000)]
        self.assertEqual(self.warn(rows), [])

    def test_correction_mark_warns_once(self):
        """Исправление отметкой вниз даёт одно предупреждение, дальнейшие показания не задевает."""
        rows = [mark(1, '2026-09-29', 150000), mark(2, '2026-09-30', 149900), mark(3, '2026-10-01', 150050)]
        w = self.warn(rows)
        self.assertEqual([x['event_id'] for x in w], [2])
        self.assertIn('Отметка показаний', w[0]['text'])

    def test_reading_below_registration_with_date(self):
        w = self.warn([rd(1, '2026-09-05', 'Ремонт', 43520)], reg_reading=47415, reg_date='2026-09-01')
        self.assertEqual(len(w), 1)
        self.assertIn('при регистрации', w[0]['text'])
        self.assertIn('01.09.2026', w[0]['text'])

    def test_registration_without_date_not_compared(self):
        """reg_reading без даты (текущий пробег из листа «Машины») в проверке монотонности не участвует,
        а события между собой по-прежнему сравниваются."""
        self.assertEqual(self.warn([rd(1, '2026-09-05', 'Ремонт', 43520)], reg_reading=47415), [])
        rows = [rd(1, '2026-09-01', 'Ремонт', 45000), rd(2, '2026-09-05', 'Ремонт', 43520)]
        w = self.warn(rows, reg_reading=47415)
        self.assertEqual([x['event_id'] for x in w], [2])
        self.assertIn('01.09.2026', w[0]['text'])
        self.assertNotIn('при регистрации', w[0]['text'])

    def test_to_without_reading(self):
        w = self.warn(events=[ev(7, '2026-09-05', 'ТО-1')])
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]['event_id'], 7)
        self.assertIn('ТО-1', w[0]['text'])
        self.assertIn('показание', w[0]['text'])

    def test_date_out_before_date(self):
        w = self.warn(events=[ev(7, '2026-09-05', 'Ремонт', date_out='2026-09-03')])
        self.assertEqual(len(w), 1)
        self.assertIn('раньше', w[0]['text'])

    def test_negative_duration(self):
        w = self.warn(events=[ev(7, '2026-09-05', 'Ремонт', time_in='09:00', date_out='2026-09-05', time_out='07:00')])
        self.assertEqual(len(w), 1)
        self.assertIn('отрицательная длительность', w[0]['text'])

    def test_time_out_without_date_out(self):
        w = self.warn(events=[ev(7, '2026-09-05', 'Ремонт', time_out='07:00')])
        self.assertEqual(len(w), 1)
        self.assertIn('без даты выхода', w[0]['text'])

    def test_future_date_reported_once(self):
        """Событие в будущем попадает и в readings, и в state_events — предупреждение одно."""
        e = ev(7, '2026-10-05', 'ТО-1', reading=160000)
        w = self.warn([rd(7, '2026-10-05', 'ТО-1', 160000)], [e])
        self.assertEqual(len(w), 1)
        self.assertIn('в будущем', w[0]['text'])
        self.assertIn('05.10.2026', w[0]['text'])


class BuildViewTest(unittest.TestCase):
    """Сборка MachineView (SPEC, раздел 5) и пример R8 из ТЗ."""

    SPEC_FIELDS = [
        'unit', 'interval', 'warn', 'rule_keyword', 'auto',
        'reading', 'reading_base', 'reading_base_date', 'reading_estimated', 'reading_auto_add',
        'state', 'state_label', 'state_event_id', 'state_work', 'state_since', 'state_seconds',
        'last_repair_seconds', 'line_hours', 'repair_hours', 'idle_hours',
        'to_status', 'to_status_label', 'to_count', 'last_to', 'next_to_kind', 'next_to_at',
        'remaining', 'overdue_by', 'missed',
        'rate', 'rate_source', 'rate_days', 'daily_norm', 'daily_norm_source',
        'forecast_date', 'forecast_days',
        'parts_order', 'tyres_red', 'units_attention', 'data_warnings',
    ]

    def build(self, m=None, readings=(), events=(), orders=(), tyres=(), units=(), d=D):
        return logic.build_machine_view(m or machine(), list(readings), list(events), list(orders),
                                        list(tyres), list(units), RULES, SETTINGS, d, NOW)

    def test_all_fields_present_for_empty_machine(self):
        v = self.build()
        for field in self.SPEC_FIELDS:
            self.assertIn(field, v, field)
        for field in machine():
            self.assertIn(field, v, field)                     # поля таблицы machines сохраняются
        self.assertEqual(set(v['auto']), {'unit', 'interval', 'warn', 'norm', 'rule_keyword'})
        self.assertEqual((v['unit'], v['interval'], v['warn']), ('км', 10000, 1000))
        self.assertIsNone(v['reading'])
        self.assertEqual((v['to_status'], v['state'], v['next_to_kind']), ('no_reading', 'line', 'ТО-1'))
        self.assertEqual((v['parts_order'], v['tyres_red'], v['units_attention'], v['data_warnings']), ('', 0, 0, []))
        self.assertEqual((v['rate'], v['rate_source'], v['daily_norm'], v['daily_norm_source']),
                         (100, 'norm', 100, 'default'))
        self.assertIsNone(v['forecast_date'])

    def test_r8_to_done_on_lower_reading(self):
        """R8: отметка 153000, ТО сделано на 150000 сегодня -> показание 153000, следующее на 160000, осталось 7000."""
        to = ev(2, D, 'ТО-2', reading=150000, work='Масло, фильтры')
        v = self.build(readings=[mark(1, '2026-10-01', 153000), rd(2, D, 'ТО-2', 150000)], events=[to])
        self.assertEqual((v['reading'], v['next_to_at'], v['remaining'], v['to_status']),
                         (153000, 160000, 7000, 'ok'))
        self.assertEqual((v['next_to_kind'], v['to_count']), ('ТО-3', 1))
        self.assertEqual(v['last_to'], {'event_id': 2, 'kind': 'ТО-2', 'reading': 150000, 'date': D,
                                         'date_unknown': False})
        self.assertEqual((v['state'], v['state_event_id'], v['state_work']), ('to', 2, 'Масло, фильтры'))
        self.assertEqual((v['forecast_days'], v['forecast_date']), (70, '2026-12-11'))
        self.assertEqual(v['data_warnings'], [])
        self.assertFalse(v['reading_estimated'])

    def test_r1_r5_overdue_from_last_to(self):
        """R1/R5: ТО на 125000, интервал 10000, приехал на вахту — на счётчике 147300 -> просрочено, пропущено 2."""
        v = self.build(readings=[rd(1, '2026-06-01', 'ТО-1', 125000), mark(2, D, 147300)])
        self.assertEqual((v['to_status'], v['overdue_by'], v['missed'], v['next_to_at'], v['next_to_kind']),
                         ('overdue', 12300, 2, 135000, 'ТО-2'))

    def test_estimated_reading_with_auto(self):
        v = self.build(readings=[mark(1, '2026-09-29', 150000), rd(2, '2026-09-01', 'ТО-1', 145000),
                                 auto(3, '2026-09-30', 100), auto(4, '2026-10-01', 100), auto(5, D, 100)])
        self.assertEqual((v['reading'], v['reading_base'], v['reading_auto_add'], v['reading_estimated']),
                         (150300, 150000, 300, True))
        self.assertEqual(v['remaining'], 4700)

    def test_report_for_past_date(self):
        """Отчёт за прошлую дату использует только события до неё."""
        rows = [mark(1, '2026-09-01', 150000), rd(2, '2026-09-10', 'ТО-1', 150500), mark(3, '2026-10-01', 153000)]
        v = self.build(readings=rows, d='2026-09-05')
        self.assertEqual((v['reading'], v['to_status'], v['line_hours']), (150000, 'no_to', 24.0))
        v = self.build(readings=rows, d='2026-09-10')
        self.assertEqual((v['reading'], v['to_status'], v['remaining']), (150500, 'ok', 10000))

    def test_repair_state_and_hours(self):
        events = [ev(5, D, 'Ремонт', time_in='08:00', work='Течь масла', parts_order='Сальник'),
                  ev(4, '2026-09-20', 'Ремонт', time_in='08:00', date_out='2026-09-20', time_out='18:00')]
        v = self.build(events=events, orders=[events[0]])
        self.assertEqual((v['state'], v['state_event_id'], v['state_work'], v['state_seconds']),
                         ('repair', 5, 'Течь масла', 23400))
        self.assertEqual((v['line_hours'], v['repair_hours'], v['idle_hours']), (8.0, 6.5, 0.0))
        self.assertEqual(v['last_repair_seconds'], 36000)
        self.assertEqual(v['parts_order'], 'Сальник')

    def test_parts_order_joined(self):
        orders = [ev(9, '2026-09-25', 'Ремонт', parts_order=' Фильтр масляный '),
                  ev(3, '2026-09-20', 'Заметка', parts_order='Ремень'),
                  ev(10, '2026-09-26', 'Заметка', parts_order='  ')]
        self.assertEqual(self.build(orders=orders)['parts_order'], 'Ремень; Фильтр масляный')

    def test_tyres_and_units_counters(self):
        tyres = [{'id': 1, 'resource': 100000, 'run': 95000, 'install_reading': None},
                 {'id': 2, 'resource': 100000, 'run': 80000, 'install_reading': None},
                 {'id': 3, 'resource': 100000, 'run': None, 'install_reading': 60000}]     # 153000-60000 = 93% -> red
        units = [{'id': 1, 'resource': 100000, 'install_reading': 60000},                  # red
                 {'id': 2, 'resource': 100000, 'install_reading': 75000},                  # 78% -> yellow
                 {'id': 3, 'resource': 100000, 'install_reading': 150000},                 # green
                 {'id': 4, 'resource': 100000, 'install_reading': 160000}]                 # check
        v = self.build(readings=[mark(1, '2026-10-01', 153000)], tyres=tyres, units=units)
        self.assertEqual((v['tyres_red'], v['units_attention']), (2, 2))

    def test_data_warnings_in_view(self):
        v = self.build(readings=[mark(1, '2026-09-01', 153000), rd(2, '2026-09-05', 'Ремонт', 150000)])
        self.assertEqual(len(v['data_warnings']), 1)
        self.assertIsInstance(v['data_warnings'][0], str)
        self.assertEqual(v['data_warning_items'][0]['event_id'], 2)
        self.assertEqual(v['data_warning_items'][0]['text'], v['data_warnings'][0])

    def test_conserved_machine(self):
        v = self.build(m=machine(status='На консервации'), readings=[mark(1, '2026-10-01', 153000)])
        self.assertEqual((v['state'], v['line_hours'], v['repair_hours'], v['idle_hours']),
                         ('conserved', None, None, None))

    def test_view_is_json_serializable(self):
        import json
        v = self.build(readings=[mark(1, '2026-10-01', 153000), rd(2, D, 'ТО-2', 150000)],
                       events=[ev(2, D, 'ТО-2', reading=150000)])
        json.dumps(v, ensure_ascii=False)

    def test_accepts_sqlite_rows(self):
        """Строки можно передавать как sqlite3.Row (без преобразования в dict) — так делает build_views."""
        from app import db
        conn = db.open_db(':memory:')
        try:
            conn.execute("INSERT INTO machines(id, garage_no, model, mtype) VALUES(1, '101', 'SHACMAN X3000', 'Самосвал')")
            conn.executemany(
                "INSERT INTO events(id, machine_id, date, kind, reading, delta, source, time_in, work, parts_order, "
                "updated_at) VALUES(?, 1, ?, ?, ?, ?, ?, ?, ?, ?, '2026-10-01T10:00:00')",
                [(1, '2026-09-30', MARK, 153000, None, 'mark', None, '', ''),
                 (2, '2026-09-30', 'ТО-2', 150000, None, 'manual', None, 'Масло', ''),
                 (3, '2026-10-01', MARK, None, 100, 'auto', None, '', ''),
                 (4, D, 'Ремонт', None, None, 'manual', '08:00', 'Течь масла', 'Сальник')])
            conn.execute("INSERT INTO tyres(machine_id, resource, run) VALUES(1, 100000, 95000)")
            conn.execute("INSERT INTO units(machine_id, resource, install_reading) VALUES(1, 100000, 60000)")
            m = conn.execute('SELECT * FROM machines').fetchone()
            readings = conn.execute(
                "SELECT id, machine_id, date, kind, reading, reading_estimated, delta, source, updated_at "
                "FROM events WHERE reading IS NOT NULL OR source='auto'").fetchall()
            states = conn.execute("SELECT * FROM events WHERE kind LIKE 'Ремонт%' OR kind LIKE 'ТО-%'").fetchall()
            orders = conn.execute("SELECT * FROM events WHERE parts_order<>'' AND order_done=0").fetchall()
            tyres = conn.execute('SELECT * FROM tyres').fetchall()
            units = conn.execute('SELECT * FROM units').fetchall()
            v = logic.build_machine_view(m, readings, states, orders, tyres, units,
                                         db.get_rules(conn), db.get_settings(conn), D, NOW)
            self.assertEqual((v['reading'], v['reading_estimated'], v['remaining'], v['to_status']),
                             (153100, True, 6900, 'ok'))
            self.assertEqual((v['state'], v['state_event_id'], v['state_seconds'], v['repair_hours']),
                             ('repair', 4, 23400, 6.5))
            self.assertEqual((v['parts_order'], v['tyres_red'], v['units_attention']), ('Сальник', 1, 1))
            self.assertEqual(logic.event_duration(states[-1], NOW)['open'], True)
            self.assertEqual(logic.tyre_view(tyres[0], v['reading'], db.get_settings(conn))['status'], 'red')
            self.assertEqual(logic.days_to_add(v, states, {'2026-10-01'}, '2026-10-01', D, D), ([], 'в ремонте'))
        finally:
            db.close_db(conn)

    def test_fleet_summary(self):
        views = [
            self.build(),                                                                   # line, no_reading
            self.build(readings=[mark(1, '2026-10-01', 153000), rd(2, '2026-09-01', 'ТО-1', 150000)]),   # line, ok
            self.build(readings=[mark(1, '2026-10-01', 162000), rd(2, '2026-09-01', 'ТО-1', 150000)],
                       events=[ev(3, '2026-10-01', 'Ремонт')],
                       tyres=[{'id': 1, 'resource': 100, 'run': 95, 'install_reading': None}]),          # repair, overdue
            self.build(m=machine(status='На консервации'), readings=[mark(1, '2026-10-01', 5000)]),      # conserved, no_to
        ]
        s = logic.fleet_summary(views)
        self.assertEqual(s, {'total': 4, 'line': 2, 'repair': 1, 'to': 0, 'idle': 0, 'conserved': 1,
                             'overdue': 1, 'soon': 0, 'ok': 1, 'no_to': 1, 'no_reading': 1,
                             'tyres_red': 1, 'units_attention': 0})


class DaysToAddTest(unittest.TestCase):
    """«Накинуть за день»: какие дни периода начисляются."""

    def view(self, **kw):
        v = {'status': 'В работе', 'reading_base': 150000, 'reading_base_date': '2026-09-28'}
        v.update(kw)
        return v

    def days(self, mv=None, events=(), autos=(), date_from=D, date_to=D, today=D):
        return logic.days_to_add(mv or self.view(), list(events), set(autos), date_from, date_to, today)

    def test_simple_day(self):
        self.assertEqual(self.days(), ([D], ''))

    def test_period_after_base_date(self):
        """Дни не позже даты базы не начисляются."""
        days, reason = self.days(date_from='2026-09-27', date_to=D)
        self.assertEqual(days, ['2026-09-29', '2026-09-30', '2026-10-01', '2026-10-02'])
        self.assertEqual(reason, '')

    def test_reading_entered_today(self):
        days, reason = self.days(self.view(reading_base_date=D))
        self.assertEqual((days, reason), ([], 'показание внесено сегодня'))

    def test_base_without_date_counts_all_days(self):
        """База без даты (показание при регистрации) — начисляются все дни периода."""
        days, _ = self.days(self.view(reading_base_date=None), date_from='2026-10-01', date_to=D)
        self.assertEqual(days, ['2026-10-01', D])

    def test_no_readings(self):
        self.assertEqual(self.days(self.view(reading_base=None, reading_base_date=None)), ([], 'нет показаний'))

    def test_conserved_and_retired(self):
        self.assertEqual(self.days(self.view(status='На консервации')), ([], 'консервация'))
        self.assertEqual(self.days(self.view(status='Списана'))[0], [])

    def test_already_added(self):
        self.assertEqual(self.days(autos={D}), ([], 'уже накинуто'))
        days, reason = self.days(autos={'2026-10-01'}, date_from='2026-09-30', date_to=D)
        self.assertEqual((days, reason), (['2026-09-30', D], ''))

    def test_repair_skipped(self):
        self.assertEqual(self.days(events=[ev(1, '2026-10-01', 'Ремонт')]), ([], 'в ремонте'))

    def test_repair_days_skipped_inside_period(self):
        """Ремонт 30.09–02.10: дни 30.09 и 01.10 пропускаются, день выхода 02.10 начисляется."""
        e = [ev(1, '2026-09-30', 'Ремонт', time_in='08:00', date_out='2026-10-02', time_out='09:00')]
        days, reason = self.days(events=e, date_from='2026-09-29', date_to=D)
        self.assertEqual((days, reason), (['2026-09-29', D], ''))

    def test_idle_skipped(self):
        self.assertEqual(self.days(events=[ev(1, '2026-10-01', 'Простой')]), ([], 'простой'))

    def test_to_day_is_added(self):
        """В день ТО машина работает — день начисляется."""
        self.assertEqual(self.days(events=[ev(1, D, 'ТО-1', reading=150000)]), ([D], ''))

    def test_future_days_not_added(self):
        days, _ = self.days(date_from='2026-10-01', date_to='2026-10-05')
        self.assertEqual(days, ['2026-10-01', D])

    def test_with_built_view(self):
        """Работает с настоящим MachineView: отметка от 30.09 -> начисляются 01.10 и 02.10."""
        mv = logic.build_machine_view(machine(), [mark(1, '2026-09-30', 150000)], [], [], [], [],
                                      RULES, SETTINGS, D, NOW)
        days, reason = logic.days_to_add(mv, [], set(), '2026-09-29', D, D)
        self.assertEqual((days, reason), (['2026-10-01', D], ''))


if __name__ == '__main__':
    unittest.main()
