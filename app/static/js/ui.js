// UI-кит «Учёт ТО» — единственный источник компонентов для страниц.
// Документация с примерами: app/static/UI_KIT.md.
// Модуль не трогает document на верхнем уровне (чистые функции можно проверять в node).

import { icon, iconNames } from './icons.js';

export { icon, iconNames };

const NBSP = '\u00A0'; // неразрывный пробел
const MINUS = '\u2212'; // знак «минус»

let uidSeq = 0;
/** Уникальный id для связки label/for, aria-controls и т.п. */
export function uid(prefix = 'id') {
  uidSeq += 1;
  return `${prefix}-${uidSeq}`;
}

// ───────────────────────────── DOM ─────────────────────────────

const DIRECT_PROPS = new Set([
  'checked', 'disabled', 'selected', 'readOnly', 'hidden', 'indeterminate', 'multiple',
  'required', 'open', 'htmlFor', 'tabIndex', 'colSpan', 'rowSpan',
]);

function isAttrs(a) {
  return a !== null && typeof a === 'object' && !Array.isArray(a) && !(a instanceof Node);
}

function appendChild(el, child) {
  if (child === null || child === undefined || child === false || child === true) return;
  if (Array.isArray(child)) {
    for (const c of child) appendChild(el, c);
    return;
  }
  el.append(child instanceof Node ? child : document.createTextNode(String(child)));
}

function classList(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value.flatMap(classList);
  return String(value).split(/\s+/).filter(Boolean);
}

/**
 * Создать элемент: h('div.card', {onclick}, 'текст', h('b', null, 'жирно')).
 * tag — 'div', 'button.btn.btn-primary', 'span#id.muted'.
 * attrs — объект или null; class (строка/массив), style (строка/объект), dataset, on<событие>, остальное — атрибуты.
 * Значения null/undefined/false пропускаются, true — атрибут без значения. Дети: строки, числа, узлы, массивы.
 */
export function h(tag, attrs, ...children) {
  let name = 'div';
  let id = null;
  const classes = [];
  for (const part of String(tag).split(/(?=[.#])/)) {
    if (part[0] === '.') classes.push(part.slice(1));
    else if (part[0] === '#') id = part.slice(1);
    else if (part) name = part;
  }
  const el = document.createElement(name);
  if (id) el.id = id;
  let value; // value ставится после детей — иначе <select> его не примет

  if (isAttrs(attrs)) {
    for (const [key, val] of Object.entries(attrs)) {
      if (val === null || val === undefined || val === false) continue;
      if (key === 'class' || key === 'className') classes.push(...classList(val));
      else if (key === 'style') {
        if (typeof val === 'string') el.style.cssText = val;
        else {
          for (const [p, v] of Object.entries(val)) {
            if (v === null || v === undefined || v === false) continue;
            if (p.startsWith('--')) el.style.setProperty(p, v);
            else el.style[p] = v;
          }
        }
      } else if (key === 'dataset' || key === 'data') {
        for (const [p, v] of Object.entries(val)) {
          if (v !== null && v !== undefined) el.dataset[p] = v;
        }
      } else if (key.startsWith('on') && typeof val === 'function') {
        el.addEventListener(key.slice(2).toLowerCase(), val);
      } else if (key === 'value') value = val;
      else if (DIRECT_PROPS.has(key)) el[key] = val;
      else if (val === true) el.setAttribute(key, '');
      else el.setAttribute(key, String(val));
    }
  } else {
    children.unshift(attrs);
  }
  if (classes.length) el.className = classes.join(' ');
  appendChild(el, children);
  if (value !== undefined) el.value = value;
  return el;
}

/** Очистить элемент. */
export function clear(el) {
  el.replaceChildren();
  return el;
}

/** Заменить содержимое элемента (дети — как в h). */
export function mount(el, ...children) {
  el.replaceChildren();
  appendChild(el, children);
  return el;
}

/** Добавить <style id> в <head> один раз (CSS конкретной страницы). Цвета — только через var(--токен). */
export function addStyle(id, css) {
  if (document.getElementById(id)) return;
  const style = document.createElement('style');
  style.id = id;
  style.textContent = css;
  document.head.append(style);
}

/** Отложенный вызов: fn выполнится через ms после последнего вызова (поиск, фильтры). */
export function debounce(fn, ms = 250) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), '
  + 'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusables(root) {
  return [...root.querySelectorAll(FOCUSABLE)].filter((el) => el.getClientRects().length > 0);
}

function px(v) {
  return typeof v === 'number' ? `${v}px` : v;
}

// ─────────────────────── числа, даты, форматтеры ───────────────────────

/**
 * Разобрать число из ввода: «153 000», «1250,5», «1 250.5», «−5», «153000 км» → число; пусто или не число → null.
 */
export function parseNum(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  let s = String(value).trim();
  if (!s) return null;
  s = s.replace(/[\s\u00A0\u202F\u2009']/g, '').replace(/[\u2212\u2013\u2014]/g, '-');
  s = s.replace(/[^\d.,-]+$/, ''); // хвост с единицей: «км», «м/ч»
  if (!/^-?[\d.,]+$/.test(s) || !/\d/.test(s)) return null;
  const seps = s.match(/[.,]/g) || [];
  if (seps.length > 1) {
    const lastCh = seps[seps.length - 1];
    const others = seps.slice(0, -1);
    if (others.every((c) => c !== lastCh)) {
      // «1,250.5» / «1.250,5»: последний разделитель — десятичный, остальные — тысячи
      const last = s.lastIndexOf(lastCh);
      s = `${s.slice(0, last).replace(/[.,]/g, '')}.${s.slice(last + 1)}`;
    } else if (new Set(seps).size === 1 && /^-?\d{1,3}([.,]\d{3})+$/.test(s)) {
      s = s.replace(/[.,]/g, ''); // «1.250.000» — только разделители тысяч
    } else return null;
  } else {
    s = s.replace(',', '.');
  }
  if (!/^-?\d*\.?\d*$/.test(s)) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

function toNum(v) {
  return typeof v === 'number' ? (Number.isFinite(v) ? v : null) : parseNum(v);
}

function groupDigits(n, digits, space, minus) {
  const factor = 10 ** digits;
  const abs = Math.round((Math.abs(n) + Number.EPSILON) * factor) / factor;
  let [int, frac = ''] = abs.toFixed(digits).split('.');
  frac = frac.replace(/0+$/, '');
  int = int.replace(/\B(?=(\d{3})+(?!\d))/g, space);
  const sign = n < 0 && abs !== 0 ? minus : '';
  return sign + int + (frac ? `,${frac}` : '');
}

/**
 * Число для показа: fmtNum(1234567) → «1 234 567», fmtNum(1250.5) → «1 250,5», fmtNum(-2000) → «−2 000».
 * digits — максимум знаков после запятой (по умолчанию 2, хвостовые нули убираются). Пусто → ''.
 */
export function fmtNum(value, digits = 2) {
  const n = toNum(value);
  if (n === null) return '';
  return groupDigits(n, digits, NBSP, MINUS);
}

/** Показание с единицей: fmtReading(153000, 'км', true) → «≈ 153 000 км». Пусто → «—». */
export function fmtReading(value, unit, estimated) {
  const n = toNum(value);
  if (n === null) return '—';
  const digits = unit === 'км' ? 0 : 1;
  return (estimated ? `≈${NBSP}` : '') + groupDigits(n, digits, NBSP, MINUS) + (unit ? NBSP + unit : '');
}

/** «2026-10-02» (или ISO-метка времени) → «02.10.2026». Пусто → ''. */
export function fmtDate(iso) {
  if (!iso) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
  return m ? `${m[3]}.${m[2]}.${m[1]}` : String(iso);
}

/** Дата события или последнего ТО ({date, date_unknown}): «дата не указана», если её не знали при вводе
 *  (тогда date — дата внесения); иначе как fmtDate. */
export function fmtEventDate(o) {
  if (!o) return '';
  return o.date_unknown ? 'дата не указана' : fmtDate(o.date);
}

/** Ячейка «Дата» в журналах: обычная дата или серое «дата не указана» + мелко «внесено ДД.ММ.ГГГГ». */
export function eventDateCell(e) {
  if (!e || !e.date_unknown) return fmtDate(e && e.date);
  return [h('span.muted', null, 'дата не указана'), h('div.muted.small', null, `внесено ${fmtDate(e.date)}`)];
}

/** «2026-11-12» → «12.11» (без года). */
export function fmtDateShort(iso) {
  if (!iso) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
  return m ? `${m[3]}.${m[2]}` : String(iso);
}

/** «2026-10-02T14:30:05» → «02.10.2026 14:30». Без времени — только дата. */
export function fmtDateTime(iso) {
  if (!iso) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(String(iso));
  if (!m) return String(iso);
  return `${m[3]}.${m[2]}.${m[1]}${m[4] ? ` ${m[4]}:${m[5]}` : ''}`;
}

/** Длительность из секунд: 98400 → «1 д 3 ч 20 мин». Пусто → ''. */
export function fmtDuration(seconds) {
  const s = toNum(seconds);
  if (s === null) return '';
  const total = Math.floor(Math.abs(s) / 60);
  const d = Math.floor(total / 1440);
  const hh = Math.floor((total % 1440) / 60);
  const mm = total % 60;
  const parts = [];
  if (d) parts.push(`${d}${NBSP}д`);
  if (hh) parts.push(`${hh}${NBSP}ч`);
  if (mm || !parts.length) parts.push(`${mm}${NBSP}мин`);
  return (s < 0 ? MINUS : '') + parts.join(' ');
}

/** Часы: fmtHours(7.5) → «7,5 ч». Пусто → ''. */
export function fmtHours(hours, digits = 1) {
  const n = toNum(hours);
  if (n === null) return '';
  return `${groupDigits(n, digits, NBSP, MINUS)}${NBSP}ч`;
}

/** Доля в процентах: fmtPct(0.874) → «87 %». Пусто → ''. */
export function fmtPct(ratio, digits = 0) {
  const n = toNum(ratio);
  if (n === null) return '';
  return `${groupDigits(n * 100, digits, NBSP, MINUS)}${NBSP}%`;
}

/** Склонение: plural(5, 'машина', 'машины', 'машин') → «машин». */
export function plural(n, one, few, many) {
  const a = Math.abs(Math.trunc(Number(n) || 0));
  const d10 = a % 10;
  const d100 = a % 100;
  if (d10 === 1 && d100 !== 11) return one;
  if (d10 >= 2 && d10 <= 4 && (d100 < 12 || d100 > 14)) return few;
  return many;
}

function pad2(n) {
  return String(n).padStart(2, '0');
}

function isoDate(d) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

/** Сегодняшняя дата по часам этого компьютера, «YYYY-MM-DD». */
export function today() {
  return isoDate(new Date());
}

/** Текущее время «HH:MM». */
export function nowTime() {
  const d = new Date();
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

/** Сдвинуть ISO-дату на n дней: addDays('2026-10-31', 1) → '2026-11-01'. */
export function addDays(iso, n) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
  if (!m) return '';
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]) + Number(n || 0));
  return isoDate(d);
}

/** Разница в днях между ISO-датами (b − a). */
export function daysBetween(a, b) {
  const pa = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(a || ''));
  const pb = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(b || ''));
  if (!pa || !pb) return null;
  const da = Date.UTC(Number(pa[1]), Number(pa[2]) - 1, Number(pa[3]));
  const db = Date.UTC(Number(pb[1]), Number(pb[2]) - 1, Number(pb[3]));
  return Math.round((db - da) / 86400000);
}

