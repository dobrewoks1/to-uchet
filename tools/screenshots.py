"""Скриншоты главных экранов на демо-данных (docs/img/*.png) — без Playwright, через headless Edge/Chrome.

    python tools/screenshots.py [--data demo-data] [--out docs/img] [--scale 1]

Сервер программы поднимается в этом же процессе на свободном порту, на папке с демо-данными
(базы нет — она создаётся seed_demo.py). Браузер — первый найденный на Chromium (как у самой программы:
Edge, Chrome, …); он запускается с отдельным временным профилем в режиме --headless --screenshot,
поэтому профиль и вкладки пользователя не затрагиваются. Данные не меняются: снимаются только экраны.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import db, server  # noqa: E402
from app import main as app_main  # noqa: E402

WIDTH = 1366              # окно, под которое сделан главный экран (docs/КОНЦЕПЦИЯ_v2.md, п. 4.2)

# имя файла, адрес экрана ({id:гар. №} — id машины по гаражному номеру, {today} — сегодня), высота окна
SHOTS = [
    ('fleet.png', '#/fleet', 768),
    ('card.png', '#/machine/{id:106}', 1000),
    ('plan.png', '#/plan', 1150),
    ('workorder.png', '#/workorder?machine_id={id:105}&date={today}', 1000),
    ('tyres.png', '#/tyres', 600),
    ('settings-data.png', '#/settings?tab=data', 768),
]


def resolve(route, conn):
    """Подставить в адрес id машин и сегодняшнюю дату."""
    route = route.replace('{today}', date.today().isoformat())
    while '{id:' in route:
        start = route.index('{id:')
        end = route.index('}', start)
        garage_no = route[start + 4:end]
        row = conn.execute('SELECT id FROM machines WHERE garage_no=?', (garage_no,)).fetchone()
        if row is None:
            raise SystemExit(f'Нет машины с гаражным № {garage_no} — это не демо-база?')
        route = route[:start] + str(row['id']) + route[end + 1:]
    return route


def shoot(browser, url, path, height, scale, profile):
    cmd = [browser, '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
           '--no-default-browser-check', '--disable-extensions', f'--user-data-dir={profile}',
           f'--window-size={WIDTH},{height}', f'--force-device-scale-factor={scale}',
           '--virtual-time-budget=10000', f'--screenshot={path}', url]
    if path.exists():
        path.unlink()
    subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=120, check=False)
    if not path.exists():
        raise SystemExit(f'Браузер не сохранил {path.name} ({url})')


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description='Скриншоты экранов «Учёт ТО» на демо-данных')
    parser.add_argument('--data', default='demo-data', help='папка демо-данных (по умолчанию demo-data)')
    parser.add_argument('--out', default='docs/img', help='куда сохранить PNG (по умолчанию docs/img)')
    parser.add_argument('--scale', type=float, default=1.0, help='масштаб (device scale factor), по умолчанию 1')
    parser.add_argument('--only', nargs='*', help='снять только эти файлы (например, fleet.png card.png)')
    args = parser.parse_args(argv)

    data = Path(args.data) if Path(args.data).is_absolute() else ROOT / args.data
    out = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    if not (data / 'to.db').exists():
        import seed_demo
        seed_demo.seed(data)
        print(f'Демо-база создана: {data / "to.db"}')

    found = app_main.find_browser()
    if not found:
        raise SystemExit('Не найден браузер на Chromium (Edge, Chrome, …) — снять скриншоты нечем')
    name, browser = found
    print(f'Браузер: {name}')

    srv, port, ctx = server.make_server(data, 0)
    server.serve_in_thread(srv)
    profile = Path(tempfile.mkdtemp(prefix='to-uchet-shots-'))
    try:
        for file_name, route, height in SHOTS:
            if args.only and file_name not in args.only:
                continue
            with db.LOCK:
                url = f'http://127.0.0.1:{port}/' + resolve(route, ctx.conn)
            shoot(browser, url, out / file_name, height, args.scale, profile)
            print(f'  {file_name}  ← {url}')
    finally:
        server.stop_server(srv)
        shutil.rmtree(profile, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
