// Оболочка «Учёт ТО»: боковая панель, верхняя строка с поиском машины, hash-роутер, ленивые страницы.
// Контракт страницы: export async function render(root, params, ctx) — см. app/static/UI_KIT.md.

import * as api from './api.js';
import * as ui from './ui.js';

const { h, icon } = ui;

const APP_NAME = 'Учёт ТО';
const DEFAULT_HASH = '#/fleet';

// имя маршрута → заголовок, модуль страницы (js/pages/<module>.js), подсвечиваемый пункт навигации.
// v2 (docs/КОНЦЕПЦИЯ_v2.md): «Показания», «Журнал» и «Отчёты» слиты в главную — их адреса ведут туда (см. legacyTarget).
const ROUTES = {
  fleet: { title: 'Главная', module: 'fleet', nav: 'fleet' },
  machine: { title: 'Карточка машины', module: 'card', nav: 'fleet' },
  plan: { title: 'Список на ТО', module: 'plan', nav: 'plan' },
  workorder: { title: 'Заказ-наряд', module: 'workorder', nav: 'workorder' },
  machines: { title: 'Машины', module: 'machines', nav: 'machines' },
  tyres: { title: 'Шины', module: 'tyres', nav: 'tyres' },
  units: { title: 'Узлы', module: 'units', nav: 'units' },
  settings: { title: 'Настройки', module: 'settings', nav: 'settings' },
  welcome: { title: 'Первый запуск', module: 'welcome', nav: null },
};

// [маршрут, подпись, иконка]; строка — подпись-заголовок группы; null — разделитель
const NAV = [
  ['fleet', 'Главная', 'truck'],
  ['plan', 'Список на ТО', 'calendar-check'],
  'Служебное',
  ['workorder', 'Заказ-наряд', 'clipboard-text'],
  ['machines', 'Машины', 'list-details'],
  ['tyres', 'Шины', 'wheel'],
  ['units', 'Узлы', 'engine'],
  null,
  ['settings', 'Настройки', 'settings'],
];

// экраны, доступные при пустой базе (остальные ведут на «Первый запуск»)
const FIRST_RUN_OPEN = new Set(['welcome', 'settings', 'machines']);

// Диалоги грузятся при первом обращении: ошибка в dialogs.js не должна ронять всю программу.
// ctx.dialogs.<имя>(...) → Promise, как и описано в SPEC 7.1.
const dialogs = new Proxy({}, {
  get(target, name) {
    if (typeof name !== 'string' || name === 'then') return undefined;
    return async (...args) => {
      const mod = await import('./dialogs.js');
      if (typeof mod[name] !== 'function') throw new Error(`Диалог «${name}» не найден в dialogs.js`);
      return mod[name](...args);
    };
  },
});

const ctx = {
  boot: null,
  api,
  ui,
  dialogs,
  navigate,
  refresh,
  setTitle,
  setLeaveGuard,
};

const els = {};
let renderSeq = 0;
let pageCleanup = null;
let leaveGuard = null;
let currentHash = '';
let recoveredSeen = false;
const importBust = {};

// ───────────────────────────── оболочка ─────────────────────────────

function buildSearch() {
  let loadedAt = 0;
  const input = ui.combo({
    strict: true,
    pickOnBlur: false,
    placeholder: 'Найти машину: гаражный № или госномер',
    ariaLabel: 'Поиск машины по гаражному номеру или госномеру',
    emptyText: 'Такой машины нет',
    cls: 'search-input',
    onPick: (opt) => {
      input.setValue(null);
      input.blur();
      navigate(`#/machine/${opt.value}`);
    },
  });
  const load = async () => {
    if (Date.now() - loadedAt < 3000) return;
    loadedAt = Date.now();
    try {
      const machines = await api.get('/machines');
      input.setOptions((machines || []).map((m) => ({
        value: m.id,
        label: m.model ? `${m.garage_no} · ${m.model}` : String(m.garage_no),
        hint: [m.plate, m.status === 'Списана' ? 'списана' : ''].filter(Boolean).join(' · '),
        search: `${m.plate || ''} ${m.vin || ''}`,
      })));
    } catch (err) {
      loadedAt = 0; // не получилось — попробуем при следующем фокусе
    }
  };
  input.addEventListener('focus', load);
  input.addEventListener('blur', () => {
    setTimeout(() => {
      if (document.activeElement !== input) input.setValue(null);
    }, 0);
  });
  return h('div.topbar-search', null, icon('search', 16), input);
}

