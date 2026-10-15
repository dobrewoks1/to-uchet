// Главная (КОНЦЕПЦИЯ_v2, раздел 4): одна таблица на весь парк — строка = машина.
// Кремовые поля — то, что вписывает механик (новое показание; последнее ТО, если его нет в программе),
// белые ячейки — то, что посчитала программа (/api/fleet). Здесь только показ, отбор, сортировка и пакетное сохранение.
//
// Быстрый ввод (техника прежнего экрана «Показания»): поля каждой строки создаются один раз и хранятся в состоянии
// строки (st). При вводе таблица НЕ перерисовывается — меняется только пометка под полем и панель сохранения внизу.
// При отборе, сортировке и обновлении данных те же самые поля переставляются в новые ячейки, введённое не теряется.
// Enter и стрелки ↓↑ по строкам — ui.table({inputNav: true}); в колонке «Последнее ТО» — свой переход (navTo).

const NO_SITE = '\u0000без участка';
const NBSP = ' ';
const AUTO_REFRESH_MS = 5 * 60 * 1000; // экран бывает открыт сутками — тихо обновляем данные
const STALE_MS = 60 * 1000;

const TO_ORDER = { overdue: 0, soon: 1, ok: 2, no_to: 3, no_reading: 4 };
const STATES = new Set(['line', 'repair', 'to', 'idle', 'conserved']);

// чипы-фильтры над таблицей; key '' — «Все». Одновременно включён один чип (или ни одного)
const CHIPS = [
  { key: '', label: 'Все', test: () => true },
  { key: 'overdue', label: '🔴 Просрочено ТО', color: 'red', test: (m) => m.to_status === 'overdue' },
  { key: 'soon', label: '🟡 Скоро ТО', color: 'amber', test: (m) => m.to_status === 'soon' },
  {
    key: 'nodata', label: '⚪ Нет данных о ТО', color: 'grey',
    title: 'Не внесено последнее ТО или нет показаний',
    test: (m) => m.to_status === 'no_to' || m.to_status === 'no_reading',
  },
  {
    key: 'offline', label: '🔧 Не на линии', color: 'blue',
    title: 'Ремонт, простой, ТО, консервация',
    test: (m) => !!m.state && m.state !== 'line',
  },
];

// отбор и сортировка живут, пока открыта программа: вернулся из карточки машины — список в том же виде
const memory = { q: '', site: '', chip: '', sort: null };

const hasValue = (v) => v !== undefined && v !== null && v !== '';

/** Порядок по умолчанию «сначала просроченные»: просрочено (сильнее — выше), скоро, в норме (меньше осталось — выше),
 *  без данных. Км и моточасы сравниваются долей интервала. */
function overdueFirst(a, b) {
  const d = (TO_ORDER[a.to_status] ?? 9) - (TO_ORDER[b.to_status] ?? 9);
  if (d) return d;
  const share = (r, v) => (hasValue(v) && r.interval ? v / r.interval : null);
  if (a.to_status === 'overdue') return (share(b, b.overdue_by) || 0) - (share(a, a.overdue_by) || 0);
  const ra = share(a, a.remaining);
  const rb = share(b, b.remaining);
  if (ra === null || rb === null) return 0;
  return ra - rb;
}

/** Ссылки вида #/fleet?to_status=overdue или ?state=repair (из других экранов) → чип-фильтр. */
function chipFromParams(params) {
  const ts = params.to_status;
  if (ts === 'overdue' || ts === 'soon') return ts;
  if (ts === 'no_to' || ts === 'no_reading' || ts === 'nodata') return 'nodata';
  if (params.state && params.state !== 'line' && STATES.has(params.state)) return 'offline';
  return null;
}

// Ширина при 1366×768 (боковая панель 216 px): 1366 − 216 − 2×24 (поля) − 2 (рамка) − 17 (полоса прокрутки таблицы)
// ≈ 1083 px на таблицу. Колонки: Машина ≤ 172 · Показание ≈ 107 · Новое показание 116 · До ТО ≤ 172 ·
// Последнее ТО 196 · Что делали 152…232 (гибкая) · действия ≈ 156 — итого ≈ 1060. Модель/участок и текст записи
// обрезаются с «…», полный текст — во всплывающей подсказке.
const CSS = `
.fl-table .table th { padding: 0 6px; }
.fl-table .table td { padding: 4px 6px; vertical-align: top; }
.fl-table .table td.cell-input { padding: 3px 6px; }
.fl-table .table td.cell-actions { padding: 4px 6px; }
.fl-table .table td.fl-noclick { cursor: default; }
.fl-table .chip { padding: 0 7px; }
.fl-machine { max-width: 172px; }
.fl-garage { white-space: nowrap; }
.fl-sub { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fl-state { margin-top: 2px; }
.fl-to-cell { max-width: 172px; }
.fl-to-cell .small { white-space: normal; }
.fl-to-cell .chip + .small { margin-top: 2px; }
.fl-table .table .fl-new .input { width: 104px; min-width: 0; }
.fl-to-row { display: flex; gap: 4px; }
.fl-table .table .fl-to-kind { flex: none; width: 64px; min-width: 0; }
.fl-table .table .fl-to-num { flex: none; width: 116px; min-width: 0; }
.fl-to-num::placeholder { font-size: 12px; }
.fl-note { display: flex; align-items: flex-start; gap: 4px; margin-top: 2px; font-size: 12px; line-height: 1.25; white-space: normal; }
.fl-new .fl-note { max-width: 104px; }
.fl-to .fl-note { max-width: 184px; }
.fl-note .icon { flex: none; margin-top: 1px; }
.fl-note-info { color: var(--text-muted); }
.fl-note-warn { color: var(--amber); font-weight: 600; }
.fl-note-error { color: var(--red); font-weight: 600; }
.fl-did { min-width: 140px; max-width: 220px; white-space: normal; }
.fl-did-top { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fl-did-text { display: -webkit-box; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow-wrap: anywhere; }
.fl-actions { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; }
.fl-actions .row-actions { gap: 2px; }
/* липкие колонки: «Машина» слева и действия справа видны и в узком окне, когда таблица прокручивается вбок */
.fl-table .table th.fl-stick-l, .fl-table .table td.fl-stick-l { position: sticky; left: 0; z-index: 1; }
.fl-table .table td.fl-stick-l { box-shadow: inset -1px 0 0 var(--border); }
.fl-table .table th.fl-act, .fl-table .table td.fl-act { position: sticky; right: 0; z-index: 1; }
.fl-table .table th.fl-act { border-left: 1px solid var(--primary-dark); }
.fl-table .table td.fl-act { border-left: 1px solid var(--border); }
/* липкая ячейка непрозрачна и красится вместе со своей строкой: полоса, красная/жёлтая строка, наведение */
.fl-table .table td.fl-stick { background: var(--surface); }
.fl-table .table-zebra tbody tr:nth-child(even) td.fl-stick { background: var(--zebra); }
.fl-table .table tbody tr.row-red td.fl-stick { background: var(--red-bg); }
.fl-table .table tbody tr.row-amber td.fl-stick { background: var(--amber-bg); }
.fl-table .table tbody tr.clickable:hover td.fl-stick { background: var(--grey-bg); }
.fl-warn ul { max-height: 150px; overflow: auto; }
@media print {
  .fl-table .table th.fl-stick-l, .fl-table .table td.fl-stick-l,
  .fl-table .table th.fl-act, .fl-table .table td.fl-act { position: static; box-shadow: none; }
  .fl-table .table td { vertical-align: top; }
  .fl-note-warn, .fl-note-error { font-weight: 400; }
}
`;

