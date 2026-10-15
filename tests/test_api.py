"""Тесты HTTP-слоя: сервер на свободном порту во временной папке, запросы через urllib/http.client
(SPEC, раздел 8). Заодно — запуск app/main.py отдельным процессом и сторож выхода."""

import contextlib
import http.client
import importlib.util
import io
import json
import os
import shutil
import signal
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zlib
from datetime import date, timedelta
from pathlib import Path
from unittest import mock
from urllib.parse import quote, unquote

from app import VERSION, db, main, server, services

ROOT = Path(__file__).resolve().parent.parent
HAS_XLSX = importlib.util.find_spec('app.xlsx_io') is not None
TODAY = date.today().isoformat()


def day(n):
    return (date.today() + timedelta(days=n)).isoformat()


STATIC_FILES = {
    'index.html': '<!doctype html><title>Учёт ТО</title>',
    'js/app.js': 'export const x = 1;',
    'css/app.css': 'body { margin: 0 }',
    'icons/truck.svg': '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
    'UI_KIT.md': '# UI-кит',
    'notes.txt': 'текст',
    'data.json': '{"a": 1}',
    'blob.bin': 'двоичное',
}


class ApiCase(unittest.TestCase):
    """Общий сервер на весь класс тестов: временная папка данных и временная статика."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='to-uchet-api-'))
        cls.static = cls.tmp / 'static'
        for name, text in STATIC_FILES.items():
            path = cls.static / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        cls.data_dir = cls.tmp / 'данные с пробелом'
        cls.server, cls.port, cls.ctx = server.make_server(cls.data_dir, 0, static_dir=cls.static)
        server.serve_in_thread(cls.server)
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        server.stop_server(cls.server)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---- помощники ----

    def raw(self, method, path, body=None, headers=None):
        """Запрос -> (статус, заголовки, байты ответа)."""
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', data=body, method=method,
                                     headers=headers or {})
        try:
            with self.opener.open(req, timeout=30) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as err:
            with err:
                return err.code, err.headers, err.read()

    def call(self, method, path, body=None):
        """JSON-запрос -> (статус, разобранный JSON)."""
        data = headers = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers = {'Content-Type': 'application/json'}
        status, resp_headers, payload = self.raw(method, path, data, headers)
        self.assertEqual(resp_headers.get_content_type(), 'application/json', payload[:200])
        return status, json.loads(payload.decode('utf-8'))

    def ok(self, method, path, body=None):
        status, data = self.call(method, path, body)
        self.assertEqual(status, 200, data)
        return data

    def machine(self, garage_no, **fields):
        return self.ok('POST', '/api/machines', {'garage_no': garage_no, 'model': 'SHACMAN X3000',
                                                 'mtype': 'Самосвал', **fields})


class HttpLayerTest(ApiCase):
    def test_ping_and_headers(self):
        status, headers, payload = self.raw('GET', '/api/ping')
        data = json.loads(payload.decode('utf-8'))
        self.assertEqual(status, 200)
        self.assertEqual((data['app'], data['version']), ('to-uchet', VERSION))
        self.assertEqual(headers['Content-Type'], 'application/json; charset=utf-8')
        self.assertEqual(int(headers['Content-Length']), len(payload))
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertFalse([h for h in headers.keys() if h.lower().startswith('access-control')])   # CORS не отдаём

    def test_ping_marks_activity(self):
        self.ctx.last_ping = 0
        self.ok('GET', '/api/ping')
        self.assertGreater(self.ctx.last_ping, 0)

    def _with_host(self, host, path='/api/ping'):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            conn.putrequest('GET', path, skip_host=True)
            if host is not None:
                conn.putheader('Host', host)
            conn.endheaders()
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def test_foreign_host_403(self):
        for host in ('evil.example', f'evil.example:{self.port}', '192.168.1.5', f'127.0.0.1:{self.port + 1}',
                     '127.0.0.1.evil.example', None):
            with self.subTest(host=host):
                status, payload = self._with_host(host)
                self.assertEqual(status, 403)
                self.assertIn('error', json.loads(payload.decode('utf-8')))
                self.assertEqual(self._with_host(host, '/')[0], 403)             # статика тоже закрыта
        for host in (f'127.0.0.1:{self.port}', f'localhost:{self.port}', '127.0.0.1', 'LOCALHOST'):
            with self.subTest(host=host):
                self.assertEqual(self._with_host(host)[0], 200)

    def test_foreign_origin_403(self):
        body = json.dumps({'garage_no': 'чужой'}).encode('utf-8')
        for origin in ('http://evil.example', 'null', f'http://evil.example:{self.port}', f'https://127.0.0.1:{self.port}'):
            with self.subTest(origin=origin):
                status, _, _ = self.raw('POST', '/api/machines', body,
                                        {'Content-Type': 'application/json', 'Origin': origin})
                self.assertEqual(status, 403)
        self.assertEqual(self.raw('POST', '/api/backups', b'', {'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.ok('GET', '/api/machines'), [])            # ничего не создано
        for origin in (f'http://127.0.0.1:{self.port}', f'http://localhost:{self.port}'):
            with self.subTest(origin=origin):
                self.assertEqual(self.raw('GET', '/api/ping', None, {'Origin': origin})[0], 200)

    def test_static_types(self):
        expected = {
            '/': 'text/html; charset=utf-8',
            '/index.html': 'text/html; charset=utf-8',
            '/js/app.js': 'text/javascript; charset=utf-8',
            '/css/app.css': 'text/css; charset=utf-8',
            '/icons/truck.svg': 'image/svg+xml',
            '/UI_KIT.md': 'text/plain; charset=utf-8',
            '/notes.txt': 'text/plain; charset=utf-8',
            '/data.json': 'application/json',
            '/blob.bin': 'application/octet-stream',
        }
        for path, content_type in expected.items():
            with self.subTest(path=path):
                status, headers, payload = self.raw('GET', path)
                self.assertEqual(status, 200)
                self.assertEqual(headers['Content-Type'], content_type)
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertEqual(int(headers['Content-Length']), len(payload))
        status, _, payload = self.raw('GET', '/?v=1#/fleet')
        self.assertEqual((status, payload.decode('utf-8')), (200, STATIC_FILES['index.html']))
        status, headers, payload = self.raw('HEAD', '/js/app.js')
        self.assertEqual((status, payload), (200, b''))
        self.assertEqual(int(headers['Content-Length']), len(STATIC_FILES['js/app.js'].encode('utf-8')))

    def test_static_outside_folder_404(self):
        (self.tmp / 'secret.txt').write_text('секрет', encoding='utf-8')
        for path in ('/../secret.txt', '/js/../../secret.txt', '/..%2fsecret.txt', '/..%5csecret.txt',
                     '/js/..%5c..%5csecret.txt', '/%2e%2e/secret.txt', '//secret.txt', '//../secret.txt',
                     '/' + quote('нет-такого.js'), '/js', '/js/', '/C:/Windows/win.ini', '/index.html/',
                     '/..%2f' + quote(self.data_dir.name) + '/to.db'):
            with self.subTest(path=path):
                conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
                try:
                    conn.request('GET', path)                 # http.client не нормализует путь
                    resp = conn.getresponse()
                    body = resp.read()
                finally:
                    conn.close()
                self.assertEqual(resp.status, 404, body)
                self.assertNotIn('секрет'.encode('utf-8'), body)
        self.assertEqual(self.raw('POST', '/index.html', b'')[0], 405)

    def test_real_static_folder_if_present(self):
        index = server.default_static_dir() / 'index.html'
        if not index.is_file():
            self.skipTest('app/static/index.html ещё нет')
        self.assertTrue(server.default_static_dir().samefile(ROOT / 'app' / 'static'))

    def test_unknown_route_and_method(self):
        status, data = self.call('GET', '/api/nothing')
        self.assertEqual(status, 404)
        self.assertTrue(data['error'])
        self.assertEqual(self.call('DELETE', '/api/fleet')[0], 405)
        self.assertEqual(self.call('GET', '/api/machines/abc/view')[0], 404)
        self.assertEqual(self.call('GET', '/api/machines/99999/view')[0], 404)
        self.assertEqual(self.call('GET', '/api')[0], 404)

    def test_bad_bodies(self):
        status, _, payload = self.raw('POST', '/api/machines', b'{"garage_no": ', {'Content-Type': 'application/json'})
        self.assertEqual(status, 400)
        self.assertIn('JSON', json.loads(payload.decode('utf-8'))['error'])
        status, _, _ = self.raw('POST', '/api/machines', b'{"garage_no": "x"}', {'Content-Type': 'text/plain'})
        self.assertEqual(status, 400)
        self.assertEqual(self.call('POST', '/api/machines', ['не объект'])[0], 400)
        status, _, _ = self.raw('POST', '/api/machines', b'{}', {'Content-Type': 'application/json',
                                                                  'Content-Length': 'abc'})
        self.assertEqual(status, 400)
        # пустое тело — Content-Type не проверяется
        self.assertEqual(self.raw('POST', '/api/backups', b'', {'Content-Type': 'text/plain'})[0], 200)
        # JSON с charset — принимается
        status, _, _ = self.raw('PUT', '/api/settings', '{"org_mechanic_post": "Ст. механик"}'.encode('utf-8'),
                                {'Content-Type': 'application/json; charset=utf-8'})
        self.assertEqual(status, 200)

    def test_body_limit(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=60)
        try:
            body = bytes(server.MAX_BODY + 1)
            conn.request('POST', '/api/import/xlsx?dry_run=1', body=body,
                         headers={'Content-Type': 'application/octet-stream'})
            resp = conn.getresponse()
            data = json.loads(resp.read().decode('utf-8'))
        finally:
            conn.close()
        self.assertEqual(resp.status, 400)
        self.assertIn('40', data['error'])

    def test_internal_error_500(self):
        stderr = io.StringIO()
        with mock.patch.object(services, 'fleet', side_effect=RuntimeError('сломалось')), \
                contextlib.redirect_stderr(stderr):
            status, data = self.call('GET', '/api/fleet')
        self.assertEqual(status, 500)
        self.assertTrue(data['error'].startswith('Внутренняя ошибка: '))
        self.assertIn('сломалось', data['error'])
        self.assertIn('Traceback', stderr.getvalue())                 # traceback ушёл в журнал
        self.assertFalse(self.ctx.conn.in_transaction)
        self.assertEqual(self.call('GET', '/api/fleet')[0], 200)      # сервер жив

    def test_port_busy(self):
        with self.assertRaises(server.PortBusyError):
            server.make_server(self.tmp / 'other', self.port)
        self.assertFalse((self.tmp / 'other' / 'to.db').exists())    # база при занятом порте не открывается


class ScenarioTest(ApiCase):
    """Основные эндпоинты подряд — как ими пользуется фронтенд."""

    def test_1_bootstrap(self):
        boot = self.ok('GET', '/api/bootstrap')
        self.assertEqual((boot['version'], boot['today']), (VERSION, TODAY))
        self.assertTrue(boot['first_run'])
        self.assertTrue(os.path.samefile(boot['data_dir'], self.data_dir))
        for key in ('settings', 'lists', 'rules', 'regulation', 'now'):
            self.assertIn(key, boot)
        self.assertEqual(self.ok('GET', '/api/fleet')['rows'], [])

    def test_2_full_flow(self):
        # --- машины ---
        a = self.machine('А-01', plate='А000АА00', vin='VIN001', site='Северный')
        b = self.machine('Э-02', model='SANY SY215', mtype='Экскаватор')
        self.assertEqual((a['unit'], a['interval'], b['unit'], b['interval']), ('км', 10000, 'м/ч', 250))
        status, data = self.call('POST', '/api/machines', {'garage_no': 'а-01'})
        self.assertEqual(status, 400)
        self.assertIn('уже есть', data['error'])
        self.assertFalse(self.ok('GET', '/api/bootstrap')['first_run'])
        self.assertEqual([m['garage_no'] for m in self.ok('GET', '/api/machines')], ['А-01', 'Э-02'])
        upd = self.ok('PUT', f'/api/machines/{a["id"]}', {'site': 'Мирный'})
        self.assertEqual((upd['site'], upd['plate']), ('Мирный', 'А000АА00'))

        # --- пакетные показания + «последнее ТО» (R4, R8) ---
        res = self.ok('POST', '/api/readings/bulk', {
            'date': TODAY,
            'items': [{'machine_id': a['id'], 'reading': '153 000'}, {'machine_id': b['id'], 'reading': '1250,5'}],
            'baseline': [{'machine_id': a['id'], 'kind': 'ТО-2', 'reading': 150000, 'date': day(-10)}]})
        self.assertEqual(res, {'marks_saved': 2, 'to_saved': 1, 'errors': []})
        view = self.ok('GET', f'/api/machines/{a["id"]}/view')
        self.assertEqual((view['reading'], view['next_to_at'], view['remaining'], view['next_to_kind']),
                         (153000, 160000, 7000, 'ТО-3'))
        # 409 -> force
        low = {'date': TODAY, 'items': [{'machine_id': a['id'], 'reading': 149000}]}
        status, data = self.call('POST', '/api/readings/bulk', low)
        self.assertEqual(status, 409)
        self.assertTrue(data['needs_confirm'])
        self.assertEqual(data['items'][0]['garage_no'], 'А-01')
        self.assertTrue(data['warnings'])
        self.assertEqual(self.ok('GET', f'/api/machines/{a["id"]}/view')['reading'], 153000)

        # --- «ТО сделано» ---
        done = self.ok('POST', '/api/to-done', {'machine_id': b['id'], 'kind': 'ТО-1', 'date': TODAY,
                                                'reading': 1250.5, 'work': 'Замена масла', 'who': 'Петров'})
        self.assertEqual((done['machine']['next_to_kind'], done['machine']['next_to_at'], done['machine']['state']),
                         ('ТО-2', 1500.5, 'to'))
        self.assertEqual(done['event']['kind'], 'ТО-1')
        self.assertEqual(done['note'], '')
        status, data = self.call('POST', '/api/to-done', {'machine_id': b['id'], 'kind': 'ТО-2', 'reading': ''})
        self.assertEqual(status, 400)

        # --- журнал ---
        repair = self.ok('POST', '/api/events', {'garage_no': 'А-01', 'kind': 'Ремонт', 'date': day(-1),
                                                 'time_in': '08:00', 'work': 'Замена КПП',
                                                 'parts_order': 'Сальник КПП'})
        self.assertTrue(repair['open'])
        status, data = self.call('POST', '/api/events', {'machine_id': a['id'], 'kind': 'Ремонт', 'date': TODAY})
        self.assertEqual((status, data['needs_confirm']), (409, True))            # второй открытый ремонт
        self.assertEqual(self.call('POST', '/api/events', {'machine_id': a['id'], 'kind': 'ТО-1',
                                                           'date': TODAY})[0], 400)
        fleet = self.ok('GET', '/api/fleet')
        self.assertEqual(fleet['summary']['total'], 2)
        self.assertEqual((fleet['summary']['repair'], fleet['summary']['to']), (1, 1))
        row = fleet['rows'][0]
        self.assertEqual((row['state'], row['state_event_id'], row['parts_order']),
                         ('repair', repair['id'], 'Сальник КПП'))
        self.assertEqual(self.ok('GET', f'/api/fleet?date={day(-5)}')['rows'][0]['state'], 'line')
        self.assertEqual(self.call('GET', '/api/fleet?date=' + quote('вчера'))[0], 400)

        closed = self.ok('PUT', f'/api/events/{repair["id"]}', {'date_out': TODAY, 'time_out': '09:30'})
        self.assertEqual((closed['open'], closed['work'], closed['duration_seconds']),
                         (False, 'Замена КПП', 25 * 3600 + 1800))
        self.assertEqual(self.ok('GET', f'/api/events/{repair["id"]}')['id'], repair['id'])
        events = self.ok('GET', '/api/events?kind=' + quote('ТО'))
        self.assertEqual(sorted(e['kind'] for e in events['rows']), ['ТО-1', 'ТО-2'])
        events = self.ok('GET', '/api/events?q=' + quote('кпп') + f'&machine_id={a["id"]}')
        self.assertEqual((events['total'], events['rows'][0]['id']), (1, repair['id']))
        self.assertEqual(self.ok('GET', '/api/events?limit=1')['total'], 5)
        self.assertEqual(len(self.ok('GET', '/api/events?limit=1&offset=1')['rows']), 1)

        # --- план и заявки ---
        plan = self.ok('GET', '/api/plan?horizon_days=3650')
        self.assertEqual({r['garage_no'] for r in plan['rows']}, {'А-01', 'Э-02'})
        self.assertEqual([e['id'] for e in plan['parts_orders']], [repair['id']])
        self.assertTrue(plan['materials'])
        self.assertTrue(self.ok('POST', f'/api/events/{repair["id"]}/order-done', {'done': True})['order_done'])
        self.assertEqual(self.ok('GET', '/api/plan')['parts_orders'], [])

        # --- заказ-наряд и карточка ---
        order = self.ok('GET', f'/api/workorder?machine_id={b["id"]}&date={TODAY}')
        self.assertEqual((order['machine']['garage_no'], order['machine']['unit']), ('Э-02', 'м/ч'))
        self.assertEqual(order['works'][0]['name'], 'ТО-1 — Замена масла')
        card = self.ok('GET', f'/api/machines/{a["id"]}/card')
        self.assertEqual(card['machine']['id'], a['id'])
        self.assertEqual(len(card['events']), 3)
        self.assertEqual(card['regulation_next']['to_kind'], 'ТО-3')

        # --- «накинуть за день» ---
        c = self.machine('В-03')
        self.ok('POST', '/api/readings/bulk', {'date': day(-2), 'items': [{'machine_id': c['id'], 'reading': 5000}]})
        preview = self.ok('GET', f'/api/daily/preview?from={day(-1)}&to={TODAY}')
        rows = {r['garage_no']: r for r in preview['rows']}
        self.assertEqual(rows['А-01']['skip_reason'], 'показание внесено сегодня')
        self.assertEqual((rows['В-03']['days_to_add'], rows['В-03']['norm'], rows['В-03']['add']), (2, 100, 200))
        self.assertEqual(self.call('GET', f'/api/daily/preview?from={TODAY}&to={day(1)}')[0], 400)
        before = self.ok('GET', '/api/events?include_auto=1')['total']
        applied = self.ok('POST', '/api/daily/apply', {'from': day(-1), 'to': TODAY,
                                                       'items': [{'machine_id': c['id'], 'norm': 100},
                                                                 {'machine_id': a['id'], 'norm': 100}]})
        self.assertEqual(applied, {'created': 2, 'skipped': 1, 'machines': 1})
        view = self.ok('GET', f'/api/machines/{c["id"]}/view')
        self.assertEqual((view['reading'], view['reading_estimated']), (5200, True))
        self.assertEqual(self.ok('GET', '/api/events?include_auto=1')['total'], before + 2)
        self.assertEqual(self.ok('GET', '/api/events')['total'], before)
        self.assertEqual(self.ok('POST', '/api/daily/undo', {'from': day(-1), 'to': TODAY}), {'deleted': 2})

        # --- шины и узлы ---
        tyre = self.ok('POST', '/api/tyres', {'machine_id': a['id'], 'tyre_no': 'Ш-1', 'resource': 100000,
                                              'install_reading': 60000})
        self.assertEqual((tyre['status'], tyre['garage_no']), ('red', 'А-01'))
        self.assertEqual(self.ok('PUT', f'/api/tyres/{tyre["id"]}', {'resource': 200000})['status'], 'green')
        self.assertEqual(len(self.ok('GET', f'/api/tyres?machine_id={a["id"]}')), 1)
        unit = self.ok('POST', '/api/units', {'machine_id': a['id'], 'unit': 'КПП', 'resource': 10000,
                                              'install_reading': 145000})
        self.assertEqual((unit['status'], unit['unit_of_measure']), ('yellow', 'км'))
        self.assertEqual(self.ok('PUT', f'/api/units/{unit["id"]}', {'note': 'контроль'})['note'], 'контроль')
        self.assertEqual(len(self.ok('GET', '/api/units')), 1)
        self.assertEqual(self.ok('DELETE', f'/api/units/{unit["id"]}'), {'ok': True})

        # --- удаление машины: 409 -> ?force=1 ---
        status, data = self.call('DELETE', f'/api/machines/{a["id"]}')
        self.assertEqual((status, data['needs_confirm']), (409, True))
        self.assertEqual(self.ok('DELETE', f'/api/machines/{a["id"]}?force=1'), {'ok': True})
        self.assertEqual([m['garage_no'] for m in self.ok('GET', '/api/machines')], ['Э-02', 'В-03'])
        self.assertEqual(self.ok('GET', '/api/tyres'), [])

    def test_3_settings_lists_rules(self):
        settings = self.ok('PUT', '/api/settings', {'default_norm_km': 120})
        self.assertEqual((settings['default_norm_km'], settings['default_interval_mh']), (120, 250))
        self.assertEqual(self.ok('GET', '/api/bootstrap')['settings']['default_norm_km'], 120)
        self.assertEqual(self.call('PUT', '/api/settings', {'default_norm_km': 0})[0], 400)

        kinds = self.ok('PUT', '/api/lists/kinds', ['Мойка'])
        self.assertTrue(set(db.PROTECTED_KINDS) <= set(kinds))
        self.assertIn('Мойка', kinds)
        self.assertEqual(self.call('PUT', '/api/lists/machine_statuses', ['x'])[0], 400)
        self.assertEqual(self.call('PUT', '/api/lists/unknown', ['x'])[0], 404)
        self.assertEqual(self.ok('PUT', '/api/lists/sites', ['Карьер']), ['Карьер'])
        self.assertEqual(self.ok('GET', '/api/bootstrap')['lists']['sites'], ['Карьер'])

        rules = self.ok('PUT', '/api/rules', [{'keyword': 'КАМАЗ', 'unit': 'км', 'interval': 12000,
                                               'warn': None, 'daily_norm': None}])
        self.assertEqual([(r['keyword'], r['interval']) for r in rules], [('КАМАЗ', 12000)])
        self.assertEqual(self.call('PUT', '/api/rules', [{'keyword': 'X', 'unit': '?', 'interval': 1}])[0], 400)
        regulation = self.ok('PUT', '/api/regulation', [{'to_kind': 'ТО-1', 'works': 'Масло', 'oil': '10W-40'}])
        self.assertEqual((len(regulation), regulation[0]['oil']), (1, '10W-40'))

    def test_4_backups(self):
        self.machine('Б-77')
        name = self.ok('POST', '/api/backups')['name']
        self.assertRegex(name, r'^to-\d{8}-\d{6}\.db$')
        self.assertIn(name, [b['name'] for b in self.ok('GET', '/api/backups')])
        self.machine('Б-78')
        self.assertTrue(self.ok('POST', '/api/backups/restore', {'name': name})['ok'])
        garages = [m['garage_no'] for m in self.ok('GET', '/api/machines')]
        self.assertIn('Б-77', garages)
        self.assertNotIn('Б-78', garages)
        self.assertEqual(self.call('POST', '/api/backups/restore', {'name': '../to.db'})[0], 400)
        self.assertEqual(self.call('POST', '/api/backups/restore', {'name': 'to-20200101-000000.db'})[0], 404)
        self.assertEqual(self.call('POST', '/api/backups/restore', {})[0], 400)

    @unittest.skipUnless(HAS_XLSX, 'app/xlsx_io.py ещё нет')
    def test_5_export_xlsx(self):
        self.machine('Экспорт-1')
        status, headers, payload = self.raw('GET', '/api/export/xlsx')
        self.assertEqual(status, 200, payload[:300])
        self.assertEqual(headers['Content-Type'], server.XLSX_MIME)
        self.assertEqual(payload[:2], b'PK')                           # xlsx — это zip
        disposition = headers['Content-Disposition']
        self.assertIn(f'attachment; filename="TO_export_{TODAY}.xlsx"', disposition)
        encoded = disposition.split("filename*=UTF-8''")[1]
        self.assertEqual(unquote(encoded), f'Учёт_ТО_{TODAY}.xlsx')    # кириллическое имя
        disposition.encode('latin-1')                                  # заголовок — только ASCII

    @unittest.skipUnless(HAS_XLSX, 'app/xlsx_io.py ещё нет')
    def test_6_import_xlsx_errors(self):
        octet = {'Content-Type': 'application/octet-stream'}
        status, _, payload = self.raw('POST', '/api/import/xlsx?dry_run=1', b'this is not xlsx', octet)
        self.assertEqual(status, 400, payload[:300])
        self.assertTrue(json.loads(payload.decode('utf-8'))['error'])
        status, _, _ = self.raw('POST', '/api/import/xlsx?dry_run=1', b'{}', {'Content-Type': 'application/json'})
        self.assertEqual(status, 400)
        self.assertEqual(self.raw('POST', '/api/import/xlsx?dry_run=1', b'', octet)[0], 400)
        self.assertFalse(self.ctx.conn.in_transaction)

    @unittest.skipUnless(HAS_XLSX, 'app/xlsx_io.py ещё нет')
    def test_7_export_import_roundtrip(self):
        status, _, book = self.raw('GET', '/api/export/xlsx')
        self.assertEqual(status, 200)
        status, _, payload = self.raw('POST', '/api/import/xlsx?dry_run=1', book,
                                      {'Content-Type': 'application/octet-stream'})
        self.assertEqual(status, 200, payload[:500])
        report = json.loads(payload.decode('utf-8'))
        self.assertTrue(report['dry_run'])
        self.assertEqual(report['machines_new'], 0)                   # свои же машины новыми не считаются

        # настоящий импорт: перед изменением базы снимается страховочная копия
        machines_before = [m['garage_no'] for m in self.ok('GET', '/api/machines')]
        self.assertTrue(machines_before)
        backups_before = len(self.ok('GET', '/api/backups'))
        status, _, payload = self.raw('POST', '/api/import/xlsx?dry_run=0', book,
                                      {'Content-Type': 'application/octet-stream'})
        self.assertEqual(status, 200, payload[:500])
        self.assertFalse(json.loads(payload.decode('utf-8'))['dry_run'])
        self.assertEqual(len(self.ok('GET', '/api/backups')), backups_before + 1)
        self.assertEqual([m['garage_no'] for m in self.ok('GET', '/api/machines')], machines_before)
        self.assertFalse(self.ctx.conn.in_transaction)


def png_bytes():
    """Настоящий PNG 1×1 (сигнатура, IHDR, IDAT, IEND с контрольными суммами)."""
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b''))


# Минимальный JPEG: SOI, заголовок JFIF, EOI.
JPEG_BYTES = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9'
OCTET = {'Content-Type': 'application/octet-stream'}


class PhotoApiTest(ApiCase):
    """Фото машин (КОНЦЕПЦИЯ_v2, 9.2): файлы в <данные>/photos/<id>/, список, отдача, удаление."""

    def setUp(self):
        self.mid = self.machine(f'Ф-{self.id().rsplit(".", 1)[-1]}')['id']
        self.base = f'/api/machines/{self.mid}/photos'

    def upload(self, name, data, mid=None, headers=None):
        """POST фото -> (статус, JSON). name=None — без параметра name."""
        path = f'/api/machines/{mid or self.mid}/photos'
        if name is not None:
            path += '?name=' + quote(name, safe='')
        status, resp_headers, payload = self.raw('POST', path, data, headers or OCTET)
        self.assertEqual(resp_headers.get_content_type(), 'application/json', payload[:200])
        return status, json.loads(payload.decode('utf-8'))

    def folder(self, mid=None):
        return self.ctx.photos_dir / str(mid or self.mid)

    def test_upload_list_get(self):
        png = png_bytes()
        status, saved = self.upload('vin.png', png)
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved, {'name': 'vin.png', 'size': len(png), 'url': f'{self.base}/vin.png'})
        self.assertEqual((self.folder() / 'vin.png').read_bytes(), png)
        status, saved2 = self.upload('Кабина.jpeg', JPEG_BYTES)
        self.assertEqual(status, 200, saved2)

        photos = self.ok('GET', self.base)
        self.assertEqual([p['name'] for p in photos], ['vin.png', 'Кабина.jpeg'])     # старые первыми
        self.assertEqual(photos[0]['size'], len(png))
        self.assertEqual(photos[0]['url'], saved['url'])
        self.assertRegex(photos[0]['mtime'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$')

        status, headers, payload = self.raw('GET', saved['url'])
        self.assertEqual((status, payload), (200, png))
        self.assertEqual(headers['Content-Type'], 'image/png')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(int(headers['Content-Length']), len(png))
        status, headers, payload = self.raw('GET', saved2['url'])
        self.assertEqual((status, headers['Content-Type'], payload), (200, 'image/jpeg', JPEG_BYTES))

        card = self.ok('GET', f'/api/machines/{self.mid}/card')            # карточка отдаёт тот же список
        self.assertEqual(card['photos'], photos)

    def test_cyrillic_spaces_and_duplicates(self):
        status, saved = self.upload('Фото кабины 1.JPG', JPEG_BYTES)
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved['name'], 'Фото кабины 1.jpg')
        self.assertEqual(saved['url'], f'{self.base}/{quote("Фото кабины 1.jpg", safe="")}')
        self.assertNotIn(' ', saved['url'])
        status, headers, payload = self.raw('GET', saved['url'])
        self.assertEqual((status, headers['Content-Type'], payload), (200, 'image/jpeg', JPEG_BYTES))

        names = [self.upload('Фото кабины 1.jpg', JPEG_BYTES)[1]['name'],
                 self.upload('Фото кабины 1.jpg', JPEG_BYTES)[1]['name'],
                 self.upload('ФОТО КАБИНЫ 1.jpg', JPEG_BYTES)[1]['name']]        # регистр не важен (Windows)
        self.assertEqual(names, ['Фото кабины 1-2.jpg', 'Фото кабины 1-3.jpg', 'ФОТО КАБИНЫ 1-4.jpg'])

        status, saved = self.upload('C:\\fakepath\\Мой  снимок (копия)!.png', png_bytes())   # путь и лишние знаки
        self.assertEqual((status, saved['name']), (200, 'Мой снимок копия.png'))
        status, saved = self.upload('###.png', png_bytes())                                 # пустое имя
        self.assertRegex(saved['name'], r'^foto-\d{8}-\d{6}\.png$')
        status, saved = self.upload(None, JPEG_BYTES)                                       # без имени — по содержимому
        self.assertRegex(saved['name'], r'^foto-\d{8}-\d{6}\.jpg$')
        self.assertEqual(len(self.ok('GET', self.base)), 7)

    def test_bad_files(self):
        png = png_bytes()
        for name in ('документ.pdf', 'фото', 'фото.', 'image.heic', 'x.png.exe'):
            with self.subTest(name=name):
                status, data = self.upload(name, png)
                self.assertEqual(status, 400)
                self.assertEqual(data['error'], 'Можно загружать только фото: jpg, png, webp, gif')
        for name, data in (('подделка.png', b'MZ\x90\x00 not a png at all'), ('jpeg-как.png', JPEG_BYTES),
                           ('p.jpg', png), ('a.gif', b'GIF'), ('w.webp', b'RIFF\x00\x00\x00\x00WAVEfmt ')):
            with self.subTest(name=name):
                status, body = self.upload(name, data)
                self.assertEqual(status, 400, body)
                self.assertIn('не похож на фото', body['error'])
        status, body = self.upload('пусто.png', b'')
        self.assertEqual(status, 400)
        self.assertIn('пустой', body['error'])
        status, body = self.upload('json.png', png, headers={'Content-Type': 'application/json'})
        self.assertEqual(status, 400)
        self.assertEqual(self.ok('GET', self.base), [])
        self.assertFalse(self.folder().exists() and any(self.folder().iterdir()))

    def test_size_limit(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=60)
        try:
            body = b'\x89PNG\r\n\x1a\n' + bytes(services.PHOTO_MAX_BYTES - 7)
            conn.request('POST', f'{self.base}?name=big.png', body=body, headers=OCTET)
            resp = conn.getresponse()
            data = json.loads(resp.read().decode('utf-8'))
        finally:
            conn.close()
        self.assertEqual(resp.status, 400)
        self.assertEqual(data['error'], 'Фото слишком большое (больше 25 МБ)')
        self.assertEqual(self.ok('GET', self.base), [])

    def test_path_tricks_404(self):
        self.upload('vin.png', png_bytes())
        (self.ctx.data_dir / 'secret.png').write_bytes(png_bytes())
        status, saved = self.upload('../../secret.jpg', JPEG_BYTES)         # путь в имени отбрасывается
        self.assertEqual((status, saved['name']), (200, 'secret.jpg'))
        self.assertTrue((self.folder() / 'secret.jpg').is_file())
        for name in ('..', '..%5C..%5Csecret.png', '..%2F..%2Fsecret.png', '%2E%2E', '..%5Cto.db',
                     'vin.png%00.png', 'vin.png%3Astream.png', '.vin.png.part', 'vin.txt', quote('нет.png')):
            for method in ('GET', 'DELETE'):
                with self.subTest(name=name, method=method):
                    conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
                    try:
                        conn.request(method, f'{self.base}/{name}')        # http.client не нормализует путь
                        resp = conn.getresponse()
                        body = resp.read()
                    finally:
                        conn.close()
                    self.assertEqual(resp.status, 404, body)
                    self.assertNotEqual(body[:8], b'\x89PNG\r\n\x1a\n')
        self.assertTrue((self.ctx.data_dir / 'secret.png').is_file())
        self.assertTrue((self.ctx.data_dir / 'to.db').is_file())
        self.assertEqual(self.raw('GET', f'{self.base}/vin.png')[0], 200)

    def test_delete_photo(self):
        saved = self.upload('vin.png', png_bytes())[1]
        self.upload('vin2.png', png_bytes())
        self.assertEqual(self.ok('DELETE', saved['url']), {'ok': True})
        self.assertEqual([p['name'] for p in self.ok('GET', self.base)], ['vin2.png'])
        self.assertEqual(self.raw('GET', saved['url'])[0], 404)
        self.assertEqual(self.call('DELETE', saved['url'])[0], 404)
        self.assertEqual(self.call('POST', saved['url'])[0], 405)

    def test_delete_machine_removes_folder(self):
        self.upload('vin.png', png_bytes())
        self.assertTrue(self.folder().is_dir())
        status, data = self.call('DELETE', f'/api/machines/{self.mid}')      # фото — тоже история: 409
        self.assertEqual((status, data['needs_confirm']), (409, True))
        self.assertIn('Будет удалено фото: 1', data['warnings'])
        self.assertTrue(self.folder().is_dir())
        self.assertEqual(self.ok('DELETE', f'/api/machines/{self.mid}?force=1'), {'ok': True})
        self.assertFalse(self.folder().exists())
        # машина без фото и истории удаляется без вопросов
        other = self.machine('Ф-без-фото')['id']
        self.assertEqual(self.ok('DELETE', f'/api/machines/{other}'), {'ok': True})

    def test_unknown_machine_404(self):
        for method, path in (('GET', '/api/machines/99999/photos'), ('GET', '/api/machines/99999/photos/vin.png'),
                             ('DELETE', '/api/machines/99999/photos/vin.png')):
            with self.subTest(method=method, path=path):
                status, data = self.call(method, path)
                self.assertEqual(status, 404)
                self.assertEqual(data['error'], 'Машина не найдена')
        status, data = self.upload('vin.png', png_bytes(), mid=99999)
        self.assertEqual((status, data['error']), (404, 'Машина не найдена'))
        self.assertFalse((self.ctx.photos_dir / '99999').exists())
        self.assertEqual(self.ok('GET', self.base), [])                    # у машины без фото — пустой список


# --------------------------------------------------------------------------------------
# main.py
# --------------------------------------------------------------------------------------

class WatchdogTest(unittest.TestCase):
    class FakeCtx:
        last_ping = 0.0

    def test_exit_condition(self):
        ctx = self.FakeCtx()
        dog = main.Watchdog(0.0)
        self.assertFalse(dog.tick(2, ctx, browser_alive=False))        # пингов нет всего 2 с
        self.assertFalse(dog.tick(30, ctx, False))
        self.assertFalse(dog.tick(60, ctx, False))
        self.assertFalse(dog.tick(90, ctx, False))
        self.assertTrue(dog.tick(91, ctx, False))                      # > 90 с без пингов и браузера нет

    def test_browser_alive_keeps_running(self):
        ctx = self.FakeCtx()
        dog = main.Watchdog(0.0)
        for t in range(2, 600, 2):
            self.assertFalse(dog.tick(t, ctx, browser_alive=True))
        self.assertTrue(dog.tick(600, ctx, browser_alive=False))       # браузер закрылся, пингов давно нет

    def test_pings_keep_running(self):
        ctx = self.FakeCtx()
        dog = main.Watchdog(0.0)
        for t in range(2, 600, 2):
            if t % 20 == 0:
                ctx.last_ping = t                                      # фронтенд пингует каждые 20 с
            self.assertFalse(dog.tick(t, ctx, browser_alive=False))

    def test_sleep_gap_resets_ping(self):
        ctx = self.FakeCtx()
        dog = main.Watchdog(0.0)
        self.assertFalse(dog.tick(2, ctx, False))
        # компьютер спал час: между тиками > 30 с — отметка пинга сбрасывается, сразу не выходим
        self.assertFalse(dog.tick(3602, ctx, False))
        self.assertEqual(ctx.last_ping, 3602)
        self.assertFalse(dog.tick(3604, ctx, False))
        self.assertFalse(dog.tick(3604 + 28, ctx, False))
        self.assertFalse(dog.tick(3604 + 56, ctx, False))
        self.assertFalse(dog.tick(3604 + 84, ctx, False))
        self.assertTrue(dog.tick(3604 + 112, ctx, False))                # 110 с без пингов после пробуждения


class MainHelpersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='to-uchet-main-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_args(self):
        args = main.parse_args([])
        self.assertEqual((args.port, args.data, args.no_window), (8765, None, False))
        args = main.parse_args(['--port', '9000', '--data', 'x', '--no-window'])
        self.assertEqual((args.port, args.data, args.no_window), (9000, 'x', True))

    def test_data_dir(self):
        target = self.tmp / 'папка данных' / 'вложенная'
        self.assertEqual(main.choose_data_dir(str(target)), target.resolve())
        self.assertTrue(target.is_dir())
        self.assertEqual(main.base_dir(), ROOT)
        blocked = self.tmp / 'blocked'
        with mock.patch.object(main, 'base_dir', return_value=self.tmp), \
                mock.patch.object(main, '_writable', return_value=False), \
                mock.patch.dict(os.environ, {'APPDATA': str(blocked)}):
            self.assertEqual(main.choose_data_dir(None), blocked / 'TO-Uchet')      # рядом писать нельзя
        with mock.patch.object(main, 'base_dir', return_value=self.tmp):
            self.assertEqual(main.choose_data_dir(None), self.tmp / 'data')

    def test_periodic_checkpoint(self):
        """Периодический wal_checkpoint: данные попадают в сам to.db, пока программа работает
        (если процесс убьют при выключении Windows, to.db без -wal всё равно актуален)."""
        srv, _, ctx = server.make_server(self.tmp, 0, static_dir=self.tmp)
        try:
            with db.LOCK:
                services.create_machine(ctx.conn, {'garage_no': 'CP-1'})
            copy = self.tmp / 'copy.db'
            shutil.copyfile(self.tmp / 'to.db', copy)                   # только главный файл, без -wal
            with contextlib.closing(sqlite3.connect(copy)) as c:
                before = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='machines'").fetchone()[0] and \
                    c.execute('SELECT COUNT(*) FROM machines').fetchone()[0]
            self.assertEqual(before, 0)                                  # пока всё в журнале to.db-wal
            saver = main._Checkpointer(ctx, every=0)
            saver.tick()
            shutil.copyfile(self.tmp / 'to.db', copy)
            with contextlib.closing(sqlite3.connect(copy)) as c:
                self.assertEqual(c.execute('SELECT garage_no FROM machines').fetchall(), [('CP-1',)])
            server.checkpoint(None)                                      # без сервера — ничего не делает
        finally:
            server.stop_server(srv)
        server.checkpoint(ctx)                                           # после остановки — тоже без ошибок

    def test_checkpoint_after_change(self):
        """После каждого изменяющего запроса to.db (без -wal) уже содержит запись: его можно копировать сразу."""
        srv, port, ctx = server.make_server(self.tmp, 0, static_dir=self.tmp)
        try:
            server.serve_in_thread(srv)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            req = urllib.request.Request(
                f'http://127.0.0.1:{port}/api/machines', method='POST', headers={'Content-Type': 'application/json'},
                data=json.dumps({'garage_no': 'WAL-1'}).encode('utf-8'))
            with opener.open(req, timeout=30) as resp:
                self.assertEqual(resp.status, 200)
            copy = self.tmp / 'copy.db'
            shutil.copyfile(self.tmp / 'to.db', copy)                   # только главный файл, без -wal
            with contextlib.closing(sqlite3.connect(copy)) as c:
                self.assertEqual(c.execute('SELECT garage_no FROM machines').fetchall(), [('WAL-1',)])
        finally:
            server.stop_server(srv)

    def test_server_json(self):
        self.assertIsNone(main.read_server_json(self.tmp))
        main.write_server_json(self.tmp, 4321)
        self.assertEqual(main.read_server_json(self.tmp), {'port': 4321, 'pid': os.getpid()})
        self.assertIsNone(main.find_running(self.tmp))                 # свой собственный pid — не «уже запущена»
        main.remove_server_json(self.tmp)
        self.assertFalse((self.tmp / 'server.json').exists())
        (self.tmp / 'server.json').write_text('{"port": 1, "pid": 99999999}', encoding='utf-8')
        self.assertIsNone(main.find_running(self.tmp))                 # процесс не существует
        main.remove_server_json(self.tmp)
        self.assertTrue((self.tmp / 'server.json').exists())           # чужой файл не трогаем
        self.assertTrue(main._pid_alive(os.getpid()))
        self.assertFalse(main._pid_alive(99999999))

    def test_log_redirect(self):
        with mock.patch.object(sys, 'stdout', None), mock.patch.object(sys, 'stderr', None):
            self.assertTrue(main._ensure_streams())
            print('до журнала')                                        # не падает
            main.redirect_to_log(self.tmp)
            print('строка в журнал')
            server.log('ошибка в журнал')
            sys.stdout.close()
            main._placeholder.close()
        text = (self.tmp / 'app.log').read_text(encoding='utf-8')
        self.assertIn('строка в журнал', text)
        self.assertIn('ошибка в журнал', text)
        self.assertNotIn('до журнала', text)


class MainProcessTest(unittest.TestCase):
    """Запуск точки входа отдельным процессом: и как файл, и как модуль."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='to-uchet-proc-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.procs = []
        self.addCleanup(self._kill_all)

    def _kill_all(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            for stream in (proc.stdout, proc.stderr):
                if stream:
                    stream.close()

    def _start(self, cmd, cwd, data_dir):
        flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
        env = dict(os.environ, PYTHONIOENCODING='utf-8')
        proc = subprocess.Popen(cmd + ['--no-window', '--port', '0', '--data', str(data_dir)], cwd=str(cwd),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=flags, env=env)
        self.procs.append(proc)
        return proc

    def _wait_port(self, proc, data_dir):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            # pid в server.json не сравниваем с proc.pid: python.exe из .venv — это пускач,
            # настоящий интерпретатор работает дочерним процессом с другим номером
            info = main.read_server_json(data_dir)
            if info and info.get('port'):
                return info['port']
            if proc.poll() is not None:
                self.fail('процесс завершился: ' + proc.stderr.read().decode('utf-8', 'replace'))
            time.sleep(0.1)
        self.fail('сервер не поднялся за 20 секунд')

    def _check(self, cmd, cwd):
        data_dir = self.tmp / 'data dir'
        proc = self._start(cmd, cwd, data_dir)
        port = self._wait_port(proc, data_dir)
        reply = main.ping_app(port, tries=10)
        self.assertIsNotNone(reply)
        self.assertEqual(reply['app'], 'to-uchet')
        self.assertTrue(os.path.samefile(reply['data_dir'], data_dir))
        self.assertEqual(main.find_running(data_dir), port)
        first_pid = main.read_server_json(data_dir)['pid']

        # второй запуск с той же папкой данных: сервер не поднимает, сообщает адрес первого и выходит
        second = self._start(cmd, cwd, data_dir)
        out, err = second.communicate(timeout=30)
        self.assertEqual(second.returncode, 0, err.decode('utf-8', 'replace'))
        self.assertIn(f'http://127.0.0.1:{port}/', out.decode('utf-8', 'replace'))
        self.assertEqual(main.read_server_json(data_dir)['pid'], first_pid)

        # корректное завершение по Ctrl+Break: база закрыта, server.json удалён
        stopped = False
        if hasattr(signal, 'CTRL_BREAK_EVENT'):
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
                proc.wait(timeout=15)
                stopped = True
            except (OSError, subprocess.TimeoutExpired):
                pass
        if stopped:
            self.assertEqual(proc.returncode, 0, proc.stderr.read().decode('utf-8', 'replace'))
            self.assertFalse((data_dir / 'server.json').exists())
            self.assertFalse((data_dir / 'to.db-wal').exists())
            self.assertTrue((data_dir / 'to.db').is_file())
        self.assertIsNone(main.ping_app(port, tries=1, timeout=0.5) if stopped else None)

    def test_run_as_script(self):
        self._check([sys.executable, str(ROOT / 'app' / 'main.py')], cwd=self.tmp)

    def test_run_as_module(self):
        self._check([sys.executable, '-m', 'app.main'], cwd=ROOT)

    def test_run_dev(self):
        self._check([sys.executable, str(ROOT / 'run_dev.py')], cwd=self.tmp)


if __name__ == '__main__':
    unittest.main()
