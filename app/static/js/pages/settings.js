// Экран «Настройки» (SPEC 7.2, п. 10). Вкладки: «ТО и нормы», «Регламент ТО», «Справочники», «Заказ-наряд», «Данные».
// В каждом разделе своя кнопка «Сохранить»; после сохранения — ctx.refresh() (остальные экраны берут настройки из ctx.boot).
// Несохранённым может быть только один раздел: при переходе на другую вкладку программа переспрашивает.

import { importFromExcel, GOOGLE_HINT } from './_import.js';

const TABS = [
  { key: 'norms', label: 'ТО и нормы', icon: 'gauge' },
  { key: 'regulation', label: 'Регламент ТО', icon: 'clipboard-text' },
  { key: 'lists', label: 'Справочники', icon: 'list-details' },
  { key: 'order', label: 'Заказ-наряд', icon: 'printer' },
  { key: 'data', label: 'Данные', icon: 'database' },
];

// Виды событий, на которых держатся расчёты: сервер не даёт их удалить (SPEC 5, PUT /api/lists/kinds).
const PROTECTED_KINDS = ['ТО-1', 'ТО-2', 'ТО-3', 'Ремонт', 'Простой', 'Заметка', 'Отметка моточасов'];

const LISTS = [
  {
    key: 'kinds',
    title: 'Виды событий',
    hint: 'Что можно записать в историю машины. Свои виды добавлять можно: всё, что начинается с «ТО-», программа считает ТО, '
      + 'с «Ремонт» — ремонтом, с «Простой» — простоем. Серые значения убрать или переименовать нельзя — на них держатся расчёты. '
      + '«Отметка моточасов» — это отметка показаний (и для километров тоже).',
  },
  { key: 'sites', title: 'Участки', hint: 'Где работает техника. Участок выбирается в данных машины, по нему фильтруется главный экран.' },
  { key: 'shifts', title: 'Смены', hint: 'В новых записях смена больше не спрашивается; список нужен только для старых записей, где она указана.' },
  { key: 'mtypes', title: 'Типы машин', hint: 'Самосвал, экскаватор… По типу программа понимает, считать машину по моточасам или по километрам.' },
  { key: 'unit_names', title: 'Узлы и агрегаты', hint: 'Названия для экрана «Узлы».' },
  { key: 'tyre_types', title: 'Типы шин', hint: 'Размеры шин для экрана «Шины».' },
  { key: 'positions', title: 'Позиции шин', hint: 'Где на машине стоит шина — для экрана «Шины».' },
];

const REG_FIELDS = ['to_kind', 'periodicity', 'works', 'oil', 'filters', 'parts', 'note'];

// Что открыть после ctx.refresh(): экран перерисовывается целиком, а человек должен остаться там, где сохранял.
let keepTab = null;
let keepList = null;

const CSS = `
.set-panel { max-width: 1180px; }
.set-lead { max-width: 820px; margin: 0 0 12px; color: var(--text-muted); }
.set-card-sub { margin: -8px 0 12px; color: var(--text-muted); font-size: 13px; }
.set-note { margin: 8px 0 0; color: var(--text-muted); font-size: 13px; }
/* липкая полоса «Сохранить» прижата к самому низу окна: bottom −20px перекрывает нижний отступ .content
   (20px), иначе в эту щель под полосой видно прокручиваемые поля */
.set-savebar {
  position: sticky; bottom: -20px; z-index: 3;
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px;
  margin-top: 16px; padding: 12px 0 32px;
  border-top: 1px solid var(--border); background: var(--bg);
}
.card .set-savebar { background: var(--surface); padding-bottom: 20px; }
@media print { .set-savebar { position: static; padding-bottom: 12px; } }
.set-savebar.dirty .set-savebar-state { color: var(--amber); font-weight: 600; }
.set-rules .table { min-width: 860px; }
.set-reg .table { min-width: 1040px; }
.set-reg .table td { vertical-align: top; }
.set-reg .table .textarea { height: 88px; min-height: 60px; }
.set-under-table { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin-top: 12px; }
.set-lists { display: grid; grid-template-columns: 240px minmax(0, 1fr); gap: 16px; align-items: start; }
.set-listnav { display: flex; flex-direction: column; gap: 2px; padding: 8px; }
.set-listnav-item {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  min-height: 34px; margin: 0; padding: 0 10px;
  border: 0; border-radius: var(--radius); background: none;
  color: var(--text); font: inherit; text-align: left; cursor: pointer;
  transition: background-color var(--ease), color var(--ease);
}
.set-listnav-item:hover { background: var(--grey-bg); }
.set-listnav-item.active { background: var(--zebra); box-shadow: inset 3px 0 0 var(--primary); color: var(--primary); font-weight: 600; }
.set-listnav-count { color: var(--text-muted); font-size: 13px; font-weight: 400; }
.set-listnav-note { margin: 8px 10px 4px; color: var(--text-muted); font-size: 13px; }
.set-items { display: flex; flex-direction: column; gap: 6px; max-width: 620px; }
.set-item { display: grid; grid-template-columns: 30px minmax(0, 1fr) 96px; gap: 8px; align-items: center; }
.set-item-no { text-align: right; }
.set-item-add { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }
.set-item-add { grid-template-columns: 30px minmax(0, 1fr) auto; }
.set-order { max-width: 760px; }
.set-data-actions { margin-top: 12px; }
.set-path { overflow-wrap: anywhere; user-select: all; }
.set-backups { margin-top: 12px; }
`;

// ───────────────────────────── общие помощники ─────────────────────────────

