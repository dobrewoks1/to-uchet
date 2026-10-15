"""Точка входа программы «Учёт ТО» (SPEC, раздел 2).

Поднимает HTTP-сервер на 127.0.0.1 и открывает окно браузера на Chromium (Edge, Chrome, Яндекс,
Brave, Vivaldi, Chromium, Opera) в режиме --app; ни одного нет — браузер по умолчанию.
Запускается тремя способами: «python -m app.main», «python app/main.py» и как exe (PyInstaller onefile).
Код совместим с Python 3.8+ (сборка для старых ПК — 32-бит, Python 3.8, Windows 7 SP1 и новее).

Аргументы: --port N (по умолчанию 8765; 0 — любой свободный), --data ПАПКА, --no-window (без окна, до Ctrl+C).

Порядок старта: папка данных -> журнал (если нет консоли) -> «не запущена ли уже?» (server.json, затем
занятый порт + /api/ping) -> сервер -> server.json -> окно -> сторож выхода -> корректное завершение.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

# При запуске файлом (python app/main.py) в sys.path лежит папка app/, а не корень проекта —
# добавляем корень, чтобы работали абсолютные импорты «from app import ...».
if __package__ in (None, ''):
    _root = str(Path(__file__).resolve().parent.parent)
    if _root not in sys.path:
        sys.path.insert(0, _root)

from app import VERSION  # noqa: E402
from app import server as http_server  # noqa: E402

DEFAULT_PORT = 8765
APP_DIR_NAME = 'TO-Uchet'
PING_TIMEOUT = 90          # сколько секунд без обращений фронтенда считается «окно закрыто»
SLEEP_GAP = 30             # пауза между тиками сторожа больше этой — компьютер спал
LOG_MAX_BYTES = 2 * 1024 * 1024
CHECKPOINT_EVERY = 300     # раз в столько секунд журнал базы (to.db-wal) переносится в to.db

_placeholder = None        # заглушка вместо отсутствующих stdout/stderr (exe без консоли)


# --------------------------------------------------------------------------------------
# Папки и журнал
# --------------------------------------------------------------------------------------

def base_dir():
    """Базовая папка: рядом с exe (в сборке) или корень проекта (родитель app/)."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _writable(folder):
    """Можно ли писать в папку (создаёт её при необходимости)."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f'.write-test-{os.getpid()}'
        probe.write_bytes(b'')
        probe.unlink()
        return True
    except OSError:
        return False


def choose_data_dir(arg=None):
    """Папка данных: --data, иначе <базовая>/data, если туда можно писать, иначе %APPDATA%/TO-Uchet."""
    if arg:
        folder = Path(arg).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        # resolve() — после mkdir: в Python 3.8 на Windows он оставляет несуществующий относительный путь относительным
        return folder.resolve()
    local = base_dir() / 'data'
    if _writable(local):
        return local
    folder = Path(os.environ.get('APPDATA') or Path.home()) / APP_DIR_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def browser_profile_dir():
    """Профиль браузера — в %LOCALAPPDATA% (не в папке данных: она может жить на флешке)."""
    root = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA') or str(Path.home())
    folder = Path(root) / APP_DIR_NAME / 'browser-profile'
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return folder


def _ensure_streams():
    """В exe без консоли sys.stdout/sys.stderr равны None — ставим заглушку, чтобы print и argparse
    не падали до того, как откроется app.log. Возвращает True, если консоли нет."""
    global _placeholder
    if sys.stdout is not None and sys.stderr is not None:
        return False
    _placeholder = open(os.devnull, 'w', encoding='utf-8')
    if sys.stdout is None:
        sys.stdout = _placeholder
    if sys.stderr is None:
        sys.stderr = _placeholder
    return True


def redirect_to_log(data_dir):
    """Перенаправить отсутствующие stdout/stderr в <data>/app.log (до старта сервера)."""
    path = Path(data_dir) / 'app.log'
    try:
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            os.replace(path, path.with_name('app.log.1'))     # старый журнал — один файл про запас
        stream = open(path, 'a', encoding='utf-8', errors='replace', buffering=1)
    except OSError:
        return
    if sys.stdout is _placeholder or sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is _placeholder or sys.stderr is None:
        sys.stderr = stream


def _safe_console():
    """Консоль Windows может не уметь часть символов (≈, «», эмодзи) — заменять, а не падать."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError, OSError):
            pass