/**
 * Разобрать время из ввода: «130» → «01:30», «9» → «09:00», «1.30» → «01:30», «17:5» → «17:05».
 * Пусто → '', не время → null.
 */
export function parseTime(value) {
  if (value === null || value === undefined) return '';
  const s = String(value).trim();
  if (!s) return '';
  let hh;
  let mm;
  let m = /^(\d{1,2})\s*[^\d\s]\s*(\d{1,2})(?::\d{2})?$/.exec(s) || /^(\d{1,2})\s+(\d{1,2})$/.exec(s);
  if (m) {
    hh = Number(m[1]);
    mm = Number(m[2]);
  } else {
    m = /^\d{1,4}$/.exec(s);
    if (!m) return null;
    if (s.length <= 2) {
      hh = Number(s);
      mm = 0;
    } else {
      hh = Number(s.slice(0, s.length - 2));
      mm = Number(s.slice(-2));
    }
  }
  if (hh > 23 || mm > 59) return null;
  return `${pad2(hh)}:${pad2(mm)}`;
}

/**
 * Живая подсказка «что будет после ТО» — единственный расчёт на фронте (SPEC 7.1).
 * machine — MachineView (нужны interval, warn, unit, reading); kind — вид вносимого ТО;
 * reading — показание на момент ТО; current — показание сейчас (если пусто — machine.reading);
 * settings — ctx.boot.settings (нужен to_cycle).
 * Возвращает {next_kind, next_at, remaining, status}; status: 'overdue' | 'soon' | 'ok' | 'none' (нет данных).
 */
export function previewTo(machine, { kind, reading, current } = {}, settings) {
  const cycle = settings && Array.isArray(settings.to_cycle) && settings.to_cycle.length
    ? settings.to_cycle : ['ТО-1', 'ТО-2', 'ТО-3'];
  const k = String(kind || '');
  let idx = -1;
  let best = 0;
  cycle.forEach((c, i) => {
    if (c && k.startsWith(c) && c.length > best) {
      idx = i;
      best = c.length;
    }
  });
  const next_kind = cycle[idx >= 0 ? (idx + 1) % cycle.length : 0];
  const m = machine || {};
  const r = toNum(reading);
  const interval = toNum(m.interval);
  if (r === null || interval === null) return { next_kind, next_at: null, remaining: null, status: 'none' };
  const round = m.unit === 'км' ? (v) => Math.round(v) : (v) => Math.round(v * 10) / 10;
  const next_at = round(r + interval);
  let cur = toNum(current);
  if (cur === null) cur = toNum(m.reading);
  if (cur === null) return { next_kind, next_at, remaining: null, status: 'none' };
  const remaining = round(next_at - cur);
  const warn = toNum(m.warn) || 0;
  const status = remaining <= 0 ? 'overdue' : remaining <= warn ? 'soon' : 'ok';
  return { next_kind, next_at, remaining, status };
}

/**
 * Печать текущего экрана. title — заголовок листа (печатается сверху и подставляется в имя PDF);
 * landscape: true — альбомная ориентация (для широких таблиц). Что скрыть на печати — класс .no-print.
 */
export function printPage(title, { landscape = false } = {}) {
  const oldTitle = document.title;
  let orient = null;
  if (title) {
    document.title = title;
    document.body.dataset.printTitle = title;
  }
  if (landscape) {
    orient = document.createElement('style');
    orient.textContent = '@page { size: A4 landscape; margin: 10mm; }';
    document.head.append(orient);
  }
  const restore = () => {
    window.removeEventListener('afterprint', restore);
    document.title = oldTitle;
    delete document.body.dataset.printTitle;
    if (orient) orient.remove();
  };
  window.addEventListener('afterprint', restore);
  window.print();
}

// ───────────────────────────── кнопки ─────────────────────────────

/**
 * Кнопка. kind: 'default' | 'primary' | 'accent' | 'danger' | 'ghost'.
 * onClick может быть async: на время выполнения кнопка блокируется; необработанная ошибка показывается тостом.
 */
export function button(label, opts = {}) {
  const { kind = 'default', icon: iconName, onClick, title, big, small, disabled, type = 'button', cls } = opts;
  const el = h('button.btn', {
    type,
    title,
    class: [kind !== 'default' && `btn-${kind}`, big && 'btn-big', small && 'btn-sm', cls],
    disabled: !!disabled,
  }, iconName ? icon(iconName, small ? 15 : 18) : null, label !== null && label !== undefined && label !== '' ? h('span', null, label) : null);
  if (opts.autofocus) el.dataset.autofocus = '1';
  if (onClick) {
    el.addEventListener('click', async (e) => {
      let res;
      try {
        res = onClick(e);
      } catch (err) {
        toast(err && err.message ? err.message : String(err), 'error');
        return;
      }
      if (res && typeof res.then === 'function') {
        const was = el.disabled;
        el.disabled = true;
        el.classList.add('busy');
        try {
          await res;
        } catch (err) {
          if (!(err && err.status !== undefined)) console.error(err);
          toast(err && err.message ? err.message : String(err), 'error');
        } finally {
          el.disabled = was;
          el.classList.remove('busy');
        }
      }
    });
  }
  return el;
}

/** Кнопка-иконка без подписи; title обязателен (подсказка и имя для экранного чтения). */
export function iconButton(iconName, title, onClick, opts = {}) {
  const el = button('', { kind: 'ghost', ...opts, icon: iconName, title, onClick, cls: ['btn-icon', opts.cls] });
  el.setAttribute('aria-label', title);
  return el;
}