function buildShell() {
  els.nav = h('nav.nav', { 'aria-label': 'Разделы программы' });
  for (const item of NAV) {
    if (!item) {
      els.nav.append(h('div.nav-sep', { role: 'separator' }));
      continue;
    }
    if (typeof item === 'string') {
      els.nav.append(h('div.nav-caption', null, item));
      continue;
    }
    const [name, label, iconName] = item;
    els.nav.append(h('a.nav-item', { href: `#/${name}`, dataset: { nav: name } }, icon(iconName, 20), h('span', null, label)));
  }
  els.version = h('div.sidebar-foot');
  els.title = h('h1.page-title', null, APP_NAME);
  els.date = h('div.topbar-date');
  els.banner = h('div.conn-banner', { role: 'alert', hidden: true },
    icon('alert-triangle', 18),
    h('span', null, 'Нет связи с программой. Возможно, она закрыта — запустите её заново. Это окно не закрывайте, если в нём есть несохранённый ввод.'));
  els.recovered = h('div.conn-banner', { role: 'alert', hidden: true });
  els.page = h('main.content', { id: 'page' });

  document.getElementById('app').replaceChildren(
    h('aside.sidebar', null,
      h('a.brand', { href: DEFAULT_HASH, title: 'На главную' }, icon('gauge', 22), h('span', null, APP_NAME)),
      els.nav,
      els.version),
    h('div.main', null,
      h('header.topbar', null, els.title, h('div.topbar-right', null, els.date, buildSearch())),
      els.banner,
      els.recovered,
      els.page),
  );
}

function setTitle(text) {
  els.title.textContent = text;
  document.title = text && text !== APP_NAME ? `${text} — ${APP_NAME}` : APP_NAME;
}

function markNav(name) {
  for (const a of els.nav.querySelectorAll('.nav-item')) {
    const on = a.dataset.nav === name;
    a.classList.toggle('active', on);
    if (on) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  }
}

function showMeta() {
  els.version.textContent = ctx.boot && ctx.boot.version ? `версия ${ctx.boot.version}` : '';
  els.date.textContent = new Date().toLocaleDateString('ru-RU', { weekday: 'short', day: 'numeric', month: 'long' });
  showRecovered();
}

/** База при запуске оказалась повреждена и восстановлена из копии — сказать об этом, пока не закроют. */
function showRecovered() {
  const info = ctx.boot && ctx.boot.db_recovered;
  if (!info || recoveredSeen) {
    els.recovered.hidden = true;
    return;
  }
  const m = /^to-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})/.exec(info.restored_from || '');
  const what = m
    ? `База программы была повреждена — восстановлена резервная копия от ${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]}. `
      + 'Записи, сделанные после неё, нужно внести заново.'
    : 'База программы была повреждена, исправной резервной копии не нашлось — программа начата с пустой базы. '
      + 'Загрузите данные из таблицы (Настройки → Данные) или подложите копию to.db.';
  els.recovered.replaceChildren(
    icon('alert-triangle', 18),
    h('span', null, `${what} Повреждённый файл сохранён в папке данных как «${info.broken}».`),
    ui.button('Понятно', { small: true, onClick: () => { recoveredSeen = true; showRecovered(); } }));
  els.recovered.hidden = false;
}

// ───────────────────────────── роутер ─────────────────────────────

function safeDecode(s) {
  try {
    return decodeURIComponent(s);
  } catch (err) {
    return s;
  }
}