export async function render(root, params, ctx) {
  const { ui, api } = ctx;
  const { h } = ui;

  if (ctx.boot && ctx.boot.first_run) {
    ctx.navigate('#/welcome');
    return undefined;
  }
  ui.addStyle('page-fleet', CSS);

  // первая загрузка: ошибку покажет оболочка (текст с сервера и «Повторить»)
  let data = await api.get('/fleet');
  root.classList.add('page-fill');

  const fromParams = chipFromParams(params);
  if (fromParams !== null) memory.chip = fromParams;

  // ───────────── состояние ─────────────
  // Все переменные объявлены до первого вызова функций ниже: иначе обращение к let до объявления уронит экран.

  const lists = (ctx.boot && ctx.boot.lists) || {};
  const settings = (ctx.boot && ctx.boot.settings) || {};
  let toKinds = (lists.kinds || []).filter((k) => String(k).startsWith('ТО-'));
  if (!toKinds.length) toKinds = settings.to_cycle && settings.to_cycle.length ? settings.to_cycle : ['ТО-1', 'ТО-2', 'ТО-3'];

  let rows = []; // состояния строк в порядке по умолчанию («сначала просроченные»)
  const byId = new Map();
  let shown = []; // строки, прошедшие отбор
  let loadedAt = Date.now();
  let seq = 0; // номер загрузки: ответ устаревшего запроса не перерисует экран
  let saving = false;
  let barShown = false;
  let openedOn = ui.today(); // «сегодня» на момент открытия экрана (или последней смены суток)

  function newRow(m) {
    // inp — «Новое показание»; kind, toReading — «Последнее ТО» (только у машин без него);
    // errItem/errTo — ошибки сервера по частям строки, warn — его предупреждение (409);
    // live — пометку можно показывать (пока человек набирает число, пометка молчит: «48» из «48 600» — ещё не ошибка)
    return {
      id: m.id, m, rank: 0,
      inp: null, newBox: null, noteNew: h('div.fl-note', { hidden: true }),
      kind: null, toReading: null, toBox: null, noteTo: h('div.fl-note', { hidden: true }),
      errItem: '', errTo: '', warn: '', live: true,
    };
  }

  // км — целые, моточасы — до десятых
  const digits = (m) => (m.unit === 'км' ? 0 : 1);
  const fmtVal = (v, m, est) => (hasValue(v) ? (est ? `≈${NBSP}` : '') + ui.fmtNum(v, digits(m)) : '—');
  const amount = (v, m) => `${ui.fmtNum(v, digits(m))}${m.unit ? NBSP + m.unit : ''}`;
  const shortDate = (iso) => (String(iso || '').slice(0, 4) === ui.today().slice(0, 4) ? ui.fmtDateShort(iso) : ui.fmtDate(iso));

  const filled = (el) => !!el && el.value.trim() !== '';
  const hasNew = (st) => filled(st.inp);
  const hasTo = (st) => !st.m.last_to && filled(st.toReading); // у машины с последним ТО этих полей на экране нет
  const isDirty = (st) => hasNew(st) || hasTo(st);
  const busy = () => !!(document.querySelector('.modal-overlay') || document.querySelector('.popover'));

  /** Свежий ответ /api/fleet → состояния строк (поля и введённое сохраняются), порядок по умолчанию. */
  function merge(list) {
    const next = [];
    for (const m of list || []) {
      let st = byId.get(m.id);
      if (st) st.m = m;
      else {
        st = newRow(m);
        byId.set(m.id, st);
      }
      // последнее ТО тем временем внесли (диалогом «ТО сделано») — его поля больше не нужны
      if (m.last_to && filled(st.toReading)) clearTo(st);
      next.push(st);
    }
    const ids = new Set(next.map((st) => st.id));
    for (const id of [...byId.keys()]) {
      if (!ids.has(id)) byId.delete(id); // машину списали в другом окне
    }
    next.sort((a, b) => overdueFirst(a.m, b.m)); // сортировка устойчивая: равные — в порядке парка
    next.forEach((st, i) => {
      st.rank = i;
    });
    rows = next;
  }

  // ───────────── поля строки (создаются один раз) ─────────────

  function newBox(st) {
    if (!st.newBox) {
      const m = st.m;
      st.inp = ui.numInput({
        decimals: digits(m),
        thousands: m.unit === 'км',
        ariaLabel: `Новое показание, машина ${m.garage_no}`,
        onInput: () => typing(st, 'item'),
      });
      st.inp.addEventListener('blur', () => settle(st));
      st.newBox = h('div.fl-new', null, st.inp, st.noteNew);
    }
    return st.newBox;
  }

  function toBox(st) {
    if (!st.toBox) {
      const m = st.m;
      st.kind = ui.select({
        options: toKinds,
        value: m.next_to_kind || toKinds[0],
        cls: 'fl-to-kind',
        ariaLabel: `Вид последнего ТО, машина ${m.garage_no}`,
        title: 'Какое ТО сделали последним',
        onChange: () => changed(st),
      });
      st.toReading = ui.numInput({
        decimals: digits(m),
        thousands: m.unit === 'км',
        placeholder: 'показание ТО', // короткая подсказка: «на каком показании» не влезает в поле при 1366 px
        cls: 'fl-to-num',
        ariaLabel: `На каком показании сделано последнее ТО, машина ${m.garage_no}`,
        title: 'Показание счётчика, на котором сделали последнее ТО. Дата запишется как «не указана» — поправить можно в карточке машины',
        onInput: () => typing(st, 'baseline'),
      });
      st.toReading.addEventListener('blur', () => settle(st));
      st.toReading.addEventListener('keydown', navTo);
      st.toBox = h('div.fl-to', null, h('div.fl-to-row', null, st.kind, st.toReading), st.noteTo);
    }
    return st.toBox;
  }

  /** Enter/↓/↑ в поле «на каком показании» — к такому же полю ближайшей строки, где оно есть.
   *  (Кит перешёл бы к первому полю ячейки — списку «вид ТО».) preventDefault — кит такие нажатия пропускает. */
  function navTo(e) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    let step = 0;
    if (e.key === 'Enter') step = e.shiftKey ? -1 : 1;
    else if (e.key === 'ArrowDown') step = 1;
    else if (e.key === 'ArrowUp') step = -1;
    if (!step) return;
    e.preventDefault();
    let tr = e.currentTarget.closest('tr');
    while (tr) {
      tr = step > 0 ? tr.nextElementSibling : tr.previousElementSibling;
      const next = tr ? tr.querySelector('input.fl-to-num:not([disabled])') : null;
      if (next) {
        next.focus();
        next.select();
        return;
      }
    }
  }

  /** Идёт набор: прежние замечания сервера к этой части строки больше не относятся, пометка молчит до выхода из поля. */
  function typing(st, part) {
    if (part === 'item') st.errItem = '';
    else st.errTo = '';
    st.warn = '';
    st.live = false;
    paint(st);
    updateBar();
  }

  /** Человек вышел из поля (Enter, стрелка, Tab, мышь): показываем пометку по итоговому значению. */
  function settle(st) {
    st.live = true;
    paint(st);
    updateBar();
  }

  /** Выбор из списка — ввод закончен сразу. */
  function changed(st) {
    typing(st, 'baseline');
    settle(st);
  }

  // ───────────── пометки под полями ─────────────

  /** Ошибка ввода в поле (не число, меньше нуля) — такие строки не отправляются. */
  function numError(el) {
    const v = el ? el.getValue() : null;
    if (v === null) return '';
    if (typeof v !== 'number') return 'Не число';
    if (v < 0) return 'Меньше нуля';
    return '';
  }

  function inputError(st) {
    return numError(st.inp) || (st.m.last_to ? '' : numError(st.toReading));
  }

  /** Предупреждение сервера (409) относится ко всей строке: показываем его под новым показанием,
   *  а если вписано только последнее ТО — под ним. */
  const warnUnderTo = (st) => !hasNew(st) && hasTo(st);

  /** Пометка под «Новым показанием»: [вид, текст]; вид — error | warn | info | ''. */
  function noteNew(st) {
    if (st.errItem) return ['error', st.errItem];
    if (st.warn && !warnUnderTo(st)) return ['warn', st.warn];
    if (!st.live) return ['', ''];
    const bad = numError(st.inp);
    if (bad) return ['error', bad];
    const m = st.m;
    const v = st.inp ? st.inp.getValue() : null;
    if (v === null) return ['', ''];
    if (hasValue(m.reading_base) && v < m.reading_base) return ['warn', `Меньше последнего факта ${fmtVal(m.reading_base, m)}`];
    if (m.reading_estimated && hasValue(m.reading) && v < m.reading) return ['info', `Расчёт был ${fmtVal(m.reading, m, true)}`];
    return ['', ''];
  }

  /** Пометка под полями «Последнее ТО». */
  function noteTo(st) {
    if (st.m.last_to) return ['', ''];
    if (st.errTo) return ['error', st.errTo];
    if (st.warn && warnUnderTo(st)) return ['warn', st.warn];
    if (!st.live) return ['', ''];
    const bad = numError(st.toReading);
    if (bad) return ['error', bad];
    const m = st.m;
    const tv = st.toReading ? st.toReading.getValue() : null;
    if (tv === null) return ['', ''];
    const v = st.inp ? st.inp.getValue() : null;
    const now = typeof v === 'number' ? v : m.reading;
    if (hasValue(now) && tv > now) return ['warn', 'Больше текущего показания'];
    if (hasValue(now) && m.interval > 0 && tv < now - m.interval) {
      return ['warn', 'Меньше текущего больше чем на интервал ТО — проверьте разрядность'];
    }
    return ['', ''];
  }

  function paintNote(el, [kind, text]) {
    el.className = kind ? `fl-note fl-note-${kind}` : 'fl-note';
    ui.mount(el, kind === 'error' || kind === 'warn' ? ui.icon('alert-triangle', 13) : null, text ? h('span', null, text) : null);
    el.hidden = !text;
  }

  function paint(st) {
    paintNote(st.noteNew, noteNew(st));
    paintNote(st.noteTo, noteTo(st));
  }

  // ───────────── верх экрана: кнопки, поиск, участок ─────────────

  const search = ui.textInput({
    value: memory.q,
    placeholder: 'Гар. №, модель, госномер',
    ariaLabel: 'Поиск машины в таблице',
    width: 230,
    onInput: ui.debounce((v) => {
      memory.q = v;
      applyFilters();
    }, 150),
  });
  const siteSel = ui.select({
    placeholder: 'Все участки',
    options: [],
    ariaLabel: 'Участок',
    onChange: (v) => {
      memory.site = v;
      applyFilters();
    },
  });
  const head = h('div.toolbar', null,
    ui.button('Накинуть за день', {
      icon: 'calendar-plus',
      title: 'Прибавить суточную наработку машинам, которые были на линии',
      onClick: () => act(ctx.dialogs.openDailyDialog(ctx)),
    }),
    h('a.btn.btn-primary', { href: '#/plan', title: 'Машины, которым пора на ТО, и что для них заказать' },
      ui.icon('calendar-check'), h('span', null, 'Список на ТО')),
    ui.button('Печать', {
      icon: 'printer',
      title: 'Распечатать таблицу как есть: пустые кремовые поля — бланк для обхода',
      onClick: () => ui.printPage(`Машины и ТО на ${ui.fmtDate(ui.today())}`, { landscape: true }),
    }),
    h('div.spacer'),
    search,
    siteSel);

  // ───────────── чипы-фильтры ─────────────

  const chips = CHIPS.map((c) => {
    const count = h('span.filter-chip-count');
    const el = h('button.filter-chip', {
      type: 'button',
      class: c.color ? `filter-chip-${c.color}` : 'filter-chip-all',
      title: c.title || null,
      'aria-pressed': 'false',
      onclick: () => onChip(c.key),
    }, h('span', null, c.label), h('span.filter-chip-sep', { 'aria-hidden': 'true' }, '·'), count);
    return { ...c, el, count };
  });
  const countEl = h('span.muted.small.nowrap', { role: 'status' });
  const resetBtn = ui.button('Сбросить', {
    kind: 'ghost', small: true, icon: 'x',
    title: 'Показать все машины в порядке «сначала просроченные»',
    onClick: () => resetFilters(),
  });
  const chipsRow = h('div.filter-chips', { role: 'group', 'aria-label': 'Отбор машин' },
    chips.map((c) => c.el), h('div.spacer'), countEl, resetBtn);

  const warnBox = h('div.fl-warn.no-print');
  const emptyBox = h('div');

  // ───────────── таблица ─────────────

  function columns() {
    return [
      {
        key: 'garage_no', title: 'Машина', cls: 'fl-stick fl-stick-l', headCls: 'fl-stick-l',
        hint: 'Гаражный №, модель и участок; если машина не на линии — что с ней и сколько',
        sortValue: (st) => st.m.garage_no,
        render: (st) => machineCell(st.m),
      },
      {
        key: 'reading', title: 'Показание', align: 'right',
        hint: 'Текущее показание счётчика и дата последнего снятого. «≈» — расчётное: накинуто после последнего показания',
        sortValue: (st) => st.m.reading,
        render: (st) => readingCell(st.m),
      },
      {
        key: 'new', title: 'Новое показание', input: true, sortable: false, cls: 'fl-noclick',
        hint: 'Впишите показание со счётчика. Enter — следующая строка. Сохранить — кнопкой внизу или Ctrl+S',
        render: (st) => newBox(st),
      },
      {
        key: 'to', title: 'До ТО',
        hint: 'Сколько осталось до следующего ТО или насколько оно просрочено. Порядок по умолчанию — сначала просроченные',
        sortValue: (st) => st.rank,
        render: (st) => toCell(st.m),
      },
      {
        // ТО без даты («дата не указана») при сортировке — среди самых давних
        key: 'last_to', title: 'Последнее ТО',
        hint: 'Последнее ТО в программе. Если его нет — впишите вид ТО и на каком показании его сделали',
        sortValue: (st) => (st.m.last_to ? (st.m.last_to.date_unknown ? '0000-00-00' : st.m.last_to.date) : null),
        cls: (st) => (st.m.last_to ? null : 'cell-input fl-noclick'),
        render: (st) => (st.m.last_to ? lastToCell(st.m) : toBox(st)),
      },
      {
        key: 'note', title: 'Что делали',
        hint: 'Последняя запись журнала: ремонт, простой, ТО или заметка',
        sortValue: (st) => {
          const n = st.m.last_note;
          return n ? (n.date_unknown ? '0000-00-00' : n.date) : null;
        },
        render: (st) => didCell(st.m),
      },
      {
        key: 'act', title: '', sortable: false,
        cls: 'cell-actions fl-act fl-stick fl-noclick no-print', headCls: 'fl-act no-print',
        render: (st) => actionsCell(st.m),
      },
    ];
  }

  function machineCell(m) {
    const sub = [m.model, m.site].filter(Boolean).join(' · ');
    const full = [m.model, m.mtype, m.plate].filter(Boolean).join(' · ');
    return h('div.fl-machine', { title: full || null },
      h('div.fl-garage.strong', null, m.garage_no),
      sub ? h('div.fl-sub.small.muted', null, sub) : null,
      m.state && m.state !== 'line' ? h('div.fl-state', null, stateChip(m)) : null);
  }

  /** Чип состояния; у ремонта и простоя — сколько уже длится: «🔴 В ремонте 3 д 4 ч» (дольше суток — без минут). */
  function stateChip(m) {
    let label = m.state_label;
    let since = null;
    if ((m.state === 'repair' || m.state === 'idle') && hasValue(m.state_seconds)) {
      const sec = m.state_seconds >= 86400 ? Math.floor(m.state_seconds / 3600) * 3600 : m.state_seconds;
      label = `${m.state === 'repair' ? '🔴 В ремонте' : '🟡 Простой'} ${ui.fmtDuration(sec)}`;
      since = m.state_since ? `с ${ui.fmtDateTime(m.state_since)}` : null;
    }
    const chip = ui.statusChip(m.state, label);
    if (since) chip.title = since;
    return chip;
  }

  function readingTitle(m) {
    const parts = [];
    if (hasValue(m.reading_base)) {
      parts.push(`Последний факт: ${ui.fmtReading(m.reading_base, m.unit)}`
        + (m.reading_base_date ? ` от ${ui.fmtDate(m.reading_base_date)}` : ' (при регистрации)'));
    }
    if (m.reading_estimated) parts.push(`накинуто: +${ui.fmtReading(m.reading_auto_add, m.unit)}`);
    return parts.join('; ') || null;
  }

  function readingCell(m) {
    if (!hasValue(m.reading)) {
      return h('span.muted', { title: 'Показаний ещё нет — впишите в колонке «Новое показание»' }, '—');
    }
    let when = '';
    if (hasValue(m.reading_base)) when = m.reading_base_date ? shortDate(m.reading_base_date) : 'при регистрации';
    return h('div', { title: readingTitle(m) },
      h('div.nowrap', null, ui.fmtReading(m.reading, m.unit, m.reading_estimated)),
      when ? h('div.small.muted.nowrap', null, when) : null);
  }

  function forecastTitle(m) {
    const parts = [];
    if (m.forecast_date) {
      parts.push(`ТО подойдёт ≈ ${ui.fmtDate(m.forecast_date)}${hasValue(m.forecast_days) ? `, через ${m.forecast_days} дн.` : ''}`);
    }
    if (hasValue(m.rate)) {
      parts.push(`средняя наработка ${ui.fmtReading(m.rate, m.unit)} в сутки (${m.rate_source === 'fact'
        ? `по факту за ${m.rate_days} дн.` : 'по норме'})`);
    }
    return parts.join('; ') || null;
  }

  function toCell(m) {
    const at = hasValue(m.next_to_at) ? ui.fmtNum(m.next_to_at, digits(m)) : '';
    if (m.to_status === 'overdue') {
      const by = hasValue(m.overdue_by) ? m.overdue_by : (hasValue(m.remaining) ? -m.remaining : null);
      const sub = [
        at ? `${m.next_to_kind} был на ${at}` : (m.next_to_kind || ''),
        m.missed > 1 ? `пропущено ТО: ${m.missed}` : '',
      ].filter(Boolean).join(' · ');
      return h('div.fl-to-cell', null,
        h('div.strong.text-red.nowrap', null, by > 0 ? `Просрочено на ${amount(by, m)}` : 'Подошло ТО'),
        sub ? h('div.small', null, sub) : null);
    }
    if (m.to_status === 'soon' || m.to_status === 'ok') {
      const sub = [
        at ? `${m.next_to_kind} на ${at}` : '',
        m.forecast_date ? `≈${NBSP}${shortDate(m.forecast_date)}` : '',
      ].filter(Boolean).join(' · ');
      return h('div.fl-to-cell', { title: forecastTitle(m) },
        h('div.strong.nowrap', { class: m.to_status === 'soon' ? 'text-amber' : null },
          hasValue(m.remaining) ? `Осталось ${amount(m.remaining, m)}` : (m.to_status_label || '')),
        sub ? h('div.small', null, sub) : null);
    }
    if (m.to_status === 'no_to') {
      return h('div.fl-to-cell', null,
        ui.statusChip('amber', 'Внесите последнее ТО'),
        h('div.small.muted', null, 'впишите справа →'));
    }
    if (m.to_status === 'no_reading') {
      return h('div.fl-to-cell', null,
        ui.statusChip('grey', 'Нет показаний'),
        h('div.small.muted', null, 'впишите показание ←'));
    }
    return h('span.muted', null, m.to_status_label || '—');
  }

  function lastToCell(m) {
    const t = m.last_to;
    return h('div', null,
      h('div.nowrap', null, `${t.kind} · ${hasValue(t.reading) ? ui.fmtNum(t.reading, digits(m)) : '—'}`),
      h('div.small.muted.nowrap', null, ui.fmtEventDate(t)));
  }

  /** «Что делали» — последняя запись журнала (last_note, КОНЦЕПЦИЯ_v2 п. 9.1). Нет поля или записи — «—». */
  function didCell(m) {
    const n = m.last_note;
    if (!n) return h('span.muted', null, '—');
    const when = n.date_unknown ? 'дата не указана' : shortDate(n.date);
    const kind = n.kind && !String(n.kind).startsWith('Заметка') ? (n.kind_title || n.kind) : '';
    const top = [when, kind].filter(Boolean).join(' · ');
    const text = String(n.text || '').trim();
    return h('div.fl-did', { title: [top, text].filter(Boolean).join('\n') || null },
      top ? h('div.fl-did-top.small.muted', null, top) : null,
      text ? h('div.fl-did-text', null, text) : null);
  }

  function actionsCell(m) {
    const closable = (m.state === 'repair' || m.state === 'idle') && hasValue(m.state_event_id);
    return h('div.fl-actions', null,
      h('div.row-actions', null,
        ui.button('ТО сделано', {
          small: true, kind: 'accent',
          title: 'Отметить, что ТО сделано: вид ТО, показание на момент ТО и сейчас',
          onClick: () => act(ctx.dialogs.openToDoneDialog(ctx, m.id)),
        }),
        ui.iconButton('plus', 'Запись: ремонт, простой, заметка',
          () => act(ctx.dialogs.openEventDialog(ctx, { machineId: m.id })), { small: true }),
        ui.iconButton('dots', 'Ещё', (e) => ui.menu(e.currentTarget, [
          { label: 'Ввести показание', icon: 'gauge', onClick: () => act(ctx.dialogs.openReadingDialog(ctx, m.id)) },
          '-',
          { label: 'Карточка машины', icon: 'truck', onClick: () => ctx.navigate(`#/machine/${m.id}`) },
          { label: 'Заказ-наряд', icon: 'clipboard-text', onClick: () => ctx.navigate(`#/workorder?machine_id=${m.id}&date=${ui.today()}`) },
        ]), { small: true })),
      closable ? h('div.row-actions', null,
        ui.button(m.state === 'repair' ? 'Закрыть ремонт' : 'Закрыть простой', {
          small: true, icon: 'check',
          onClick: () => act(ctx.dialogs.openCloseRepairDialog(ctx, m.state_event_id)),
        })) : null);
  }

  const tbl = ui.table({
    fill: true,
    inputNav: true,
    cls: 'fl-table print-blank',
    columns: columns(),
    rows: [],
    sort: memory.sort,
    onSort: (sort) => {
      memory.sort = sort;
      resetBtn.hidden = !anyFilter() && !memory.sort;
    },
    // клик по ячейке с полями или кнопками (в т. ч. мимо самого поля) карточку не открывает
    onRowClick: (st, e) => {
      if (e && e.target instanceof Element && e.target.closest('.fl-noclick')) return;
      ctx.navigate(`#/machine/${st.id}`);
    },
    rowClass: (st) => (st.m.to_status === 'overdue' ? 'row-red' : st.m.to_status === 'soon' ? 'row-amber' : ''),
    empty: ui.emptyState('Ни одна машина не подходит под отбор.', {
      icon: 'filter', title: 'Ничего не найдено', action: { label: 'Сбросить', icon: 'x', onClick: () => resetFilters() },
    }),
  });

  // ───────────── панель сохранения (внизу, пока есть вписанное) ─────────────

  const barCount = h('span.save-bar-count', { role: 'status' });
  const dateInp = ui.dateInput({
    value: ui.today(), max: ui.today(),
    ariaLabel: 'Дата показаний', title: 'На какую дату записать вписанные показания',
  });
  const clearBtn = ui.button('Очистить', {
    kind: 'ghost', icon: 'x', title: 'Стереть всё вписанное в таблицу, ничего не сохраняя', onClick: clearAll,
  });
  const saveBtn = ui.button('Сохранить всё', {
    kind: 'accent', big: true, icon: 'check',
    title: 'Сохранить все вписанные показания и последние ТО (Ctrl+S)', onClick: save,
  });
  const bar = h('div.save-bar.no-print', { hidden: true },
    barCount, h('span.toolbar-label', null, 'Дата показаний'), dateInp, h('div.spacer'), clearBtn, saveBtn);

  function updateBar() {
    let nNew = 0;
    let nTo = 0;
    let nBad = 0;
    for (const st of rows) {
      const a = hasNew(st);
      const b = hasTo(st);
      if (a) nNew += 1;
      if (b) nTo += 1;
      if ((a || b) && (st.errItem || st.errTo || (st.live && inputError(st)))) nBad += 1;
    }
    ui.mount(barCount,
      `Показаний: ${nNew}`,
      nTo ? ` · последних ТО: ${nTo}` : null,
      nBad ? h('span.text-red', null, ` · с ошибкой: ${nBad}`) : null);
    const show = nNew + nTo > 0;
    if (show === barShown) return;
    barShown = show;
    bar.hidden = !show;
    // панель отнимает высоту у таблицы — поле, в котором стоит человек, не должно уехать под неё
    const a = document.activeElement;
    if (show && a && tbl.contains(a)) requestAnimationFrame(() => a.scrollIntoView({ block: 'nearest' }));
  }

  root.append(head, chipsRow, warnBox, tbl, emptyBox, bar);
  merge(data.rows);
  draw();

  // ───────────── отбор ─────────────

  function anyFilter() {
    return !!(memory.q || memory.site || memory.chip);
  }

  function onChip(key) {
    memory.chip = key && memory.chip === key ? '' : key; // повторный клик снимает чип
    applyFilters();
  }

  function resetFilters() {
    memory.q = '';
    memory.site = '';
    memory.chip = '';
    memory.sort = null;
    search.setValue('');
    siteSel.setValue('');
    tbl.setSort(null); // исходный порядок таблицы — это и есть «сначала просроченные»
    applyFilters();
  }

  /** Поиск и участок (чипы считают машины внутри этого отбора). */
  function matchesBase(m, words) {
    if (memory.site && (m.site || '') !== (memory.site === NO_SITE ? '' : memory.site)) return false;
    if (words.length) {
      const hay = `${m.garage_no} ${m.model} ${m.mtype} ${m.plate} ${m.vin} ${m.site}`.toLowerCase();
      for (const w of words) {
        if (!hay.includes(w)) return false;
      }
    }
    return true;
  }

  function applyFilters() {
    const words = memory.q.toLowerCase().split(/\s+/).filter(Boolean);
    const base = rows.filter((st) => matchesBase(st.m, words));
    const chip = chips.find((c) => c.key === memory.chip) || chips[0];
    if (chip.key !== memory.chip) memory.chip = '';
    shown = base.filter((st) => chip.test(st.m));
    tbl.setRows(shown);
    for (const c of chips) {
      const n = c.key ? base.filter((st) => c.test(st.m)).length : base.length;
      const on = c.key === memory.chip;
      c.count.textContent = String(n);
      c.el.setAttribute('aria-pressed', on ? 'true' : 'false');
      c.el.classList.toggle('filter-chip-zero', !!c.key && !n && !on); // ноль — без цвета: тревожить нечем
    }
    const filtered = anyFilter();
    countEl.hidden = !filtered;
    countEl.textContent = `${shown.length} из ${rows.length}`;
    resetBtn.hidden = !filtered && !memory.sort;
  }

  function siteOptions() {
    const present = new Set(rows.map((st) => st.m.site || ''));
    const known = (lists.sites || []).filter((s) => present.has(s));
    const extra = [...present].filter((s) => s && !known.includes(s)).sort((a, b) => a.localeCompare(b, 'ru'));
    const list = [...known, ...extra].map((s) => [s, s]);
    if (present.has('') && list.length) list.push([NO_SITE, 'Без участка']);
    return list;
  }

  function drawWarnings() {
    const list = (data && data.warnings) || [];
    if (!list.length) {
      ui.clear(warnBox);
      return;
    }
    const old = warnBox.querySelector('details');
    const n = list.length;
    ui.mount(warnBox, ui.notice(
      h('details.details', { open: !!(old && old.open) },
        h('summary', null, `Проверьте записи: ${n} ${ui.plural(n, 'замечание', 'замечания', 'замечаний')}`),
        h('ul', null, list.map((w) => h('li', null,
          h('a', { href: `#/machine/${w.machine_id}` }, w.garage_no), ': ', w.text,
          hasValue(w.event_id) ? [' ', h('button.link', {
            type: 'button',
            onclick: () => act(ctx.dialogs.openEventDialog(ctx, { event: w.event_id })),
          }, 'Исправить запись')] : null)))),
      'warn'));
  }

  /** Перерисовать экран по rows/data. */
  function draw() {
    const has = rows.length > 0;
    head.hidden = !has;
    chipsRow.hidden = !has;
    warnBox.hidden = !has;
    tbl.hidden = !has;
    if (!has) {
      bar.hidden = true;
      barShown = false;
      ui.mount(emptyBox, ui.emptyState(
        'Здесь будет весь парк: что с каждой машиной и сколько осталось до ТО. Добавьте первую машину — или верните в работу списанные на экране «Машины».',
        {
          icon: 'truck',
          title: 'В парке пока нет машин',
          action: {
            label: 'Добавить машину',
            icon: 'plus',
            onClick: async () => {
              if (await ctx.dialogs.openMachineDialog(ctx)) await ctx.refresh();
            },
          },
        }));
      return;
    }
    ui.clear(emptyBox);
    const opts = siteOptions();
    if (memory.site && !opts.some(([v]) => v === memory.site)) memory.site = ''; // участок пропал из парка
    siteSel.value = ''; // иначе setOptions вернёт пропавший участок отдельным пунктом
    siteSel.setOptions(opts);
    siteSel.setValue(memory.site);
    drawWarnings();
    applyFilters();
    rows.forEach(paint);
    updateBar();
  }

  // ───────────── данные ─────────────

  /** Перечитать парк (введённое в таблицу сохраняется). false — ответ устарел (пришёл более новый запрос)
   *  или skipIf() сказал «не применять» (тихое обновление, а человек тем временем занялся таблицей). */
  async function fetchFleet(skipIf = null) {
    seq += 1;
    const my = seq;
    const d = await api.get('/fleet');
    if (my !== seq) return false;
    if (skipIf && skipIf()) return false;
    data = d;
    loadedAt = Date.now();
    merge(d.rows);
    return true;
  }

  async function reload({ quiet = false } = {}) {
    try {
      // Тихое обновление проверяет «человек не занят» до запроса, а ответ приходит позже: за это время механик
      // мог щёлкнуть в поле (часто — тем же щелчком, которым вернулся в окно) и начать набирать. Перерисовка
      // переставляет поля в новые строки и выбивает фокус — цифры уходили в никуда. Поэтому проверяем ещё раз
      // по ответу и в таком случае его не применяем: обновимся при следующем возврате в окно или по таймеру.
      if (await fetchFleet(quiet ? userBusy : null)) draw();
    } catch (err) {
      // таблицу с вписанным не трогаем: остаётся то, что на экране
      if (!quiet) ui.toast(`Таблица не обновилась: ${err && err.message ? err.message : err}`, 'error');
    }
  }

  /** Дождаться диалога; если что-то сохранено — перечитать парк. */
  async function act(promise) {
    if (await promise) await reload();
  }

  /** Наступили новые сутки: дата показаний (если её не меняли) становится сегодняшней, календарь пускает на сегодня. */
  function followToday() {
    const today = ui.today();
    if (today === openedOn) return;
    if (!dateInp.getValue() || dateInp.getValue() === openedOn) dateInp.setValue(today);
    dateInp.max = today;
    openedOn = today;
  }

  /** Человек занят таблицей: идёт сохранение, открыто окно или меню, что-то вписано или он стоит в поле таблицы. */
  function userBusy() {
    if (saving || busy() || rows.some(isDirty)) return true;
    const a = document.activeElement;
    return !!(a && tbl.contains(a) && a.matches('input, select')); // перерисовка выбила бы его из поля
  }

  /** Тихое обновление — только когда человек ничего не вписал, не стоит в поле таблицы и не открыл окно или меню. */
  function refreshQuietly(onlyIfStale) {
    followToday();
    if (userBusy()) return;
    const dayChanged = !!(data && data.date && data.date !== ui.today());
    if (!dayChanged && onlyIfStale && Date.now() - loadedAt < STALE_MS) return;
    reload({ quiet: true });
  }

  // ───────────── сохранение ─────────────

  function clearTo(st) {
    if (st.toReading) st.toReading.setValue(null);
    if (st.kind) st.kind.setValue(st.m.next_to_kind || toKinds[0]);
  }

  function clearRow(st) {
    if (st.inp) st.inp.setValue(null);
    clearTo(st);
    st.errItem = '';
    st.errTo = '';
    st.warn = '';
    st.live = true;
  }

  async function clearAll() {
    const n = rows.filter(isDirty).length;
    if (!n) return;
    const ok = await ui.confirm(
      `Стереть вписанное в ${n} ${ui.plural(n, 'строке', 'строках', 'строках')}? Ничего не сохранится.`,
      { title: 'Очистить таблицу', danger: true, okLabel: 'Очистить' });
    if (!ok) return;
    for (const st of rows) {
      clearRow(st);
      paint(st);
    }
    updateBar();
  }

  /** Строки предупреждений для вопроса «сохранить как есть?»: одинаковое у многих машин сворачивается в одну строку. */
  function warningLines(err) {
    if (!err.items.length) return err.warnings;
    const byText = new Map();
    for (const it of err.items) {
      if (!byText.has(it.text)) byText.set(it.text, []);
      byText.get(it.text).push(it.garage_no);
    }
    const lines = [];
    for (const [text, nos] of byText) {
      if (nos.length > 5) lines.push(`${text} — у ${nos.length} ${ui.plural(nos.length, 'машины', 'машин', 'машин')}`);
      else for (const no of nos) lines.push(`${no}: ${text}`);
    }
    return lines;
  }

  function markWarnings(items) {
    for (const st of rows) st.warn = '';
    for (const it of items) {
      const st = byId.get(Number(it.machine_id));
      if (st) st.warn = st.warn ? `${st.warn}; ${it.text}` : it.text;
    }
    rows.forEach(paint);
    updateBar();
  }

  /** Поставить человека в первое поле строки, где что-то не так (строку, скрытую отбором, сначала показать). */
  function focusFirst(test) {
    const st = shown.find(test) || rows.find(test);
    if (!st) return;
    if (!shown.includes(st)) {
      memory.q = '';
      memory.site = '';
      memory.chip = '';
      search.setValue('');
      siteSel.setValue('');
      applyFilters();
    }
    const newBad = !!st.errItem || !!numError(st.inp) || (!!st.warn && !warnUnderTo(st));
    const useTo = !newBad && !!st.toReading && st.toReading.isConnected && (hasTo(st) || !!st.errTo);
    const el = useTo ? st.toReading : st.inp;
    if (el && el.isConnected) el.focus();
  }

  async function save() {
    if (saving) return; // повторное нажатие или Ctrl+S, пока идёт сохранение
    for (const st of rows) st.live = true;
    followToday();
    const date = dateInp.getValue();
    if (!date) {
      ui.toast('Укажите дату показаний', 'error');
      dateInp.focus();
      return;
    }
    const bad = rows.filter((st) => isDirty(st) && inputError(st));
    if (bad.length) {
      // пакет не отправляется частично: иначе одна часть строки сохранится, а другая останется с ошибкой
      rows.forEach(paint);
      updateBar();
      ui.toast(`Исправьте ${bad.length === 1 ? 'строку' : `строки (${bad.length})`} с ошибкой ввода — ничего не сохранено`, 'error');
      focusFirst((st) => isDirty(st) && !!inputError(st));
      return;
    }
    const items = [];
    const baseline = [];
    const sent = [];
    for (const st of rows) {
      const withNew = hasNew(st);
      const withTo = hasTo(st);
      if (withNew) items.push({ machine_id: st.id, reading: st.inp.getValue() });
      // дата последнего ТО не спрашивается: сервер запишет дату пакета с пометкой «дата не указана»
      if (withTo) baseline.push({ machine_id: st.id, kind: st.kind.getValue(), reading: st.toReading.getValue() });
      if (withNew || withTo) sent.push(st);
    }
    if (!sent.length) {
      ui.toast('Сохранять пока нечего — впишите новые показания в таблицу', 'info');
      return;
    }

    saving = true;
    saveBtn.disabled = true;
    clearBtn.disabled = true;
    try {
      let res;
      try {
        res = await api.post('/readings/bulk', { date, items, baseline, force: false });
      } catch (err) {
        if (!err || !err.needsConfirm) throw err;
        // сервер сомневается в части показаний: помечаем эти строки и спрашиваем
        markWarnings(err.items);
        const ok = await ui.confirm(err.message, {
          title: 'Проверьте показания',
          details: warningLines(err),
          okLabel: 'Всё равно сохранить',
          cancelLabel: 'Вернуться и исправить',
        });
        if (!ok) {
          focusFirst((st) => !!st.warn);
          return;
        }
        res = await api.post('/readings/bulk', { date, items, baseline, force: true });
      }
      await afterSave(res, sent);
    } catch (err) {
      ui.toast(err && err.message ? err.message : String(err), 'error');
    } finally {
      saving = false;
      saveBtn.disabled = false;
      clearBtn.disabled = false;
    }
  }

  async function afterSave(res, sent) {
    // строки, которые сервер не принял, остаются с введённым и текстом ошибки под полем;
    // part — какая часть строки не сохранена (остальное в строке сохранено и очищается)
    const failed = new Map(); // id → {item: [тексты], baseline: [тексты]}
    const orphan = [];
    for (const e of res.errors || []) {
      const st = byId.get(Number(e.machine_id));
      if (!st) {
        orphan.push(e.error);
        continue;
      }
      if (!failed.has(st.id)) failed.set(st.id, { item: [], baseline: [], no: st.m.garage_no });
      const f = failed.get(st.id);
      let parts = e.part === 'item' || e.part === 'baseline' ? [e.part] : [];
      if (!parts.length) {
        // часть неизвестна — оставляем всё, что было вписано
        parts = [hasNew(st) && 'item', hasTo(st) && 'baseline'].filter(Boolean);
        if (!parts.length) parts = ['item'];
      }
      for (const p of parts) {
        if (!f[p].includes(e.error)) f[p].push(e.error);
      }
    }

    let reloaded = false;
    try {
      reloaded = await fetchFleet();
    } catch (err) {
      ui.toast(`Сохранено, но таблица не обновилась: ${err && err.message ? err.message : err}. Нажмите F5.`, 'warn');
    }
    // Машину удалили в другом окне, пока здесь вписывали: сервер вернул по ней ошибку, а после перечитывания
    // её строки в таблице нет. Без этого тост говорил «причина написана под полем», а поля нигде не было.
    for (const [id, f] of failed) {
      if (byId.has(id)) continue;
      orphan.push(...[...f.item, ...f.baseline].map((text) => `${f.no}: ${text}`));
      failed.delete(id);
    }

    for (const st of sent) {
      const f = failed.get(st.id);
      st.warn = '';
      if (!f) {
        clearRow(st);
        continue;
      }
      st.live = true;
      st.errItem = f.item.join('; ');
      st.errTo = f.baseline.join('; ');
      if (!f.item.length && st.inp) st.inp.setValue(null);
      if (!f.baseline.length) clearTo(st);
      if (f.item.length && filled(st.inp)) st.inp.classList.add('invalid');
      if (f.baseline.length && filled(st.toReading)) st.toReading.classList.add('invalid');
    }
    for (const st of rows) {
      if (!failed.has(st.id)) st.warn = '';
    }
    draw(); // строки перекрашиваются и встают в порядок «сначала просроченные»

    const marks = res.marks_saved || 0;
    const tos = res.to_saved || 0;
    if (orphan.length) ui.toast(`Часть строк не сохранена: ${[...new Set(orphan)].join('; ')}`, 'error');
    if (marks || tos) {
      const s = reloaded && data.summary ? data.summary : null;
      ui.toast(`Сохранено: показаний ${marks}, последних ТО ${tos}.`
        + (s ? ` Сейчас: просрочено ${s.overdue || 0}, скоро ${s.soon || 0}` : ''));
    } else if (!failed.size && !orphan.length) {
      ui.toast('Новых данных не было: такие показания уже внесены', 'info');
    }
    if (failed.size) {
      ui.toast(`Не сохранено строк: ${failed.size} — причина написана под полем`, 'error');
      focusFirst((st) => !!(st.errItem || st.errTo));
    }
  }

  // ───────────── клавиши, защита ввода, тихое обновление ─────────────

  // Ctrl+S — сохранить, не отрывая рук от клавиатуры (пока открыт диалог — не наше нажатие)
  const onKey = (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || e.code !== 'KeyS') return;
    if (busy()) return;
    e.preventDefault();
    if (rows.length) save();
  };
  document.addEventListener('keydown', onKey);

  ctx.setLeaveGuard(() => {
    const n = rows.filter(isDirty).length;
    return n
      ? `Вписанное в таблицу не сохранено: ${n} ${ui.plural(n, 'строка', 'строки', 'строк')}. Уйти без сохранения?`
      : null;
  });

  const timer = setInterval(() => refreshQuietly(false), AUTO_REFRESH_MS);
  const onVisible = () => {
    if (!document.hidden) refreshQuietly(true);
  };
  document.addEventListener('visibilitychange', onVisible);
  window.addEventListener('focus', onVisible);

  return () => {
    clearInterval(timer);
    document.removeEventListener('visibilitychange', onVisible);
    window.removeEventListener('focus', onVisible);
    document.removeEventListener('keydown', onKey);
  };
}