// ───────────────────────────── всплывающие списки ─────────────────────────────

let currentPopover = null;

function setPopover(p) {
  if (currentPopover && currentPopover !== p) currentPopover.close();
  currentPopover = p;
}

/** Закрыть открытый выпадающий список/меню (роутер и модальные окна вызывают сами). */
export function closePopovers() {
  if (currentPopover) currentPopover.close();
  currentPopover = null;
}

function placePopover(pop, anchor, { align = 'left', matchWidth = false, maxHeight = 280 } = {}) {
  const r = anchor.getBoundingClientRect();
  const vw = document.documentElement.clientWidth;
  const vh = document.documentElement.clientHeight;
  const below = vh - r.bottom - 8;
  const above = r.top - 8;
  const up = below < 140 && above > below;
  pop.style.maxHeight = `${Math.max(96, Math.min(maxHeight, up ? above : below))}px`;
  if (matchWidth) pop.style.minWidth = `${r.width}px`;
  pop.style.maxWidth = `${Math.max(220, vw - 16)}px`;
  const w = pop.offsetWidth;
  let left = align === 'right' ? r.right - w : r.left;
  left = Math.max(8, Math.min(left, vw - w - 8));
  pop.style.left = `${left}px`;
  if (up) {
    pop.style.top = '';
    pop.style.bottom = `${vh - r.top + 2}px`;
  } else {
    pop.style.bottom = '';
    pop.style.top = `${r.bottom + 2}px`;
  }
}

/**
 * Выпадающее меню у кнопки («⋯»): menu(btn, [{label, icon, onClick, danger, disabled}, '-', ...]).
 * Стрелки ходят по пунктам, Esc закрывает и возвращает фокус на кнопку.
 */
export function menu(anchor, items, { align = 'right' } = {}) {
  const pop = h('div.popover.menu', { role: 'menu' });
  let closed = false;
  const ctl = {
    close(refocus) {
      if (closed) return;
      closed = true;
      pop.remove();
      document.removeEventListener('mousedown', onDocDown, true);
      window.removeEventListener('resize', onMove);
      window.removeEventListener('scroll', onMove, true);
      anchor.setAttribute('aria-expanded', 'false');
      if (currentPopover === ctl) currentPopover = null;
      if (refocus && anchor.isConnected) anchor.focus();
    },
  };
  const onDocDown = (e) => {
    if (!pop.contains(e.target)) ctl.close(false);
  };
  const onMove = (e) => {
    if (e && e.target && e.target instanceof Node && pop.contains(e.target)) return;
    ctl.close(false);
  };
  for (const it of items || []) {
    if (!it || it === '-') {
      pop.append(h('div.menu-sep', { role: 'separator' }));
      continue;
    }
    pop.append(h('button.menu-item', {
      type: 'button',
      role: 'menuitem',
      class: it.danger && 'menu-item-danger',
      disabled: !!it.disabled,
      title: it.title,
      onclick: () => {
        ctl.close(true);
        if (it.onClick) {
          const res = it.onClick();
          if (res && typeof res.catch === 'function') {
            res.catch((err) => toast(err && err.message ? err.message : String(err), 'error'));
          }
        }
      },
    }, it.icon ? icon(it.icon, 16) : null, h('span', null, it.label)));
  }
  pop.addEventListener('keydown', (e) => {
    const list = [...pop.querySelectorAll('.menu-item:not([disabled])')];
    const i = list.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      e.stopPropagation();
      if (!list.length) return;
      const next = e.key === 'ArrowDown' ? (i + 1) % list.length : (i - 1 + list.length) % list.length;
      list[next].focus();
    } else if (e.key === 'Escape' || e.key === 'Tab') {
      e.preventDefault();
      e.stopPropagation();
      ctl.close(true);
    }
  });
  setPopover(ctl);
  document.body.append(pop);
  placePopover(pop, anchor, { align, maxHeight: 360 });
  anchor.setAttribute('aria-haspopup', 'menu');
  anchor.setAttribute('aria-expanded', 'true');
  const first = pop.querySelector('.menu-item:not([disabled])');
  if (first) first.focus();
  // слушатели — после текущего клика, иначе меню закроется тем же нажатием
  setTimeout(() => {
    if (closed) return;
    document.addEventListener('mousedown', onDocDown, true);
    window.addEventListener('resize', onMove);
    window.addEventListener('scroll', onMove, true);
  }, 0);
  return ctl;
}

// ───────────────────────────── модальные окна ─────────────────────────────

const modalStack = [];
let modalKeysInstalled = false;
const NO_ENTER_TYPES = new Set(['checkbox', 'radio', 'button', 'submit', 'reset', 'file', 'range', 'color']);

function installModalKeys() {
  if (modalKeysInstalled) return;
  modalKeysInstalled = true;
  document.addEventListener('keydown', (e) => {
    const top = modalStack[modalStack.length - 1];
    if (top) top.onKey(e);
  });
}

function syncInert() {
  const app = document.getElementById('app');
  const top = modalStack[modalStack.length - 1];
  if (app) app.inert = modalStack.length > 0;
  for (const m of modalStack) m.overlay.inert = m !== top;
}

/**
 * Модальное окно. Возвращает контроллер {el, body, buttons, result, close(value), setBusy, setError, focusFirst}.
 * buttons: [{label, kind, icon, primary, onClick(m), value, align: 'left', disabled, title}].
 *  - без onClick кнопка закрывает окно с value;
 *  - с onClick окно НЕ закрывается само: обработчик вызывает m.close(value); пока он выполняется, кнопки заблокированы;
 *    исключение показывается красной строкой внутри окна (окно остаётся открытым).
 * Esc и крестик закрывают с undefined. Enter в однострочном поле (Ctrl+Enter в многострочном) нажимает кнопку primary.
 */
