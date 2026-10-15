"""HTTP-сервер «Учёт ТО» (SPEC, разделы 2 и 5): маршруты /api/*, статика, JSON, ошибки.

    make_server(data_dir, port, static_dir=None) -> (server, port, ctx)   bind + открытие базы
    serve_in_thread(server) -> Thread                                    serve_forever в фоновом потоке
    stop_server(server)                                                  остановить сервер и закрыть базу

Только 127.0.0.1; запросы с чужим Host -> 403; CORS-заголовков нет. Каждый обработчик /api
выполняется целиком под замком db.LOCK (кроме /api/ping — он базу не трогает и должен отвечать
даже во время долгого импорта). Ошибки services.ApiError -> JSON с её статусом; прочие исключения ->
traceback в журнал (stderr, в exe это app.log) и HTTP 500.
"""

import json
import os
import re
import socket
import socketserver
import sqlite3
import sys
import threading
import time
import traceback
import zipfile
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote

from app import VERSION, db, services
from app.services import ApiError

HOST = '127.0.0.1'
MAX_BODY = 40 * 1024 * 1024          # лимит тела запроса (загрузка xlsx), байт
DISCARD_LIMIT = 256 * 1024 * 1024    # слишком большое тело дочитываем (чтобы клиент получил ответ), но не больше этого

# Собственная таблица типов (НЕ mimetypes: на Windows он читает реестр и может отдать .js как text/plain).
MIME_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.md': 'text/plain; charset=utf-8',
    '.txt': 'text/plain; charset=utf-8',
    '.json': 'application/json',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.ico': 'image/x-icon',
    '.woff2': 'font/woff2',
}
DEFAULT_MIME = 'application/octet-stream'
XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
JSON_MIME = 'application/json; charset=utf-8'
# Клиент оборвал или «подвесил» соединение. socket.timeout стал синонимом TimeoutError только в
# Python 3.10 — для 3.8 (сборка для старых ПК) он указан отдельно.
DISCONNECT_ERRORS = (ConnectionError, TimeoutError, socket.timeout)


class PortBusyError(OSError):
    """Не удалось занять порт (скорее всего, программа уже запущена)."""


class Ctx:
    """Общее состояние сервера: соединение с базой, папки, отметка последнего обращения фронтенда."""

    def __init__(self, conn, data_dir, static_dir, port):
        self.conn = conn
        self.data_dir = Path(data_dir)
        self.backups_dir = self.data_dir / 'backups'
        self.photos_dir = self.data_dir / 'photos'      # фото машин: photos/<machine_id>/<имя> (в копии не входят)
        self.static_dir = Path(static_dir)
        self.port = port
        self.last_ping = time.monotonic()   # когда фронтенд последний раз обращался (для сторожа выхода)
        self.thread = None
        self.closed = False
        self.recovered = None               # база была повреждена: {broken, restored_from, error} (для баннера)


class Request:
    """Разобранный запрос к /api: args — группы из пути, query — параметры строки запроса,
    body — разобранный JSON (или None), raw — тело как байты."""
    __slots__ = ('method', 'path', 'args', 'query', 'body', 'raw')

    def __init__(self, method, path, args, query, body, raw):
        self.method, self.path, self.args = method, path, args
        self.query, self.body, self.raw = query, body, raw

    @property
    def force(self):
        """Подтверждение: "force": true в теле или ?force=1."""
        if isinstance(self.body, dict) and services.norm_bool(self.body.get('force')):
            return True
        return services.norm_bool(self.query.get('force', ''))

    def id(self, n=0):
        return int(self.args[n])


class FileResponse:
    """Ответ-файл (не JSON)."""

    def __init__(self, data, content_type, headers=None):
        self.data, self.content_type, self.headers = data, content_type, headers or {}


def log(text):
    """Строка в журнал программы (stderr; в exe без консоли перенаправлен в app.log)."""
    try:
        stream = sys.stderr
        if stream is not None:
            stream.write(f'[{datetime.now().isoformat(timespec="seconds")}] {text}\n')
            stream.flush()
    except Exception:
        pass


def default_static_dir():
    """Папка статики: внутри exe — <_MEIPASS>/app/static, иначе рядом с этим файлом."""
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent)) / 'app' / 'static'
    return Path(__file__).resolve().parent / 'static'


