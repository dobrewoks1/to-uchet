"""Запуск для разработки: сервер без окна браузера, до Ctrl+C.

    python run_dev.py [--port 8765] [--data dev-data]
    python run_dev.py --demo            # демо-парк: папка demo-data, база создаётся seed_demo.py при первом запуске

То же, что «python -m app.main --no-window», но данные по умолчанию — в папке dev-data
рядом с проектом (чтобы не трогать рабочую базу в data/). Потом открыть http://127.0.0.1:<порт>/ в браузере.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import main as app_main  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='Учёт ТО — запуск для разработки (без окна)')
    parser.add_argument('--port', type=int, default=app_main.DEFAULT_PORT,
                        help=f'порт (по умолчанию {app_main.DEFAULT_PORT}; 0 — любой свободный)')
    parser.add_argument('--data', default=None,
                        help='папка данных (по умолчанию dev-data в проекте, с --demo — demo-data)')
    parser.add_argument('--demo', action='store_true',
                        help='демо-данные: вымышленный парк из 20 машин (seed_demo.py), если базы ещё нет')
    parser.add_argument('--no-window', action='store_true', help=argparse.SUPPRESS)   # и так без окна
    args = parser.parse_args()
    data = Path(args.data or ('demo-data' if args.demo else 'dev-data'))
    if not data.is_absolute():
        data = ROOT / data
    if args.demo and not (data / 'to.db').exists():
        import seed_demo
        report = seed_demo.seed(data)
        print(f'Демо-база создана: {data / "to.db"} — машин {report["machines_new"]}, '
              f'записей журнала {report["events_new"]}', flush=True)
    return app_main.main(['--no-window', '--port', str(args.port), '--data', str(data)])


if __name__ == '__main__':
    sys.exit(main())