export function modal({ title = '', body, buttons = [], wide = false, width, onClose, closeOnBackdrop = false, cls } = {}) {
  installModalKeys();
  closePopovers();
  const prevFocus = document.activeElement;
  let closed = false;
  let busy = false;
  let saved = null;
  let resolveResult;
  const result = new Promise((resolve) => {
    resolveResult = resolve;
  });

  const titleId = uid('modal-title');
  const errorEl = h('div.modal-error', { role: 'alert', hidden: true });
  const bodyEl = h('div.modal-body', null, body);
  const footEl = h('div.modal-foot');
  const closeBtn = h('button.modal-close', { type: 'button', title: 'Закрыть (Esc)', 'aria-label': 'Закрыть' }, icon('x', 18));
  const dialog = h('div.modal', {
    role: 'dialog',
    'aria-modal': 'true',
    'aria-labelledby': titleId,
    tabindex: '-1',
    class: [wide && 'modal-wide', cls],
  },
  h('div.modal-head', null, h('h2.modal-title', { id: titleId }, title), closeBtn),
  bodyEl, errorEl, footEl);
  if (width) dialog.style.width = px(width);
  const overlay = h('div.modal-overlay', null, dialog);

  const ctl = {
    el: dialog,
    overlay,
    body: bodyEl,
    buttons: [],
    result,
    close,
    setBusy,
    setError,
    focusFirst,
    setTitle(text) {
      dialog.querySelector('.modal-title').textContent = text;
    },
    onKey,
  };

  const specs = (buttons || []).filter(Boolean).map((spec) => {
    const el = h('button.btn', {
      type: 'button',
      title: spec.title,
      class: [spec.kind && spec.kind !== 'default' && `btn-${spec.kind}`, spec.align === 'left' && 'btn-left'],
      disabled: !!spec.disabled,
    }, spec.icon ? icon(spec.icon, 18) : null, h('span', null, spec.label));
    const rec = { spec, el };
    el.addEventListener('click', () => run(rec));
    footEl.append(el);
    ctl.buttons.push(el);
    return rec;
  });
  if (!specs.length) footEl.hidden = true;
  const primary = specs.find((b) => b.spec.primary)
    || specs.find((b) => b.spec.kind === 'primary' || b.spec.kind === 'accent') || null;

  closeBtn.addEventListener('click', () => {
    if (!busy) close(undefined);
  });
  if (closeOnBackdrop) {
    let downOnOverlay = false;
    overlay.addEventListener('mousedown', (e) => {
      downOnOverlay = e.target === overlay;
    });
    overlay.addEventListener('click', (e) => {
      if (downOnOverlay && e.target === overlay && !busy) close(undefined);
    });
  }

  async function run(rec) {
    if (busy || closed) return;
    if (!rec.spec.onClick) {
      close(rec.spec.value);
      return;
    }
    setBusy(true);
    setError('');
    try {
      await rec.spec.onClick(ctl);
    } catch (err) {
      if (!(err && err.status !== undefined)) console.error(err);
      setError(err && err.message ? err.message : String(err));
    } finally {
      if (!closed) setBusy(false);
    }
  }

  function setBusy(on) {
    if (on === busy) return;
    busy = on;
    dialog.classList.toggle('busy', on);
    if (on) {
      saved = ctl.buttons.map((b) => b.disabled);
      ctl.buttons.forEach((b) => {
        b.disabled = true;
      });
    } else if (saved) {
      ctl.buttons.forEach((b, i) => {
        b.disabled = saved[i];
      });
      saved = null;
    }
  }

  function setError(text) {
    errorEl.textContent = text || '';
    errorEl.hidden = !text;
    if (text) errorEl.scrollIntoView({ block: 'nearest' });
  }

  function focusFirst() {
    const auto = bodyEl.querySelector('[data-autofocus], [autofocus]');
    const target = (auto && !auto.disabled ? auto : null) || focusables(bodyEl)[0]
      || (primary && !primary.el.disabled ? primary.el : null) || focusables(footEl)[0] || dialog;
    target.focus();
  }

  function onKey(e) {
    if (e.defaultPrevented) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      if (!busy) close(undefined);
      return;
    }
    if (e.key === 'Tab') {
      const list = focusables(dialog);
      const active = document.activeElement;
      if (!list.length) {
        e.preventDefault();
        dialog.focus();
      } else if (!dialog.contains(active)) {
        e.preventDefault();
        (e.shiftKey ? list[list.length - 1] : list[0]).focus();
      } else if (e.shiftKey && (active === list[0] || active === dialog)) {
        e.preventDefault();
        list[list.length - 1].focus();
      } else if (!e.shiftKey && active === list[list.length - 1]) {
        e.preventDefault();
        list[0].focus();
      }
      return;
    }
    if (e.key === 'Enter' && !e.isComposing && !e.shiftKey && !e.altKey) {
      const t = e.target;
      if (!(t instanceof Element) || !dialog.contains(t)) return;
      const singleLine = t instanceof HTMLInputElement && !NO_ENTER_TYPES.has(t.type) && !t.hasAttribute('data-no-enter');
      const ctrlInArea = t instanceof HTMLTextAreaElement && (e.ctrlKey || e.metaKey);
      if ((singleLine || ctrlInArea) && primary && !primary.el.disabled) {
        e.preventDefault();
        run(primary);
      }
    }
  }

  function close(value) {
    if (closed) return;
    closed = true;
    closePopovers();
    const i = modalStack.indexOf(ctl);
    if (i >= 0) modalStack.splice(i, 1);
    overlay.remove();
    syncInert();
    if (onClose) {
      try {
        onClose(value);
      } catch (err) {
        console.error(err);
      }
    }
    if (prevFocus && prevFocus.isConnected && typeof prevFocus.focus === 'function') prevFocus.focus();
    resolveResult(value);
  }

  modalStack.push(ctl);
  document.body.append(overlay);
  syncInert();
  focusFirst();
  return ctl;
}

function detailsBlock(details) {
  if (!details) return null;
  if (details instanceof Node) return h('div.confirm-details', null, details);
  const list = (Array.isArray(details) ? details : [details]).filter((x) => x !== null && x !== undefined && x !== '');
  if (!list.length) return null;
  return h('ul.confirm-details', null, list.map((line) => h('li', null, line)));
}

/**
 * Вопрос «да/нет». Возвращает Promise<boolean>. danger: красная кнопка, фокус на «Отмена».
 * details — строка, массив строк или узел (список предупреждений).
 */
export function confirm(text, { danger = false, details, title, okLabel, cancelLabel = 'Отмена' } = {}) {
  const cancel = { label: cancelLabel, value: false };
  const ok = { label: okLabel || 'Подтвердить', kind: danger ? 'danger' : 'primary', primary: true, value: true };
  const m = modal({
    title: title || 'Подтвердите действие',
    body: [h('p.confirm-text', null, text), detailsBlock(details)],
    buttons: [cancel, ok],
    cls: 'modal-confirm',
  });
  (danger ? m.buttons[0] : m.buttons[1]).focus();
  return m.result.then((v) => v === true);
}

/** Сообщение с одной кнопкой «Понятно». Возвращает Promise. */
export function alert(text, { title = 'Сообщение', details, okLabel = 'Понятно' } = {}) {
  const m = modal({
    title,
    body: [h('p.confirm-text', null, text), detailsBlock(details)],
    buttons: [{ label: okLabel, kind: 'primary', primary: true, value: true }],
    cls: 'modal-confirm',
  });
  m.buttons[0].focus();
  return m.result.then(() => undefined);
}

// ───────────────────────────── тосты ─────────────────────────────

const TOAST_ICONS = { ok: 'circle-check', error: 'alert-triangle', warn: 'alert-triangle', info: 'info-circle' };
const TOAST_TIMEOUT = { ok: 3500, info: 4500, warn: 7000, error: 9000 };

/**
 * Короткое сообщение в углу экрана. kind: 'ok' (по умолчанию, «сохранено») | 'error' | 'warn' | 'info'.
 * Исчезает само (ошибки висят дольше), можно закрыть крестиком.
 */
export function toast(text, kind = 'ok', { timeout } = {}) {
  const k = TOAST_ICONS[kind] ? kind : 'info';
  let host = document.querySelector('.toasts');
  if (!host) {
    host = h('div.toasts', { role: 'status', 'aria-live': 'polite' });
    document.body.append(host);
  }
  let timer = null;
  const el = h('div.toast', { class: `toast-${k}` },
    icon(TOAST_ICONS[k], 18),
    h('div.toast-text', null, text),
    h('button.toast-close', { type: 'button', 'aria-label': 'Закрыть сообщение', onclick: () => close() }, icon('x', 14)));
  function close() {
    clearTimeout(timer);
    el.remove();
  }
  host.append(el);
  while (host.children.length > 4) host.firstElementChild.remove();
  timer = setTimeout(close, timeout || TOAST_TIMEOUT[k]);
  return { close };
}

// ───────────────────────────── поля ввода ─────────────────────────────

function applyCommon(el, o) {
  if (o.id) el.id = o.id;
  if (o.name) el.name = o.name;
  if (o.placeholder) el.placeholder = o.placeholder;
  if (o.title) el.title = o.title;
  if (o.ariaLabel) el.setAttribute('aria-label', o.ariaLabel);
  if (o.disabled) el.disabled = true;
  if (o.readOnly) el.readOnly = true;
  if (o.maxLength) el.maxLength = o.maxLength;
  if (o.autofocus) el.dataset.autofocus = '1';
  if (o.cls) el.classList.add(...classList(o.cls));
  if (o.width) el.style.width = px(o.width);
}

function selectAllOnFocus(input) {
  let byMouse = false;
  input.addEventListener('focus', () => input.select());
  input.addEventListener('mousedown', () => {
    byMouse = document.activeElement !== input;
  });
  input.addEventListener('mouseup', (e) => {
    if (byMouse) {
      e.preventDefault(); // иначе клик сбросит выделение, только что поставленное по фокусу
      byMouse = false;
    }
  });
  // Окно ставит фокус в поле раньше, чем программа подставит в него значение (диалог «ТО сделано»):
  // выделение по фокусу пришлось на пустое поле, и набранные цифры дописались бы в хвост подставленного.
  // Если поле в фокусе и было выделено целиком (или пусто), после подстановки выделяем его снова.
  const set = input.setValue;
  if (typeof set === 'function') {
    input.setValue = (v) => {
      const whole = document.activeElement === input
        && input.selectionStart === 0 && input.selectionEnd === input.value.length;
      set(v);
      if (whole && input.value) input.select();
    };
  }
}

/**
 * Подпись + поле: field('Гаражный №', input, {required: true, hint: 'как на борту'}).
 * У результата есть setError(text) (красная строка под полем, '' — убрать) и setHint(text).
 */
