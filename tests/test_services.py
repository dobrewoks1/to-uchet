"""Тесты операций над базой (app/services.py) на временной базе в памяти (SPEC, раздел 8)."""

import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from app import db, logic, services
from app.logic import KIND_MARK
from app.services import ApiError

NOW = datetime(2026, 10, 2, 12, 0, 0)
TODAY = '2026-10-02'


def day(n):
    """Дата относительно «сегодня» тестов: day(-1) — вчера."""
    return logic.add_days(TODAY, n)


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = db.open_db(':memory:')
        self.addCleanup(self.conn.close)

    # ---- помощники ----

    def err(self, status, fn, *args, **kwargs):
        """Вызов должен закончиться ApiError с этим статусом; возвращает исключение."""
        with self.assertRaises(ApiError) as cm:
            fn(*args, **kwargs)
        self.assertEqual(cm.exception.status, status, cm.exception.message)
        return cm.exception

    def machine(self, garage_no, model='SHACMAN X3000', **fields):
        data = {'garage_no': garage_no, 'model': model, 'mtype': 'Самосвал'}
        data.update(fields)
        return services.create_machine(self.conn, data, now=NOW)

    def view(self, machine_id, date=None):
        return services.machine_view(self.conn, machine_id, date, now=NOW)

    def mark(self, machine_id, reading, date=TODAY, force=True):
        return services.readings_bulk(
            self.conn, {'date': date, 'items': [{'machine_id': machine_id, 'reading': reading}]},
            force=force, now=NOW)

    def event(self, machine_id, kind, date=TODAY, force=False, **fields):
        data = {'machine_id': machine_id, 'kind': kind, 'date': date}
        data.update(fields)
        return services.create_event(self.conn, data, force=force, now=NOW)

    def count(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()[0]


# --------------------------------------------------------------------------------------

class MachinesTest(Base):
    def test_create_and_formats(self):
        m = self.machine(' 101 ', model=None, reg_reading='47 415', reg_date='', plate=None,
                         interval_override='12 000,5', unit_override='')
        self.assertEqual(m['garage_no'], '101')
        self.assertEqual(m['model'], '')            # null на входе у текста = ''
        self.assertEqual(m['plate'], '')
        self.assertEqual(m['reg_reading'], 47415)   # «47 415» строкой -> число
        self.assertIsNone(m['reg_date'])            # '' -> null
        self.assertIsNone(m['unit_override'])
        self.assertEqual(m['interval_override'], 12000.5)
        self.assertEqual(m['status'], 'В работе')
        self.assertEqual(m['reading'], 47415)
        self.assertEqual(m['to_status'], 'no_to')
        self.assertIn('auto', m)

    def test_garage_no_required_and_unique(self):
        self.machine('А-12')
        self.err(400, services.create_machine, self.conn, {'garage_no': '  '}, now=NOW)
        self.err(400, services.create_machine, self.conn, {'model': 'КАМАЗ'}, now=NOW)
        e = self.err(400, services.create_machine, self.conn, {'garage_no': ' а-12 '}, now=NOW)
        self.assertIn('уже есть', e.message)

    def test_validation(self):
        self.err(400, services.create_machine, self.conn, {'garage_no': '1', 'status': 'Продана'}, now=NOW)
        self.err(400, services.create_machine, self.conn, {'garage_no': '1', 'reg_reading': 'абв'}, now=NOW)
        self.err(400, services.create_machine, self.conn, {'garage_no': '1', 'reg_reading': -5}, now=NOW)
        self.err(400, services.create_machine, self.conn, {'garage_no': '1', 'reg_date': '02.10.2026'}, now=NOW)
        self.err(400, services.create_machine, self.conn, {'garage_no': '1', 'unit_override': 'литры'}, now=NOW)
        self.err(400, services.create_machine, self.conn, {'garage_no': '1', 'interval_override': 0}, now=NOW)
        self.err(400, services.create_machine, self.conn, {'garage_no': '1', 'norm_override': -1}, now=NOW)
        self.assertEqual(self.count('SELECT COUNT(*) FROM machines'), 0)

    def test_put_is_partial(self):
        m = self.machine('7', plate='А000АА', site='Северный', norm_override=120)
        upd = services.update_machine(self.conn, m['id'], {'site': 'Мирный'}, now=NOW)
        self.assertEqual(upd['site'], 'Мирный')
        self.assertEqual(upd['plate'], 'А000АА')          # не переданное не меняется
        self.assertEqual(upd['norm_override'], 120)
        upd = services.update_machine(self.conn, m['id'], {'norm_override': ''}, now=NOW)
        self.assertIsNone(upd['norm_override'])
        # свой же гаражный № при правке не считается дублем
        upd = services.update_machine(self.conn, m['id'], {'garage_no': '7', 'model': 'SITRAK C7H'}, now=NOW)
        self.assertEqual((upd['unit'], upd['interval']), ('км', 15000))
        self.err(404, services.update_machine, self.conn, 999, {'site': 'x'}, now=NOW)

    def test_params_resolution(self):
        excavator = self.machine('E1', model='SANY SY215', mtype='Экскаватор')
        self.assertEqual((excavator['unit'], excavator['interval']), ('м/ч', 250))
        kamaz = self.machine('K1', model='КАМАЗ', unit_override='км')
        self.assertEqual((kamaz['unit'], kamaz['interval'], kamaz['warn']), ('км', 10000, 1000))
        self.assertEqual(kamaz['auto']['unit'], 'м/ч')

    def test_unit_change_needs_confirm(self):
        m = self.machine('9')
        services.update_machine(self.conn, m['id'], {'unit_override': 'м/ч'}, now=NOW)   # показаний нет — без вопросов
        services.update_machine(self.conn, m['id'], {'unit_override': None}, now=NOW)
        self.mark(m['id'], 1000)
        e = self.err(409, services.update_machine, self.conn, m['id'], {'unit_override': 'м/ч'}, now=NOW)
        self.assertTrue(e.extra['needs_confirm'])
        self.assertIn('прежних единицах', e.message)
        self.assertEqual(self.view(m['id'])['unit'], 'км')
        upd = services.update_machine(self.conn, m['id'], {'unit_override': 'м/ч'}, force=True, now=NOW)
        self.assertEqual(upd['unit'], 'м/ч')

    def test_delete(self):
        empty = self.machine('1')
        self.assertEqual(services.delete_machine(self.conn, empty['id']), {'ok': True})
        m = self.machine('2')
        self.mark(m['id'], 100)
        services.create_tyre(self.conn, {'machine_id': m['id'], 'tyre_no': 'Ш1'}, now=NOW)
        services.create_unit(self.conn, {'machine_id': m['id'], 'unit': 'КПП'}, now=NOW)
        e = self.err(409, services.delete_machine, self.conn, m['id'])
        self.assertTrue(e.extra['needs_confirm'])
        self.assertEqual(len(e.extra['warnings']), 3)
        self.assertEqual(self.count('SELECT COUNT(*) FROM machines'), 1)
        services.delete_machine(self.conn, m['id'], force=True)
        for table in ('machines', 'events', 'tyres', 'units'):
            self.assertEqual(self.count(f'SELECT COUNT(*) FROM {table}'), 0, table)
        self.err(404, services.delete_machine, self.conn, m['id'])

    def test_list_and_fleet(self):
        a = self.machine('A')
        b = self.machine('B', status='Списана')
        c = self.machine('C', status='На консервации')
        self.assertEqual([m['garage_no'] for m in services.list_machines(self.conn, now=NOW)], ['A', 'B', 'C'])
        fleet = services.fleet(self.conn, now=NOW)
        self.assertEqual([m['garage_no'] for m in fleet['rows']], ['A', 'C'])      # без списанных
        self.assertEqual(fleet['date'], TODAY)
        self.assertEqual(fleet['summary']['total'], 2)
        self.assertEqual(fleet['summary']['conserved'], 1)
        self.assertEqual(fleet['summary']['line'], 1)
        self.assertEqual(fleet['summary']['no_reading'], 2)
        self.err(400, services.fleet, self.conn, 'вчера', now=NOW)
        self.err(404, services.machine_view, self.conn, 12345, now=NOW)
        self.assertEqual(self.view(b['id'])['status'], 'Списана')
        self.assertEqual(self.view(c['id'])['state'], 'conserved')
        self.assertEqual(self.view(a['id'])['state'], 'line')

    def test_fleet_on_past_date_and_warnings(self):
        m = self.machine('A')
        self.mark(m['id'], 100000, date=day(-10))
        self.mark(m['id'], 101000, date=day(-1))
        self.assertEqual(services.fleet(self.conn, day(-5), now=NOW)['rows'][0]['reading'], 100000)
        self.assertEqual(services.fleet(self.conn, now=NOW)['rows'][0]['reading'], 101000)
        ev = self.event(m['id'], 'Ремонт', date=day(3), force=True)
        warnings = services.fleet(self.conn, now=NOW)['warnings']
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]['machine_id'], m['id'])
        self.assertEqual(warnings[0]['garage_no'], 'A')
        self.assertEqual(warnings[0]['event_id'], ev['id'])
        self.assertIn('будущем', warnings[0]['text'])

    def test_bootstrap(self):
        boot = services.bootstrap(self.conn, 'C:/data', now=NOW)
        self.assertTrue(boot['first_run'])
        self.assertEqual(boot['today'], TODAY)
        self.assertEqual(boot['now'], '2026-10-02T12:00:00')
        self.assertEqual(boot['data_dir'], 'C:/data')
        self.assertEqual(boot['settings']['default_interval_mh'], 250)
        self.assertIn(KIND_MARK, boot['lists']['kinds'])
        self.assertEqual(len(boot['rules']), 9)
        self.assertEqual(len(boot['regulation']), 3)
        self.machine('1')
        self.assertFalse(services.bootstrap(self.conn, now=NOW)['first_run'])

    def test_card(self):
        m = self.machine('A')
        services.readings_bulk(self.conn, {
            'date': day(-3), 'items': [{'machine_id': m['id'], 'reading': 150000}],
            'baseline': [{'machine_id': m['id'], 'kind': 'ТО-1', 'reading': 149000}]}, now=NOW)
        services.daily_apply(self.conn, {'from': day(-2), 'to': day(-1),
                                         'items': [{'machine_id': m['id'], 'norm': 100}]}, now=NOW)
        services.create_tyre(self.conn, {'machine_id': m['id'], 'tyre_no': 'Ш1', 'resource': 1000, 'run': 950}, now=NOW)
        services.create_unit(self.conn, {'machine_id': m['id'], 'unit': 'КПП'}, now=NOW)
        card = services.machine_card(self.conn, m['id'], now=NOW)
        self.assertEqual(card['machine']['reading'], 150200)
        self.assertEqual(len(card['events']), 4)                       # ТО, отметка и два накинутых
        dates = [e['date'] for e in card['events']]
        self.assertEqual(dates, sorted(dates, reverse=True))           # новые сверху
        self.assertEqual(sum(1 for e in card['events'] if e['source'] == 'auto'), 2)
        self.assertEqual(card['tyres'][0]['status'], 'red')
        self.assertEqual(card['units'][0]['unit'], 'КПП')
        self.assertEqual(card['machine']['next_to_kind'], 'ТО-2')
        self.assertEqual(card['regulation_next']['to_kind'], 'ТО-2')
        self.err(404, services.machine_card, self.conn, 999, now=NOW)


