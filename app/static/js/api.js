// Обращения к серверу «Учёт ТО». Документация: app/static/UI_KIT.md, раздел «api.js».

import { confirm } from './ui.js';

const OFFLINE_TEXT = 'Нет связи с программой. Возможно, она закрыта — запустите её заново.';

/**
 * Ошибка запроса. message — русский текст с сервера; status — код HTTP (0 — нет связи);
 * needsConfirm — сервер просит подтверждение (409); warnings — строки предупреждений; items — [{machine_id, garage_no, text}].
 */
export class ApiError extends Error {
  constructor(message, { status = 0, data = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
    this.needsConfirm = !!(data && data.needs_confirm);
    this.warnings = data && Array.isArray(data.warnings) ? data.warnings : [];
    this.items = data && Array.isArray(data.items) ? data.items : [];
  }
}

// ── связь с сервером ──

let online = true;
const connectionListeners = new Set();

function setOnline(value) {
  if (online === value) return;
  online = value;
  for (const cb of connectionListeners) {
    try {
      cb(online);
    } catch (err) {
      console.error(err);
    }
  }
}

/** Подписка на «связь пропала / появилась»: cb(true|false). Возвращает функцию отписки. */
export function onConnectionChange(cb) {
  connectionListeners.add(cb);
  return () => connectionListeners.delete(cb);
}

/** Есть ли сейчас связь с сервером (по последнему запросу). */
export function isOnline() {
  return online;
}

// ── запросы ──

function buildUrl(path, params) {
  let url = String(path);
  if (!url.startsWith('/')) url = `/${url}`;
  if (url !== '/api' && !url.startsWith('/api/')) url = `/api${url}`;
  if (params) {
    const q = new URLSearchParams();
    for (const [key, val] of Object.entries(params)) {
      if (val === null || val === undefined || val === '') continue;
      q.append(key, val === true ? '1' : val === false ? '0' : String(val));
    }
    const s = q.toString();
    if (s) url += (url.includes('?') ? '&' : '?') + s;
  }
  return url;
}

async function send(method, path, { params, json, binary } = {}) {
  const init = { method, headers: {}, cache: 'no-store' };
  if (json !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(json);
  } else if (binary !== undefined) {
    init.headers['Content-Type'] = 'application/octet-stream';
    init.body = binary;
  }
  let resp;
  try {
    resp = await fetch(buildUrl(path, params), init);
  } catch (err) {
    setOnline(false);
    throw new ApiError(OFFLINE_TEXT, { status: 0 });
  }
  setOnline(true);
  return resp;
}

async function failure(resp) {
  let data = null;
  try {
    data = JSON.parse(await resp.text());
  } catch (err) {
    data = null;
  }
  const message = data && typeof data.error === 'string' && data.error
    ? data.error : `Ошибка сервера (код ${resp.status})`;
  return new ApiError(message, { status: resp.status, data });
}

async function readJson(resp) {
  if (!resp.ok) throw await failure(resp);
  const text = await resp.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch (err) {
    throw new ApiError('Сервер ответил непонятно. Попробуйте ещё раз.', { status: resp.status });
  }
}

/** GET. path — '/fleet' или '/api/fleet'; params — объект query-параметров (пустые значения пропускаются). */
export async function get(path, params) {
  return readJson(await send('GET', path, { params }));
}

/** POST с JSON-телом. */
export async function post(path, body) {
  return readJson(await send('POST', path, { json: body === undefined ? {} : body }));
}

/** PUT с JSON-телом (частичное обновление: передавайте только изменённые ключи). */
export async function put(path, body) {
  return readJson(await send('PUT', path, { json: body === undefined ? {} : body }));
}

/** DELETE. params — query-параметры, например {force: 1}. */
export async function del(path, params) {
  return readJson(await send('DELETE', path, { params }));
}

/** Отправить файл (File/Blob/ArrayBuffer) телом POST как application/octet-stream. params — query, например {dry_run: 1}. */
export async function upload(path, file, params) {
  return readJson(await send('POST', path, { params, binary: file }));
}

function fileNameFrom(resp) {
  const cd = resp.headers.get('Content-Disposition') || '';
  const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(cd);
  if (star) {
    try {
      return decodeURIComponent(star[1].trim());
    } catch (err) {
      // имя закодировано неверно — берём обычное
    }
  }
  const plain = /filename\s*=\s*"([^"]+)"/i.exec(cd) || /filename\s*=\s*([^;]+)/i.exec(cd);
  return plain ? plain[1].trim() : 'download';
}

/** Скачать файл (GET) и отдать его браузеру на сохранение. Возвращает имя файла. */
export async function download(path, params) {
  const resp = await send('GET', path, { params });
  if (!resp.ok) throw await failure(resp);
  const blob = await resp.blob();
  const name = fileNameFrom(resp);
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.style.display = 'none';
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  return name;
}

/**
 * Запрос с подтверждением: fn(force) выполняется с force=false; если сервер ответил 409 «нужно подтверждение»,
 * показывается вопрос со списком предупреждений и при согласии fn(true).
 * Возвращает результат fn или null, если пользователь отказался. Прочие ошибки пробрасываются.
 */
export async function withConfirm(fn, { title = 'Проверьте данные', okLabel = 'Всё равно сохранить', cancelLabel = 'Вернуться и исправить' } = {}) {
  try {
    return await fn(false);
  } catch (err) {
    if (!(err && err.needsConfirm)) throw err;
    const lines = [
      ...err.warnings.map((w) => String(w)),
      ...err.items.map((it) => (it && it.garage_no ? `${it.garage_no}: ` : '') + (it && it.text ? it.text : '')),
    ].filter(Boolean);
    const details = [...new Set(lines)];
    const ok = await confirm(err.message, { title, details, okLabel, cancelLabel });
    if (!ok) return null;
    return fn(true);
  }
}

// ── пинг: пока окно открыто, сервер знает, что программа нужна ──

let pingTimer = null;

/** Проверить связь прямо сейчас (обычно не нужно: пинг идёт сам). */
export async function ping() {
  try {
    const resp = await fetch('/api/ping', { cache: 'no-store' });
    setOnline(resp.ok);
  } catch (err) {
    setOnline(false);
  }
}

/** Запустить пинг /api/ping каждые 20 с (и сразу при возврате в окно). Повторный вызов ничего не делает. */
export function startPing(intervalMs = 20000) {
  if (pingTimer) return;
  pingTimer = setInterval(ping, intervalMs);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) ping();
  });
  ping();
}