export function field(label, control, { required = false, hint, cls } = {}) {
  const input = control instanceof Element
    ? (control.matches('input, select, textarea') ? control : control.querySelector('input, select, textarea'))
    : null;
  if (input && !input.id) input.id = uid('f');
  if (input && required) input.setAttribute('aria-required', 'true');
  const hintEl = h('div.field-hint', { hidden: !hint }, hint);
  const errEl = h('div.field-error', { role: 'alert', hidden: true });
  const el = h('div.field', { class: cls },
    h('label.field-label', { for: input ? input.id : null }, label,
      required ? h('span.field-req', { title: 'Обязательное поле' }, ' *') : null),
    control, hintEl, errEl);
  el.control = control;
  el.setError = (text) => {
    errEl.textContent = text || '';
    errEl.hidden = !text;
    el.classList.toggle('has-error', !!text);
    if (input) input.classList.toggle('invalid', !!text);
  };
  el.setHint = (content) => {
    mount(hintEl, content);
    hintEl.hidden = content === null || content === undefined || content === '';
  };
  return el;
}

/** Однострочное текстовое поле. getValue() → строка без крайних пробелов. */
export function textInput(opts = {}) {
  const input = h('input.input', { type: opts.type || 'text', autocomplete: 'off', spellcheck: 'false' });
  applyCommon(input, opts);
  input.getValue = () => input.value.trim();
  input.setValue = (v) => {
    input.value = v === null || v === undefined ? '' : String(v);
    input.classList.remove('invalid');
  };
  input.isValid = () => true;
  if (opts.onInput) input.addEventListener('input', () => opts.onInput(input.getValue()));
  if (opts.onChange) input.addEventListener('change', () => opts.onChange(input.getValue()));
  if (opts.onEnter) {
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.defaultPrevented) opts.onEnter(input.getValue(), e);
    });
  }
  input.setValue(opts.value);
  return input;
}

/** Многострочное поле. rows — высота в строках (по умолчанию 3). */
export function textArea(opts = {}) {
  const area = h('textarea.input.textarea', { rows: opts.rows || 3 });
  applyCommon(area, opts);
  area.getValue = () => area.value.trim();
  area.setValue = (v) => {
    area.value = v === null || v === undefined ? '' : String(v);
    area.classList.remove('invalid');
  };
  area.isValid = () => true;
  if (opts.onInput) area.addEventListener('input', () => opts.onInput(area.getValue()));
  if (opts.onChange) area.addEventListener('change', () => opts.onChange(area.getValue()));
  area.setValue(opts.value);
  return area;
}

/** Галочка с подписью. Возвращает <label>; .input — сам чекбокс; getValue() → boolean. */
export function checkbox(label, opts = {}) {
  const input = h('input', { type: 'checkbox', checked: !!opts.checked, disabled: !!opts.disabled });
  if (opts.id) input.id = opts.id;
  const el = h('label.checkbox', { class: opts.cls, title: opts.title }, input, label ? h('span', null, label) : null);
  el.input = input;
  el.getValue = () => input.checked;
  el.setValue = (v) => {
    input.checked = !!v;
  };
  if (opts.onChange) input.addEventListener('change', () => opts.onChange(input.checked));
  return el;
}

function normOption(o) {
  if (Array.isArray(o)) return { value: o[0], label: String(o[1] ?? o[0]) };
  if (o !== null && typeof o === 'object') {
    return { ...o, value: o.value, label: String(o.label ?? o.value ?? '') };
  }
  return { value: o, label: String(o ?? '') };
}

/**
 * Выпадающий список. options: строки, {value, label, disabled} или [value, label].
 * placeholder — подпись пустого пункта (значение ''). getValue() → строка; setValue(v) добавит отсутствующее значение
 * отдельным пунктом (старые записи со значением, которого уже нет в справочнике, не теряются).
 */
export function select(opts = {}) {
  const el = h('select.input.select');
  applyCommon(el, { ...opts, placeholder: null });
  el.setOptions = (list) => {
    const keep = el.value;
    el.replaceChildren();
    if (opts.placeholder !== undefined && opts.placeholder !== null) el.append(new Option(opts.placeholder, ''));
    for (const raw of list || []) {
      const o = normOption(raw);
      const opt = new Option(o.label, o.value === null || o.value === undefined ? '' : String(o.value));
      if (o.disabled) opt.disabled = true;
      el.append(opt);
    }
    if (keep) el.setValue(keep);
  };
  el.getValue = () => el.value;
  el.setValue = (v) => {
    const s = v === null || v === undefined ? '' : String(v);
    if (s && ![...el.options].some((o) => o.value === s)) el.append(new Option(s, s));
    el.value = s;
    el.classList.remove('invalid');
  };
  el.isValid = () => true;
  el.setOptions(opts.options);
  if (opts.value !== undefined && opts.value !== null) el.setValue(opts.value);
  if (opts.onChange) el.addEventListener('change', () => opts.onChange(el.value));
  return el;
}

/**
 * Поле даты (родной календарь). Значение — «YYYY-MM-DD» или ''. opts: value, min, max, onChange(value).
 */
export function dateInput(opts = {}) {
  const input = h('input.input.input-date', { type: 'date' });
  applyCommon(input, opts);
  if (opts.min) input.min = opts.min;
  if (opts.max) input.max = opts.max;
  input.getValue = () => input.value;
  input.setValue = (v) => {
    input.value = v ? String(v).slice(0, 10) : '';
    input.classList.remove('invalid');
  };
  input.isValid = () => input.validity.valid;
  if (opts.onChange) input.addEventListener('change', () => opts.onChange(input.value));
  input.setValue(opts.value);
  return input;
}

/**
 * Поле времени. Понимает «130» → 01:30, «9» → 09:00, «1.30» → 01:30, «17:5» → 17:05 (приводит при уходе из поля и по Enter).
 * getValue() → «HH:MM», '' (пусто) или введённый текст как есть, если это не время (тогда isValid() → false).
 */
export function timeInput(opts = {}) {
  const input = h('input.input.input-time', {
    type: 'text', inputmode: 'numeric', autocomplete: 'off', maxlength: '5', placeholder: opts.placeholder || 'чч:мм',
  });
  applyCommon(input, { ...opts, placeholder: null });
  const normalize = () => {
    const t = parseTime(input.value);
    input.classList.toggle('invalid', t === null);
    if (t !== null) input.value = t;
  };
  input.getValue = () => {
    const t = parseTime(input.value);
    return t === null ? input.value.trim() : t;
  };
  input.setValue = (v) => {
    const t = parseTime(v);
    input.value = t === null ? String(v) : t;
    input.classList.toggle('invalid', t === null);
  };
  input.isValid = () => parseTime(input.value) !== null;
  input.addEventListener('blur', normalize);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') normalize();
  });
  input.addEventListener('input', () => {
    input.classList.remove('invalid');
    if (opts.onInput) opts.onInput(input.getValue());
  });
  input.addEventListener('change', () => {
    normalize();
    if (opts.onChange) opts.onChange(input.getValue());
  });
  if (opts.selectAll !== false) selectAllOnFocus(input);
  input.setValue(opts.value);
  return input;
}

/**
 * Числовое поле (показания, нормы, ресурс). Принимает «153 000», «1250,5»; при уходе из поля расставляет пробелы тысяч.
 * opts: value, decimals (макс. знаков после запятой, по умолчанию 3; 0 — целое), allowNegative, onInput(v), onChange(v),
 * thousands (bool или функция → bool; по умолчанию decimals === 0): «153.000»/«153,000» читать как 153 000 (для км).
 * getValue() → число, null (пусто) или введённый текст как есть, если это не число (тогда isValid() → false,
 * сервер ответит понятной ошибкой — пустым значение молча не станет).
 */
export function numInput(opts = {}) {
  const decimals = opts.decimals === undefined ? 3 : opts.decimals;
  const input = h('input.input.input-num', { type: 'text', inputmode: 'decimal', autocomplete: 'off' });
  applyCommon(input, opts);
  const good = (n) => n !== null && (opts.allowNegative || n >= 0);
  const fmt = (n) => groupDigits(n, decimals, ' ', '-');
  // км — целые: «153.000» / «153,000» (одна точка или запятая и ровно 3 цифры за ней) — это 153 тысячи, а не 153
  const thousands = () => {
    const t = opts.thousands === undefined ? decimals === 0 : opts.thousands;
    return typeof t === 'function' ? !!t() : !!t;
  };
  const parse = (s) => {
    const t = String(s).replace(/[\s   ']/g, '').replace(/[^\d.,-]+$/, '');
    if (thousands() && /^-?\d{1,3}[.,]\d{3}$/.test(t)) return parseNum(t.replace(/[.,]/, ''));
    return parseNum(s);
  };
  const normalize = () => {
    const s = input.value.trim();
    if (!s) {
      input.value = '';
      input.classList.remove('invalid');
      return;
    }
    const n = parse(s);
    input.classList.toggle('invalid', !good(n));
    if (good(n)) input.value = fmt(n);
  };
  input.getValue = () => {
    const s = input.value.trim();
    if (!s) return null;
    const n = parse(s);
    if (n === null) return s;
    return decimals === 0 ? Math.round(n) : n;
  };
  input.setValue = (v) => {
    if (v === null || v === undefined || v === '') input.value = '';
    else {
      const n = parseNum(v);
      input.value = n === null ? String(v) : fmt(n);
    }
    input.classList.remove('invalid');
  };
  input.isValid = () => {
    const s = input.value.trim();
    return !s || good(parse(s));
  };
  input.addEventListener('blur', normalize);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') normalize();
  });
  input.addEventListener('input', () => {
    input.classList.remove('invalid');
    if (opts.onInput) opts.onInput(input.getValue());
  });
  input.addEventListener('change', () => {
    normalize();
    if (opts.onChange) opts.onChange(input.getValue());
  });
  if (opts.selectAll !== false) selectAllOnFocus(input);
  input.setValue(opts.value);
  return input;
}