class BuildViewsTest(Base):
    def _query_count(self):
        statements = []
        self.conn.set_trace_callback(statements.append)
        try:
            views = services.build_views(self.conn, TODAY, NOW)
        finally:
            self.conn.set_trace_callback(None)
        return len(statements), len(views)

    def test_fixed_number_of_queries(self):
        for n in range(3):
            m = self.machine(f'M{n}')
            self.mark(m['id'], 1000 + n)
        few, count_few = self._query_count()
        for n in range(3, 30):
            m = self.machine(f'M{n}')
            self.mark(m['id'], 1000 + n)
            self.event(m['id'], 'Ремонт', work='x')
        many, count_many = self._query_count()
        self.assertEqual((count_few, count_many), (3, 30))
        self.assertEqual(few, many)           # число запросов не зависит от числа машин
        self.assertLessEqual(many, 9)         # 7 запросов (с последними записями) + настройки + правила

    def test_machine_ids_filter(self):
        a = self.machine('A')
        b = self.machine('B')
        self.mark(a['id'], 10)
        self.mark(b['id'], 20)
        views = services.build_views(self.conn, TODAY, NOW, [b['id']])
        self.assertEqual([(v['garage_no'], v['reading']) for v in views], [('B', 20)])
        self.assertEqual(services.build_views(self.conn, TODAY, NOW, []), [])


class LastNoteTest(Base):
    """last_note в MachineView (КОНЦЕПЦИЯ_v2, 9.1) — последняя запись машины для колонки «Что делали»."""

    def setUp(self):
        super().setUp()
        self.mid = self.machine('A-1')['id']

    def note(self, date=None, machine_id=None):
        return self.view(machine_id or self.mid, date)['last_note']

    def test_no_records(self):
        self.assertIsNone(self.note())
        self.assertIsNone(self.machine('B-2')['last_note'])               # и в ответе создания машины

    def test_latest_by_date_then_id(self):
        old = self.event(self.mid, 'Заметка', date=day(-3), work='Осмотр')
        repair = self.event(self.mid, 'Ремонт', date=day(-1), time_in='08:00', work='Замена КПП')
        later = self.event(self.mid, 'Заметка', date=day(-1), work='Подтянул хомуты')     # та же дата, id больше
        self.event(self.mid, 'Заметка', date=day(-2), work='Внесено позже, дата раньше')   # id больше, дата раньше
        self.assertEqual(self.note(), {
            'event_id': later['id'], 'date': day(-1), 'date_unknown': False, 'kind': 'Заметка',
            'kind_title': 'Заметка', 'text': 'Подтянул хомуты', 'open': False})
        services.delete_event(self.conn, later['id'])
        note = self.note()
        self.assertEqual((note['event_id'], note['kind'], note['text'], note['open']),
                         (repair['id'], 'Ремонт', 'Замена КПП', True))   # ремонт без даты выхода — открыт
        services.update_event(self.conn, repair['id'], {'date_out': TODAY}, now=NOW)
        self.assertIs(self.note()['open'], False)
        # записи с датой позже даты отчёта D не считаются
        self.assertEqual(self.note(day(-2))['text'], 'Внесено позже, дата раньше')
        self.assertEqual(self.note(day(-3))['event_id'], old['id'])
        self.assertIsNone(self.note(day(-4)))

    def test_text_fallback_and_limit(self):
        self.event(self.mid, 'Заметка', date=day(-4), remarks='Стучит подвеска', parts_used='Масло')
        self.assertEqual(self.note()['text'], 'Стучит подвеска')            # work пусто -> remarks
        self.event(self.mid, 'Простой', date=day(-3), parts_used='Фильтр топливный')
        note = self.note()
        self.assertEqual((note['kind'], note['text'], note['open']), ('Простой', 'Фильтр топливный', True))
        self.event(self.mid, 'Заметка', date=day(-2), work='   ', remarks='')
        self.assertEqual(self.note()['text'], '')                           # текста нет — пустая строка
        long_text = 'Замена втулок ' * 40
        self.event(self.mid, 'Заметка', date=day(-1), work=long_text, remarks='не это')
        text = self.note()['text']
        self.assertEqual(len(text), services.NOTE_TEXT_MAX)
        self.assertEqual(text, long_text.strip()[:300])

    def test_auto_and_marks_ignored(self):
        note = self.event(self.mid, 'Заметка', date=day(-5), work='Осмотр')
        self.mark(self.mid, 1000, date=day(-4))                                 # отметка показаний (source mark)
        self.event(self.mid, KIND_MARK, date=day(-3), reading=1100, force=True)  # отметка, внесённая журналом
        services.daily_apply(self.conn, {'from': day(-2), 'to': TODAY,
                                         'items': [{'machine_id': self.mid, 'norm': 100}]}, now=NOW)
        self.assertEqual(self.count("SELECT COUNT(*) FROM events WHERE source='auto'"), 3)
        self.assertEqual(self.note()['event_id'], note['id'])
        other = self.machine('B-2')['id']
        self.mark(other, 500)
        self.assertIsNone(self.note(machine_id=other))                         # только отметки — записи нет

    def test_to_and_unknown_date(self):
        services.readings_bulk(self.conn, {'date': day(-1), 'items': [], 'baseline': [
            {'machine_id': self.mid, 'kind': 'ТО-2', 'reading': 50000}]}, force=True, now=NOW)
        note = self.note()
        self.assertEqual(
            (note['kind'], note['kind_title'], note['date'], note['date_unknown'], note['text'], note['open']),
            ('ТО-2', 'ТО-2', day(-1), True, '', False))
        res = services.to_done(self.conn, {'machine_id': self.mid, 'kind': 'ТО-3', 'date': TODAY, 'reading': 60000,
                                           'work': 'Замена масла КПП'}, force=True, now=NOW)
        self.assertEqual((res['machine']['last_note']['kind'], res['machine']['last_note']['text']),
                         ('ТО-3', 'Замена масла КПП'))

    def test_in_all_responses(self):
        ev_id = self.event(self.mid, 'Ремонт', date=day(-1), work='Сварка рамы')['id']
        self.assertEqual(services.fleet(self.conn, now=NOW)['rows'][0]['last_note']['event_id'], ev_id)
        self.assertEqual(services.machine_card(self.conn, self.mid, now=NOW)['machine']['last_note']['event_id'], ev_id)
        self.assertEqual(services.list_machines(self.conn, now=NOW)[0]['last_note']['event_id'], ev_id)
        self.assertEqual(services.plan(self.conn, now=NOW)['no_to'][0]['last_note']['event_id'], ev_id)
        self.assertIsNone(services.fleet(self.conn, day(-2), now=NOW)['rows'][0]['last_note'])
        self.assertIsNone(services.machine_card(self.conn, self.mid, day(-2), now=NOW)['machine']['last_note'])
        b = self.machine('B-2')['id']
        services.readings_bulk(self.conn, {
            'date': day(-1), 'items': [{'machine_id': b, 'reading': 111000}],
            'baseline': [{'machine_id': b, 'kind': 'ТО-1', 'reading': 100000, 'date': day(-30), 'work': 'Масло ДВС'}]},
            force=True, now=NOW)
        row = next(r for r in services.plan(self.conn, now=NOW)['rows'] if r['id'] == b)
        self.assertEqual((row['to_status'], row['last_note']['kind'], row['last_note']['text']),
                         ('overdue', 'ТО-1', 'Масло ДВС'))