def _message_box(text):
    """Сообщение об ошибке окном (в exe без консоли пользователь иначе ничего не увидит)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, 'Учёт ТО', 0x10)
    except Exception:
        pass


# --------------------------------------------------------------------------------------
# Один экземпляр
# --------------------------------------------------------------------------------------

def _same_dir(a, b):
    """Одна и та же папка? Учитывает разное написание пути (регистр, короткие имена вида WINDOW~1)."""
    try:
        return os.path.samefile(str(a), str(b))
    except (OSError, ValueError):
        pass
    try:
        return os.path.normcase(os.path.realpath(str(a))) == os.path.normcase(os.path.realpath(str(b)))
    except (OSError, TypeError, ValueError):
        return False


def _pid_alive(pid):
    """Жив ли процесс с таким номером. (os.kill(pid, 0) на Windows использовать нельзя — он завершает процесс.)"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == 'nt':
        try:
            import ctypes
            from ctypes import wintypes
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            handle = kernel32.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return True
                return code.value == 259                            # STILL_ACTIVE
            finally:
                kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
                kernel32.CloseHandle(handle)
        except Exception:
            return True          # не смогли проверить — пусть решит пинг
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def ping_app(port, tries=3, timeout=1.5):
    """GET /api/ping на 127.0.0.1:port. Ответ нашей программы -> dict, иначе None."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # без системного прокси
    for attempt in range(tries):
        try:
            with opener.open(f'http://127.0.0.1:{int(port)}/api/ping', timeout=timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            if isinstance(data, dict) and data.get('app') == 'to-uchet':
                return data
            return None
        except (OSError, ValueError):
            if attempt + 1 < tries:
                time.sleep(0.3)
    return None


def read_server_json(data_dir):
    try:
        info = json.loads((Path(data_dir) / 'server.json').read_text(encoding='utf-8'))
        return info if isinstance(info, dict) else None
    except (OSError, ValueError):
        return None


def write_server_json(data_dir, port):
    """server.json пишется после успешного bind: {"port":.., "pid":..}."""
    path = Path(data_dir) / 'server.json'
    path.write_text(json.dumps({'port': port, 'pid': os.getpid()}), encoding='utf-8')


def remove_server_json(data_dir):
    """Удалить server.json при выходе — только свой (чужой экземпляр мог записать его заново)."""
    path = Path(data_dir) / 'server.json'
    info = read_server_json(data_dir)
    if info is not None and info.get('pid') != os.getpid():
        return
    try:
        path.unlink()
    except OSError:
        pass


def _is_our_instance(reply, data_dir):
    """Ответ /api/ping принадлежит экземпляру с этой же папкой данных."""
    if reply is None:
        return False
    other = reply.get('data_dir')
    return other is None or _same_dir(other, data_dir)


def find_running(data_dir):
    """Порт уже запущенного экземпляра с этой папкой данных (по server.json) или None."""
    info = read_server_json(data_dir)
    if not info or info.get('pid') == os.getpid() or not _pid_alive(info.get('pid')):
        return None
    try:
        port = int(info.get('port'))
    except (TypeError, ValueError):
        return None
    return port if _is_our_instance(ping_app(port), data_dir) else None


# --------------------------------------------------------------------------------------
# Окно
# --------------------------------------------------------------------------------------

# Браузеры на Chromium (у всех работает --app), в порядке предпочтения:
# (название для журнала, пути относительно корней из _browser_roots(), имя в реестре App Paths или None).
BROWSERS = (
    ('Microsoft Edge', (r'Microsoft\Edge\Application\msedge.exe',), 'msedge.exe'),
    ('Google Chrome', (r'Google\Chrome\Application\chrome.exe',), 'chrome.exe'),
    ('Яндекс.Браузер', (r'Yandex\YandexBrowser\Application\browser.exe',), None),
    ('Brave', (r'BraveSoftware\Brave-Browser\Application\brave.exe',), 'brave.exe'),
    ('Vivaldi', (r'Vivaldi\Application\vivaldi.exe',), 'vivaldi.exe'),
    ('Chromium', (r'Chromium\Application\chrome.exe',), None),
    ('Opera', (r'Opera\opera.exe', r'Opera\launcher.exe',
               r'Programs\Opera\opera.exe', r'Programs\Opera\launcher.exe'), 'opera.exe'),
)
WINDOW_SIZE = (1400, 900)      # размер окна программы, если он помещается на экране


def _browser_roots():
    """Папки установки программ. ProgramW6432 нужен 32-битному exe на 64-битной Windows: там
    %ProgramFiles% указывает на «Program Files (x86)», а 64-битный Chrome стоит в «Program Files»."""
    roots = []
    for name in ('ProgramFiles(x86)', 'ProgramFiles', 'ProgramW6432', 'LOCALAPPDATA'):
        value = os.environ.get(name)
        if value and os.path.normcase(value) not in (os.path.normcase(r) for r in roots):
            roots.append(value)
    return roots


def _registry_app_path(exe_name):
    """Путь из реестра «App Paths» (браузер установлен не в стандартную папку) или None."""
    try:
        import winreg
    except ImportError:
        return None
    sub = 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\' + exe_name
    views = (getattr(winreg, 'KEY_WOW64_64KEY', 0), getattr(winreg, 'KEY_WOW64_32KEY', 0))
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in views:
            try:
                with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | view) as key:
                    value = winreg.QueryValueEx(key, '')[0]
            except OSError:
                continue
            if not isinstance(value, str):
                continue
            path = Path(os.path.expandvars(value.strip().strip('"')))
            try:
                if path.name.lower() == exe_name and path.is_file():
                    return str(path)
            except OSError:
                continue
    return None


def find_browser():
    """Первый найденный браузер из BROWSERS: (название, путь к exe); ни одного -> None."""
    roots = _browser_roots()
    for name, subs, exe_name in BROWSERS:
        for sub in subs:
            for root in roots:
                path = Path(root) / sub
                try:
                    if path.is_file():
                        return name, str(path)
                except OSError:
                    pass
        if exe_name:
            path = _registry_app_path(exe_name)
            if path:
                return name, path
    return None


def screen_size():
    """Рабочая область основного экрана (без панели задач): (ширина, высота) или None, если не узнать.
    Процесс не объявляет поддержку DPI, поэтому Windows отдаёт размер в тех же «логических» точках,
    в которых браузер понимает --window-size."""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):     # SPI_GETWORKAREA
            width, height = rect.right - rect.left, rect.bottom - rect.top
        else:
            width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)   # SM_CXSCREEN, SM_CYSCREEN
        if width > 0 and height > 0:
            return int(width), int(height)
    except Exception:
        pass
    return None


def window_args(screen):
    """Размер окна: 1400×900, если помещается; иначе (ноутбук 1366×768 и т.п.) — развернуть на весь экран.
    Вместе их не передавать: --window-size переводит окно в обычное состояние и отменяет --start-maximized."""
    width, height = WINDOW_SIZE
    if screen is not None and (screen[0] < width or screen[1] < height):
        return ['--start-maximized']
    return [f'--window-size={width},{height}']


def open_window(port):
    """Открыть окно программы: браузер на Chromium в режиме --app со своим профилем. Возвращает процесс
    браузера (Popen) или None, если браузер не найден (тогда — обычный браузер по умолчанию)."""
    url = f'http://127.0.0.1:{port}/'
    found = find_browser()
    if found:
        name, exe = found
        size = window_args(screen_size())
        cmd = [exe, f'--app={url}', f'--user-data-dir={browser_profile_dir()}', '--no-first-run',
               '--no-default-browser-check', '--disable-background-mode'] + size
        http_server.log(f'Окно программы: {name} ({exe}), {" ".join(size)}')
        try:
            return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError:
            http_server.log('Не удалось запустить браузер\n' + traceback.format_exc())
    else:
        http_server.log('Браузер на Chromium (Edge, Chrome, Яндекс, Brave, Vivaldi, Chromium, Opera) не найден — '
                        'открываю браузер по умолчанию')
    try:
        webbrowser.open(url)
    except Exception:
        http_server.log('Не удалось открыть браузер по умолчанию\n' + traceback.format_exc())
    return None


# --------------------------------------------------------------------------------------
# Сторож выхода
# --------------------------------------------------------------------------------------

class Watchdog:
    """Единое условие выхода: процесс браузера завершился (или не запускался) И от фронтенда
    нет обращений дольше ping_timeout секунд. Время считается собственными тиками: если между
    тиками прошло больше sleep_gap секунд (компьютер спал) — отметка последнего обращения
    сбрасывается на «сейчас», чтобы программа не закрылась сразу после пробуждения."""

    def __init__(self, now, ping_timeout=PING_TIMEOUT, sleep_gap=SLEEP_GAP):
        self.last_tick = now
        self.ping_timeout = ping_timeout
        self.sleep_gap = sleep_gap

    def tick(self, now, ctx, browser_alive):
        """Один тик. ctx.last_ping — время последнего обращения (те же часы, что и now). True — пора выходить."""
        if now - self.last_tick > self.sleep_gap:
            ctx.last_ping = now
        self.last_tick = now
        if browser_alive:
            return False
        return now - ctx.last_ping > self.ping_timeout


class _Checkpointer:
    """Раз в CHECKPOINT_EVERY секунд переносит журнал базы (to.db-wal) в to.db."""

    def __init__(self, ctx, every=None):
        self.ctx = ctx
        self.every = CHECKPOINT_EVERY if every is None else every
        self.last = time.monotonic()

    def tick(self):
        now = time.monotonic()
        if now - self.last >= self.every:
            self.last = now
            http_server.checkpoint(self.ctx)


def watch(ctx, proc, interval=2.0):
    """Ждать условия выхода (главный поток)."""
    dog = Watchdog(time.monotonic())
    saver = _Checkpointer(ctx)
    while True:
        time.sleep(interval)
        saver.tick()
        alive = proc is not None and proc.poll() is None
        if dog.tick(time.monotonic(), ctx, alive):
            return


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt


def _install_signals():
    """Завершение по SIGTERM / Ctrl+Break — так же аккуратно, как по Ctrl+C."""
    for name in ('SIGTERM', 'SIGBREAK'):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _raise_interrupt)
            except (ValueError, OSError):
                pass


# --------------------------------------------------------------------------------------
# Запуск
# --------------------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog='TO-Uchet', description='Учёт ТО — локальная программа механика')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT,
                        help=f'порт на 127.0.0.1 (по умолчанию {DEFAULT_PORT}; 0 — любой свободный)')
    parser.add_argument('--data', default=None, help='папка данных (база, копии, журнал)')
    parser.add_argument('--no-window', action='store_true', help='не открывать окно, работать до Ctrl+C')
    return parser.parse_args(argv)


def _show_existing(port, no_window):
    """Программа уже запущена: показать её окно (или адрес) и выйти."""
    if no_window:
        print(f'Программа уже запущена: http://127.0.0.1:{port}/', flush=True)
    else:
        open_window(port)
    return 0


def run(args, no_console=False):
    data_dir = choose_data_dir(args.data)
    if no_console:
        redirect_to_log(data_dir)
    _safe_console()

    running = find_running(data_dir)
    if running is not None:
        return _show_existing(running, args.no_window)

    try:
        srv, port, ctx = http_server.make_server(data_dir, args.port)
    except http_server.PortBusyError:
        # Порт занят: если там наша программа с этой же папкой данных — открыть окно на неё и выйти,
        # иначе взять любой свободный порт.
        if _is_our_instance(ping_app(args.port), data_dir):
            return _show_existing(args.port, args.no_window)
        srv, port, ctx = http_server.make_server(data_dir, 0)

    try:
        write_server_json(data_dir, port)
        http_server.serve_in_thread(srv)
        http_server.log(f'Учёт ТО {VERSION} запущен: http://127.0.0.1:{port}/  данные: {data_dir}')
        _install_signals()
        if args.no_window:
            print(f'Учёт ТО {VERSION}: http://127.0.0.1:{port}/  (данные: {data_dir}). '
                  'Остановить — Ctrl+C.', flush=True)
            saver = _Checkpointer(ctx)
            while True:
                time.sleep(0.5)
                saver.tick()
        else:
            watch(ctx, open_window(port))
    except KeyboardInterrupt:
        pass
    finally:
        http_server.stop_server(srv)        # shutdown, wal_checkpoint(TRUNCATE), закрытие базы
        remove_server_json(data_dir)
        http_server.log('Учёт ТО остановлен')
    return 0


def main(argv=None):
    no_console = _ensure_streams()
    args = parse_args(argv)
    try:
        return run(args, no_console)
    except KeyboardInterrupt:
        return 0
    except Exception as err:
        http_server.log('Программа не запустилась\n' + traceback.format_exc())
        if no_console and not args.no_window:
            _message_box(f'Программа «Учёт ТО» не запустилась:\n{err}\n\nПодробности — в файле app.log в папке данных.'
                         f'\n\nЕсли повреждена база: в папке данных переименуйте to.db (например, в to.db.old), '
                         f'возьмите самую свежую копию из папки backups, положите её рядом под именем to.db '
                         f'и запустите программу снова.')
        return 1


if __name__ == '__main__':
    sys.exit(main())