/**
 * Поле с подсказками. Возвращает сам <input> с методами getValue/setValue/setOptions/getOption/isValid.
 * options: строки или {value, label, hint, search}.
 *  - обычный режим (strict: false): свободный текст, подсказки только помогают; getValue() → введённая строка;
 *  - strict: true: выбор из списка (машина, справочник); getValue() → value выбранного пункта или null.
 * onChange(value, option) — когда значение выбрано/изменено; onPick(option) — только при выборе из списка.
 */
export function combo(opts = {}) {
  const strict = !!opts.strict;
  const maxItems = opts.maxItems || 60;
  const pickOnBlur = opts.pickOnBlur !== false;
  const input = h('input.input.combo', {
    type: 'text', autocomplete: 'off', spellcheck: 'false', role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'false',
  });
  applyCommon(input, opts);
  const listId = uid('combo');
  let options = [];
  let picked = null; // выбранный пункт (strict)
  let list = null; // открытый список
  let items = [];
  let active = -1;
  let lastNotified;

  const prep = (raw) => {
    const o = normOption(raw);
    o.searchText = `${o.label} ${o.hint || ''} ${o.search || ''}`.toLowerCase();
    return o;
  };
  const query = () => input.value.trim().toLowerCase();
  const exact = () => {
    const q = query();
    return q ? options.find((o) => o.label.toLowerCase() === q) || null : null;
  };
  const filter = (useQuery) => {
    const q = useQuery ? query() : '';
    if (!q) return options.slice(0, maxItems);
    const starts = [];
    const rest = [];
    for (const o of options) {
      if (o.label.toLowerCase().startsWith(q)) starts.push(o);
      else if (o.searchText.includes(q)) rest.push(o);
    }
    return starts.concat(rest).slice(0, maxItems);
  };

  const ctl = { close };
  const onMove = (e) => {
    if (e && e.target instanceof Node && list && list.contains(e.target)) return;
    if (!input.isConnected) close();
    else if (list) placePopover(list, input, { matchWidth: true });
  };

  function open(useQuery) {
    items = filter(useQuery);
    const q = query();
    if (!items.length && !(strict && useQuery && q)) {
      close();
      return;
    }
    if (!list) {
      list = h('div.popover.combo-list', { id: listId, role: 'listbox' });
      list.addEventListener('mousedown', (e) => e.preventDefault()); // фокус остаётся в поле
      list.addEventListener('click', (e) => {
        const row = e.target.closest('.combo-item');
        if (row) pick(items[Number(row.dataset.i)]);
      });
      setPopover(ctl);
      document.body.append(list);
      window.addEventListener('resize', onMove);
      window.addEventListener('scroll', onMove, true);
      input.setAttribute('aria-expanded', 'true');
      input.setAttribute('aria-controls', listId);
    }
    active = strict && useQuery && q && items.length ? 0 : -1;
    list.replaceChildren(...(items.length
      ? items.map((o, i) => h('div.combo-item', { role: 'option', id: `${listId}-${i}`, dataset: { i } },
        h('span.combo-label', null, o.label), o.hint ? h('span.combo-hint', null, o.hint) : null))
      : [h('div.combo-empty', null, opts.emptyText || 'Ничего не найдено')]));
    highlight();
    placePopover(list, input, { matchWidth: true });
  }

  function highlight() {
    if (!list) return;
    [...list.children].forEach((row, i) => {
      row.classList.toggle('active', i === active);
      if (row.getAttribute('role') === 'option') row.setAttribute('aria-selected', i === active ? 'true' : 'false');
    });
    if (active >= 0) {
      input.setAttribute('aria-activedescendant', `${listId}-${active}`);
      const row = list.children[active];
      if (row) row.scrollIntoView({ block: 'nearest' });
    } else input.removeAttribute('aria-activedescendant');
  }

  function close() {
    if (!list) return;
    list.remove();
    list = null;
    active = -1;
    window.removeEventListener('resize', onMove);
    window.removeEventListener('scroll', onMove, true);
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    if (currentPopover === ctl) currentPopover = null;
  }

  function notify() {
    const v = input.getValue();
    if (v === lastNotified) return;
    lastNotified = v;
    if (opts.onChange) opts.onChange(v, picked);
  }

  function pick(o) {
    if (!o) return;
    picked = o;
    input.value = o.label;
    input.classList.remove('invalid');
    close();
    notify();
    if (opts.onPick) opts.onPick(o);
  }

  input.getValue = () => {
    if (!strict) return input.value.trim();
    const o = picked && picked.label.toLowerCase() === query() ? picked : exact();
    return o ? o.value : null;
  };
  input.getOption = () => (strict ? (picked && picked.label.toLowerCase() === query() ? picked : exact()) : null);
  input.setValue = (v) => {
    if (strict) {
      picked = v === null || v === undefined || v === '' ? null : options.find((o) => String(o.value) === String(v)) || null;
      input.value = picked ? picked.label : '';
    } else {
      input.value = v === null || v === undefined ? '' : String(v);
    }
    input.classList.remove('invalid');
    lastNotified = input.getValue();
  };
  input.setOptions = (rawList) => {
    options = (rawList || []).map(prep);
    if (strict && picked) picked = options.find((o) => String(o.value) === String(picked.value)) || null;
    if (list) open(true);
  };
  input.isValid = () => !strict || !query() || input.getValue() !== null;

  input.addEventListener('focus', () => open(false));
  input.addEventListener('click', () => {
    if (!list) open(false);
  });
  input.addEventListener('input', () => {
    picked = null;
    input.classList.remove('invalid');
    open(true);
    if (opts.onInput) opts.onInput(input.value.trim());
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!list) {
        open(false);
        return;
      }
      if (!items.length) return;
      if (e.key === 'ArrowDown') active = active < items.length - 1 ? active + 1 : 0;
      else active = active > 0 ? active - 1 : items.length - 1;
      highlight();
    } else if (e.key === 'Enter') {
      if (list && active >= 0 && items[active]) {
        e.preventDefault();
        e.stopPropagation();
        pick(items[active]);
      } else if (list) close();
    } else if (e.key === 'Escape') {
      if (list) {
        e.preventDefault();
        e.stopPropagation();
        close();
      }
    } else if (e.key === 'Tab') {
      if (list && strict && active >= 0 && items[active]) pick(items[active]);
    }
  });
  input.addEventListener('blur', () => {
    close();
    if (strict) {
      const q = query();
      if (!q) picked = null;
      else if (!(picked && picked.label.toLowerCase() === q)) {
        const match = exact();
        const near = match ? null : filter(true);
        if (match) picked = match;
        else if (pickOnBlur && near.length === 1) picked = near[0];
        else picked = null;
        if (picked) input.value = picked.label;
      }
      input.classList.toggle('invalid', !!q && !picked);
    }
    notify();
  });

  input.setOptions(opts.options);
  input.setValue(opts.value);
  return input;
}

/**
 * Открыть выбор файла: pickFile('.xlsx') → Promise<File | null> (null — пользователь отменил).
 * С {multiple: true} можно выбрать несколько: pickFile('image/*', {multiple: true}) → Promise<File[]> (отмена — []).
 */
export function pickFile(accept, { multiple = false } = {}) {
  return new Promise((resolve) => {
    const input = h('input', { type: 'file', accept, multiple, style: 'display:none' });
    let done = false;
    const finish = (file) => {
      if (done) return;
      done = true;
      input.remove();
      resolve(file);
    };
    const picked = () => (multiple
      ? Array.from(input.files || [])
      : (input.files && input.files[0] ? input.files[0] : null));
    input.addEventListener('change', () => finish(picked()));
    input.addEventListener('cancel', () => finish(multiple ? [] : null));
    document.body.append(input);
    input.click();
  });
}