class PhotoNamesTest(unittest.TestCase):
    """Правила имён файлов фото (КОНЦЕПЦИЯ_v2, 9.2) и распознавание формата по первым байтам."""

    def test_name_parts(self):
        cases = {
            'vin.png': ('vin', 'png'),
            'Фото кабины 1.JPG': ('Фото кабины 1', 'jpg'),
            'C:\\Users\\Иван\\Мой  файл (копия)!!.png': ('Мой файл копия', 'png'),
            '../../etc/passwd.gif': ('passwd', 'gif'),
            'a..b__c--d.webp': ('a.b_c-d', 'webp'),
            '  .скрытый. .jpeg ': ('скрытый', 'jpeg'),
            '###.png': ('', 'png'),
            'без расширения': ('без расширения', ''),
            'con.png': ('foto-con', 'png'),
            '': ('', ''),
            None: ('', ''),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(services.photo_name_parts(raw), expected)
        stem, ext = services.photo_name_parts('я' * 200 + '.jpeg')
        self.assertEqual(len(f'{stem}.{ext}'), services.PHOTO_NAME_MAX)

    def test_format(self):
        self.assertEqual(services.photo_format(b'\xff\xd8\xff\xe0rest'), 'JPEG')
        self.assertEqual(services.photo_format(b'\x89PNG\r\n\x1a\n....'), 'PNG')
        self.assertEqual(services.photo_format(b'RIFF\x10\x00\x00\x00WEBPVP8 '), 'WEBP')
        self.assertEqual(services.photo_format(b'GIF89a'), 'GIF')
        self.assertIsNone(services.photo_format(b'RIFF\x10\x00\x00\x00WAVE'))
        self.assertIsNone(services.photo_format(b'%PDF-1.7'))
        self.assertIsNone(services.photo_format(b''))


# --------------------------------------------------------------------------------------

class EventsTest(Base):
    def setUp(self):
        super().setUp()
        self.m = self.machine('A-1')
        self.mid = self.m['id']

    def test_create_and_view_fields(self):
        ev = self.event(self.mid, 'Ремонт', date=day(-1), time_in='8:30', date_out=TODAY, time_out='10:00',
                        work='Замена коробки', parts_order='Сальник', reading='153 000', who=None)
        self.assertEqual(ev['garage_no'], 'A-1')
        self.assertEqual(ev['model'], 'SHACMAN X3000')
        self.assertEqual(ev['unit'], 'км')
        self.assertEqual(ev['source'], 'manual')
        self.assertEqual(ev['time_in'], '08:30')
        self.assertEqual(ev['reading'], 153000)
        self.assertEqual(ev['who'], '')
        self.assertIs(ev['order_done'], False)
        self.assertIs(ev['reading_estimated'], False)
        self.assertIs(ev['is_to'], False)
        self.assertIs(ev['open'], False)
        self.assertIs(ev['duration_error'], False)
        self.assertEqual(ev['duration_seconds'], 25 * 3600 + 1800)
        self.assertEqual(self.view(self.mid)['reading'], 153000)

    def test_open_repair_and_state(self):
        ev = self.event(self.mid, 'Ремонт', date=day(-1), time_in='12:00', work='ДВС')
        self.assertIs(ev['open'], True)
        self.assertEqual(ev['duration_seconds'], 24 * 3600)
        v = self.view(self.mid)
        self.assertEqual((v['state'], v['state_event_id'], v['state_work']), ('repair', ev['id'], 'ДВС'))
        closed = services.update_event(self.conn, ev['id'], {'date_out': TODAY, 'time_out': '09:00'}, now=NOW)
        self.assertIs(closed['open'], False)
        self.assertEqual(closed['work'], 'ДВС')                         # PUT — частичное обновление
        self.assertEqual(self.view(self.mid)['state'], 'line')

    def test_reference_by_garage_no(self):
        ev = services.create_event(self.conn, {'garage_no': ' a-1 ', 'kind': 'Заметка', 'date': TODAY}, now=NOW)
        self.assertEqual(ev['machine_id'], self.mid)
        self.assertIsNone(ev['reading'])                                # нет показания у не-ТО — остаётся null
        self.assertIsNone(self.view(self.mid)['reading'])
        self.err(400, services.create_event, self.conn, {'garage_no': 'нет такой', 'kind': 'Заметка', 'date': TODAY}, now=NOW)

    def test_hard_validation(self):
        ok = {'machine_id': self.mid, 'kind': 'Ремонт', 'date': TODAY}
        bad = [
            {'machine_id': None},
            {'machine_id': 9999},
            {'date': ''},
            {'date': '02.10.2026'},
            {'date': '2026-13-01'},
            {'kind': ' '},
            {'time_in': '25:00'},
            {'time_in': 'утром'},
            {'reading': 'много'},
            {'reading': -1},
            {'date_out': day(-1)},
            {'date_out': TODAY, 'time_in': '10:00', 'time_out': '09:59'},
            {'time_out': '10:00'},                                  # время выхода без даты выхода
            {'kind': 'ТО-1'},                                       # ТО без показания
            {'kind': 'ТО-2 сезонное', 'reading': ''},
            {'kind': KIND_MARK},                                    # отметка без показания
        ]
        for patch in bad:
            with self.subTest(patch=patch):
                self.err(400, services.create_event, self.conn, {**ok, **patch}, force=True, now=NOW)
        self.assertEqual(self.count('SELECT COUNT(*) FROM events'), 0)
        self.err(400, services.create_event, self.conn, ['не объект'], now=NOW)

    def test_update_delete_and_auto(self):
        ev = self.event(self.mid, 'Заметка', work='было')
        upd = services.update_event(self.conn, ev['id'], {'work': 'стало', 'reading': '1 500'}, now=NOW)
        self.assertEqual((upd['work'], upd['reading'], upd['kind']), ('стало', 1500, 'Заметка'))
        # итоговая запись проверяется целиком
        self.err(400, services.update_event, self.conn, ev['id'], {'kind': 'ТО-1', 'reading': None}, now=NOW)
        self.err(400, services.update_event, self.conn, ev['id'], {'time_out': '10:00'}, now=NOW)
        self.err(404, services.update_event, self.conn, 9999, {'work': 'x'}, now=NOW)

        self.mark(self.mid, 2000, date=day(-2))
        services.daily_apply(self.conn, {'from': day(-1), 'to': day(-1),
                                         'items': [{'machine_id': self.mid, 'norm': 100}]}, now=NOW)
        auto_id = self.conn.execute("SELECT id FROM events WHERE source='auto'").fetchone()[0]
        e = self.err(400, services.update_event, self.conn, auto_id, {'work': 'x'}, now=NOW)
        self.assertIn('не редактируется', e.message)
        self.assertEqual(services.delete_event(self.conn, auto_id), {'ok': True})    # удалить можно
        self.assertEqual(services.delete_event(self.conn, ev['id']), {'ok': True})
        self.err(404, services.delete_event, self.conn, ev['id'])
        self.err(404, services.get_event, self.conn, ev['id'], now=NOW)

    def test_order_done(self):
        ev = self.event(self.mid, 'Ремонт', date_out=TODAY, parts_order='Фильтр масляный')
        self.assertEqual(self.view(self.mid)['parts_order'], 'Фильтр масляный')
        done = services.set_order_done(self.conn, ev['id'], True, now=NOW)
        self.assertIs(done['order_done'], True)
        self.assertEqual(self.view(self.mid)['parts_order'], '')
        self.assertIs(services.set_order_done(self.conn, ev['id'], False, now=NOW)['order_done'], False)
        self.err(404, services.set_order_done, self.conn, 9999, True, now=NOW)

    def test_soft_future_date(self):
        data = {'machine_id': self.mid, 'kind': 'Заметка', 'date': day(1)}
        e = self.err(409, services.create_event, self.conn, data, now=NOW)
        self.assertTrue(e.extra['needs_confirm'])
        self.assertTrue(any('будущем' in w for w in e.extra['warnings']))
        self.assertEqual(self.count('SELECT COUNT(*) FROM events'), 0)
        services.create_event(self.conn, data, force=True, now=NOW)
        self.assertEqual(self.count('SELECT COUNT(*) FROM events'), 1)

    def test_soft_reading_lower_than_base(self):
        self.mark(self.mid, 152000, date=day(-5))
        data = {'machine_id': self.mid, 'kind': 'Ремонт', 'date': day(-1), 'date_out': day(-1), 'reading': 148000}
        e = self.err(409, services.create_event, self.conn, data, now=NOW)
        self.assertTrue(any('меньше последнего факта' in w and '152 000' in w for w in e.extra['warnings']))
        services.create_event(self.conn, data, force=True, now=NOW)
        self.assertEqual(self.view(self.mid)['reading'], 152000)        # ремонт показание не понижает
        # событие «задним числом» (раньше даты базы) с меньшим показанием — без вопросов
        self.event(self.mid, 'Заметка', date=day(-9), reading=140000)
        # ТО на меньшем показании — штатно, без 409 (R8)
        self.event(self.mid, 'ТО-1', date=day(-1), reading=150000)
        self.assertEqual(self.view(self.mid)['reading'], 152000)

    def test_soft_reading_higher_than_later_fact(self):
        self.mark(self.mid, 152000, date=day(-2))
        data = {'machine_id': self.mid, 'kind': 'Заметка', 'date': day(-6), 'reading': 152500}
        e = self.err(409, services.create_event, self.conn, data, now=NOW)
        self.assertTrue(any('более поздней даты' in w for w in e.extra['warnings']))

    def test_soft_jump(self):
        self.mark(self.mid, 150000, date=day(-10))
        # порог: max(3 × 10000, 3 × 100 × 10) = 30000
        self.event(self.mid, 'Заметка', reading=180000)
        self.conn.execute("DELETE FROM events WHERE kind='Заметка'")
        self.conn.commit()
        data = {'machine_id': self.mid, 'kind': 'Заметка', 'date': TODAY, 'reading': 1530000}
        e = self.err(409, services.create_event, self.conn, data, now=NOW)
        self.assertTrue(any('выросло' in w for w in e.extra['warnings']))
        # для ТО скачок тоже подозрителен
        self.err(409, services.create_event, self.conn, {**data, 'kind': 'ТО-1'}, now=NOW)

    def test_soft_second_open_repair(self):
        first = self.event(self.mid, 'Ремонт', date=day(-1))
        data = {'machine_id': self.mid, 'kind': 'Ремонт гидравлики', 'date': TODAY}
        e = self.err(409, services.create_event, self.conn, data, now=NOW)
        self.assertTrue(any('незакрытый ремонт' in w for w in e.extra['warnings']))
        # правка самого открытого ремонта вопросов не вызывает (проверки исключают редактируемое событие)
        services.update_event(self.conn, first['id'], {'work': 'ждём запчасть'}, now=NOW)
        # закрытый ремонт рядом с открытым — без вопросов
        self.event(self.mid, 'Ремонт', date=day(-1), date_out=day(-1))
        services.create_event(self.conn, data, force=True, now=NOW)
        self.assertEqual(self.count("SELECT COUNT(*) FROM events WHERE date_out IS NULL"), 2)

    def test_edit_excludes_itself_from_checks(self):
        self.mark(self.mid, 150000, date=day(-3))
        ev = self.event(self.mid, 'Заметка', date=day(-1), reading=151000)
        # исправление вниз своего же показания: сравнение идёт с отметкой 150000, а не с самим собой
        upd = services.update_event(self.conn, ev['id'], {'reading': 150500}, now=NOW)
        self.assertEqual(upd['reading'], 150500)
        self.assertEqual(self.view(self.mid)['reading'], 150500)
        self.err(409, services.update_event, self.conn, ev['id'], {'reading': 149000}, now=NOW)

    def test_list_filters(self):
        other = self.machine('Б-2', model='SANY SY215', mtype='Экскаватор')
        self.event(self.mid, 'ТО-1', date=day(-9), reading=100000, work='Замена масла')
        self.event(self.mid, 'ТО-2 сезонное', date=day(-5), reading=110000)
        self.event(self.mid, 'Ремонт', date=day(-4), date_out=day(-3), work='Замена КОРОБКИ', who='Иванов')
        self.event(other['id'], 'Простой', date=day(-2), date_out=day(-1), remarks='нет топлива')
        self.event(other['id'], 'Заметка', date=day(-1), parts_order='Фильтр ГИДРАВЛИКИ')
        self.mark(other['id'], 500, date=day(-2))
        services.daily_apply(self.conn, {'from': day(-1), 'to': TODAY,
                                         'items': [{'machine_id': other['id'], 'norm': 11}]}, now=NOW)

        def kinds(**kw):
            res = services.list_events(self.conn, now=NOW, **kw)
            self.assertEqual(res['total'], len(res['rows']))
            return [r['kind'] for r in res['rows']]

        everything = services.list_events(self.conn, now=NOW)
        self.assertEqual(everything['total'], 6)                              # накинутое скрыто
        keys = [(r['date'], r['id']) for r in everything['rows']]
        self.assertEqual(keys, sorted(keys, reverse=True))                    # date desc, id desc
        self.assertEqual(services.list_events(self.conn, include_auto='1', now=NOW)['total'], 8)
        self.assertEqual(sorted(kinds(kind='ТО')), ['ТО-1', 'ТО-2 сезонное'])
        self.assertEqual(kinds(kind='Ремонт'), ['Ремонт'])
        self.assertEqual(kinds(kind=KIND_MARK), [KIND_MARK])
        self.assertEqual(len(kinds(machine_id=other['id'])), 3)
        self.assertEqual(len(kinds(machine_id=str(other['id']), include_auto=True)), 5)
        self.assertEqual(kinds(q='коробки'), ['Ремонт'])                      # без учёта регистра, кириллица
        self.assertEqual(kinds(q='ИВАНОВ'), ['Ремонт'])
        self.assertEqual(kinds(q='гидравлики'), ['Заметка'])
        self.assertEqual(kinds(q='топлива'), ['Простой'])
        self.assertEqual(len(kinds(q='б-2')), 3)                              # по гаражному №
        self.assertEqual(len(kinds(date_from=day(-5), date_to=day(-3))), 2)
        page = services.list_events(self.conn, limit=2, offset=1, now=NOW)
        self.assertEqual(page['total'], 6)
        self.assertEqual([r['id'] for r in page['rows']], [r['id'] for r in everything['rows'][1:3]])
        self.assertEqual([r['unit'] for r in services.list_events(self.conn, machine_id=other['id'], now=NOW)['rows']],
                         ['м/ч'] * 3)
        self.err(400, services.list_events, self.conn, date_from='01.10.2026', now=NOW)


# --------------------------------------------------------------------------------------

class BulkTest(Base):
    def setUp(self):
        super().setUp()
        self.a = self.machine('A')['id']
        self.b = self.machine('B')['id']

    def bulk(self, items=(), baseline=(), date=TODAY, force=False):
        return services.readings_bulk(
            self.conn, {'date': date, 'items': list(items), 'baseline': list(baseline)}, force=force, now=NOW)

    def test_upsert_mark(self):
        res = self.bulk([{'machine_id': self.a, 'reading': '153 000'}, {'machine_id': self.b, 'reading': ''}])
        self.assertEqual(res, {'marks_saved': 1, 'to_saved': 0, 'errors': []})      # пустое показание пропущено
        row = self.conn.execute('SELECT * FROM events').fetchone()
        self.assertEqual((row['kind'], row['source'], row['reading'], row['date']), (KIND_MARK, 'mark', 153000, TODAY))
        # повторный ввод меньшего значения той же датой заменяет показание — без вопросов
        res = self.bulk([{'machine_id': self.a, 'reading': 152500}])
        self.assertEqual(res['marks_saved'], 1)
        self.assertEqual(self.count('SELECT COUNT(*) FROM events'), 1)
        self.assertEqual(self.view(self.a)['reading'], 152500)
        # другая дата — другая отметка
        self.bulk([{'machine_id': self.a, 'reading': 152000}], date=day(-1))
        self.assertEqual(self.count("SELECT COUNT(*) FROM events WHERE source='mark'"), 2)

    def test_upsert_same_date_compares_with_older_facts_only(self):
        """Интеграция: при замене отметки той же датой сама заменяемая отметка в сравнении не участвует
        (SPEC 5: «для отметки-upsert той же даты не спрашивать»), а более ранние факты — участвуют."""
        self.conn.execute('UPDATE machines SET reg_reading=40000 WHERE id=?', (self.a,))
        self.conn.commit()
        self.bulk([{'machine_id': self.a, 'reading': 50000}])
        res = self.bulk([{'machine_id': self.a, 'reading': 45000}])           # ниже своей же отметки — без вопроса
        self.assertEqual(res['marks_saved'], 1)
        self.assertEqual(self.view(self.a)['reading'], 45000)
        self.err(409, self.bulk, [{'machine_id': self.a, 'reading': 39000}])    # ниже показания при регистрации
        self.assertEqual(self.view(self.a)['reading'], 45000)
        self.assertEqual(self.count("SELECT COUNT(*) FROM events WHERE source='mark'"), 1)

    def test_other_sources_untouched(self):
        note = self.event(self.a, KIND_MARK, reading=1000)                # отметка из журнала (manual)
        self.bulk([{'machine_id': self.a, 'reading': 1100}])
        rows = self.conn.execute('SELECT id, source, reading FROM events ORDER BY id').fetchall()
        self.assertEqual([(r['source'], r['reading']) for r in rows], [('manual', 1000), ('mark', 1100)])
        self.assertEqual(rows[0]['id'], note['id'])
        self.assertEqual(self.view(self.a)['reading'], 1100)

    def test_errors_do_not_block_others(self):
        res = self.bulk([{'machine_id': self.a, 'reading': 'сто'},
                         {'machine_id': self.b, 'reading': 5000},
                         {'machine_id': 777, 'reading': 1},
                         {'machine_id': self.a, 'reading': -3}],
                        baseline=[{'machine_id': self.b, 'kind': 'Ремонт', 'reading': 10},
                                  {'machine_id': self.b, 'kind': '', 'reading': 10}])
        self.assertEqual((res['marks_saved'], res['to_saved']), (1, 0))
        self.assertEqual(len(res['errors']), 5)
        by_machine = {(e['machine_id'], e['garage_no']) for e in res['errors']}
        self.assertEqual(by_machine, {(self.a, 'A'), (777, ''), (self.b, 'B')})
        self.assertTrue(all(e['error'] for e in res['errors']))
        self.assertEqual(self.view(self.b)['reading'], 5000)
        self.assertIsNone(self.view(self.a)['reading'])

    def test_soft_warning_409_then_force(self):
        self.bulk([{'machine_id': self.a, 'reading': 152000}], date=day(-5))
        items = [{'machine_id': self.a, 'reading': 151000}, {'machine_id': self.b, 'reading': 700}]
        with self.assertRaises(ApiError) as cm:
            self.bulk(items)
        e = cm.exception
        self.assertEqual(e.status, 409)
        self.assertTrue(e.extra['needs_confirm'])
        self.assertEqual(len(e.extra['items']), 1)
        self.assertEqual((e.extra['items'][0]['machine_id'], e.extra['items'][0]['garage_no']), (self.a, 'A'))
        self.assertIn('меньше последнего факта', e.extra['items'][0]['text'])
        self.assertTrue(e.extra['warnings'][0].startswith('A: '))
        # НИЧЕГО не сохранено — даже строка без предупреждений
        self.assertEqual(self.count('SELECT COUNT(*) FROM events'), 1)
        self.assertIsNone(self.view(self.b)['reading'])
        res = self.bulk(items, force=True)
        self.assertEqual(res['marks_saved'], 2)
        self.assertEqual(self.view(self.a)['reading'], 151000)             # отметка может понизить показание
        self.assertEqual(self.view(self.b)['reading'], 700)

    def test_future_date_is_soft(self):
        with self.assertRaises(ApiError) as cm:
            self.bulk([{'machine_id': self.a, 'reading': 10}], date=day(2))
        self.assertEqual(cm.exception.status, 409)
        self.err(400, services.readings_bulk, self.conn, {'date': '2.10.26', 'items': []}, now=NOW)
        self.err(400, services.readings_bulk, self.conn, {'items': 'x'}, now=NOW)

    def test_baseline(self):
        res = self.bulk(items=[{'machine_id': self.a, 'reading': 153000}],
                        baseline=[{'machine_id': self.a, 'kind': 'ТО-2', 'reading': 150000},          # без даты
                                  {'machine_id': self.b, 'kind': 'ТО-1', 'reading': '125 000',
                                   'date': day(-20), 'work': 'сделали без меня'},
                                  {'machine_id': self.b, 'kind': 'ТО-3', 'reading': None}])
        self.assertEqual(res, {'marks_saved': 1, 'to_saved': 2, 'errors': []})
        rows = {r['machine_id']: r for r in self.conn.execute("SELECT * FROM events WHERE source='baseline'")}
        self.assertEqual(rows[self.a]['date'], TODAY)                       # дата пуста -> общая дата пакета
        self.assertEqual((rows[self.b]['date'], rows[self.b]['work']), (day(-20), 'сделали без меня'))
        va = self.view(self.a)
        # R8 одним пакетом: ТО на 150000, сейчас 153000 -> следующее на 160000, осталось 7000
        self.assertEqual((va['reading'], va['next_to_at'], va['remaining'], va['to_status']),
                         (153000, 160000, 7000, 'ok'))
        self.assertEqual(va['next_to_kind'], 'ТО-3')
        self.assertEqual(va['state'], 'line')                               # baseline-ТО сегодня не ставит «на ТО»
        vb = self.view(self.b)
        self.assertEqual((vb['reading'], vb['last_to']['kind'], vb['to_status']), (125000, 'ТО-1', 'ok'))
        # дубль (машина, дата, вид, показание) не создаётся
        res = self.bulk(baseline=[{'machine_id': self.a, 'kind': 'ТО-2', 'reading': 150000}])
        self.assertEqual(res['to_saved'], 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM events WHERE source='baseline'"), 2)

    def test_checks_see_rows_of_same_batch(self):
        # baseline идёт первым: отметка 140000 ниже только что внесённого ТО на 150000 -> вопрос
        with self.assertRaises(ApiError) as cm:
            self.bulk(items=[{'machine_id': self.a, 'reading': 140000}],
                      baseline=[{'machine_id': self.a, 'kind': 'ТО-1', 'reading': 150000}])
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(self.count('SELECT COUNT(*) FROM events'), 0)


# --------------------------------------------------------------------------------------

class ToDoneTest(Base):
    def setUp(self):
        super().setUp()
        self.mid = self.machine('A')['id']

    def done(self, force=False, **fields):
        data = {'machine_id': self.mid, 'kind': 'ТО-2', 'date': TODAY}
        data.update(fields)
        return services.to_done(self.conn, data, force=force, now=NOW)

    def test_r8_to_lower_than_current(self):
        self.mark(self.mid, 153000)
        res = self.done(reading=150000, work='Замена масла', who='Петров')      # без 409
        m = res['machine']
        self.assertEqual(m['reading'], 153000)
        self.assertEqual((m['next_to_at'], m['remaining'], m['to_status']), (160000, 7000, 'ok'))
        self.assertEqual(m['next_to_kind'], 'ТО-3')
        self.assertEqual(m['state'], 'to')
        self.assertIn('153 000', res['note'])
        ev = res['event']
        self.assertEqual((ev['kind'], ev['source'], ev['reading'], ev['work'], ev['who']),
                         ('ТО-2', 'manual', 150000, 'Замена масла', 'Петров'))
        self.assertIs(ev['is_to'], True)
        # круг ТО: ТО-3 -> ТО-1
        res = self.done(kind='ТО-3', reading=153000)
        self.assertEqual(res['machine']['next_to_kind'], 'ТО-1')
        self.assertEqual(res['note'], '')

    def test_current_reading_makes_mark_today(self):
        self.mark(self.mid, 140000, date=day(-30))
        res = self.done(reading=150000, date=day(-5), current_reading='153 000')
        m = res['machine']
        self.assertEqual((m['reading'], m['reading_base_date'], m['remaining']), (153000, TODAY, 7000))
        mark = self.conn.execute("SELECT * FROM events WHERE source='mark' AND date=?", (TODAY,)).fetchone()
        self.assertEqual((mark['kind'], mark['reading']), (KIND_MARK, 153000))
        self.assertEqual(res['event']['date'], day(-5))
        # показание сейчас совпадает с текущим — новая отметка не нужна
        before = self.count('SELECT COUNT(*) FROM events')
        self.done(kind='ТО-3', reading=153000, current_reading=153000)
        self.assertEqual(self.count('SELECT COUNT(*) FROM events'), before + 1)
        # повторно с другим «сейчас» — отметка сегодняшнего дня заменяется, а не дублируется
        self.done(kind='ТО-1', reading=153000, current_reading=153200)
        self.assertEqual(self.count("SELECT COUNT(*) FROM events WHERE source='mark' AND date=?", TODAY), 1)
        self.assertEqual(self.view(self.mid)['reading'], 153200)

    def test_validation(self):
        self.err(400, self.done, reading=150000, current_reading=149000)
        self.err(400, self.done, reading=None)
        self.err(400, self.done, reading='', kind='ТО-1')
        self.err(400, self.done, kind='Ремонт', reading=1)
        self.err(400, self.done, kind='', reading=1)
        self.err(400, self.done, machine_id=None, reading=1)
        self.err(400, self.done, machine_id=555, reading=1)
        self.err(400, self.done, reading=1, date='сегодня')
        self.assertEqual(self.count('SELECT COUNT(*) FROM events'), 0)

    def test_soft_and_rollback(self):
        self.mark(self.mid, 150000, date=day(-10))
        # «сейчас» с лишней цифрой -> вопрос; ничего не сохранено (ни отметка, ни ТО)
        e = self.err(409, self.done, reading=150500, current_reading=1510000)
        self.assertTrue(e.extra['needs_confirm'])
        self.assertEqual(self.count('SELECT COUNT(*) FROM events'), 1)
        self.err(409, self.done, reading=150500, date=day(1))                # дата в будущем
        self.done(reading=150500, current_reading=1510000, force=True)
        self.assertEqual(self.count('SELECT COUNT(*) FROM events'), 3)

    def test_estimated_reading_is_not_a_base(self):
        self.mark(self.mid, 150000, date=day(-3))
        services.daily_apply(self.conn, {'from': day(-2), 'to': TODAY,
                                         'items': [{'machine_id': self.mid, 'norm': 100}]}, now=NOW)
        before = self.view(self.mid)
        self.assertEqual((before['reading'], before['reading_estimated']), (150300, True))
        # «показание сейчас» не трогали — диалог его не отправляет (отправленное считается фактом, см. AuditFixesTest)
        res = self.done(reading=150300, reading_estimated=True)
        m = res['machine']
        self.assertIs(res['event']['reading_estimated'], True)
        self.assertEqual((m['reading_base'], m['reading_base_date']), (150000, day(-3)))   # база прежняя
        self.assertEqual((m['reading'], m['reading_estimated']), (150300, True))
        self.assertEqual((m['last_to']['reading'], m['remaining']), (150300, 10000))
        self.assertEqual(self.count("SELECT COUNT(*) FROM events WHERE source='mark'"), 1)   # новой отметки нет
        # фактическое показание позже исправляет расчётное
        self.mark(self.mid, 150250)
        after = self.view(self.mid)
        self.assertEqual((after['reading'], after['reading_estimated']), (150250, False))


# --------------------------------------------------------------------------------------

class DailyTest(Base):
    def setUp(self):
        super().setUp()
        self.a = self.machine('A')['id']                       # на линии, база 3 дня назад
        self.b = self.machine('B')['id']                       # в ремонте
        self.c = self.machine('C')['id']                       # сегодня на ТО
        self.d = self.machine('D')['id']                       # нет показаний
        self.e = self.machine('E')['id']                       # показание внесено сегодня
        self.f = self.machine('F', status='На консервации')['id']
        self.g = self.machine('G', status='Списана')['id']
        for mid in (self.a, self.b, self.c, self.f, self.g):
            self.mark(mid, 150000, date=day(-3))
        self.event(self.b, 'Ремонт', date=day(-2))
        self.event(self.c, 'ТО-1', date=TODAY, reading=150000)
        self.mark(self.e, 150000)
        self.all_items = [{'machine_id': m, 'norm': 100}
                          for m in (self.a, self.b, self.c, self.d, self.e, self.f, self.g)]

    def preview(self, date_from=None, date_to=None):
        res = services.daily_preview(self.conn, date_from, date_to, now=NOW)
        return res, {r['garage_no']: r for r in res['rows']}

    def apply(self, date_from, date_to, items=None):
        return services.daily_apply(self.conn, {'from': date_from, 'to': date_to,
                                                'items': self.all_items if items is None else items}, now=NOW)

    def test_preview(self):
        res, rows = self.preview(day(-2), TODAY)
        self.assertEqual((res['from'], res['to']), (day(-2), TODAY))
        self.assertEqual(sorted(rows), ['A', 'B', 'C', 'D', 'E', 'F'])            # без списанной
        a = rows['A']
        self.assertEqual((a['days_to_add'], a['norm'], a['norm_source'], a['add'], a['checked'], a['skip_reason']),
                         (3, 100, 'default', 300, True, ''))
        self.assertEqual((a['state'], a['unit'], a['reading'], a['reading_estimated']), ('line', 'км', 150000, False))
        self.assertEqual((rows['B']['days_to_add'], rows['B']['skip_reason'], rows['B']['checked'], rows['B']['state']),
                         (0, 'в ремонте', False, 'repair'))
        self.assertEqual((rows['C']['days_to_add'], rows['C']['state']), (3, 'to'))      # на ТО начисляется
        self.assertEqual((rows['D']['days_to_add'], rows['D']['skip_reason']), (0, 'нет показаний'))
        self.assertEqual((rows['E']['days_to_add'], rows['E']['skip_reason']), (0, 'показание внесено сегодня'))
        self.assertEqual((rows['F']['days_to_add'], rows['F']['skip_reason']), (0, 'консервация'))
        # дни не позже даты базы не начисляются
        _, wide = self.preview(day(-6), TODAY)
        self.assertEqual(wide['A']['days_to_add'], 3)
        # по умолчанию — сегодня
        res, rows = self.preview()
        self.assertEqual((res['from'], res['to'], rows['A']['days_to_add']), (TODAY, TODAY, 1))
        self.assertEqual((res['last_auto_date'], res['gap_days'], res['suggested_from']), (None, None, TODAY))

    def test_period_validation(self):
        self.err(400, services.daily_preview, self.conn, TODAY, day(-1), now=NOW)       # from > to
        self.err(400, services.daily_preview, self.conn, TODAY, day(1), now=NOW)        # to > сегодня
        self.err(400, services.daily_preview, self.conn, '1.10.2026', TODAY, now=NOW)
        self.err(400, self.apply, TODAY, day(1))
        self.err(400, self.apply, day(-1), day(-2))
        self.err(400, services.daily_apply, self.conn, {'from': TODAY, 'to': TODAY}, now=NOW)
        self.err(400, services.daily_undo, self.conn, {'from': TODAY, 'to': day(-1)}, now=NOW)

    def test_apply_undo(self):
        res = self.apply(day(-2), TODAY)
        self.assertEqual(res, {'created': 6, 'skipped': 5, 'machines': 2})
        rows = self.conn.execute("SELECT * FROM events WHERE source='auto' AND machine_id=? ORDER BY date",
                                 (self.a,)).fetchall()
        self.assertEqual([r['date'] for r in rows], [day(-2), day(-1), TODAY])
        self.assertTrue(all((r['kind'], r['delta'], r['reading'], r['who']) == (KIND_MARK, 100, None, 'авто')
                            for r in rows))
        va = self.view(self.a)
        self.assertEqual((va['reading'], va['reading_estimated'], va['reading_auto_add'], va['reading_base']),
                         (150300, True, 300, 150000))
        self.assertEqual(self.view(self.b)['reading'], 150000)                    # ремонт пропущен
        self.assertEqual(self.view(self.c)['reading'], 150300)
        # повтор не дублирует
        self.assertEqual(self.apply(day(-2), TODAY), {'created': 0, 'skipped': 7, 'machines': 0})
        self.assertEqual(self.count("SELECT COUNT(*) FROM events WHERE source='auto'"), 6)
        res, rows = self.preview(day(-2), TODAY)
        self.assertEqual((rows['A']['days_to_add'], rows['A']['skip_reason']), (0, 'уже накинуто'))
        self.assertEqual((res['last_auto_date'], res['gap_days'], res['suggested_from']), (TODAY, 0, TODAY))
        # отмена периода
        self.assertEqual(services.daily_undo(self.conn, {'from': day(-1), 'to': TODAY}, now=NOW), {'deleted': 4})
        self.assertEqual(self.view(self.a)['reading'], 150100)
        self.assertEqual(services.daily_undo(self.conn, {'from': day(-9), 'to': TODAY}, now=NOW), {'deleted': 2})
        va = self.view(self.a)
        self.assertEqual((va['reading'], va['reading_estimated']), (150000, False))

    def test_norm_from_item_and_default(self):
        res = self.apply(TODAY, TODAY, [{'machine_id': self.a, 'norm': '120,4'}, {'machine_id': self.c}])
        self.assertEqual(res, {'created': 2, 'skipped': 0, 'machines': 2})
        self.assertEqual(self.view(self.a)['reading'], 150120)                    # км — до целого
        self.assertEqual(self.view(self.c)['reading'], 150100)                    # норма не передана — норма машины
        self.assertEqual(self.apply(day(-1), day(-1), [{'machine_id': self.a, 'norm': 0}])['skipped'], 1)
        self.err(400, self.apply, TODAY, TODAY, [{'machine_id': self.a, 'norm': 'много'}])

    def test_mark_after_auto_same_day(self):
        self.apply(day(-2), TODAY, [{'machine_id': self.a, 'norm': 100}])
        self.assertEqual(self.view(self.a)['reading'], 150300)
        self.mark(self.a, 150250)                         # факт сегодня: накинутое за сегодня и раньше не считается
        va = self.view(self.a)
        self.assertEqual((va['reading'], va['reading_estimated'], va['reading_auto_add']), (150250, False, 0))
        # вчера показание было расчётным
        self.assertEqual(self.view(self.a, day(-1))['reading'], 150200)

    def test_suggested_from(self):
        self.apply(day(-2), day(-2), [{'machine_id': self.a, 'norm': 100}])
        res, _ = self.preview()
        self.assertEqual((res['last_auto_date'], res['gap_days'], res['suggested_from']), (day(-2), 2, day(-1)))
        self.conn.execute("UPDATE events SET date=? WHERE source='auto'", (day(-9),))
        self.conn.commit()
        self.mark(self.a, 150000, date=day(-10))
        res, _ = self.preview()
        self.assertEqual((res['gap_days'], res['suggested_from']), (9, TODAY))     # больше недели — только сегодня


# --------------------------------------------------------------------------------------

class PlanTest(Base):
    def fill(self, garage_no, to_reading, now_reading, model='SHACMAN X3000', **fields):
        mid = self.machine(garage_no, model=model, **fields)['id']
        baseline = [] if to_reading is None else [{'machine_id': mid, 'kind': 'ТО-1', 'reading': to_reading,
                                                   'date': day(-40)}]
        items = [] if now_reading is None else [{'machine_id': mid, 'reading': now_reading}]
        services.readings_bulk(self.conn, {'date': TODAY, 'items': items, 'baseline': baseline},
                               force=True, now=NOW)
        return mid

    def test_plan(self):
        soon = self.fill('M1', 125000, 134200)                                   # осталось 800
        over = self.fill('M2', 150000, 162000, model=' shacman x3000 ')          # просрочено на 2000
        self.fill('M3', None, 5000, model='SITRAK C7H')                          # нет ТО
        self.fill('M4', 150000, 153000)                                          # осталось 7000; 75 км/сут -> 94 дня
        self.fill('M5', 100000, 190000, status='Списана')
        self.fill('M6', None, None)                                              # нет показаний
        worse = self.fill('M7', 150000, 172500, model='SITRAK C7H', interval_override=10000)   # просрочено на 12500
        order = self.event(soon, 'Ремонт', date=day(-1), date_out=day(-1), parts_order='Сальник')
        self.event(soon, 'Заметка', date=TODAY, parts_order='Масло 10W-40')

        p = services.plan(self.conn, now=NOW)
        self.assertEqual((p['date'], p['horizon_days']), (TODAY, 30))
        self.assertEqual([r['garage_no'] for r in p['rows']], ['M7', 'M2', 'M1'])   # просроченные по убыванию, затем остаток
        self.assertEqual([r['garage_no'] for r in p['no_to']], ['M3', 'M6'])
        first = p['rows'][0]
        self.assertEqual((first['id'], first['overdue_by'], first['missed']), (worse, 12500, 2))
        self.assertEqual(first['regulation']['to_kind'], 'ТО-2')
        self.assertTrue(first['regulation']['oil'])
        materials = {(m['to_kind'], m['model'].lower()): m for m in p['materials']}
        self.assertEqual(len(materials), 2)
        shacman = materials[('ТО-2', 'shacman x3000')]                             # модель — без учёта регистра и пробелов
        self.assertEqual((shacman['count'], sorted(shacman['garage_nos'])), (2, ['M1', 'M2']))
        self.assertEqual(shacman['filters'], first['regulation']['filters'])
        self.assertEqual(materials[('ТО-2', 'sitrak c7h')]['count'], 1)
        self.assertEqual([e['parts_order'] for e in p['parts_orders']], ['Масло 10W-40', 'Сальник'])   # новые сверху
        self.assertEqual(p['parts_orders'][1]['garage_no'], 'M1')

        services.set_order_done(self.conn, order['id'], True, now=NOW)
        self.assertEqual(len(services.plan(self.conn, now=NOW)['parts_orders']), 1)
        # горизонт: M4 попадает в план при прогнозе в пределах горизонта
        self.assertEqual([r['garage_no'] for r in services.plan(self.conn, 93, now=NOW)['rows']],
                         ['M7', 'M2', 'M1'])
        self.assertEqual([r['garage_no'] for r in services.plan(self.conn, 94, now=NOW)['rows']],
                         ['M7', 'M2', 'M1', 'M4'])
        self.assertEqual(services.plan(self.conn, '60', now=NOW)['horizon_days'], 60)
        self.assertNotIn(over, [r['id'] for r in p['no_to']])
        self.err(400, services.plan, self.conn, 'месяц', now=NOW)

    def test_workorder(self):
        mid = self.machine('77', model='SANY SY215', mtype='Экскаватор', plate='1234 АВ 77', vin='XYZ123')['id']
        self.mark(mid, 1250.5, date=day(-1))
        self.event(mid, 'Ремонт', time_in='08:00', date_out=TODAY, time_out='10:30',
                   work='Замена коробки', parts_used='Масло КПП')
        self.event(mid, 'ТО-1', reading=1250.5, time_in='11:00', work='Замена масла', parts_order='Фильтр')
        self.event(mid, 'Заметка', work='не входит')
        self.event(mid, 'Ремонт', date=day(-1), date_out=day(-1), work='вчерашний')
        w = services.workorder(self.conn, mid, TODAY, now=NOW)
        self.assertEqual(w['contract'], 'Приложение № 1 к договору (образец)')
        self.assertEqual((w['mechanic_post'], w['mechanic_name']), ('Ст. механик', 'Иванов И. И.'))
        self.assertEqual(w['date'], TODAY)
        self.assertEqual(w['machine'], {'id': mid, 'mtype': 'Экскаватор', 'model': 'SANY SY215',
                                        'plate': '1234 АВ 77', 'vin': 'XYZ123', 'garage_no': '77',
                                        'reading': 1250.5, 'reading_estimated': False, 'unit': 'м/ч'})
        self.assertEqual([(x['name'], x['hours']) for x in w['works']],
                         [('Ремонт — Замена коробки', 2.5), ('ТО-1 — Замена масла', None)])
        self.assertEqual(w['parts'], [{'name': 'Масло КПП'}, {'name': 'Фильтр'}])
        self.assertEqual((w['time_start'], w['time_end']), ('08:00', '10:30'))
        self.assertEqual((w['repair_in'], w['repair_out']), (f'{TODAY}T08:00:00', f'{TODAY}T10:30:00'))
        self.assertEqual(w['repair_total_seconds'], 9000)
        empty = services.workorder(self.conn, str(mid), day(-5), now=NOW)
        self.assertEqual((empty['works'], empty['parts'], empty['time_start'], empty['repair_total_seconds']),
                         ([], [], None, None))
        self.assertIsNone(empty['machine']['reading'])                            # на ту дату показаний ещё не было
        self.err(400, services.workorder, self.conn, None, TODAY, now=NOW)
        self.err(404, services.workorder, self.conn, 999, TODAY, now=NOW)


# --------------------------------------------------------------------------------------

class TyresUnitsTest(Base):
    def setUp(self):
        super().setUp()
        self.mid = self.machine('A')['id']
        self.mark(self.mid, 150000)

    def test_tyres(self):
        red = services.create_tyre(self.conn, {'machine_id': self.mid, 'tyre_no': 'Ш-1', 'ttype': '12.00R20',
                                               'position': 'Перед. левая', 'install_date': day(-100),
                                               'resource': '100 000', 'install_reading': 60000}, now=NOW)
        self.assertEqual((red['run_eff'], red['left'], red['wear'], red['status']), (90000, 10000, 0.9, 'red'))
        self.assertEqual((red['garage_no'], red['model'], red['unit']), ('A', 'SHACMAN X3000', 'км'))
        self.assertTrue(red['status_label'])
        green = services.create_tyre(self.conn, {'garage_no': 'a', 'resource': 100000, 'run': 50000}, now=NOW)
        self.assertEqual((green['run_eff'], green['status'], green['tyre_no'], green['install_date']),
                         (50000, 'green', '', None))
        none = services.create_tyre(self.conn, {'machine_id': self.mid, 'resource': 0, 'run': 10}, now=NOW)
        self.assertEqual((none['status'], none['wear'], none['left']), ('none', None, None))
        self.assertEqual(len(services.list_tyres(self.conn, now=NOW)), 3)
        self.assertEqual(self.view(self.mid)['tyres_red'], 1)
        self.assertEqual(services.fleet(self.conn, now=NOW)['summary']['tyres_red'], 1)

        upd = services.update_tyre(self.conn, red['id'], {'install_reading': 80000}, now=NOW)
        self.assertEqual((upd['status'], upd['tyre_no'], upd['resource']), ('green', 'Ш-1', 100000))   # частичное обновление
        self.err(400, services.update_tyre, self.conn, red['id'], {'resource': 'x'}, now=NOW)
        self.err(400, services.create_tyre, self.conn, {'tyre_no': 'без машины'}, now=NOW)
        self.err(400, services.create_tyre, self.conn, {'machine_id': 999}, now=NOW)
        self.err(404, services.update_tyre, self.conn, 999, {'note': 'x'}, now=NOW)
        self.assertEqual(services.delete_tyre(self.conn, none['id']), {'ok': True})
        self.err(404, services.delete_tyre, self.conn, none['id'])
        self.assertEqual(len(services.list_tyres(self.conn, self.mid, now=NOW)), 2)

    def test_units(self):
        yellow = services.create_unit(self.conn, {'machine_id': self.mid, 'unit': 'КПП', 'resource': 10000,
                                                  'install_reading': 142000, 'note': 'после капремонта'}, now=NOW)
        self.assertEqual((yellow['run'], yellow['left'], yellow['wear'], yellow['status']), (8000, 2000, 0.8, 'yellow'))
        self.assertEqual((yellow['unit'], yellow['unit_of_measure'], yellow['garage_no']), ('КПП', 'км', 'A'))
        check = services.create_unit(self.conn, {'machine_id': self.mid, 'unit': 'Двигатель', 'resource': 10000,
                                                 'install_reading': 160000}, now=NOW)
        self.assertEqual(check['status'], 'check')
        self.assertEqual(self.view(self.mid)['units_attention'], 1)
        upd = services.update_unit(self.conn, yellow['id'], {'resource': 100000}, now=NOW)
        self.assertEqual((upd['status'], upd['note']), ('green', 'после капремонта'))
        self.err(400, services.create_unit, self.conn, {'machine_id': self.mid}, now=NOW)        # не указан узел
        self.err(404, services.update_unit, self.conn, 999, {'note': 'x'}, now=NOW)
        self.assertEqual(services.delete_unit(self.conn, check['id']), {'ok': True})
        self.assertEqual([u['id'] for u in services.list_units(self.conn, now=NOW)], [yellow['id']])

    def test_retired_machines_hidden_in_common_lists(self):
        retired = self.machine('R', status='Списана')['id']
        services.create_tyre(self.conn, {'machine_id': retired, 'tyre_no': 'старая'}, now=NOW)
        services.create_unit(self.conn, {'machine_id': retired, 'unit': 'КПП'}, now=NOW)
        self.assertEqual(services.list_tyres(self.conn, now=NOW), [])
        self.assertEqual(services.list_units(self.conn, now=NOW), [])
        self.assertEqual(len(services.list_tyres(self.conn, retired, now=NOW)), 1)      # по самой машине — видны
        self.assertEqual(len(services.list_units(self.conn, retired, now=NOW)), 1)


# --------------------------------------------------------------------------------------

class SettingsTest(Base):
    def test_settings(self):
        res = services.update_settings(self.conn, {'default_norm_km': '120', 'mh_types': 'Экскаватор, Кран',
                                                   'norm_use_fact': False, 'org_mechanic_name': ' Петров П. '})
        self.assertEqual(res['default_norm_km'], 120)
        self.assertEqual(res['mh_types'], ['экскаватор', 'кран'])
        self.assertIs(res['norm_use_fact'], False)
        self.assertEqual(res['org_mechanic_name'], 'Петров П.')
        self.assertEqual(res['default_interval_mh'], 250)                       # остальное не тронуто
        self.assertEqual(db.get_settings(self.conn)['default_norm_km'], 120)
        for bad in ({'default_interval_km': 0}, {'default_norm_mh': 'x'}, {'default_warn_mh': -1},
                    {'to_cycle': []}, {'tyre_red': 90}, {'tyre_yellow': 0.95}, {'rate_window_days': 1.5},
                    {'неизвестно': 1}, {'mh_types': 5}):
            with self.subTest(bad=bad):
                self.err(400, services.update_settings, self.conn, bad)
        self.err(400, services.update_settings, self.conn, ['список'])
        self.assertEqual(db.get_settings(self.conn)['tyre_red'], 0.9)

    def test_settings_change_calculation(self):
        m = self.machine('K', model='КАМАЗ', unit_override='км')
        self.assertEqual(m['interval'], 10000)
        services.update_settings(self.conn, {'default_interval_km': 12000, 'to_cycle': ['ТО-1', 'ТО-2']})
        v = self.view(m['id'])
        self.assertEqual((v['interval'], v['warn']), (12000, 1200))
        services.to_done(self.conn, {'machine_id': m['id'], 'kind': 'ТО-2', 'reading': 100}, now=NOW)
        self.assertEqual(self.view(m['id'])['next_to_kind'], 'ТО-1')

    def test_lists_protection(self):
        kinds = services.update_list(self.conn, 'kinds', ['Заметка', 'Мойка', ' ', 'Мойка'])
        for kind in db.PROTECTED_KINDS:
            self.assertIn(kind, kinds)                                           # обязательные виды вернулись на место
        self.assertIn('Мойка', kinds)
        self.assertEqual(len(kinds), len(db.PROTECTED_KINDS) + 1)
        self.assertEqual(db.get_lists(self.conn)['kinds'], kinds)
        e = self.err(400, services.update_list, self.conn, 'machine_statuses', ['Продана'])
        self.assertIn('не редактируются', e.message)
        self.assertEqual(db.get_lists(self.conn)['machine_statuses'], ['В работе', 'На консервации', 'Списана'])
        self.err(404, services.update_list, self.conn, 'colors', ['x'])
        self.err(400, services.update_list, self.conn, 'sites', 'Северный')
        self.assertEqual(services.update_list(self.conn, 'sites', ['Карьер', 'База']), ['Карьер', 'База'])
        self.assertEqual(services.update_list(self.conn, 'shifts', []), [])

    def test_rules(self):
        rules = services.update_rules(self.conn, [
            {'keyword': ' КАМАЗ ', 'unit': 'км', 'interval': '12 000', 'warn': '', 'daily_norm': 150},
            {'keyword': 'KOMATSU', 'unit': 'м/ч', 'interval': 500, 'warn': 50, 'daily_norm': None}])
        self.assertEqual([(r['keyword'], r['unit'], r['interval'], r['warn'], r['daily_norm']) for r in rules],
                         [('КАМАЗ', 'км', 12000, None, 150), ('KOMATSU', 'м/ч', 500, 50, None)])
        m = self.machine('K', model='КамАЗ 6520')
        self.assertEqual((m['unit'], m['interval'], m['warn'], m['daily_norm'], m['daily_norm_source']),
                         ('км', 12000, 1200, 150, 'rule'))
        for bad in ([{'keyword': '', 'unit': 'км', 'interval': 1}],
                    [{'keyword': 'X', 'unit': 'литры', 'interval': 1}],
                    [{'keyword': 'X', 'unit': 'км', 'interval': 0}],
                    [{'keyword': 'X', 'unit': 'км', 'interval': 10, 'warn': -1}],
                    [{'keyword': 'X', 'unit': 'км', 'interval': 10, 'daily_norm': 0}],
                    ['x'], {'keyword': 'X'}):
            with self.subTest(bad=bad):
                self.err(400, services.update_rules, self.conn, bad)
        self.assertEqual(len(db.get_rules(self.conn)), 2)                        # неудачная замена ничего не меняет
        self.assertEqual(services.update_rules(self.conn, []), [])

    def test_regulation(self):
        rows = services.update_regulation(self.conn, [
            {'to_kind': 'ТО-1', 'periodicity': '10000', 'works': 'Масло', 'oil': '10W-40', 'filters': None}])
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['to_kind'], rows[0]['oil'], rows[0]['filters'], rows[0]['note']),
                         ('ТО-1', '10W-40', '', ''))
        self.err(400, services.update_regulation, self.conn, [{'works': 'без вида'}])
        self.err(400, services.update_regulation, self.conn, 'x')
        self.assertEqual(len(db.get_regulation(self.conn)), 1)