def _inside(path, root):
    """path лежит внутри root (или совпадает с ним). Замена Path.is_relative_to — его нет в Python 3.8."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------------------
# Обработчики /api (тонкие: разобрать запрос -> вызвать services)
# --------------------------------------------------------------------------------------

def h_ping(ctx, req):
    return {'app': 'to-uchet', 'version': VERSION, 'data_dir': str(ctx.data_dir)}


def h_bootstrap(ctx, req):
    result = services.bootstrap(ctx.conn, ctx.data_dir)
    result['db_recovered'] = ctx.recovered
    return result


def h_fleet(ctx, req):
    return services.fleet(ctx.conn, req.query.get('date'))


def h_machines(ctx, req):
    return services.list_machines(ctx.conn)


def h_machine_create(ctx, req):
    return services.create_machine(ctx.conn, req.body)


def h_machine_view(ctx, req):
    return services.machine_view(ctx.conn, req.id(), req.query.get('date'))


def h_machine_card(ctx, req):
    return services.machine_card(ctx.conn, req.id(), req.query.get('date'), photos_root=ctx.photos_dir)


def h_machine_update(ctx, req):
    return services.update_machine(ctx.conn, req.id(), req.body, force=req.force)


def h_machine_delete(ctx, req):
    return services.delete_machine(ctx.conn, req.id(), force=req.force, photos_root=ctx.photos_dir)


def h_photos(ctx, req):
    return services.list_photos(ctx.conn, ctx.photos_dir, req.id())


def h_photo_upload(ctx, req):
    return services.add_photo(ctx.conn, ctx.photos_dir, req.id(), req.query.get('name'), req.raw)


def h_photo_get(ctx, req):
    data, content_type = services.photo_file(ctx.conn, ctx.photos_dir, req.id(), req.args[1])
    return FileResponse(data, content_type)


def h_photo_delete(ctx, req):
    return services.delete_photo(ctx.conn, ctx.photos_dir, req.id(), req.args[1])


def h_events(ctx, req):
    q = req.query
    return services.list_events(
        ctx.conn, machine_id=q.get('machine_id'), kind=q.get('kind'), date_from=q.get('from'),
        date_to=q.get('to'), q=q.get('q'), include_auto=q.get('include_auto', ''),
        limit=q.get('limit'), offset=q.get('offset'))


def h_event_get(ctx, req):
    return services.get_event(ctx.conn, req.id())


def h_event_create(ctx, req):
    return services.create_event(ctx.conn, req.body, force=req.force)


def h_event_update(ctx, req):
    return services.update_event(ctx.conn, req.id(), req.body, force=req.force)


def h_event_delete(ctx, req):
    return services.delete_event(ctx.conn, req.id())


def h_order_done(ctx, req):
    done = req.body.get('done', True) if isinstance(req.body, dict) else True
    return services.set_order_done(ctx.conn, req.id(), done)


def h_to_done(ctx, req):
    return services.to_done(ctx.conn, req.body, force=req.force)


def h_bulk(ctx, req):
    return services.readings_bulk(ctx.conn, req.body, force=req.force)


def h_daily_preview(ctx, req):
    return services.daily_preview(ctx.conn, req.query.get('from'), req.query.get('to'))


def h_daily_apply(ctx, req):
    return services.daily_apply(ctx.conn, req.body)


def h_daily_undo(ctx, req):
    return services.daily_undo(ctx.conn, req.body)


def h_plan(ctx, req):
    return services.plan(ctx.conn, req.query.get('horizon_days'))


def h_workorder(ctx, req):
    return services.workorder(ctx.conn, req.query.get('machine_id'), req.query.get('date'))


def h_tyres(ctx, req):
    return services.list_tyres(ctx.conn, req.query.get('machine_id'))


def h_tyre_create(ctx, req):
    return services.create_tyre(ctx.conn, req.body)


def h_tyre_update(ctx, req):
    return services.update_tyre(ctx.conn, req.id(), req.body)


def h_tyre_delete(ctx, req):
    return services.delete_tyre(ctx.conn, req.id())


def h_units(ctx, req):
    return services.list_units(ctx.conn, req.query.get('machine_id'))


def h_unit_create(ctx, req):
    return services.create_unit(ctx.conn, req.body)


def h_unit_update(ctx, req):
    return services.update_unit(ctx.conn, req.id(), req.body)


def h_unit_delete(ctx, req):
    return services.delete_unit(ctx.conn, req.id())


def h_settings_get(ctx, req):
    return db.get_settings(ctx.conn)


def h_settings(ctx, req):
    return services.update_settings(ctx.conn, req.body)


def h_list(ctx, req):
    return services.update_list(ctx.conn, req.args[0], req.body)


def h_rules(ctx, req):
    return services.update_rules(ctx.conn, req.body)


def h_regulation(ctx, req):
    return services.update_regulation(ctx.conn, req.body)


def _xlsx_io():
    """Модуль импорта/экспорта подключается лениво: без него остальная программа работает."""
    try:
        from app import xlsx_io
    except ImportError:
        raise ApiError(501, 'Импорт и экспорт Excel в этой сборке недоступны') from None
    return xlsx_io


def h_import(ctx, req):
    xlsx_io = _xlsx_io()
    if not req.raw:
        raise ApiError(400, 'Файл пустой — выберите файл таблицы (.xlsx)')
    dry_run = services.norm_bool(req.query.get('dry_run', ''))
    if not dry_run and ctx.conn.execute('SELECT COUNT(*) FROM machines').fetchone()[0]:
        db.backup_now(ctx.conn, ctx.backups_dir)     # страховочная копия перед изменением базы
    try:
        return xlsx_io.import_v8(ctx.conn, req.raw, dry_run=dry_run)
    except ApiError:
        raise
    except zipfile.BadZipFile:
        raise ApiError(400, 'Не удалось прочитать файл: это не книга Excel (.xlsx)') from None
    except ValueError as err:
        raise ApiError(400, str(err) or 'Не удалось прочитать файл') from None


def h_export(ctx, req):
    data = _xlsx_io().export_xlsx(ctx.conn)
    day = date.today().isoformat()
    disposition = (f'attachment; filename="TO_export_{day}.xlsx"; '
                   f"filename*=UTF-8''{quote(f'Учёт_ТО_{day}.xlsx')}")
    return FileResponse(data, XLSX_MIME, {'Content-Disposition': disposition})


def h_backups(ctx, req):
    return services.list_backups(ctx.backups_dir)


def h_backup_create(ctx, req):
    return services.create_backup(ctx.conn, ctx.backups_dir)


def h_backup_restore(ctx, req):
    name = req.body.get('name') if isinstance(req.body, dict) else None
    return services.restore_backup(ctx.conn, ctx.backups_dir, name)


# Таблица маршрутов: метод, шаблон пути (совпадение целиком), обработчик.
ROUTES = [(method, re.compile(pattern), handler) for method, pattern, handler in [
    ('GET', r'/api/ping', h_ping),
    ('GET', r'/api/bootstrap', h_bootstrap),
    ('GET', r'/api/fleet', h_fleet),
    ('GET', r'/api/machines', h_machines),
    ('POST', r'/api/machines', h_machine_create),
    ('GET', r'/api/machines/(\d+)', h_machine_view),
    ('GET', r'/api/machines/(\d+)/view', h_machine_view),
    ('GET', r'/api/machines/(\d+)/card', h_machine_card),
    ('PUT', r'/api/machines/(\d+)', h_machine_update),
    ('DELETE', r'/api/machines/(\d+)', h_machine_delete),
    ('GET', r'/api/machines/(\d+)/photos', h_photos),
    ('POST', r'/api/machines/(\d+)/photos', h_photo_upload),
    # имя файла — один сегмент пути (уже раскодированный); «..», «\» и прочее отсекает services (404)
    ('GET', r'/api/machines/(\d+)/photos/([^/]+)', h_photo_get),
    ('DELETE', r'/api/machines/(\d+)/photos/([^/]+)', h_photo_delete),
    ('GET', r'/api/events', h_events),
    ('POST', r'/api/events', h_event_create),
    ('GET', r'/api/events/(\d+)', h_event_get),
    ('PUT', r'/api/events/(\d+)', h_event_update),
    ('DELETE', r'/api/events/(\d+)', h_event_delete),
    ('POST', r'/api/events/(\d+)/order-done', h_order_done),
    ('POST', r'/api/to-done', h_to_done),
    ('POST', r'/api/readings/bulk', h_bulk),
    ('GET', r'/api/daily/preview', h_daily_preview),
    ('POST', r'/api/daily/apply', h_daily_apply),
    ('POST', r'/api/daily/undo', h_daily_undo),
    ('GET', r'/api/plan', h_plan),
    ('GET', r'/api/workorder', h_workorder),
    ('GET', r'/api/tyres', h_tyres),
    ('POST', r'/api/tyres', h_tyre_create),
    ('PUT', r'/api/tyres/(\d+)', h_tyre_update),
    ('DELETE', r'/api/tyres/(\d+)', h_tyre_delete),
    ('GET', r'/api/units', h_units),
    ('POST', r'/api/units', h_unit_create),
    ('PUT', r'/api/units/(\d+)', h_unit_update),
    ('DELETE', r'/api/units/(\d+)', h_unit_delete),
    ('GET', r'/api/settings', h_settings_get),
    ('PUT', r'/api/settings', h_settings),
    ('PUT', r'/api/lists/([A-Za-z_]+)', h_list),
    ('PUT', r'/api/rules', h_rules),
    ('PUT', r'/api/regulation', h_regulation),
    ('POST', r'/api/import/xlsx', h_import),
    ('GET', r'/api/export/xlsx', h_export),
    ('GET', r'/api/backups', h_backups),
    ('POST', r'/api/backups', h_backup_create),
    ('POST', r'/api/backups/restore', h_backup_restore),
]]

NO_LOCK = {h_ping}                    # обработчики, которым не нужна база
BINARY_BODY = {h_import, h_photo_upload}   # тело — байты файла (application/octet-stream), а не JSON
# лимит тела запроса и текст ошибки по обработчику; остальным — MAX_BODY
BODY_LIMITS = {h_photo_upload: (services.PHOTO_MAX_BYTES, services.PHOTO_TOO_BIG)}
TOO_BIG = 'Файл слишком большой (больше 40 МБ)'


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    server_version = 'TO-Uchet/' + VERSION
    timeout = 120                      # зависшее соединение не держит поток вечно

    def log_message(self, format, *args):   # noqa: A002 — имя параметра задано базовым классом
        pass                           # обычные запросы в журнал не пишем

    def do_GET(self):
        self._handle('GET')

    def do_HEAD(self):
        self._handle('HEAD')

    def do_POST(self):
        self._handle('POST')

    def do_PUT(self):
        self._handle('PUT')

    def do_DELETE(self):
        self._handle('DELETE')

    # ---- ответ ----

    def _send(self, status, body, content_type, headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _send_json(self, status, data):
        self._send(status, json.dumps(data, ensure_ascii=False).encode('utf-8'), JSON_MIME)

    def _send_error(self, status, message, **extra):
        self._send_json(status, {'error': message, **extra})

    # ---- разбор ----

    def _handle(self, method):
        try:
            self._route(method)
        except DISCONNECT_ERRORS:
            pass                       # клиент закрыл соединение — отвечать некому
        except Exception as err:       # сюда попадают только ошибки самого сервера
            log('Ошибка обработки запроса %s %s\n%s' % (method, self.path, traceback.format_exc()))
            try:
                self._send_error(500, f'Внутренняя ошибка: {err}')
            except Exception:
                pass

    def _host_ok(self):
        host = (self.headers.get('Host') or '').strip().lower()
        name, _, port = host.partition(':')
        if name not in ('127.0.0.1', 'localhost'):
            return False
        return not port or port == str(self.server.server_address[1])

    def _origin_ok(self):
        """Запрос с чужой веб-страницы (заголовок Origin не наш) не принимается: посторонний сайт,
        открытый в браузере на этом же компьютере, не должен менять данные программы."""
        origin = (self.headers.get('Origin') or '').strip().lower()
        if not origin:
            return True                # свой GET, curl, тесты — заголовка нет
        port = self.server.server_address[1]
        return origin in (f'http://127.0.0.1:{port}', f'http://localhost:{port}')

    def _route(self, method):
        if not self._host_ok() or not self._origin_ok():
            self._drain_body()
            self._send_error(403, 'Доступ только с этого компьютера (127.0.0.1)')
            return
        # Путь разбираем сами (не urlsplit: «//x» он принял бы за имя сервера).
        target = self.path.split('#', 1)[0]
        raw_path, _, query_string = target.partition('?')
        path = unquote(raw_path)
        if path == '/api' or path.startswith('/api/'):
            self._api(method, path, query_string)
        elif method in ('GET', 'HEAD'):
            self._static(path)
        else:
            self._drain_body()
            self._send_error(405, 'Метод не поддерживается')

    def _drain_body(self):
        """Дочитать непрочитанное тело запроса перед отказом: если закрыть сокет с непрочитанными данными,
        Windows рвёт соединение (RST) и клиент вместо ответа 403/405 получает «соединение разорвано»."""
        try:
            left = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            return
        if left > DISCARD_LIMIT:
            return
        while left > 0:
            chunk = self.rfile.read(min(left, 1 << 20))
            if not chunk:
                break
            left -= len(chunk)

    def _read_body(self, limit=MAX_BODY, too_big=TOO_BIG):
        """Тело запроса по Content-Length -> bytes. Больше limit байт -> ApiError 400 с текстом too_big."""
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            raise ApiError(400, 'Неверный заголовок Content-Length') from None
        if length < 0:
            raise ApiError(400, 'Неверный заголовок Content-Length')
        if length > limit:
            if length <= DISCARD_LIMIT:
                left = length
                while left > 0:                      # дочитать, иначе клиент не увидит ответ
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    left -= len(chunk)
            raise ApiError(400, too_big)
        return self.rfile.read(length) if length else b''

    def _api(self, method, path, query_string):
        ctx = self.server.ctx
        ctx.last_ping = time.monotonic()
        handler = match = None
        path_known = False
        for route_method, pattern, func in ROUTES:
            m = pattern.fullmatch(path)
            if m is None:
                continue
            path_known = True
            if route_method == method or (method == 'HEAD' and route_method == 'GET'):
                handler, match = func, m
                break
        try:
            raw = self._read_body(*BODY_LIMITS.get(handler, (MAX_BODY, TOO_BIG)))
            if handler is None:
                if path_known:
                    raise ApiError(405, 'Метод не поддерживается')
                raise ApiError(404, 'Не найдено')

            body = None
            if raw:
                content_type = (self.headers.get('Content-Type') or '').split(';')[0].strip().lower()
                if handler in BINARY_BODY:
                    if content_type != 'application/octet-stream':
                        raise ApiError(400, 'Файл нужно отправлять как application/octet-stream')
                else:
                    if content_type != 'application/json':
                        raise ApiError(400, 'Данные нужно отправлять как application/json')
                    try:
                        body = json.loads(raw.decode('utf-8'))
                    except (UnicodeDecodeError, ValueError):
                        raise ApiError(400, 'Данные запроса повреждены (неверный JSON)') from None

            query = {key: values[-1] for key, values in parse_qs(query_string, keep_blank_values=True).items()}
            req = Request(method, path, match.groups(), query, body, raw)
            if handler in NO_LOCK:
                result = handler(ctx, req)
            else:
                with db.LOCK:
                    try:
                        result = handler(ctx, req)
                    except BaseException:
                        self._rollback(ctx)
                        raise
        except ApiError as err:
            self._send_error(err.status, err.message, **err.extra)
            return
        except Exception as err:
            log('Ошибка в обработчике %s %s\n%s' % (method, path, traceback.format_exc()))
            self._send_error(500, f'Внутренняя ошибка: {err}')
            return

        if method in ('POST', 'PUT', 'DELETE'):
            # изменения сразу переносятся из to.db-wal в to.db: скопированный на флешку один to.db — актуален
            checkpoint(ctx)
        if isinstance(result, FileResponse):
            self._send(200, result.data, result.content_type, result.headers)
        else:
            self._send_json(200, result)

    @staticmethod
    def _rollback(ctx):
        """Незакрытая после ошибки транзакция откатывается (под замком)."""
        try:
            if ctx.conn.in_transaction:
                ctx.conn.rollback()
        except Exception:
            pass

    def _static(self, path):
        """Файл из static/: путь нормализуется, выход за папку -> 404; «/» -> index.html."""
        rel = path.lstrip('/') or 'index.html'
        names = rel.split('/')
        bad = ('\\' in rel or ':' in rel or '\x00' in rel
               or any(name in ('', '.', '..') for name in names))
        target = None
        if not bad:
            try:
                # os.path.realpath, а не Path.resolve(): в Python 3.8 resolve() на Windows бросает
                # исключение на несуществующих путях с недопустимыми символами (с 3.10 — это realpath)
                root = Path(os.path.realpath(str(self.server.ctx.static_dir)))
                candidate = Path(os.path.realpath(str(root / rel)))
                if _inside(candidate, root) and candidate.is_file():
                    target = candidate
            except (OSError, ValueError):
                target = None
        if target is None:
            self._send_error(404, 'Не найдено')
            return
        self._send(200, target.read_bytes(), MIME_TYPES.get(target.suffix.lower(), DEFAULT_MIME))


class Server(ThreadingHTTPServer):
    allow_reuse_address = False        # второй экземпляр не должен занять тот же порт
    daemon_threads = True
    ctx = None

    def server_bind(self):
        # Без HTTPServer.server_bind: он вызывает socket.getfqdn(), а это поиск в DNS,
        # который на компьютере без сети может «висеть» несколько секунд.
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):   # Windows: никто другой не «подсядет» на этот порт
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)
        self.server_name = HOST
        self.server_port = self.server_address[1]

    def handle_error(self, request, client_address):
        err = sys.exc_info()[1]
        if isinstance(err, DISCONNECT_ERRORS):
            return                     # клиент оборвал соединение — не ошибка
        log('Ошибка соединения\n' + traceback.format_exc())


def make_server(data_dir, port, static_dir=None):
    """Занять порт на 127.0.0.1 и открыть базу <data_dir>/to.db. Возвращает (server, port, ctx).

    port=0 — любой свободный. Порт занят -> PortBusyError (база при этом не открывается).
    Сервер ещё не обслуживает запросы: вызовите serve_in_thread(server) или server.serve_forever().
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        server = Server((HOST, int(port)), Handler)
    except OSError as err:
        raise PortBusyError(f'Порт {port} занят: {err}') from err
    recovered = None
    try:
        try:
            conn = db.open_db(data_dir / 'to.db')
        except sqlite3.DatabaseError as err:
            # to.db повреждён (выдернутая флешка и т.п.): отложить его и поднять свежую исправную копию
            log(f'База to.db не открывается ({err}) — восстановление из резервной копии')
            conn, recovered = db.recover_db(data_dir / 'to.db', data_dir / 'backups')
            recovered['error'] = str(err)
            log(f"Повреждённый файл сохранён как {recovered['broken']}; "
                f"восстановлена копия: {recovered['restored_from'] or 'исправной копии нет — создана пустая база'}")
    except BaseException:
        server.server_close()
        raise
    actual_port = server.server_address[1]
    ctx = Ctx(conn, data_dir, static_dir or default_static_dir(), actual_port)
    ctx.recovered = recovered
    server.ctx = ctx
    try:
        db.auto_backup(conn, ctx.backups_dir)       # копия при старте (если есть машины и прошло 6 часов)
    except Exception:
        log('Не удалось создать резервную копию при старте\n' + traceback.format_exc())
    return server, actual_port, ctx