// ───────────────────────────── таблица ─────────────────────────────

function isEmptyValue(v) {
  return v === null || v === undefined || v === '' || (typeof v === 'number' && Number.isNaN(v));
}

function compareValues(a, b) {
  if (typeof a === 'boolean') a = a ? 1 : 0;
  if (typeof b === 'boolean') b = b ? 1 : 0;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b), 'ru', { numeric: true, sensitivity: 'base' });
}

function alignClass(align) {
  return align === 'right' ? 'num' : align === 'center' ? 'ta-center' : null;
}

/**
 * Таблица с сортировкой по клику на шапку и «липкой» шапкой. Возвращает div.table-wrap с методами
 * setRows(rows), update(), setColumns(columns), setSort(sort), getSort(), getRows() и свойством .table.
 *
 * columns: [{key, title, align: 'left'|'right'|'center', width, render(row, i), sortValue(row), cls,
 *            sortable, group, input, hint, footer, hidden}]
 * sort: {key, dir: 'asc'|'desc'} | null; onSort(sort); onRowClick(row, event); rowClass(row, i) → строка классов;
 * empty — текст или узел для пустой таблицы; sticky (true) — липкая шапка; fill — занять остаток высоты экрана;
 * maxHeight — своя высота области прокрутки; inputNav — Enter/↓/↑ в полях ввода переходят по строкам в той же колонке.
 */
export function table(opts = {}) {
  const { onSort, onRowClick, rowClass, sticky = true, zebra = true, fill = false, maxHeight, inputNav = false, cls } = opts;
  let empty = opts.empty === undefined ? 'Нет данных' : opts.empty;
  let columns = [];
  let rows = Array.isArray(opts.rows) ? opts.rows : [];
  let sort = opts.sort && opts.sort.key ? { key: opts.sort.key, dir: opts.sort.dir === 'desc' ? 'desc' : 'asc' } : null;
  let shown = [];

  const colgroup = h('colgroup');
  const thead = h('thead');
  const tbody = h('tbody');
  const tfoot = h('tfoot');
  const tbl = h('table.table', { class: zebra && 'table-zebra' }, colgroup, thead, tbody, tfoot);
  const wrap = h('div.table-wrap', { class: [sticky && 'table-sticky', fill && 'table-fill', cls] }, tbl);
  if (maxHeight) wrap.style.maxHeight = px(maxHeight);

  function prepColumns(list) {
    columns = (list || [])
      .map((c, i) => (c ? { ...c, _id: c.key !== undefined && c.key !== null ? String(c.key) : `col${i}` } : null))
      .filter((c) => c && !c.hidden);
  }

  function isSortable(c) {
    return c.sortable !== false && (c.key !== undefined || typeof c.sortValue === 'function');
  }

  function headCell(c) {
    const th = h('th', { scope: 'col', class: [alignClass(c.align), c.input && 'th-input', c.headCls], title: c.hint, dataset: { col: c._id } });
    if (isSortable(c)) {
      const isActive = !!sort && sort.key === c._id;
      th.setAttribute('aria-sort', isActive ? (sort.dir === 'desc' ? 'descending' : 'ascending') : 'none');
      th.append(h('button.th-sort', { type: 'button', class: isActive && 'active', onclick: () => toggleSort(c._id) },
        h('span', null, c.title),
        isActive ? icon(sort.dir === 'desc' ? 'chevron-down' : 'chevron-up', 14) : null));
    } else {
      appendChild(th, c.title);
    }
    return th;
  }

  function renderHead() {
    colgroup.replaceChildren(...columns.map((c) => {
      const col = h('col');
      if (c.width) col.style.width = px(c.width);
      return col;
    }));
    const hasGroups = columns.some((c) => c.group);
    const row1 = h('tr');
    const row2 = h('tr');
    let i = 0;
    while (i < columns.length) {
      const c = columns[i];
      if (hasGroups && c.group) {
        let j = i;
        while (j < columns.length && columns[j].group === c.group) j += 1;
        row1.append(h('th.th-group', { scope: 'colgroup', colSpan: j - i, class: c.input && 'th-input' },
          c.groupTitle !== undefined ? c.groupTitle : c.group));
        for (let k = i; k < j; k += 1) row2.append(headCell(columns[k]));
        i = j;
      } else {
        const th = headCell(c);
        if (hasGroups) th.rowSpan = 2;
        row1.append(th);
        i += 1;
      }
    }
    thead.replaceChildren(row1, ...(hasGroups ? [row2] : []));
  }

  function sortedRows() {
    const col = sort ? columns.find((c) => c._id === sort.key) : null;
    if (!col) return rows.slice();
    const val = typeof col.sortValue === 'function' ? col.sortValue : (r) => r[col.key];
    const dir = sort.dir === 'desc' ? -1 : 1;
    return rows
      .map((r, i) => ({ r, i, v: val(r) }))
      .sort((a, b) => {
        const ae = isEmptyValue(a.v);
        const be = isEmptyValue(b.v);
        if (ae || be) return ae && be ? a.i - b.i : ae ? 1 : -1; // пустые — всегда в конце
        return compareValues(a.v, b.v) * dir || a.i - b.i;
      })
      .map((x) => x.r);
  }

  function renderBody() {
    shown = sortedRows();
    if (!shown.length) {
      tbody.replaceChildren(h('tr.table-empty', null,
        h('td', { colSpan: Math.max(columns.length, 1) },
          typeof empty === 'string' ? h('div.table-empty-text', null, empty) : empty)));
    } else {
      const frag = document.createDocumentFragment();
      shown.forEach((row, i) => {
        const tr = document.createElement('tr');
        tr.dataset.i = i;
        const rc = rowClass ? classList(rowClass(row, i)) : [];
        if (onRowClick) {
          rc.push('clickable');
          tr.tabIndex = 0;
        }
        if (rc.length) tr.className = rc.join(' ');
        for (const c of columns) {
          const td = document.createElement('td');
          const cc = classList([alignClass(c.align), c.input && 'cell-input', typeof c.cls === 'function' ? c.cls(row) : c.cls]);
          if (cc.length) td.className = cc.join(' ');
          appendChild(td, c.render ? c.render(row, i) : row[c.key]);
          tr.append(td);
        }
        frag.append(tr);
      });
      tbody.replaceChildren(frag);
    }
    if (columns.some((c) => c.footer !== undefined)) {
      tfoot.replaceChildren(h('tr', null, columns.map((c) => h('td', { class: alignClass(c.align) },
        typeof c.footer === 'function' ? c.footer(shown) : c.footer))));
    } else tfoot.replaceChildren();
  }

  function toggleSort(id) {
    if (!sort || sort.key !== id) sort = { key: id, dir: 'asc' };
    else if (sort.dir === 'asc') sort = { key: id, dir: 'desc' };
    else sort = null; // третий клик — исходный порядок
    renderHead();
    renderBody();
    const btn = [...thead.querySelectorAll('th')].find((th) => th.dataset.col === id);
    if (btn && btn.querySelector('.th-sort')) btn.querySelector('.th-sort').focus();
    if (onSort) onSort(sort ? { ...sort } : null);
  }

  tbody.addEventListener('click', (e) => {
    if (!onRowClick) return;
    if (e.target.closest('a, button, input, select, textarea, label, [data-no-row-click]')) return;
    const sel = window.getSelection ? String(window.getSelection()) : '';
    if (sel) return; // человек выделяет текст — не уводим его со страницы
    const tr = e.target.closest('tr[data-i]');
    if (tr) onRowClick(shown[Number(tr.dataset.i)], e);
  });

  tbody.addEventListener('keydown', (e) => {
    if (e.defaultPrevented) return;
    const t = e.target;
    if (onRowClick && e.key === 'Enter' && t instanceof HTMLTableRowElement && t.dataset.i !== undefined) {
      e.preventDefault();
      onRowClick(shown[Number(t.dataset.i)], e);
      return;
    }
    if (!inputNav || !(t instanceof HTMLInputElement) || t.type === 'checkbox' || t.type === 'radio') return;
    let step = 0;
    if (e.key === 'Enter') step = e.shiftKey ? -1 : 1;
    else if (t.type !== 'date' && e.key === 'ArrowDown') step = 1;
    else if (t.type !== 'date' && e.key === 'ArrowUp') step = -1;
    if (!step) return;
    const td = t.closest('td');
    let tr = td ? td.parentElement : null;
    if (!tr) return;
    const ci = [...tr.children].indexOf(td);
    for (;;) {
      tr = step > 0 ? tr.nextElementSibling : tr.previousElementSibling;
      if (!tr) return;
      const cell = tr.children[ci];
      const target = cell ? cell.querySelector('input:not([disabled]):not([type="checkbox"]), select:not([disabled])') : null;
      if (target) {
        e.preventDefault();
        target.focus();
        if (typeof target.select === 'function') target.select();
        return;
      }
    }
  });

  wrap.table = tbl;
  wrap.setRows = (list) => {
    rows = Array.isArray(list) ? list : [];
    renderBody();
  };
  wrap.update = () => renderBody();
  wrap.getRows = () => shown.slice();
  wrap.getSort = () => (sort ? { ...sort } : null);
  wrap.setSort = (s) => {
    sort = s && s.key ? { key: String(s.key), dir: s.dir === 'desc' ? 'desc' : 'asc' } : null;
    renderHead();
    renderBody();
  };
  wrap.setColumns = (list) => {
    prepColumns(list);
    renderHead();
    renderBody();
  };
  wrap.setEmpty = (content) => {
    empty = content;
    renderBody();
  };

  prepColumns(opts.columns);
  renderHead();
  renderBody();
  return wrap;
}