class AuditFixesTest(Base):
    """Дефекты, найденные проверкой на данных заказчика (v10)."""

    def setUp(self):
        super().setUp()
        self.mid = self.machine('A')['id']                    # SHACMAN: км, интервал 10 000

    def daily(self, date_from, date_to, norm=100):
        return services.daily_apply(self.conn, {'from': date_from, 'to': date_to,
                                                'items': [{'machine_id': self.mid, 'norm': norm}]}, now=NOW)

    def test_repair_entered_after_auto_cancels_it(self):
        """R10: ремонт, внесённый после «Накинуть за день» (вечером или задним числом), убирает накинутое за свой день."""
        self.mark(self.mid, 100000, date=day(-2))
        self.assertEqual(self.daily(day(-1), day(-1))['created'], 1)
        self.assertEqual(self.view(self.mid, day(-1))['reading'], 100100)
        rep = self.event(self.mid, 'Ремонт', date=day(-1), time_in='07:00', force=True)
        v = self.view(self.mid, day(-1))
        self.assertEqual((v['state'], v['reading'], v['reading_auto_add'], v['reading_estimated']),
                         ('repair', 100000, 0, False))
        res = services.daily_preview(self.conn, day(-1), day(-1), now=NOW)
        row = res['rows'][0]
        self.assertEqual((row['days_to_add'], row['skip_reason']), (0, 'в ремонте'))
        # ремонт удалили — накинутое снова учитывается (данные не терялись)
        services.delete_event(self.conn, rep['id'])
        self.assertEqual(self.view(self.mid, day(-1))['reading'], 100100)

    def test_parts_order_only_up_to_report_date(self):
        self.mark(self.mid, 100000, date=day(-5))
        self.event(self.mid, 'Ремонт', date=day(-1), parts_order='Фильтр масл. 2 шт')
        self.assertEqual(services.fleet(self.conn, day(-2), now=NOW)['rows'][0]['parts_order'], '')
        self.assertEqual(services.fleet(self.conn, now=NOW)['rows'][0]['parts_order'], 'Фильтр масл. 2 шт')

    def test_to_done_current_equal_to_estimate_becomes_fact(self):
        """«Показание сейчас», введённое человеком, — факт, даже если совпало с расчётным «≈»."""
        self.mark(self.mid, 100000, date=day(-3))
        self.daily(day(-2), TODAY)
        self.assertEqual((self.view(self.mid)['reading'], self.view(self.mid)['reading_estimated']), (100300, True))
        res = services.to_done(self.conn, {'machine_id': self.mid, 'kind': 'ТО-2', 'date': day(-1),
                                           'reading': 100100, 'current_reading': 100300}, now=NOW)
        m = res['machine']
        self.assertEqual((m['reading'], m['reading_estimated'], m['remaining']), (100300, False, 9800))
        self.assertEqual(self.count("SELECT COUNT(*) FROM events WHERE source='mark' AND date=?", TODAY), 1)

    def test_workorder_open_repair_has_no_hours(self):
        """Незакрытый ремонт: часов в заказ-наряде нет (как в v8), число не растёт с каждой печатью."""
        self.mark(self.mid, 100000, date=day(-5))
        self.event(self.mid, 'Ремонт', date=day(-1), time_in='08:00', work='замена кабины')
        w = services.workorder(self.conn, self.mid, day(-1), now=NOW)
        self.assertEqual([(x['name'], x['hours']) for x in w['works']], [('Ремонт — замена кабины', None)])
        self.assertIsNone(w['repair_total_seconds'])

    def test_edit_estimated_to_reading_makes_it_fact(self):
        """В журнале исправили показание ТО, внесённого с «≈», — оно становится фактом."""
        self.mark(self.mid, 100000, date=day(-3))
        self.daily(day(-2), TODAY)
        res = services.to_done(self.conn, {'machine_id': self.mid, 'kind': 'ТО-1', 'date': TODAY,
                                           'reading': 100300, 'reading_estimated': True}, now=NOW)
        self.assertIs(res['event']['reading_estimated'], True)
        ev = services.update_event(self.conn, res['event']['id'], {'reading': 100900}, force=True, now=NOW)
        self.assertIs(ev['reading_estimated'], False)
        m = self.view(self.mid)
        self.assertEqual((m['reading'], m['reading_estimated'], m['next_to_at'], m['remaining']),
                         (100900, False, 110900, 10000))
        # без изменения показания признак «≈» сохраняется как был
        ev2 = services.update_event(self.conn, ev['id'], {'reading_estimated': True, 'work': 'x'}, force=True, now=NOW)
        ev2 = services.update_event(self.conn, ev2['id'], {'work': 'y'}, force=True, now=NOW)
        self.assertIs(ev2['reading_estimated'], True)

    def test_to_far_below_fact_asks(self):
        """ТО на «153» (вместо 153 000) при факте 150 000 — вопрос о разрядности; просто ниже факта — без вопроса (R8)."""
        self.mark(self.mid, 150000, date=day(-1))
        e = self.err(409, services.readings_bulk, self.conn,
                     {'date': TODAY, 'baseline': [{'machine_id': self.mid, 'kind': 'ТО-1', 'reading': 153}]}, now=NOW)
        self.assertIn('разрядност', ' '.join(e.extra['warnings']))
        res = services.readings_bulk(self.conn, {'date': TODAY, 'baseline': [
            {'machine_id': self.mid, 'kind': 'ТО-1', 'reading': 145000}]}, now=NOW)
        self.assertEqual(res['to_saved'], 1)

    def test_bulk_errors_say_which_part_failed(self):
        """Показание сохранено, «Последнее ТО» отклонено — в ошибке part='baseline' (клиент очистит показание)."""
        self.mark(self.mid, 49664, date=day(-1))
        res = services.readings_bulk(self.conn, {'date': TODAY, 'items': [{'machine_id': self.mid, 'reading': 50164}],
                                                 'baseline': [{'machine_id': self.mid, 'kind': 'ТО-1',
                                                               'reading': 'x1'}]}, now=NOW)
        self.assertEqual((res['marks_saved'], res['to_saved']), (1, 0))
        self.assertEqual([(e['machine_id'], e['part']) for e in res['errors']], [(self.mid, 'baseline')])
        res = services.readings_bulk(self.conn, {'date': TODAY, 'items': [{'machine_id': self.mid, 'reading': 'x'}]},
                                     now=NOW)
        self.assertEqual([e['part'] for e in res['errors']], ['item'])