def serve_in_thread(server):
    """Запустить serve_forever в фоновом потоке."""
    thread = threading.Thread(target=server.serve_forever, name='to-uchet-http', daemon=True)
    server.ctx.thread = thread
    thread.start()
    return thread


def checkpoint(ctx):
    """Перенести накопленное из to.db-wal в сам to.db (PRAGMA wal_checkpoint(PASSIVE)) — не ждёт читателей
    и не мешает запросам. Вызывается периодически: если процесс будет убит (выключение Windows при открытом
    окне), файл to.db останется почти актуальным сам по себе. Ошибки не мешают работе."""
    if ctx is None or ctx.closed:
        return
    try:
        with db.LOCK:
            if not ctx.closed and not ctx.conn.in_transaction:
                ctx.conn.execute('PRAGMA wal_checkpoint(PASSIVE)')
    except Exception:
        log('Не удалось сбросить журнал базы (wal_checkpoint)\n' + traceback.format_exc())


def stop_server(server):
    """Остановить сервер и закрыть базу (wal_checkpoint(TRUNCATE) делает db.close_db). Повторный вызов безопасен."""
    ctx = server.ctx
    if ctx is None or ctx.closed:
        return
    ctx.closed = True
    if ctx.thread is not None and ctx.thread.is_alive():
        server.shutdown()
        ctx.thread.join(timeout=5)
    server.server_close()
    with db.LOCK:
        db.close_db(ctx.conn)