// ───────────────────────────── статусы, вкладки, состояния ─────────────────────────────

// вид → [цвет, подпись по умолчанию]. Кружки-эмодзи оставлены намеренно: механик привык к ним по таблице.
const CHIPS = {
  overdue: ['red', '🔴 Просрочено'],
  soon: ['amber', '🟡 Скоро ТО'],
  ok: ['green', '🟢 В норме'],
  none: ['grey', '⚪ Нет данных'],
  no_to: ['grey', '⚪ Внесите ТО'],
  no_reading: ['grey', '⚪ Нет показаний'],
  line: ['green', '🟢 На линии'],
  repair: ['red', '🔴 На ремонте'],
  to: ['blue', '🔧 На ТО'],
  idle: ['amber', '🟡 Простой'],
  conserved: ['grey', '🅿 Консервация'],
  red: ['red', '🔴 Менять'],
  yellow: ['amber', '🟡 Скоро менять'],
  green: ['green', '🟢 Норма'],
  check: ['grey', '⚪ Проверьте'],
  amber: ['amber', ''],
  grey: ['grey', ''],
  blue: ['blue', ''],
};

/**
 * Статус-чип. kind: overdue | soon | ok | none | no_to | no_reading (статус ТО), line | repair | to | idle | conserved
 * (состояние), red | yellow | green | check (шины/узлы), либо просто цвет: red | amber | green | grey | blue.
 * label — подпись с сервера (to_status_label, state_label, status_label); без неё — подпись по умолчанию.
 */
export function statusChip(kind, label) {
  const [color, def] = CHIPS[kind] || ['grey', ''];
  const text = label === undefined || label === null ? def : String(label);
  if (!text) return h('span.chip-empty');
  return h('span.chip', { class: [`chip-${color}`, kind && kind !== color && `chip-${kind}`] }, text);
}

/**
 * Вкладки: tabs({items: [{key, label, icon, count, disabled}], active, onChange(key)}).
 * Возвращает div.tabs с методами setActive(key) и getActive(). Содержимое вкладки рисует страница в onChange.
 */
export function tabs({ items = [], active, onChange, cls } = {}) {
  let current = active !== undefined && active !== null ? active : (items[0] ? items[0].key : null);
  const el = h('div.tabs', { role: 'tablist', class: cls });
  const btns = items.map((it) => {
    const btn = h('button.tab', { type: 'button', role: 'tab', disabled: !!it.disabled, dataset: { key: it.key } },
      it.icon ? icon(it.icon, 16) : null,
      h('span', null, it.label),
      it.count !== undefined && it.count !== null ? h('span.tab-count', null, String(it.count)) : null);
    btn.addEventListener('click', () => setActive(it.key));
    el.append(btn);
    return { key: it.key, btn };
  });
  function paint() {
    for (const b of btns) {
      const on = b.key === current;
      b.btn.setAttribute('aria-selected', on ? 'true' : 'false');
      b.btn.tabIndex = on ? 0 : -1;
    }
  }
  function setActive(key, silent) {
    if (key === current) return;
    current = key;
    paint();
    if (!silent && onChange) onChange(key);
  }
  el.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    const enabled = btns.filter((b) => !b.btn.disabled);
    const i = enabled.findIndex((b) => b.key === current);
    if (i < 0 || !enabled.length) return;
    e.preventDefault();
    const next = enabled[(i + (e.key === 'ArrowRight' ? 1 : enabled.length - 1)) % enabled.length];
    setActive(next.key);
    next.btn.focus();
  });
  el.setActive = setActive;
  el.getActive = () => current;
  paint();
  return el;
}

function actionNode(action) {
  if (!action) return null;
  if (action instanceof Node) return action;
  return button(action.label, { kind: action.kind || 'primary', icon: action.icon, onClick: action.onClick });
}

/**
 * Пустое состояние экрана/блока: иконка, одна фраза и (если есть) одна кнопка «с чего начать».
 * emptyState('Журнал пуст', {icon: 'notebook', title: 'Записей пока нет', action: {label: '+ Запись', onClick}}).
 */
export function emptyState(text, { icon: iconName = 'info-circle', title, action } = {}) {
  return h('div.empty', null,
    iconName ? h('div.empty-icon', null, icon(iconName, 36)) : null,
    title ? h('div.empty-title', null, title) : null,
    text ? h('div.empty-text', null, text) : null,
    action ? h('div.empty-action', null, actionNode(action)) : null);
}

/** Тихая строка «Загрузка…». */
export function loading(text = 'Загрузка…') {
  return h('div.loading', { role: 'status' }, text);
}

/**
 * Блок ошибки с кнопкой «Повторить»: errorState(err, () => load()). err — исключение или текст.
 */
export function errorState(err, onRetry, { title = 'Не получилось загрузить данные' } = {}) {
  const text = err && err.message ? err.message : String(err || 'Неизвестная ошибка');
  return h('div.error-state', { role: 'alert' },
    h('div.error-state-icon', null, icon('alert-triangle', 28)),
    h('div.error-state-body', null,
      h('div.error-state-title', null, title),
      h('div.error-state-text', null, text),
      onRetry ? h('div.error-state-action', null, button('Повторить', { icon: 'refresh', kind: 'primary', onClick: onRetry })) : null));
}

const NOTICE_ICONS = { info: 'info-circle', warn: 'alert-triangle', error: 'alert-triangle', ok: 'circle-check' };

/**
 * Строка-сообщение над таблицей: notice('У 3 машин не внесено последнее ТО', 'warn', {action: {label, onClick}}).
 * kind: 'info' | 'warn' | 'error' | 'ok'. text — строка или узлы.
 */
export function notice(text, kind = 'info', { action } = {}) {
  const k = NOTICE_ICONS[kind] ? kind : 'info';
  const act = action instanceof Node ? action
    : action ? button(action.label, { small: true, icon: action.icon, onClick: action.onClick }) : null;
  return h('div.notice', { class: `notice-${k}` },
    icon(NOTICE_ICONS[k], 18),
    h('div.notice-text', null, text),
    act);
}

/**
 * Плитка-счётчик: kpi({label: 'ТО просрочено', value: 4, kind: 'red', active, onClick}).
 * kind — цвет числа: red | amber | green | grey | blue. С onClick плитка — кнопка (фильтр), active подсвечивает её.
 * У результата есть setActive(bool) и setValue(v).
 */
export function kpi({ label, value, kind, active = false, onClick, hint } = {}) {
  const valueEl = h('div.kpi-value', null, value === null || value === undefined ? '—' : String(value));
  const el = h(onClick ? 'button.kpi' : 'div.kpi', {
    type: onClick ? 'button' : null,
    class: [kind && `kpi-${kind}`, active && 'active'],
    title: hint,
    'aria-pressed': onClick ? (active ? 'true' : 'false') : null,
  }, valueEl, h('div.kpi-label', null, label));
  if (onClick) el.addEventListener('click', (e) => onClick(e));
  el.setActive = (on) => {
    el.classList.toggle('active', !!on);
    if (onClick) el.setAttribute('aria-pressed', on ? 'true' : 'false');
  };
  el.setValue = (v) => {
    valueEl.textContent = v === null || v === undefined ? '—' : String(v);
  };
  return el;
}