class DateUnknownTest(Base):
    """«Последнее ТО» без даты на экране «Показания»: событие с датой пакета и признаком date_unknown."""

    def setUp(self):
        super().setUp()
        self.mid = self.machine('A')['id']

    def test_bulk_baseline_without_date_is_marked(self):
        services.readings_bulk(self.conn, {'date': TODAY, 'items': [{'machine_id': self.mid, 'reading': 153000}],
                                           'baseline': [{'machine_id': self.mid, 'kind': 'ТО-2', 'reading': 150000}]},
                               now=NOW)
        ev = services.list_events(self.conn, machine_id=self.mid, kind='ТО', now=NOW)['rows'][0]
        self.assertEqual((ev['date'], ev['date_unknown']), (TODAY, True))   # дата = дата пакета, расчёты прежние
        v = self.view(self.mid)
        self.assertEqual((v['last_to']['date'], v['last_to']['date_unknown']), (TODAY, True))
        self.assertEqual((v['next_to_at'], v['remaining'], v['state']), (160000, 7000, 'line'))
        card = services.machine_card(self.conn, self.mid, now=NOW)
        self.assertIs(next(e for e in card['events'] if e['is_to'])['date_unknown'], True)
        # дату указали при правке — признак снимается
        ev = services.update_event(self.conn, ev['id'], {'date': day(-30)}, force=True, now=NOW)
        self.assertIs(ev['date_unknown'], False)

    def test_bulk_baseline_with_date_and_other_events(self):
        services.readings_bulk(self.conn, {'date': TODAY, 'baseline': [
            {'machine_id': self.mid, 'kind': 'ТО-1', 'reading': 150000, 'date': day(-10)}]}, now=NOW)
        self.assertIs(self.view(self.mid)['last_to']['date_unknown'], False)
        self.assertIs(self.event(self.mid, 'Ремонт', reading=151000)['date_unknown'], False)
        self.assertEqual(self.count('SELECT COUNT(*) FROM events WHERE date_unknown<>0'), 0)


