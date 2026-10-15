"""Тест производительности (SPEC 4.9, раздел 8): 250 машин и 100 000 событий — /api/fleet не дольше 1.5 с.

Медленный тест (генерация базы ~1–2 с). Пропустить: переменная окружения TO_SKIP_PERF=1.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from app import db, server
from app.logic import KIND_MARK

MACHINES = 250
DAYS = 400                      # событий на машину; всего 250 × 400 = 100 000
FLEET_LIMIT = 1.5               # секунд — ориентир из SPEC
MODELS = ['SHACMAN X3000', 'SITRAK C7H', 'SANY SY215', 'VOLVO FMX', 'КАМАЗ 6520', 'Komatsu D65']
MTYPES = ['Самосвал', 'Самосвал', 'Экскаватор', 'Самосвал', 'Самосвал', 'Бульдозер']


def generate(conn):
    """Заполнить базу: 250 машин, по 400 событий на каждую (отметки, накинутое, ремонты, ТО, заметки)."""
    today = date.today()
    stamp = db.now_iso()
    machines = []
    for i in range(MACHINES):
        k = i % len(MODELS)
        machines.append((i + 1, i + 1, f'Г-{i + 1:03d}', MODELS[k], MTYPES[k], f'А{i:03d}АА77', f'VIN{i:06d}',
                         'Северный' if i % 2 else 'Мирный', 'В работе', stamp, stamp))
    events = []
    for i in range(MACHINES):
        mid = i + 1
        reading = 50000.0 + i * 100
        for n in range(DAYS):
            d = (today - timedelta(days=DAYS - 1 - n)).isoformat()
            step = n % 10
            # (machine_id, date, shift, kind, reading, delta, source, time_in, date_out, time_out, work, parts_order, order_done, who)
            if step == 0:
                reading += 700
                events.append((mid, d, 'День', KIND_MARK, reading, None, 'mark', None, None, None, '', '', 0, ''))
            elif step <= 6:
                events.append((mid, d, '', KIND_MARK, None, 100.0, 'auto', None, None, None, '', '', 0, 'авто'))
            elif step == 7:
                order = 'Фильтр масляный' if n % 50 == 7 else ''
                events.append((mid, d, 'День', 'Ремонт', None, None, 'manual', '08:00', d, '15:30',
                               'Замена сальника', order, 1 if n < DAYS - 60 else 0, 'Петров'))
            elif step == 8:
                events.append((mid, d, 'Ночь', 'Заметка', None, None, 'manual', None, None, None,
                               'Осмотр', '', 0, 'Петров'))
            elif n % 90 == 9:
                events.append((mid, d, 'День', f'ТО-{(n // 90) % 3 + 1}', reading, None, 'manual', None, None, None,
                               'Замена масла', '', 0, 'Петров'))
            else:
                events.append((mid, d, 'День', 'Простой', None, None, 'manual', '10:00', d, '12:00',
                               'Нет топлива', '', 0, 'Петров'))
    tyres = [(mid, f'Ш-{mid}-{j}', '12.00R20', 'Перед. левая', 100000.0, 50000.0 + mid * 100)
             for mid in range(1, MACHINES + 1) for j in range(4)]
    units = [(mid, 'КПП', 20000.0, 45000.0) for mid in range(1, MACHINES + 1)]

    with db.transaction(conn):
        conn.executemany(
            'INSERT INTO machines(id, pos, garage_no, model, mtype, plate, vin, site, status, created_at, updated_at) '
            'VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', machines)
        conn.executemany(
            'INSERT INTO events(machine_id, date, shift, kind, reading, delta, source, time_in, date_out, time_out, '
            'work, parts_order, order_done, who, created_at, updated_at) '
            f"VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{stamp}', '{stamp}')", events)
        conn.executemany(
            'INSERT INTO tyres(machine_id, tyre_no, ttype, position, resource, install_reading) VALUES(?, ?, ?, ?, ?, ?)',
            tyres)
        conn.executemany('INSERT INTO units(machine_id, unit, resource, install_reading) VALUES(?, ?, ?, ?)', units)
    return len(events)


@unittest.skipIf(os.environ.get('TO_SKIP_PERF') == '1', 'медленный тест отключён (TO_SKIP_PERF=1)')
class PerfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='to-uchet-perf-'))
        cls.server, cls.port, cls.ctx = server.make_server(cls.tmp / 'data', 0, static_dir=cls.tmp)
        with db.LOCK:
            cls.events = generate(cls.ctx.conn)
        server.serve_in_thread(cls.server)
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        server.stop_server(cls.server)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def timed(self, path, tries=3):
        """Лучшее время из нескольких запросов (сглаживает случайную загрузку компьютера) и ответ."""
        best = data = None
        for _ in range(tries):
            started = time.perf_counter()
            with self.opener.open(f'http://127.0.0.1:{self.port}{path}', timeout=60) as resp:
                payload = resp.read()
            elapsed = time.perf_counter() - started
            data = json.loads(payload.decode('utf-8'))
            if best is None or elapsed < best:
                best = elapsed
        return best, data

    def test_fleet(self):
        self.assertEqual(self.events, MACHINES * DAYS)
        self.assertEqual(self.ctx.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], 100000)
        elapsed, fleet = self.timed('/api/fleet')
        self.assertEqual(len(fleet['rows']), MACHINES)
        self.assertEqual(fleet['summary']['total'], MACHINES)
        row = fleet['rows'][0]
        self.assertIsNotNone(row['reading'])
        self.assertIsNotNone(row['last_to'])
        self.assertEqual(row['rate_source'], 'fact')
        self.assertLessEqual(elapsed, FLEET_LIMIT, f'/api/fleet: {elapsed:.2f} с при норме {FLEET_LIMIT} с')

    def test_other_heavy_endpoints(self):
        today = date.today()
        week_ago = (today - timedelta(days=6)).isoformat()
        limits = {
            '/api/machines': 2.0,
            '/api/plan?horizon_days=30': 2.5,
            f'/api/daily/preview?from={week_ago}&to={today.isoformat()}': 3.0,
            '/api/events?limit=500': 1.5,
            '/api/events?limit=500&include_auto=1&q=%D1%81%D0%B0%D0%BB%D1%8C%D0%BD%D0%B8%D0%BA': 2.5,
            '/api/machines/1/card': 1.5,
            '/api/tyres': 2.0,
        }
        for path, limit in limits.items():
            with self.subTest(path=path):
                elapsed, _ = self.timed(path, tries=2)
                self.assertLessEqual(elapsed, limit, f'{path}: {elapsed:.2f} с')


if __name__ == '__main__':
    unittest.main()