function sameJson(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

function isPositive(v) {
  return typeof v === 'number' && v > 0;
}

function isBlank(v) {
  return v === null || v === undefined || v === '';
}

/** «ТО-1, ТО-2; ТО-3» → ['ТО-1', 'ТО-2', 'ТО-3'] (без пустых и повторов). */
function splitWords(text) {
  const result = [];
  for (const part of String(text || '').split(/[,;\n]/)) {
    const word = part.trim();
    if (word && !result.includes(word)) result.push(word);
  }
  return result;
}

/** Доля → проценты для поля ввода: 0.9 → 90. */
function toPct(v) {
  return typeof v === 'number' ? Math.round(v * 1000) / 10 : null;
}

/** Проценты из поля → доля для сервера: 90 → 0.9. Не число — отдаём как есть (сервер ответит понятной ошибкой). */
function fromPct(v) {
  return typeof v === 'number' ? Math.round(v * 10) / 1000 : v;
}

function fmtSize(ui, bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${ui.fmtNum(n, 0)} Б`;
  if (n < 1024 * 1024) return `${ui.fmtNum(n / 1024, 0)} КБ`;
  return `${ui.fmtNum(n / 1024 / 1024, 1)} МБ`;
}

/** Нижняя строка раздела: «Сохранить», «Отменить изменения» и подпись, есть ли несохранённое. */
function saveBar(ui, { onSave, onReset }) {
  const { h } = ui;
  const state = h('span.set-savebar-state.muted.small', { role: 'status' }, 'Изменений нет');
  const save = ui.button('Сохранить', { kind: 'accent', icon: 'check', onClick: onSave, disabled: true });
  const reset = ui.button('Отменить изменения', { kind: 'ghost', onClick: onReset, disabled: true });
  const el = h('div.set-savebar', null, save, reset, state);
  el.setDirty = (dirty) => {
    save.disabled = !dirty;
    reset.disabled = !dirty;
    state.textContent = dirty ? 'Есть несохранённые изменения' : 'Изменений нет';
    el.classList.toggle('dirty', !!dirty);
  };
  return el;
}

/** Кнопки строки: выше / ниже / удалить. onDelete не задан — кнопки удаления нет (обязательное значение). */
function rowButtons(ui, { index, count, onMove, onDelete, deleteTitle = 'Удалить строку' }) {
  const { h } = ui;
  const up = ui.iconButton('chevron-up', 'Поднять выше', () => onMove(index, -1), { small: true, disabled: index === 0 });
  const down = ui.iconButton('chevron-down', 'Опустить ниже', () => onMove(index, 1), { small: true, disabled: index === count - 1 });
  up.dataset.act = 'up';
  down.dataset.act = 'down';
  let last = null;
  if (onDelete) {
    last = ui.iconButton('trash', deleteTitle, () => onDelete(index), { small: true });
    last.dataset.act = 'del';
  }
  return h('div.row-actions', null, up, down, last);
}

/** После перестановки строки вернуть фокус на ту же кнопку (чтобы можно было нажимать подряд). */
function focusAction(rowEl, act) {
  if (!rowEl) return;
  const btn = rowEl.querySelector(`[data-act="${act}"]:not([disabled])`) || rowEl.querySelector('[data-act]:not([disabled])');
  if (btn) btn.focus();
}

/** Перезагрузить окно (после импорта и восстановления меняется вся база) и вернуться на вкладку «Данные». */
function reloadOnDataTab() {
  history.replaceState(null, '', '#/settings?tab=data');
  location.reload();
}

function moveItem(list, index, step) {
  const to = index + step;
  if (to < 0 || to >= list.length) return -1;
  const [item] = list.splice(index, 1);
  list.splice(to, 0, item);
  return to;
}

// ───────────────────────────── «ТО и нормы» ─────────────────────────────

function buildNorms(env) {
  const { ctx } = env;
  const { ui, api } = ctx;
  const { h } = ui;
  const s = ctx.boot.settings || {};

  // — общие нормы —
  const intervalMh = ui.numInput({ value: s.default_interval_mh, decimals: 1 });
  const warnMh = ui.numInput({ value: s.default_warn_mh, decimals: 1 });
  const normMh = ui.numInput({ value: s.default_norm_mh, decimals: 1 });
  const intervalKm = ui.numInput({ value: s.default_interval_km, decimals: 0 });
  const normKm = ui.numInput({ value: s.default_norm_km, decimals: 0 });
  const cycle = ui.textInput({ value: (s.to_cycle || []).join(', ') });
  const mhTypes = ui.textInput({ value: (s.mh_types || []).join(', ') });
  const windowDays = ui.numInput({ value: s.rate_window_days, decimals: 0 });
  const useFact = ui.checkbox('Накидывать по фактической средней', { checked: !!s.norm_use_fact });
  const tyreRed = ui.numInput({ value: toPct(s.tyre_red), decimals: 1 });
  const tyreYellow = ui.numInput({ value: toPct(s.tyre_yellow), decimals: 1 });

  const f = {
    intervalMh: ui.field('Интервал ТО, м/ч', intervalMh, { required: true,
      hint: 'Через сколько моточасов после последнего ТО делать следующее.' }),
    warnMh: ui.field('Предупреждать за, м/ч', warnMh, { required: true,
      hint: 'Когда до ТО останется столько или меньше — машина станет жёлтой «Скоро ТО».' }),
    normMh: ui.field('Наработка за сутки, м/ч', normMh, { required: true,
      hint: 'Столько прибавляет кнопка «Накинуть за день», пока по машине нет своих данных. Одна смена — около 11 м/ч.' }),
    intervalKm: ui.field('Интервал ТО, км', intervalKm, { required: true,
      hint: 'Через сколько километров после последнего ТО делать следующее. Жёлтое «Скоро ТО» включится за 10 % интервала.' }),
    normKm: ui.field('Пробег за сутки, км', normKm, { required: true,
      hint: 'Столько прибавляет кнопка «Накинуть за день», пока по машине нет своих данных.' }),
    cycle: ui.field('Круг видов ТО', cycle, { required: true,
      hint: 'Через запятую, по порядку. После «ТО сделано» программа сама предлагает следующий вид, после последнего — снова первый.' }),
    mhTypes: ui.field('Какая техника считается по моточасам', mhTypes, {
      hint: 'Слова через запятую. Если слово есть в типе или модели машины — она считается по моточасам, даже когда её марка есть в правилах ниже (например, экскаватор SANY).' }),
    windowDays: ui.field('Средняя наработка — за сколько дней', windowDays, { required: true,
      hint: 'По показаниям за это время программа понимает, сколько машина проходит в сутки, и строит прогноз даты ТО.' }),
    useFact: ui.field('Кнопка «Накинуть за день»', useFact, {
      hint: 'Включено — прибавляется столько, сколько машина в среднем проходит на самом деле (когда показаний хватает). Выключено — всегда норма.' }),
    tyreRed: ui.field('Пора менять — износ, %', tyreRed, { required: true,
      hint: 'При таком износе шина или узел становятся красными.' }),
    tyreYellow: ui.field('Скоро менять — износ, %', tyreYellow, { required: true,
      hint: 'При таком износе — жёлтыми.' }),
  };

  // — правила по маркам —
  const rules = (ctx.boot.rules || []).map((r) => ({
    keyword: r.keyword || '',
    unit: r.unit === 'км' ? 'км' : 'м/ч',
    interval: isBlank(r.interval) ? null : r.interval,
    warn: isBlank(r.warn) ? null : r.warn,
    daily_norm: isBlank(r.daily_norm) ? null : r.daily_norm,
    bad: new Set(), // поля строки, отмеченные красным после проверки
  }));

  const isBlankRule = (r) => !String(r.keyword || '').trim() && isBlank(r.interval) && isBlank(r.warn) && isBlank(r.daily_norm);
  const cleanRules = () => rules.filter((r) => !isBlankRule(r)).map((r) => ({
    keyword: String(r.keyword || '').trim(), unit: r.unit, interval: r.interval, warn: r.warn, daily_norm: r.daily_norm,
  }));

  function ruleNum(row, key, label, placeholder) {
    const input = ui.numInput({
      value: row[key], decimals: 1, ariaLabel: label, placeholder,
      onInput: (v) => {
        row[key] = v;
        row.bad.delete(key);
      },
    });
    if (row.bad.has(key)) input.classList.add('invalid');
    return input;
  }

  function moveRule(index, step) {
    const to = moveItem(rules, index, step);
    if (to < 0) return;
    rulesTable.setRows(rules);
    touch();
    focusAction(rulesTable.table.tBodies[0].rows[to], step < 0 ? 'up' : 'down');
  }

  function deleteRule(index) {
    rules.splice(index, 1);
    rulesTable.setRows(rules);
    touch();
  }

  function addRule() {
    rules.push({ keyword: '', unit: 'км', interval: null, warn: null, daily_norm: null, bad: new Set() });
    rulesTable.setRows(rules);
    touch();
    const row = rulesTable.table.tBodies[0].rows[rules.length - 1];
    const first = row ? row.querySelector('input') : null;
    if (first) first.focus();
  }

  const rulesTable = ui.table({
    sticky: false,
    cls: 'set-rules',
    rows: rules,
    empty: 'Правил нет — вся техника считается по общим нормам.',
    columns: [
      { title: '№', width: 44, align: 'center', render: (r, i) => i + 1 },
      { title: 'Слово в названии модели', input: true, hint: 'Например: SHACMAN. Большие и маленькие буквы не различаются',
        render: (row) => {
          const input = ui.textInput({
            value: row.keyword, ariaLabel: 'Слово в названии модели', placeholder: 'например, SHACMAN',
            onInput: (v) => {
              row.keyword = v;
              row.bad.delete('keyword');
              input.classList.remove('invalid');
            },
          });
          if (row.bad.has('keyword')) input.classList.add('invalid');
          return input;
        } },
      { title: 'Единица', input: true, width: 150,
        render: (row) => ui.select({
          options: [['км', 'км'], ['м/ч', 'м/ч (моточасы)']], value: row.unit, ariaLabel: 'Единица учёта',
          onChange: (v) => {
            row.unit = v;
          },
        }) },
      { title: 'Интервал ТО', input: true, width: 140, align: 'right', render: (row) => ruleNum(row, 'interval', 'Интервал ТО') },
      { title: 'Предупреждать за', input: true, width: 150, align: 'right', render: (row) => ruleNum(row, 'warn', 'Предупреждать за', 'авто') },
      { title: 'Наработка за сутки', input: true, width: 160, align: 'right', render: (row) => ruleNum(row, 'daily_norm', 'Наработка за сутки', 'общая') },
      { title: '', width: 112, cls: 'cell-actions',
        render: (row, i) => rowButtons(ui, { index: i, count: rules.length, onMove: moveRule, onDelete: deleteRule, deleteTitle: 'Удалить правило' }) },
    ],
  });

  // — сбор значений, признак «есть изменения» —
  function collect() {
    return {
      settings: {
        default_interval_mh: intervalMh.getValue(),
        default_warn_mh: warnMh.getValue(),
        default_norm_mh: normMh.getValue(),
        default_interval_km: intervalKm.getValue(),
        default_norm_km: normKm.getValue(),
        to_cycle: splitWords(cycle.getValue()),
        mh_types: splitWords(mhTypes.getValue().toLowerCase()),
        rate_window_days: windowDays.getValue(),
        norm_use_fact: useFact.getValue(),
        tyre_red: fromPct(tyreRed.getValue()),
        tyre_yellow: fromPct(tyreYellow.getValue()),
      },
      rules: cleanRules(),
    };
  }

  const initial = collect();
  const isDirty = () => !sameJson(collect(), initial);
  const bar = saveBar(ui, { onSave: save, onReset: env.redraw });
  const touch = () => bar.setDirty(isDirty());
  env.setSection({ isDirty });

  function validate(now) {
    const v = now.settings;
    const errors = {
      intervalMh: isPositive(v.default_interval_mh) ? '' : 'Введите число больше нуля',
      warnMh: typeof v.default_warn_mh === 'number' && v.default_warn_mh >= 0 ? '' : 'Введите число (можно 0)',
      normMh: isPositive(v.default_norm_mh) ? '' : 'Введите число больше нуля',
      intervalKm: isPositive(v.default_interval_km) ? '' : 'Введите число больше нуля',
      normKm: isPositive(v.default_norm_km) ? '' : 'Введите число больше нуля',
      cycle: '',
      windowDays: typeof v.rate_window_days === 'number' && v.rate_window_days >= 3 && v.rate_window_days <= 3650
        ? '' : 'Введите целое число от 3 до 3650',
      tyreRed: isPositive(v.tyre_red) && v.tyre_red <= 1 ? '' : 'Введите число от 1 до 100',
      tyreYellow: isPositive(v.tyre_yellow) && v.tyre_yellow <= 1 ? '' : 'Введите число от 1 до 100',
    };
    if (!v.to_cycle.length) errors.cycle = 'Укажите хотя бы один вид ТО, например: ТО-1, ТО-2, ТО-3';
    else if (v.to_cycle.some((k) => !k.startsWith('ТО-'))) errors.cycle = 'Каждый вид должен начинаться с «ТО-», например ТО-1';
    if (!errors.tyreRed && !errors.tyreYellow && v.tyre_yellow > v.tyre_red) {
      errors.tyreYellow = 'Не может быть больше, чем «Пора менять»';
    }
    let ok = true;
    let firstBad = null;
    for (const [key, text] of Object.entries(errors)) {
      f[key].setError(text);
      if (text) {
        ok = false;
        if (!firstBad) firstBad = f[key];
      }
    }

    let rulesOk = true;
    for (const r of rules) {
      r.bad.clear();
      if (isBlankRule(r)) continue;
      if (!String(r.keyword || '').trim()) r.bad.add('keyword');
      if (!isPositive(r.interval)) r.bad.add('interval');
      if (!isBlank(r.warn) && !(typeof r.warn === 'number' && r.warn >= 0)) r.bad.add('warn');
      if (!isBlank(r.daily_norm) && !isPositive(r.daily_norm)) r.bad.add('daily_norm');
      if (r.bad.size) rulesOk = false;
    }
    rulesTable.update();

    if (firstBad) {
      const input = firstBad.querySelector('input');
      if (input) input.focus();
    } else if (!rulesOk) {
      const bad = rulesTable.querySelector('.invalid');
      if (bad) bad.focus();
    }
    if (!ok) ui.toast('Проверьте поля, отмеченные красным', 'error');
    else if (!rulesOk) ui.toast('В правилах по маркам у каждой строки должны быть слово и интервал больше нуля', 'error');
    return ok && rulesOk;
  }

  async function save() {
    const now = collect();
    if (!validate(now)) return;

    const changed = {};
    for (const [key, value] of Object.entries(now.settings)) {
      if (!sameJson(value, initial.settings[key])) changed[key] = value;
    }
    const settingsChanged = Object.keys(changed).length > 0;
    if (settingsChanged) await api.put('/settings', changed);

    if (!sameJson(now.rules, initial.rules)) {
      try {
        await api.put('/rules', now.rules);
      } catch (err) {
        if (!settingsChanged) throw err;
        throw new Error(`Общие нормы сохранены, а правила по маркам — нет. ${err && err.message ? err.message : err}`);
      }
    }

    // новый вид ТО из круга должен быть и в списке видов событий, иначе его не выбрать в записи по машине
    const kinds = (ctx.boot.lists && ctx.boot.lists.kinds) || [];
    const missing = now.settings.to_cycle.filter((k) => !kinds.includes(k));
    if (missing.length) {
      try {
        await api.put('/lists/kinds', [...kinds, ...missing]);
      } catch (err) {
        ui.toast(`Не получилось добавить ${missing.join(', ')} в справочник «Виды событий» — добавьте вручную на вкладке «Справочники»`, 'warn');
      }
    }
    await env.saved('Настройки сохранены');
  }

  const box = h('div.stack', null,
    h('section.card', null,
      h('h2.card-title', null, 'Техника на моточасах'),
      h('p.set-card-sub', null, 'Экскаваторы, бульдозеры, катки, грейдеры, погрузчики — и любая машина, чьей марки нет в правилах ниже.'),
      h('div.form-grid-3', null, f.intervalMh, f.warnMh, f.normMh)),
    h('section.card', null,
      h('h2.card-title', null, 'Техника на километрах'),
      h('p.set-card-sub', null, 'Для машин на километрах, чьей марки нет в правилах ниже (единица «км» задана в данных самой машины).'),
      h('div.form-grid-3', null, f.intervalKm, f.normKm)),
    h('section.card', null,
      h('h2.card-title', null, 'Правила по маркам'),
      h('p.set-lead', null, 'Если в названии модели машины есть слово из этой таблицы — для неё берутся единица, интервал и порог из этой строки. '
        + 'Строки проверяются сверху вниз, срабатывает первая подходящая. Свои параметры отдельной машины (экран «Машины») главнее правил.'),
      rulesTable,
      h('div.set-under-table', null,
        ui.button('Добавить марку', { icon: 'plus', onClick: addRule }),
        h('span.muted.small', null, 'Пустое «Предупреждать за» — 10 % интервала для километров и общий порог для моточасов. Пустая «Наработка за сутки» — общая норма.'))),
    h('section.card', null,
      h('h2.card-title', null, 'Как считать'),
      h('div.form-grid', null, f.cycle, f.mhTypes, f.windowDays, f.useFact)),
    h('section.card', null,
      h('h2.card-title', null, 'Шины и узлы'),
      h('div.form-grid-3', null, f.tyreRed, f.tyreYellow)),
    bar);
  box.addEventListener('input', touch);
  box.addEventListener('change', touch);
  return box;
}

// ───────────────────────────── «Регламент ТО» ─────────────────────────────

function buildRegulation(env) {
  const { ctx } = env;
  const { ui, api } = ctx;
  const { h } = ui;

  const rows = (ctx.boot.regulation || []).map((r) => {
    const row = { bad: false };
    for (const key of REG_FIELDS) row[key] = isBlank(r[key]) ? '' : String(r[key]);
    return row;
  });
  const toKinds = ((ctx.boot.lists && ctx.boot.lists.kinds) || []).filter((k) => String(k).startsWith('ТО-'));

  const isBlankRow = (r) => REG_FIELDS.every((key) => !String(r[key] || '').trim());
  const clean = () => rows.filter((r) => !isBlankRow(r)).map((r) => {
    const item = {};
    for (const key of REG_FIELDS) item[key] = String(r[key] || '').trim();
    return item;
  });

  const initial = clean();
  const isDirty = () => !sameJson(clean(), initial);
  const bar = saveBar(ui, { onSave: save, onReset: env.redraw });
  const touch = () => bar.setDirty(isDirty());
  env.setSection({ isDirty });

  function area(row, key, label) {
    return ui.textArea({
      value: row[key], rows: 3, ariaLabel: label,
      onInput: (v) => {
        row[key] = v;
      },
    });
  }

  function move(index, step) {
    const to = moveItem(rows, index, step);
    if (to < 0) return;
    table.setRows(rows);
    touch();
    focusAction(table.table.tBodies[0].rows[to], step < 0 ? 'up' : 'down');
  }

  function remove(index) {
    rows.splice(index, 1);
    table.setRows(rows);
    touch();
  }

  function add() {
    const row = { bad: false };
    for (const key of REG_FIELDS) row[key] = '';
    rows.push(row);
    table.setRows(rows);
    touch();
    const tr = table.table.tBodies[0].rows[rows.length - 1];
    const first = tr ? tr.querySelector('input') : null;
    if (first) first.focus();
  }

  const table = ui.table({
    sticky: false,
    cls: 'set-reg',
    rows,
    empty: ui.emptyState('Регламент подсказывает, что делать и что заказывать на каждом виде ТО.', {
      icon: 'clipboard-text', title: 'Регламент пуст', action: { label: 'Добавить строку', icon: 'plus', onClick: add },
    }),
    columns: [
      { title: 'Вид ТО', input: true, width: 104,
        render: (row) => {
          const input = ui.combo({
            options: toKinds, value: row.to_kind, ariaLabel: 'Вид ТО', placeholder: 'ТО-1',
            onInput: (text) => {
              row.to_kind = text;
              row.bad = false;
            },
            onChange: (v) => {
              row.to_kind = v;
              row.bad = false;
              touch();
            },
          });
          if (row.bad) input.classList.add('invalid');
          return input;
        } },
      { title: 'Периодичность', input: true, width: 124, hint: 'Справочно, для памяти. Сам интервал ТО задаётся на вкладке «ТО и нормы»',
        render: (row) => ui.textInput({
          value: row.periodicity, ariaLabel: 'Периодичность',
          onInput: (v) => {
            row.periodicity = v;
          },
        }) },
      { title: 'Что делать', input: true, width: '24%', render: (row) => area(row, 'works', 'Что делать') },
      { title: 'Масло', input: true, width: '14%', render: (row) => area(row, 'oil', 'Масло') },
      { title: 'Фильтры', input: true, width: '15%', render: (row) => area(row, 'filters', 'Фильтры') },
      { title: 'Запчасти', input: true, width: '13%', render: (row) => area(row, 'parts', 'Запчасти') },
      { title: 'Примечание', input: true, width: '13%', render: (row) => area(row, 'note', 'Примечание') },
      { title: '', width: 112, cls: 'cell-actions',
        render: (row, i) => rowButtons(ui, { index: i, count: rows.length, onMove: move, onDelete: remove }) },
    ],
  });

  async function save() {
    // у каждой заполненной строки должен быть вид ТО, и он не должен повторяться
    const seen = new Set();
    let problem = '';
    for (const r of rows) {
      r.bad = false;
      if (isBlankRow(r)) continue;
      const kind = String(r.to_kind || '').trim();
      if (!kind) {
        r.bad = true;
        problem = problem || 'Укажите вид ТО в каждой заполненной строке';
      } else if (seen.has(kind)) {
        r.bad = true;
        problem = problem || `Вид «${kind}» указан дважды — оставьте одну строку`;
      }
      seen.add(kind);
    }
    if (problem) {
      table.update();
      const bad = table.querySelector('.invalid');
      if (bad) bad.focus();
      ui.toast(problem, 'error');
      return;
    }
    await api.put('/regulation', clean());
    await env.saved('Регламент сохранён');
  }

  const box = h('div', null,
    h('p.set-lead', null, 'Регламент — что делать и что заказывать на каждом виде ТО. Эти тексты программа подставляет в окно «ТО сделано», '
      + 'в «Список на ТО» и в сводку «Что заказать».'),
    table,
    h('div.set-under-table', null,
      ui.button('Добавить строку', { icon: 'plus', onClick: add }),
      h('span.muted.small', null, 'Столбец «Периодичность» — справочный. Сам интервал ТО задаётся на вкладке «ТО и нормы».')),
    bar);
  box.addEventListener('input', touch);
  box.addEventListener('change', touch);
  return box;
}

// ───────────────────────────── «Справочники» ─────────────────────────────

function buildLists(env) {
  const { ctx } = env;
  const { ui, api } = ctx;
  const { h } = ui;
  const lists = ctx.boot.lists || {};

  let current = LISTS.some((l) => l.key === keepList) ? keepList : LISTS[0].key;
  keepList = null;
  let isDirty = () => false;
  env.setSection({ isDirty: () => isDirty() });

  const nav = h('nav.card.set-listnav', { 'aria-label': 'Справочники' });
  const editor = h('section.card');

  function drawNav() {
    const statuses = lists.machine_statuses || [];
    ui.mount(nav,
      LISTS.map((meta) => h('button.set-listnav-item', {
        type: 'button',
        class: meta.key === current && 'active',
        'aria-current': meta.key === current ? 'true' : null,
        onclick: async () => {
          if (meta.key === current) return;
          if (!(await env.canLeave())) return;
          current = meta.key;
          drawNav();
          drawEditor();
        },
      }, h('span', null, meta.title), h('span.set-listnav-count', null, String((lists[meta.key] || []).length)))),
      statuses.length
        ? h('p.set-listnav-note', null, `Статусы машин (${statuses.join(', ')}) не меняются: на них держатся расчёты.`)
        : null);
  }

  function drawEditor() {
    const meta = LISTS.find((l) => l.key === current);
    const original = (lists[meta.key] || []).map((v) => String(v));
    const locked = new Set(meta.key === 'kinds' ? PROTECTED_KINDS : []);
    const items = original.map((value) => ({ value, locked: locked.has(value), bad: false }));
    const values = () => items.map((it) => it.value.trim()).filter(Boolean);

    const rowsBox = h('div.set-items');
    const bar = saveBar(ui, { onSave: save, onReset: drawEditor });
    isDirty = () => !sameJson(values(), original);
    const touch = () => bar.setDirty(isDirty());

    function move(index, step) {
      const to = moveItem(items, index, step);
      if (to < 0) return;
      drawRows();
      touch();
      focusAction(rowsBox.children[to], step < 0 ? 'up' : 'down');
    }

    function remove(index) {
      items.splice(index, 1);
      drawRows();
      touch();
    }

    function drawRows() {
      if (!items.length) {
        ui.mount(rowsBox, h('div.muted', null, 'В справочнике пока нет значений — добавьте первое ниже.'));
        return;
      }
      ui.mount(rowsBox, items.map((it, i) => {
        const input = ui.textInput({
          value: it.value, readOnly: it.locked, ariaLabel: `Значение ${i + 1}`,
          title: it.locked ? 'Обязательное значение: на нём держатся расчёты, убрать или переименовать его нельзя' : null,
          onInput: (v) => {
            it.value = v;
            it.bad = false;
            input.classList.remove('invalid');
          },
        });
        if (it.bad) input.classList.add('invalid');
        return h('div.set-item', null,
          h('span.set-item-no.muted.small', null, `${i + 1}.`),
          input,
          rowButtons(ui, {
            index: i, count: items.length, onMove: move,
            onDelete: it.locked ? null : remove, deleteTitle: 'Убрать из справочника',
          }));
      }));
    }

    const addInput = ui.textInput({ placeholder: 'Новое значение', ariaLabel: 'Новое значение', onEnter: () => add() });

    function add() {
      const value = addInput.getValue();
      if (!value) {
        addInput.focus();
        return false;
      }
      if (values().includes(value)) {
        ui.toast(`«${value}» уже есть в справочнике`, 'warn');
        addInput.focus();
        return false;
      }
      items.push({ value, locked: false, bad: false });
      addInput.setValue('');
      drawRows();
      touch();
      addInput.focus();
      return true;
    }

    function sortAbc() {
      items.sort((a, b) => a.value.localeCompare(b.value, 'ru', { numeric: true, sensitivity: 'base' }));
      drawRows();
      touch();
    }

    async function save() {
      if (addInput.getValue() && !add()) return; // недописанное значение в поле «Новое значение» тоже сохраняем
      const seen = new Set();
      let repeated = null;
      for (const it of items) {
        const v = it.value.trim();
        it.bad = false;
        if (!v) continue;
        if (seen.has(v)) {
          it.bad = true;
          repeated = v;
        }
        seen.add(v);
      }
      if (repeated !== null) {
        drawRows();
        ui.toast(`Значение «${repeated}» повторяется — оставьте одно`, 'error');
        return;
      }
      await api.put(`/lists/${meta.key}`, values());
      keepList = meta.key;
      await env.saved(`Справочник «${meta.title}» сохранён`);
    }

    drawRows();
    ui.mount(editor,
      h('h2.card-title', null, meta.title),
      h('p.set-lead', null, meta.hint),
      rowsBox,
      h('div.set-item.set-item-add', null,
        h('span'),
        addInput,
        ui.button('Добавить', { icon: 'plus', onClick: () => add() })),
      h('div.set-under-table', null,
        ui.button('По алфавиту', { kind: 'ghost', small: true, onClick: sortAbc, title: 'Расставить значения по алфавиту' }),
        h('span.muted.small', null, 'Справочник — это подсказки при вводе. Если значение переименовать или убрать, в уже сделанных записях останется прежнее.')),
      bar);
  }

  editor.addEventListener('input', () => {
    const bar = editor.querySelector('.set-savebar');
    if (bar) bar.setDirty(isDirty());
  });

  drawNav();
  drawEditor();
  return h('div.set-lists', null, nav, editor);
}

// ───────────────────────────── «Заказ-наряд» ─────────────────────────────

function buildOrder(env) {
  const { ctx } = env;
  const { ui, api } = ctx;
  const { h } = ui;
  const s = ctx.boot.settings || {};

  const contract = ui.textInput({ value: s.org_contract });
  const post = ui.textInput({ value: s.org_mechanic_post });
  const name = ui.textInput({ value: s.org_mechanic_name });
  const preview = h('div.readout', { style: { whiteSpace: 'pre-wrap' } });

  const collect = () => ({
    org_contract: contract.getValue(),
    org_mechanic_post: post.getValue(),
    org_mechanic_name: name.getValue(),
  });
  const initial = collect();
  const isDirty = () => !sameJson(collect(), initial);
  const bar = saveBar(ui, { onSave: save, onReset: env.redraw });
  env.setSection({ isDirty });

  function touch() {
    const v = collect();
    preview.textContent = `Заказ-наряд выдал: (механик) ${v.org_mechanic_post || '________'}   (ФИО) ${v.org_mechanic_name || '________'}   (подпись) ________`;
    bar.setDirty(isDirty());
  }

  async function save() {
    const now = collect();
    const changed = {};
    for (const [key, value] of Object.entries(now)) {
      if (value !== initial[key]) changed[key] = value;
    }
    if (Object.keys(changed).length) await api.put('/settings', changed);
    await env.saved('Реквизиты сохранены');
  }

  const box = h('div.set-order', null,
    h('p.set-lead', null, 'Эти строки программа печатает в заказ-наряде. На экране «Заказ-наряд» их можно поправить перед печатью, а здесь — задать раз и навсегда.'),
    h('section.card', null,
      h('div.form-grid', null,
        ui.field('Шапка заказ-наряда', contract, { cls: 'span-all',
          hint: 'Печатается в правом верхнем углу формы. Например: Приложение № 1 к договору (образец).' }),
        ui.field('Должность механика', post, { hint: 'Кто выдаёт заказ-наряд. Например: Ст. механик.' }),
        ui.field('Фамилия и инициалы механика', name, { hint: 'Печатаются в строке подписи.' }),
        h('div.field.span-all', null,
          h('div.field-label', null, 'Строка подписи в заказ-наряде'),
          preview))),
    bar);
  box.addEventListener('input', touch);
  touch();
  return box;
}

// ───────────────────────────── «Данные» ─────────────────────────────

function buildData(env) {
  const { ctx } = env;
  const { ui, api } = ctx;
  const { h } = ui;
  env.setSection(null);

  // — загрузка из Excel —
  function startImport() {
    importFromExcel(ctx, { doneLabel: 'Продолжить' })
      .then((result) => {
        if (result) reloadOnDataTab();
      })
      .catch((err) => ui.toast(err && err.message ? err.message : String(err), 'error'));
  }

  // — выгрузка в Excel —
  async function exportExcel() {
    const fileName = await api.download('/export/xlsx');
    ui.toast(`Файл «${fileName}» скачан — ищите его в папке «Загрузки»`);
  }

  // — резервные копии —
  const backupsBox = h('div.set-backups');

  async function createBackup() {
    await api.post('/backups');
    ui.toast('Резервная копия создана');
    await loadBackups();
  }

  async function restore(backup) {
    const when = ui.fmtDateTime(backup.mtime);
    const ok = await ui.confirm(`Вернуть базу к состоянию на ${when}?`, {
      danger: true,
      title: 'Восстановление из копии',
      okLabel: 'Восстановить',
      details: [
        'Всё, что внесено в программу после этого времени, исчезнет.',
        'Перед восстановлением программа сама сохранит копию нынешней базы — к ней можно будет вернуться так же.',
      ],
    });
    if (!ok) return;
    const result = await api.post('/backups/restore', { name: backup.name });
    await ui.alert(`База восстановлена на ${when}.`, {
      title: 'Готово',
      details: result && result.safety ? [`Прежнее состояние сохранено в копии ${result.safety}`] : null,
      okLabel: 'Продолжить',
    });
    reloadOnDataTab();
  }

  async function loadBackups() {
    ui.mount(backupsBox, ui.loading('Загрузка списка копий…'));
    let list;
    try {
      list = await api.get('/backups');
    } catch (err) {
      ui.mount(backupsBox, ui.errorState(err, loadBackups, { title: 'Не получилось загрузить список копий' }));
      return;
    }
    if (!Array.isArray(list) || !list.length) {
      ui.mount(backupsBox, ui.emptyState('Копия — это снимок всей базы на один момент. Сделайте первую сейчас.', {
        icon: 'history', title: 'Копий пока нет',
        action: { label: 'Сделать копию сейчас', icon: 'plus', onClick: createBackup },
      }));
      return;
    }
    ui.mount(backupsBox, ui.table({
      rows: list,
      maxHeight: 340,
      columns: [
        { key: 'mtime', title: 'Когда сделана', width: 170, render: (b) => ui.fmtDateTime(b.mtime) },
        { key: 'size', title: 'Размер', width: 110, align: 'right', render: (b) => fmtSize(ui, b.size) },
        { key: 'name', title: 'Файл копии', cls: 'muted' },
        { title: '', width: 150, cls: 'cell-actions',
          render: (b) => h('div.row-actions', null,
            ui.button('Восстановить', { small: true, icon: 'history', onClick: () => restore(b),
              title: 'Вернуть базу к состоянию на это время' })) },
      ],
    }));
  }

  // — папка данных —
  const dataDir = ctx.boot.data_dir || '';
  async function copyPath() {
    try {
      await navigator.clipboard.writeText(dataDir);
      ui.toast('Путь скопирован');
    } catch (err) {
      ui.toast('Не получилось скопировать — выделите путь мышью и нажмите Ctrl+C', 'warn');
    }
  }

  loadBackups();

  return h('div.stack', null,
    h('section.card', null,
      h('h2.card-title', null, 'Загрузить из таблицы Excel'),
      h('p.set-lead', null, 'Программа заберёт из таблицы «Журнал учёта техники и ТО» машины, журнал, шины, узлы и настройки. '
        + 'Сначала покажет, что нашла в файле, и загрузит только после вашего подтверждения. То, что уже есть в программе, не задвоится.'),
      h('p.set-note', null, GOOGLE_HINT),
      h('div.set-data-actions', null,
        ui.button('Выбрать файл таблицы (.xlsx)', { kind: 'primary', icon: 'upload', onClick: startImport }))),
    h('section.card', null,
      h('h2.card-title', null, 'Выгрузить в Excel'),
      h('p.set-lead', null, 'Все машины, журнал, шины и узлы — одним файлом Excel. Его можно открыть в Excel, отправить руководству '
        + 'или загрузить обратно в программу.'),
      h('div.set-data-actions', null,
        ui.button('Скачать файл Excel', { icon: 'download', onClick: exportExcel }))),
    h('section.card', null,
      h('h2.card-title', null, 'Резервные копии'),
      h('p.set-lead', null, 'Копия — это снимок всей базы. Программа сама делает копию при запуске (не чаще раза в 6 часов) и хранит 30 последних. '
        + 'Перед рискованными правками сделайте копию вручную — к ней можно будет вернуться.'),
      h('div', null, ui.button('Сделать копию сейчас', { icon: 'plus', onClick: createBackup })),
      backupsBox),
    h('section.card', null,
      h('h2.card-title', null, 'О программе'),
      h('dl.kv', null,
        h('dt', null, 'Версия'),
        h('dd', null, ctx.boot.version || '—'),
        h('dt', null, 'Папка с данными'),
        h('dd', null, h('span.set-path', null, dataDir || '—'))),
      h('p.set-note', null, 'В этой папке лежат база (файл to.db) и резервные копии (папка backups).'),
      dataDir ? h('div.set-data-actions', null, ui.button('Скопировать путь', { small: true, onClick: copyPath })) : null));
}

// ───────────────────────────── страница ─────────────────────────────

const BUILDERS = {
  norms: buildNorms,
  regulation: buildRegulation,
  lists: buildLists,
  order: buildOrder,
  data: buildData,
};

export async function render(root, params, ctx) {
  const { ui } = ctx;
  const { h } = ui;
  ui.addStyle('settings-style', CSS);

  let active = keepTab || (TABS.some((t) => t.key === params.tab) ? params.tab : TABS[0].key);
  keepTab = null;
  let section = null; // {isDirty()} открытого раздела; null — сохранять нечего
  const hasUnsaved = () => !!(section && section.isDirty());

  const panel = h('div.set-panel');

  /** Можно ли уйти из раздела: если есть несохранённое — переспросить. */
  async function canLeave() {
    if (!hasUnsaved()) return true;
    return ui.confirm('В этом разделе есть несохранённые изменения. Если перейти, они пропадут.', {
      title: 'Изменения не сохранены', okLabel: 'Перейти без сохранения', cancelLabel: 'Остаться', danger: true,
    });
  }

  const env = {
    ctx,
    canLeave,
    setSection(s) {
      section = s;
    },
    /** Перерисовать открытый раздел заново из ctx.boot («Отменить изменения»). */
    redraw: () => draw(),
    /** Раздел сохранён: сообщить и перечитать настройки (экран перерисуется на той же вкладке). */
    async saved(text) {
      ui.toast(text);
      section = null;
      keepTab = active;
      await ctx.refresh();
    },
  };

  function draw() {
    section = null;
    ui.mount(panel, BUILDERS[active](env));
  }

  const tabs = ui.tabs({
    items: TABS,
    active,
    onChange: async (key) => {
      if (!(await canLeave())) {
        tabs.setActive(active, true);
        return;
      }
      active = key;
      draw();
    },
  });

  ctx.setLeaveGuard(() => (hasUnsaved() ? 'В настройках есть несохранённые изменения. Уйти без сохранения?' : null));

  root.append(tabs, panel);
  draw();
}