class MigrationTest(unittest.TestCase):
    def test_v1_database_gets_date_unknown(self):
        """База версии 1 (без events.date_unknown) обновляется до 2 без потери данных."""
        tmp = Path(tempfile.mkdtemp(prefix='to-uchet-test-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        path = tmp / 'to.db'
        conn = db.open_db(path)
        services.create_machine(conn, {'garage_no': 'A'}, now=NOW)
        services.readings_bulk(conn, {'date': TODAY, 'baseline': [{'machine_id': 1, 'kind': 'ТО-1',
                                                                   'reading': 100}]}, now=NOW)
        # превратить её в базу v1: убрать столбец и откатить номер версии
        conn.execute('ALTER TABLE events DROP COLUMN date_unknown')
        conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
        conn.commit()
        db.close_db(conn)

        conn = db.open_db(path)
        self.addCleanup(db.close_db, conn)
        self.assertEqual(db._schema_version(conn), db.SCHEMA_VERSION)
        self.assertEqual(db.SCHEMA_VERSION, 2)
        rows = [tuple(r) for r in conn.execute('SELECT kind, reading, source, date_unknown FROM events')]
        self.assertEqual(rows, [('ТО-1', 100, 'baseline', 0)])               # данные на месте, признак по умолчанию 0
        self.assertEqual(services.list_machines(conn, now=NOW)[0]['garage_no'], 'A')
        db._migrate_1_to_2(conn)                                              # повторный запуск безопасен


class WorkorderTimesTest(Base):
    """Заказ-наряд: время берётся только из реально заданных time_in/time_out — без выдуманного «00:00»."""

    def setUp(self):
        super().setUp()
        self.mid = self.machine('A')['id']

    def test_no_times_gives_dates_only(self):
        self.event(self.mid, 'Ремонт', date=day(-1), date_out=day(-1), work='без времени')
        w = services.workorder(self.conn, self.mid, day(-1), now=NOW)
        self.assertEqual((w['time_start'], w['time_end']), (None, None))
        self.assertEqual((w['repair_in'], w['repair_out']), (day(-1), day(-1)))
        self.assertNotIn('00:00', w['repair_in'] + w['repair_out'])

    def test_partial_times(self):
        self.event(self.mid, 'Ремонт', date=day(-1), date_out=TODAY, time_out='10:00', work='с выходом')
        self.event(self.mid, 'ТО-1', date=day(-1), reading=1000, time_in='14:00')
        w = services.workorder(self.conn, self.mid, day(-1), now=NOW)
        self.assertEqual((w['time_start'], w['time_end']), ('14:00', '10:00'))
        self.assertEqual((w['repair_in'], w['repair_out']), (f'{day(-1)}T14:00:00', f'{TODAY}T10:00:00'))


class BackupsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='to-uchet-test-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.conn = db.open_db(self.tmp / 'to.db')
        self.addCleanup(db.close_db, self.conn)
        self.backups = self.tmp / 'backups'

    def test_backup_and_restore(self):
        self.assertEqual(services.list_backups(self.backups), [])
        services.create_machine(self.conn, {'garage_no': 'A'}, now=NOW)
        name = services.create_backup(self.conn, self.backups)['name']
        self.assertRegex(name, r'^to-\d{8}-\d{6}\.db$')
        services.create_machine(self.conn, {'garage_no': 'B'}, now=NOW)
        listed = services.list_backups(self.backups)
        self.assertEqual([b['name'] for b in listed], [name])
        self.assertGreater(listed[0]['size'], 0)

        res = services.restore_backup(self.conn, self.backups, name)
        self.assertTrue(res['ok'])
        self.assertEqual([m['garage_no'] for m in services.list_machines(self.conn, now=NOW)], ['A'])
        self.assertEqual(len(services.list_backups(self.backups)), 2)            # + страховочная копия «до»
        services.create_machine(self.conn, {'garage_no': 'C'}, now=NOW)          # база после восстановления рабочая

        for bad in ('../to.db', 'to.db', '', None, 'to-20260101-000000.db.bak'):
            with self.subTest(bad=bad):
                with self.assertRaises(ApiError) as cm:
                    services.restore_backup(self.conn, self.backups, bad)
                self.assertEqual(cm.exception.status, 400)
        with self.assertRaises(ApiError) as cm:
            services.restore_backup(self.conn, self.backups, 'to-20200101-000000.db')
        self.assertEqual(cm.exception.status, 404)

    def test_backups_from_future(self):
        """Часы ПК сбились назад: копии с датой «из будущего» не отключают автокопию и удаляются при ротации первыми."""
        services.create_machine(self.conn, {'garage_no': 'A'}, now=NOW)
        self.backups.mkdir()
        future = [f'to-2099{m:02d}{d:02d}-120000.db' for m in (1, 2) for d in range(1, 16)]
        for name in future:
            shutil.copyfile(self.tmp / 'to.db', self.backups / name)
        auto = db.auto_backup(self.conn, self.backups)
        self.assertIsNotNone(auto)
        self.assertEqual(db.list_backups(self.backups)[0]['name'], auto)          # «последняя» — настоящая
        first = db.backup_now(self.conn, self.backups)
        second = db.backup_now(self.conn, self.backups)
        names = {p.name for p in self.backups.iterdir()}
        self.assertTrue({auto, first, second} <= names)
        self.assertEqual(len(names), db.BACKUP_KEEP)
        self.assertIsNone(db.auto_backup(self.conn, self.backups))               # свежая копия есть — не нужна

    def test_recover_broken_db(self):
        """to.db повреждён: сервер стартует, файл отложен как to.db.broken-…, поднята свежая исправная копия."""
        from app import server
        quiet = mock.patch.object(server, 'log')                                 # без строк журнала в выводе тестов
        quiet.start()                                                            # (enterContext — только с Python 3.11)
        self.addCleanup(quiet.stop)
        services.create_machine(self.conn, {'garage_no': 'A'}, now=NOW)
        good = db.backup_now(self.conn, self.backups)
        (self.backups / 'to-20000101-000000.db').write_bytes(b'not a database')    # битая копия пропускается
        services.create_machine(self.conn, {'garage_no': 'B'}, now=NOW)
        db.close_db(self.conn)
        size = (self.tmp / 'to.db').stat().st_size
        for how in ('half', 'header'):
            with self.subTest(how=how):
                data_dir = self.tmp / how
                (data_dir / 'backups').mkdir(parents=True)
                for p in self.backups.iterdir():
                    shutil.copyfile(p, data_dir / 'backups' / p.name)
                raw = (self.tmp / 'to.db').read_bytes()
                broken = raw[:size // 2] if how == 'half' else b'\0' * 100 + raw[100:]
                (data_dir / 'to.db').write_bytes(broken)
                srv, _, ctx = server.make_server(data_dir, 0, static_dir=self.tmp)
                try:
                    self.assertEqual(ctx.recovered['restored_from'], good)
                    self.assertTrue((data_dir / ctx.recovered['broken']).is_file())
                    self.assertEqual([r[0] for r in ctx.conn.execute('SELECT garage_no FROM machines')], ['A'])
                    self.assertEqual(server.h_bootstrap(ctx, None)['db_recovered'], ctx.recovered)
                finally:
                    server.stop_server(srv)
        # исправной копии нет — пустая база
        data_dir = self.tmp / 'nocopy'
        data_dir.mkdir()
        (data_dir / 'to.db').write_bytes(b'garbage' * 1000)
        srv, _, ctx = server.make_server(data_dir, 0, static_dir=self.tmp)
        try:
            self.assertIsNone(ctx.recovered['restored_from'])
            self.assertEqual(ctx.conn.execute('SELECT COUNT(*) FROM machines').fetchone()[0], 0)
        finally:
            server.stop_server(srv)


if __name__ == '__main__':
    unittest.main()