/** '#/workorder?machine_id=5' → {name: 'workorder', params: {machine_id: '5'}}; '#/machine/7' → {name: 'machine', params: {id: '7'}}. */
function parseHash(hash) {
  let path = String(hash || '').replace(/^#\/?/, '');
  let query = '';
  const qi = path.indexOf('?');
  if (qi >= 0) {
    query = path.slice(qi + 1);
    path = path.slice(0, qi);
  }
  const parts = path.split('/').filter(Boolean).map(safeDecode);
  const params = {};
  for (const [key, val] of new URLSearchParams(query)) params[key] = val;
  const name = parts[0] || 'fleet';
  if (name === 'machine' && parts[1]) params.id = parts[1];
  return { name, params };
}

/**
 * Старые адреса v1 не ломаются (закладки, ссылки в старых записях): «Показания» и «Отчёты» → главная,
 * «Журнал» по машине → её карточка (история там), журнал целиком → главная. null — адрес не старый.
 */
function legacyTarget(name, params) {
  if (name === 'readings' || name === 'reports') return DEFAULT_HASH;
  if (name === 'journal') {
    const id = String(params.machine_id || '').trim();
    return /^\d+$/.test(id) ? `#/machine/${id}` : DEFAULT_HASH;
  }
  return null;
}

function navigate(hash) {
  let target = String(hash || DEFAULT_HASH);
  if (!target.startsWith('#')) target = `#${target.startsWith('/') ? '' : '/'}${target}`;
  if (target === location.hash) renderRoute();
  else location.hash = target;
}

async function refresh() {
  ctx.boot = await api.get('/bootstrap');
  await renderRoute({ keepScroll: true });
}

/**
 * Страница с несохранённым вводом может попросить переспросить перед уходом:
 * ctx.setLeaveGuard(() => есть_несохранённое ? 'Показания не сохранены. Уйти с экрана?' : null). null — снять.
 */
function setLeaveGuard(fn) {
  leaveGuard = typeof fn === 'function' ? fn : null;
}

function guardMessage() {
  if (!leaveGuard) return null;
  try {
    const msg = leaveGuard();
    if (!msg) return null;
    return typeof msg === 'string' ? msg : 'На этом экране есть несохранённые данные. Уйти без сохранения?';
  } catch (err) {
    console.error(err);
    return null;
  }
}

async function onHashChange() {
  if (!ctx.boot || location.hash === currentHash) return;
  const msg = guardMessage();
  if (msg) {
    const target = location.hash;
    history.replaceState(null, '', currentHash || DEFAULT_HASH); // пока спрашиваем — адрес прежний
    const ok = await ui.confirm(msg, { title: 'Данные не сохранены', okLabel: 'Уйти без сохранения', cancelLabel: 'Остаться', danger: true });
    if (!ok) return;
    leaveGuard = null;
    location.hash = target;
    return;
  }
  renderRoute();
}

function leavePage() {
  ui.closePopovers();
  if (pageCleanup) {
    try {
      pageCleanup();
    } catch (err) {
      console.error(err);
    }
  }
  pageCleanup = null;
  leaveGuard = null;
}

async function loadPage(module) {
  const bust = importBust[module] ? `?r=${importBust[module]}` : '';
  try {
    const mod = await import(`./pages/${module}.js${bust}`);
    if (typeof mod.render !== 'function') throw new Error(`в модуле pages/${module}.js нет функции render`);
    return mod;
  } catch (err) {
    importBust[module] = Date.now(); // браузер запоминает неудачную загрузку модуля — «Повторить» берёт его заново
    if (err instanceof TypeError && /dynamically imported module/i.test(err.message)) {
      // файл экрана не скачался: если сервер не отвечает, этот запрос бросит понятное «Нет связи с программой»
      await api.get('/ping');
      throw new Error(`не найден файл экрана pages/${module}.js`);
    }
    throw err;
  }
}

function pageError(err) {
  const fromServer = err instanceof api.ApiError;
  const shown = fromServer ? err : new Error(`Ошибка в программе: ${err && err.message ? err.message : err}`);
  return ui.errorState(shown, () => renderRoute(), {
    title: fromServer ? 'Не получилось загрузить данные' : 'Этот экран не открылся',
  });
}

async function renderRoute({ keepScroll = false } = {}) {
  renderSeq += 1;
  const seq = renderSeq;
  let { name, params } = parseHash(location.hash);

  const legacy = legacyTarget(name, params);
  if (legacy) {
    // replaceState, а не location.hash: старый адрес не остаётся в истории, и «Назад» не зацикливается на редиректе
    history.replaceState(null, '', legacy);
    ({ name, params } = parseHash(location.hash));
  }

  if (ctx.boot.first_run && !FIRST_RUN_OPEN.has(name)) {
    // первую машину могли только что добавить — перепроверяем, прежде чем уводить на «Первый запуск»
    try {
      ctx.boot = await api.get('/bootstrap');
    } catch (err) {
      // сервер не ответил — остаёмся с прежними данными, ошибку покажет сам экран
    }
    if (seq !== renderSeq) return;
    if (ctx.boot.first_run) {
      history.replaceState(null, '', '#/welcome');
      ({ name, params } = parseHash(location.hash));
    }
  } else if (name === 'welcome' && !ctx.boot.first_run) {
    history.replaceState(null, '', DEFAULT_HASH);
    ({ name, params } = parseHash(location.hash));
  }
  currentHash = location.hash;
  if (!api.isOnline()) api.ping(); // связь могла вернуться — баннер «нет связи» уберётся сам

  const scrollTop = keepScroll ? els.page.scrollTop : 0;
  leavePage();
  const route = name === 'machine' && !params.id ? null : ROUTES[name];
  setTitle(route ? route.title : 'Экран не найден');
  markNav(route ? route.nav : null);
  showMeta();

  const root = h('div.page', { dataset: { page: name } });
  const wait = ui.loading();
  wait.hidden = true;
  els.page.replaceChildren(wait, root);
  els.page.scrollTop = 0;
  const timer = setTimeout(() => {
    wait.hidden = false;
  }, 250);

  try {
    if (!route) {
      root.append(ui.emptyState('Такого экрана в программе нет. Возможно, ссылка устарела.', {
        icon: 'alert-triangle',
        title: 'Экран не найден',
        action: { label: 'На главную', onClick: () => navigate(DEFAULT_HASH) },
      }));
      return;
    }
    const mod = await loadPage(route.module);
    if (seq !== renderSeq) return;
    const result = await mod.render(root, params, ctx);
    if (seq !== renderSeq) {
      if (typeof result === 'function') result(); // экран уже сменили — сразу убираем за страницей
      return;
    }
    if (typeof result === 'function') pageCleanup = result;
    if (keepScroll) els.page.scrollTop = scrollTop;
  } catch (err) {
    if (seq !== renderSeq) return;
    console.error(err);
    root.replaceChildren(pageError(err));
  } finally {
    clearTimeout(timer);
    wait.remove();
  }
}

// ───────────────────────────── запуск ─────────────────────────────

async function boot() {
  ui.mount(els.page, ui.loading('Загрузка…'));
  try {
    ctx.boot = await api.get('/bootstrap');
  } catch (err) {
    ui.mount(els.page, ui.errorState(err, boot, { title: 'Не удалось запустить программу' }));
    return;
  }
  if (!location.hash || location.hash === '#' || location.hash === '#/') {
    history.replaceState(null, '', DEFAULT_HASH);
  }
  await renderRoute();
}

function start() {
  buildShell();
  api.onConnectionChange((ok) => {
    els.banner.hidden = ok;
  });
  api.startPing();
  window.addEventListener('hashchange', onHashChange);
  window.addEventListener('beforeunload', (e) => {
    if (guardMessage()) {
      e.preventDefault();
      e.returnValue = '';
    }
  });
  // страховка: ошибка, которую никто не поймал, не должна пройти молча
  window.addEventListener('unhandledrejection', (e) => {
    const err = e.reason;
    console.error(err);
    ui.toast(err && err.message ? err.message : 'Что-то пошло не так. Попробуйте ещё раз.', 'error');
  });
  boot();
}

start();
