// Общие диалоги «Учёт ТО» (SPEC 7.1, 7.3; КОНЦЕПЦИЯ_v2, п. 6): «ТО сделано», «Показание», запись по машине (ремонт, простой, заметка),
// «Закрыть ремонт/простой», машина, «Накинуть за день».
// Контракт: каждая функция возвращает Promise<boolean> (true — что-то сохранено) и сама показывает
// ошибки и подтверждения. Имена экспортов менять нельзя: их вызывают страницы через ctx.dialogs.

import * as ui from './ui.js';
import * as api from './api.js';

const { h } = ui;

const KIND_MARK = 'Отметка моточасов'; // в базе; на экране — «Отметка показаний»
const WHO_KEY = 'to-uchet.who';
const WHO_LIST_KEY = 'to-uchet.who-list';
const NORM_SOURCE = { override: 'своя норма машины', fact: 'по факту', rule: 'по марке', default: 'общая норма' };
const STATE_ORDER = { repair: 0, idle: 1, to: 2, line: 3, conserved: 4 };

// ───────────────────────────── общее ─────────────────────────────

function addStyles() {
  ui.addStyle('dialogs-css', `
.dlg-preview { flex-wrap: wrap; gap: 2px 6px; }
.dlg-info { margin: 0 0 12px; }
.dlg-sub { margin: 16px 0 2px; font-size: 14px; font-weight: 600; }
.dlg-note { margin: 0 0 10px; color: var(--text-muted); font-size: 13px; }
.dlg-gap { margin-top: 12px; }
.dlg-period { margin-bottom: 12px; }
.dlg-period .input-date { width: 150px; }
.dlg-period-note { align-self: center; padding-top: 18px; color: var(--text-muted); font-size: 13px; }
.dlg-norm { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; white-space: nowrap; }
.dlg-norm .input-num { width: 84px; min-width: 0; }
.dlg-clip { max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.table td.dlg-wrap { min-width: 120px; white-space: normal; }
.dlg-total { margin-top: 12px; }
.dlg-work { min-height: 84px; }
`);
}

/** Выполнить диалог: любой сбой при открытии — тост с текстом, результат false. */
async function guarded(fn) {
  try {
    return (await fn()) === true;
  } catch (err) {
    if (!(err && err.status !== undefined)) console.error(err);
    ui.toast(err && err.message ? err.message : String(err), 'error');
    return false;
  }
}

function lsGet(key) {
  try {
    return localStorage.getItem(key) || '';
  } catch (err) {
    return '';
  }
}

function lsSet(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (err) {
    // хранилище недоступно — просто не запоминаем
  }
}

function whoList() {
  let list = [];
  try {
    list = JSON.parse(lsGet(WHO_LIST_KEY) || '[]');
  } catch (err) {
    list = [];
  }
  return Array.isArray(list) ? list.filter((x) => typeof x === 'string' && x) : [];
}

/** Подсказки для поля «Кто»: недавно введённые имена и механик из настроек. */
function whoOptions(ctx) {
  const list = whoList();
  const name = ctx.boot && ctx.boot.settings ? String(ctx.boot.settings.org_mechanic_name || '').trim() : '';
  if (name && !list.includes(name)) list.push(name);
  return list;
}

/** Запомнить, кто вносил запись: следующий диалог подставит это имя сам. */
function rememberWho(name) {
  const who = String(name || '').trim();
  if (!who) return;
  lsSet(WHO_KEY, who);
  const list = [who, ...whoList().filter((x) => x !== who)].slice(0, 10);
  lsSet(WHO_LIST_KEY, JSON.stringify(list));
}

function whoField(ctx, value) {
  return ui.combo({ options: whoOptions(ctx), value: value === undefined ? lsGet(WHO_KEY) : value, placeholder: 'Фамилия' });
}

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const hasValue = (v) => v !== undefined && v !== null && v !== '';
const isTo = (kind) => String(kind || '').startsWith('ТО-');
const isRepair = (kind) => String(kind || '').startsWith('Ремонт');
const isIdle = (kind) => String(kind || '').startsWith('Простой');
const kindLabel = (kind) => (kind === KIND_MARK ? 'Отметка показаний' : String(kind || ''));
const roundUnit = (v, unit) => (unit === 'км' ? Math.round(v) : Math.round(v * 10) / 10);

function machineTitle(m) {
  return m.model ? `${m.garage_no} · ${m.model}` : String(m.garage_no);
}

function machineOption(m) {
  return {
    value: m.id,
    label: machineTitle(m),
    hint: [m.plate, m.status === 'Списана' ? 'списана' : ''].filter(Boolean).join(' · '),
    search: `${m.plate || ''} ${m.vin || ''} ${m.site || ''}`,
  };
}

/** Машины для выбора в диалоге — без списанных (keepId — оставить и списанную: правка старой записи). */
async function loadMachines(keepId) {
  const list = await api.get('/machines');
  return (list || []).filter((m) => m.status !== 'Списана' || String(m.id) === String(keepId));
}

async function noMachines() {
  await ui.alert('В программе пока нет машин. Сначала добавьте машину на экране «Машины».', { title: 'Машин нет' });
  return false;
}

/** Строка регламента для вида ТО: точное название, иначе по началу («ТО-2 сезонное» → «ТО-2»). */
function regulationFor(ctx, kind) {
  const key = String(kind || '').trim().toLowerCase();
  if (!key) return null;
  let best = null;
  let bestLen = 0;
  for (const row of (ctx.boot && ctx.boot.regulation) || []) {
    const name = String(row.to_kind || '').trim().toLowerCase();
    if (!name) continue;
    if (name === key) return row;
    if (key.startsWith(name) && name.length > bestLen) {
      best = row;
      bestLen = name.length;
    }
  }
  return best;
}

/** Виды ТО для выбора: из справочника событий, плюс круг ТО из настроек. */
function toKinds(ctx) {
  const lists = (ctx.boot && ctx.boot.lists) || {};
  const settings = (ctx.boot && ctx.boot.settings) || {};
  const all = (lists.kinds || []).filter(isTo);
  for (const k of settings.to_cycle || []) {
    if (k && !all.includes(k)) all.push(k);
  }
  return all.length ? all : ['ТО-1', 'ТО-2', 'ТО-3'];
}

/** «Последний факт: 47 415 км от 01.10.2026» / «(при регистрации)» / «нет». */
function lastFactText(m) {
  if (!hasValue(m.reading_base)) return 'нет';
  return ui.fmtReading(m.reading_base, m.unit)
    + (m.reading_base_date ? ` от ${ui.fmtDate(m.reading_base_date)}` : ' (при регистрации)');
}

/** Числовое поле заполнено неверно? Ставит ошибку под полем и возвращает true. */
function badNum(fieldEl, input, required, emptyText) {
  const v = input.getValue();
  if (v === null) {
    if (required) {
      fieldEl.setError(emptyText || 'Заполните поле');
      input.focus();
      return true;
    }
    fieldEl.setError('');
    return false;
  }
  if (!isNum(v) || !input.isValid()) {
    fieldEl.setError('Это не число — проверьте цифры');
    input.focus();
    return true;
  }
  fieldEl.setError('');
  return false;
}

/** Поле времени заполнено неверно? */
function badTime(fieldEl, input) {
  if (!input.isValid()) {
    fieldEl.setError('Время — как 14:30 или 1430');
    input.focus();
    return true;
  }
  fieldEl.setError('');
  return false;
}

/** Отличия b от a по ключам b (для частичного PUT: отправляем только изменённое). */
function diff(a, b) {
  const out = {};
  for (const key of Object.keys(b)) {
    if (b[key] !== a[key]) out[key] = b[key];
  }
  return out;
}

function dateTimeMs(dateIso, time) {
  const d = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(dateIso || ''));
  if (!d) return null;
  const t = /^(\d{1,2}):(\d{2})/.exec(String(time || '')) || [0, 0, 0];
  return new Date(Number(d[1]), Number(d[2]) - 1, Number(d[3]), Number(t[1]), Number(t[2])).getTime();
}

/**
 * Дата выхода, если введено только время: день заезда, а если время выхода раньше времени заезда — следующий день
 * (SPEC, «Валидация событий»).
 */
function autoDateOut(dateIn, timeIn, timeOut) {
  if (!dateIn || !timeOut) return '';
  return timeIn && timeOut < timeIn ? ui.addDays(dateIn, 1) : dateIn;
}

// ───────────────────────────── «ТО сделано» ─────────────────────────────

/**
 * «ТО сделано» (R7, R8, R9). machineId не задан — машина выбирается в диалоге.
 * prefill — уже введённое в другом окне ({kind, date, reading, work, parts_used, who}): подставляется поверх
 * значений по умолчанию, показание при этом считается введённым человеком (не «≈»).
 */
export function openToDoneDialog(ctx, machineId, prefill = null) {
  return guarded(async () => {
    addStyles();
    const fixed = hasValue(machineId);
    let machine = null;
    let machines = [];
    if (fixed) machine = await api.get(`/machines/${machineId}/view`);
    else {
      machines = await loadMachines();
      if (!machines.length) return noMachines();
    }

    let prefillAt = null; // что программа сама подставила в «на момент ТО»
    let prefillNow = null; // …и в «сейчас»
    let nowTouched = false; // человек сам менял «Показание сейчас»
    let autoWork = ''; // текст работ, подставленный из регламента
    let loading = false;
    let loadSeq = 0;

    const machineCombo = fixed ? null : ui.combo({
      strict: true,
      options: machines.map(machineOption),
      placeholder: 'Гаражный № или модель',
      emptyText: 'Такой машины нет',
      autofocus: true,
      onChange: (id) => pickMachine(id),
    });
    const fMachine = ui.field('Машина', fixed ? h('div.readout.strong', null, machineTitle(machine)) : machineCombo,
      { required: !fixed, cls: 'span-all' });

    const kind = ui.select({ options: toKinds(ctx), onChange: () => { fillFromRegulation(); updatePreview(); } });
    const date = ui.dateInput({ value: ui.today(), max: ui.today(), onChange: () => updatePreview() });
    const fDate = ui.field('Дата ТО', date, { required: true });

    const unitAt = h('span');
    const unitNow = h('span');
    const isKm = () => !!machine && machine.unit === 'км'; // «153.000» в км — 153 тысячи
    const atTo = ui.numInput({ decimals: 1, autofocus: fixed, onInput: onAtInput, thousands: isKm });
    const nowF = ui.numInput({
      decimals: 1,
      thousands: isKm,
      onInput: () => {
        nowTouched = true;
        updatePreview();
      },
    });
    const fAt = ui.field(['Показание на момент ТО', unitAt], atTo, { required: true, hint: 'на каком показании сделали ТО' });
    const fNow = ui.field(['Показание сейчас', unitNow], nowF, { hint: 'что на счётчике сегодня' });

    const preview = h('div.readout.dlg-preview.span-all', { 'aria-live': 'polite' });

    const work = ui.textArea({ rows: 3, placeholder: 'Замена масла, фильтров…' });
    const partsUsed = ui.textInput({ placeholder: 'Что израсходовали' });
    const fParts = ui.field('Масло / запчасти', partsUsed, { cls: 'span-all' });
    const who = whoField(ctx);

    function fillFromRegulation() {
      const reg = regulationFor(ctx, kind.getValue());
      const text = reg ? String(reg.works || '').trim() : '';
      // подставляем работы, только если человек не написал своё
      if (work.getValue() === '' || work.getValue() === autoWork) work.setValue(text);
      autoWork = text;
      const parts = reg ? [reg.oil, reg.filters, reg.parts].map((x) => String(x || '').trim()).filter((x) => x && x !== '—' && x !== '-') : [];
      if (!parts.length) {
        fParts.setHint('');
        return;
      }
      const line = parts.join('; ');
      fParts.setHint(['По регламенту: ', line, '. ',
        h('button.link', { type: 'button', onclick: () => { partsUsed.setValue(line); partsUsed.focus(); } }, 'Подставить')]);
    }

    function applyMachine(mv) {
      machine = mv;
      const unit = mv ? `, ${mv.unit}` : '';
      unitAt.textContent = unit;
      unitNow.textContent = unit;
      if (mv && mv.next_to_kind) kind.setValue(mv.next_to_kind);
      const r = mv && hasValue(mv.reading) ? mv.reading : null;
      atTo.setValue(r);
      nowF.setValue(r);
      prefillAt = atTo.getValue();
      prefillNow = nowF.getValue();
      nowTouched = false;
      fAt.setError('');
      fNow.setError('');
      fNow.setHint(mv && mv.reading_estimated
        ? 'расчётное (≈) — поправьте, если посмотрели на счётчик'
        : 'что на счётчике сегодня');
      fillFromRegulation();
      if (mv && prefill) {
        applyPrefill(prefill);
        prefill = null; // только один раз: при смене машины — обычные значения
      }
      updatePreview();
    }

    function applyPrefill(p) {
      if (isTo(p.kind)) {
        kind.setValue(p.kind);
        fillFromRegulation();
      }
      if (p.date) date.setValue(p.date);
      if (String(p.work || '').trim()) work.setValue(p.work);
      if (String(p.parts_used || '').trim()) partsUsed.setValue(p.parts_used);
      if (String(p.who || '').trim()) who.setValue(p.who);
      if (isNum(p.reading)) {
        atTo.setValue(p.reading);
        prefillAt = null; // показание ввёл человек — это не расчётное «≈»
        onAtInput();
      }
    }

    async function pickMachine(id) {
      loadSeq += 1;
      const my = loadSeq;
      fMachine.setError('');
      if (!hasValue(id)) {
        loading = false;
        applyMachine(null);
        return;
      }
      loading = true;
      ui.mount(preview, h('span.muted', null, 'Загрузка…'));
      try {
        const mv = await api.get(`/machines/${id}/view`);
        if (my !== loadSeq) return;
        loading = false;
        applyMachine(mv);
      } catch (err) {
        if (my !== loadSeq) return;
        loading = false;
        applyMachine(null);
        fMachine.setError(err && err.message ? err.message : String(err));
      }
    }

    function onAtInput() {
      // пока человек не трогал «сейчас», оно не может быть меньше показания, на котором сделали ТО
      if (machine && !nowTouched) {
        const at = atTo.getValue();
        nowF.setValue(isNum(at) && (!isNum(prefillNow) || at > prefillNow) ? at : prefillNow);
      }
      updatePreview();
    }

    function updatePreview() {
      if (!machine) {
        ui.mount(preview, h('span.muted', null, 'Выберите машину — программа покажет, когда следующее ТО'));
        return;
      }
      const unit = machine.unit;
      const at = atTo.getValue();
      const shown = nowF.getValue();
      let cur = shown;
      const d = date.getValue();
      // как посчитает сервер: показание ТО, введённое человеком (не «≈»), выше последнего факта и не раньше его даты,
      // становится новой базой — накинутое до него больше не учитывается
      if (!nowTouched && machine.reading_estimated && isNum(at) && at !== prefillAt && isNum(machine.reading_base)
        && at > machine.reading_base && d && (!machine.reading_base_date || d >= machine.reading_base_date)) {
        cur = at;
      }
      const p = ui.previewTo(machine, { kind: kind.getValue(), reading: at, current: cur }, ctx.boot.settings);
      if (p.next_at === null) {
        ui.mount(preview, h('span.muted', null, 'Введите показание на момент ТО — программа покажет, когда следующее'));
        return;
      }
      if (isNum(at) && isNum(shown) && shown < at) {
        ui.mount(preview, h('span.text-red.strong', null, 'Показание сейчас меньше показания на момент ТО — проверьте цифры'));
        return;
      }
      const last = machine.last_to;
      if (last && d && isNum(at) && (d < last.date || (d === last.date && at < last.reading))) {
        // последним ТО останется внесённое раньше: следующее считается от него, а не от этого
        ui.mount(preview, h('span', null, 'ТО раньше последнего внесённого — следующее не изменится (',
          h('b', null, `${machine.next_to_kind} на ${ui.fmtReading(machine.next_to_at, unit)}`), ')'));
        return;
      }
      let tail = null;
      if (p.remaining === null) {
        // текущее показание неизвестно — остаток посчитать не из чего
      } else if (p.status === 'overdue') {
        tail = h('span.text-red.strong', null, p.remaining === 0
          ? ' — уже пора делать'
          : ` — уже просрочено на ${ui.fmtReading(-p.remaining, unit)}`);
      } else {
        tail = [', останется ', h('b', null, ui.fmtReading(p.remaining, unit))];
      }
      ui.mount(preview,
        h('span', null, 'Следующее: ', h('b', null, p.next_kind), ' на ', h('b', null, ui.fmtReading(p.next_at, unit)), tail),
        p.status === 'soon' ? ui.statusChip('soon') : null);
    }

    async function save(m) {
      if (!machine) {
        fMachine.setError(loading ? 'Данные машины ещё загружаются — нажмите ещё раз' : 'Выберите машину из списка');
        if (machineCombo) machineCombo.focus();
        return;
      }
      fDate.setError(date.getValue() ? '' : 'Укажите дату ТО');
      if (!date.getValue()) return;
      if (badNum(fAt, atTo, true, 'Введите показание, на котором сделали ТО')) return;
      if (badNum(fNow, nowF, false)) return;
      const at = atTo.getValue();
      const cur = nowF.getValue();
      const sendCurrent = nowTouched && isNum(cur);
      if (sendCurrent && cur < at) {
        fNow.setError('Не может быть меньше показания на момент ТО');
        nowF.focus();
        return;
      }
      const body = {
        machine_id: machine.id,
        kind: kind.getValue(),
        date: date.getValue(),
        reading: at,
        // показание взято из расчётного «≈», на счётчик не смотрели
        reading_estimated: !!machine.reading_estimated && isNum(prefillAt) && at === prefillAt,
        work: work.getValue(),
        parts_used: partsUsed.getValue(),
        who: who.getValue(),
      };
      if (sendCurrent) body.current_reading = cur;
      const res = await api.withConfirm((force) => api.post('/to-done', { ...body, force }));
      if (res === null) return;
      rememberWho(body.who);
      const mv = res.machine || {};
      let text = `${mv.garage_no || machine.garage_no}: ${body.kind} внесено.`;
      if (hasValue(mv.next_to_at)) {
        text += ` Следующее — ${mv.next_to_kind} на ${ui.fmtReading(mv.next_to_at, mv.unit)}`;
        if (hasValue(mv.remaining)) {
          text += mv.remaining > 0
            ? `, осталось ${ui.fmtReading(mv.remaining, mv.unit)}.`
            : `, уже просрочено на ${ui.fmtReading(-mv.remaining, mv.unit)}.`;
        } else text += '.';
      }
      ui.toast(text);
      if (res.note) ui.toast(res.note, 'info');
      m.close(true);
    }

    const m = ui.modal({
      title: 'ТО сделано',
      width: 620,
      body: h('div.form-grid', null,
        fMachine,
        ui.field('Вид ТО', kind, { required: true }),
        fDate,
        fAt,
        fNow,
        preview,
        ui.field('Что делали', work, { cls: 'span-all' }),
        fParts,
        ui.field('Кто', who)),
      buttons: [
        { label: 'Отмена', value: false },
        { label: 'ТО сделано', kind: 'accent', icon: 'check', primary: true, onClick: save },
      ],
    });
    applyMachine(machine);
    return (await m.result) === true;
  });
}

// ───────────────────────────── «Показание» ─────────────────────────────

/** «Показание» — ввод текущего показания одной машины (сохраняется как отметка, через /api/readings/bulk). */
export function openReadingDialog(ctx, machineId) {
  return guarded(async () => {
    addStyles();
    const fixed = hasValue(machineId);
    let machine = null;
    let machines = [];
    if (fixed) machine = await api.get(`/machines/${machineId}/view`);
    else {
      machines = await loadMachines();
      if (!machines.length) return noMachines();
    }
    let loading = false;
    let loadSeq = 0;

    const machineCombo = fixed ? null : ui.combo({
      strict: true,
      options: machines.map(machineOption),
      placeholder: 'Гаражный № или модель',
      emptyText: 'Такой машины нет',
      autofocus: true,
      onChange: (id) => pickMachine(id),
    });
    const fMachine = ui.field('Машина', fixed ? h('div.readout.strong', null, machineTitle(machine)) : machineCombo,
      { required: !fixed, cls: 'span-all' });
    const date = ui.dateInput({ value: ui.today(), max: ui.today() });
    const fDate = ui.field('Дата показания', date, { required: true });
    const unitNew = h('span');
    const reading = ui.numInput({ decimals: 1, autofocus: fixed, onInput: updatePreview,
      thousands: () => !!machine && machine.unit === 'км' });
    const fReading = ui.field(['Новое показание', unitNew], reading, { required: true, hint: 'цифра со счётчика' });
    const info = h('dl.kv.dlg-info.span-all');
    const preview = h('div.readout.dlg-preview.span-all', { 'aria-live': 'polite' });

    function toNowText(mv) {
      if (mv.to_status === 'no_reading') return 'нет показаний';
      if (mv.to_status === 'no_to' || !hasValue(mv.remaining)) return 'последнее ТО не внесено';
      if (mv.remaining <= 0) return `просрочено на ${ui.fmtReading(-mv.remaining, mv.unit)} (${mv.next_to_kind})`;
      return `${ui.fmtReading(mv.remaining, mv.unit)} до ${mv.next_to_kind}`;
    }

    function applyMachine(mv) {
      machine = mv;
      unitNew.textContent = mv ? `, ${mv.unit}` : '';
      info.hidden = !mv;
      if (mv) {
        ui.mount(info,
          h('dt', null, 'Последний факт'), h('dd', null, lastFactText(mv)),
          h('dt', null, 'Сейчас в программе'), h('dd', null,
            ui.fmtReading(mv.reading, mv.unit, mv.reading_estimated),
            mv.reading_estimated ? h('span.muted', null, ` — расчётное, накинуто +${ui.fmtNum(mv.reading_auto_add, 1)}`) : null),
          h('dt', null, 'До ТО'), h('dd', null, toNowText(mv)));
      }
      updatePreview();
    }

    async function pickMachine(id) {
      loadSeq += 1;
      const my = loadSeq;
      fMachine.setError('');
      if (!hasValue(id)) {
        loading = false;
        applyMachine(null);
        return;
      }
      loading = true;
      ui.mount(preview, h('span.muted', null, 'Загрузка…'));
      try {
        const mv = await api.get(`/machines/${id}/view`);
        if (my !== loadSeq) return;
        loading = false;
        applyMachine(mv);
      } catch (err) {
        if (my !== loadSeq) return;
        loading = false;
        applyMachine(null);
        fMachine.setError(err && err.message ? err.message : String(err));
      }
    }

    function updatePreview() {
      if (!machine) {
        ui.mount(preview, h('span.muted', null, 'Выберите машину'));
        return;
      }
      const v = reading.getValue();
      if (!isNum(v)) {
        ui.mount(preview, h('span.muted', null, 'Введите показание — программа покажет, сколько останется до ТО'));
        return;
      }
      const unit = machine.unit;
      const out = [];
      if (machine.last_to) {
        const p = ui.previewTo(machine, { kind: machine.last_to.kind, reading: machine.last_to.reading, current: v }, ctx.boot.settings);
        if (p.remaining === null) {
          // не хватает данных для расчёта
        } else if (p.status === 'overdue') {
          out.push(h('span.text-red.strong', null, p.remaining === 0
            ? `Пора делать ${p.next_kind}`
            : `${p.next_kind} будет просрочено на ${ui.fmtReading(-p.remaining, unit)}`));
        } else {
          out.push(h('span', null, `До ${p.next_kind} останется `, h('b', null, ui.fmtReading(p.remaining, unit))));
          if (p.status === 'soon') out.push(ui.statusChip('soon'));
        }
      } else {
        out.push(h('span.muted', null, 'Последнее ТО не внесено — остаток до ТО программа пока не считает'));
      }
      if (isNum(machine.reading_base) && v < machine.reading_base) {
        out.push(h('span.text-amber.strong', null,
          `Меньше последнего факта (${ui.fmtReading(machine.reading_base, unit)}) — программа переспросит, не ошибка ли это`));
      } else if (machine.reading_estimated && isNum(machine.reading) && v < machine.reading) {
        out.push(h('span.muted', null, `Расчёт был ${ui.fmtReading(machine.reading, unit, true)} — программа поправит его по счётчику`));
      }
      ui.mount(preview, out);
    }

    async function save(m) {
      if (!machine) {
        fMachine.setError(loading ? 'Данные машины ещё загружаются — нажмите ещё раз' : 'Выберите машину из списка');
        if (machineCombo) machineCombo.focus();
        return;
      }
      fDate.setError(date.getValue() ? '' : 'Укажите дату');
      if (!date.getValue()) return;
      if (badNum(fReading, reading, true, 'Введите показание со счётчика')) return;
      const value = reading.getValue();
      const res = await api.withConfirm((force) => api.post('/readings/bulk', {
        date: date.getValue(),
        items: [{ machine_id: machine.id, reading: value }],
        force,
      }));
      if (res === null) return;
      if (res.errors && res.errors.length) {
        fReading.setError(res.errors[0].error || 'Показание не сохранено');
        return;
      }
      ui.toast(`Показание сохранено: ${machine.garage_no} — ${ui.fmtReading(value, machine.unit)}`);
      m.close(true);
    }

    const m = ui.modal({
      title: 'Показание счётчика',
      body: h('div.form-grid', null, fMachine, fDate, fReading, info, preview),
      buttons: [
        { label: 'Отмена', value: false },
        { label: 'Сохранить', kind: 'primary', primary: true, onClick: save },
      ],
    });
    applyMachine(machine);
    return (await m.result) === true;
  });
}

// ───────────────────────────── запись по машине (ремонт, простой, заметка) ─────────────────────────────

/**
 * Запись по машине: создание (machineId, preset — начальные значения полей) или правка (event — EventView или его id).
 * В режиме создания есть «Сохранить и добавить ещё»: дата и «кто» остаются, фокус — на «Машина».
 * Полей «Смена» и «Замечания» в форме нет (КОНЦЕПЦИЯ_v2, п. 6): в базе они остаются, но диалог их не отправляет —
 * при правке старой записи они только показываются строкой под формой, чтобы ничего не терялось.
 */
export function openEventDialog(ctx, { machineId, event, preset } = {}) {
  return guarded(async () => {
    addStyles();
    if (hasValue(event) && typeof event !== 'object') event = await api.get(`/events/${event}`);
    const editing = !!(event && hasValue(event.id));
    if (editing && event.source === 'auto') {
      await ui.alert('Накинутую наработку изменить нельзя. Её можно удалить в истории карточки машины или отменить за период в окне «Накинуть за день».',
        { title: 'Запись не редактируется' });
      return false;
    }
    const base = editing ? event : { ...(preset || {}), ...(event || {}) };
    const mid0 = editing ? event.machine_id : (hasValue(machineId) ? machineId : (hasValue(base.machine_id) ? base.machine_id : null));
    const machines = await loadMachines(mid0);
    if (!machines.length) return noMachines();
    const byId = new Map(machines.map((mv) => [String(mv.id), mv]));
    const lists = (ctx.boot && ctx.boot.lists) || {};
    const kinds = lists.kinds && lists.kinds.length ? lists.kinds : ['ТО-1', 'ТО-2', 'ТО-3', 'Ремонт', 'Простой', 'Заметка', KIND_MARK];
    const defaultKind = kinds.find(isRepair) || kinds[0];
    const hadInOut = editing && (hasValue(event.time_in) || hasValue(event.date_out) || hasValue(event.time_out));
    let savedAny = false;

    const date = ui.dateInput({ value: base.date || ui.today() });
    const machine = ui.combo({
      strict: true,
      options: machines.map(machineOption),
      value: mid0,
      placeholder: 'Гаражный № или модель',
      emptyText: 'Такой машины нет',
      autofocus: !hasValue(mid0),
      onChange: () => {
        fMachine.setError('');
        updateReadingHint();
      },
      // машину выбрали из списка — дальше механик пишет, что делали (отложено: иначе Tab уведёт фокус дальше поля)
      onPick: () => setTimeout(() => work.focus(), 0),
    });
    const kind = ui.select({
      options: kinds.map((k) => ({ value: k, label: kindLabel(k) })),
      value: base.kind || defaultKind,
      onChange: onKind,
    });
    const unitSpan = h('span');
    // у машины в км «153.000» — это 153 тысячи (разделитель тысяч), у моточасов — десятичная запись
    const isKm = () => {
      const mv = byId.get(String(machine.getValue()));
      return !!mv && mv.unit === 'км';
    };
    const reading = ui.numInput({ value: base.reading, decimals: 2, thousands: isKm });
    const timeIn = ui.timeInput({ value: base.time_in || '' });
    const dateOut = ui.dateInput({ value: base.date_out || '' });
    const timeOut = ui.timeInput({ value: base.time_out || '', onChange: fillDateOut });
    // «Что делали» — главное поле записи: 3 строки; машина уже задана — фокус сюда сразу
    const work = ui.textArea({ rows: 3, value: base.work || '', cls: 'dlg-work', autofocus: hasValue(mid0) });
    const partsUsed = ui.textInput({ value: base.parts_used || '' });
    const partsOrder = ui.textInput({ value: base.parts_order || '' });
    const who = whoField(ctx, editing ? (base.who || '') : (hasValue(base.who) ? base.who : undefined));

    const fDate = ui.field('Дата', date, {
      required: true,
      cls: 'span-2',
      hint: editing && event.date_unknown ? 'Дата не указана — здесь дата внесения; поставьте настоящую, если знаете' : undefined,
    });
    const fMachine = ui.field('Машина', machine, { required: true, cls: 'span-2' });
    const fWork = ui.field('Что делали', work, { cls: 'span-all' });
    const fKind = ui.field('Событие', kind, { required: true, cls: 'span-2' });
    const fReading = ui.field(['Показание счётчика', unitSpan], reading, { cls: 'span-2' });
    const fTimeIn = ui.field('Заехала — время', timeIn);
    const fDateOut = ui.field('Вышла — дата', dateOut, { cls: 'span-2' });
    const fTimeOut = ui.field('Вышла — время', timeOut);
    const toLink = h('button.link', { type: 'button' }, 'Открыть «ТО сделано»');
    const toHint = ui.notice(['Для ТО удобнее кнопка «ТО сделано»: она подставит работы по регламенту и покажет, когда следующее ТО. ',
      editing ? null : toLink]);
    toHint.classList.add('span-all');

    const inOutVisible = () => isRepair(kind.getValue()) || isIdle(kind.getValue()) || hadInOut;

    function updateReadingHint() {
      const mv = byId.get(String(machine.getValue()));
      const k = kind.getValue();
      unitSpan.textContent = mv ? `, ${mv.unit}` : '';
      const now = mv && hasValue(mv.reading) ? `Сейчас: ${ui.fmtReading(mv.reading, mv.unit, mv.reading_estimated)}. ` : '';
      const need = isTo(k) ? 'Для ТО обязательно: на каком показании сделали.'
        : k === KIND_MARK ? 'Обязательно: отметка задаёт текущее показание машины.'
          : 'Можно не заполнять.';
      const est = editing && event.reading_estimated
        ? ' В записи расчётное показание (≈) — поправьте, если смотрели на счётчик.' : '';
      fReading.setHint(now + need + est);
    }

    function onKind() {
      const k = kind.getValue();
      const show = inOutVisible();
      fTimeIn.hidden = !show;
      fDateOut.hidden = !show;
      fTimeOut.hidden = !show;
      fDateOut.setHint(isIdle(k) ? 'пусто — машина ещё в простое' : 'пусто — машина ещё в ремонте');
      toHint.hidden = !isTo(k);
      updateReadingHint();
    }

    function fillDateOut() {
      // ввели только время выхода — дату выхода подставляем сами
      const t = timeOut.getValue();
      if (t && timeOut.isValid() && !dateOut.getValue() && date.getValue()) {
        dateOut.setValue(autoDateOut(date.getValue(), timeIn.isValid() ? timeIn.getValue() : '', t));
      }
    }

    function collect() {
      const data = {
        date: date.getValue(),
        machine_id: machine.getValue(),
        kind: kind.getValue(),
        reading: reading.getValue(),
        work: work.getValue(),
        parts_used: partsUsed.getValue(),
        parts_order: partsOrder.getValue(),
        who: who.getValue(),
        // shift и remarks намеренно не отправляются: в форме их нет, правка записи не должна их затирать
      };
      if (inOutVisible()) {
        data.time_in = timeIn.getValue() || null;
        data.date_out = dateOut.getValue() || null;
        data.time_out = timeOut.getValue() || null;
      }
      return data;
    }

    function validate() {
      fDate.setError(date.getValue() ? '' : 'Укажите дату');
      if (!date.getValue()) {
        date.focus();
        return false;
      }
      if (!hasValue(machine.getValue())) {
        fMachine.setError('Выберите машину из списка');
        machine.focus();
        return false;
      }
      fMachine.setError('');
      const k = kind.getValue();
      fKind.setError(k ? '' : 'Выберите событие');
      if (!k) return false;
      const needReading = isTo(k) || k === KIND_MARK;
      if (badNum(fReading, reading, needReading, isTo(k) ? 'Для ТО укажите показание, на котором оно сделано' : 'Укажите показание счётчика')) return false;
      if (inOutVisible()) {
        if (badTime(fTimeIn, timeIn) || badTime(fTimeOut, timeOut)) return false;
        fillDateOut();
      }
      return true;
    }

    function resetForMore() {
      machine.setValue(null);
      reading.setValue(null);
      timeIn.setValue('');
      dateOut.setValue('');
      timeOut.setValue('');
      work.setValue('');
      partsUsed.setValue('');
      partsOrder.setValue('');
      for (const f of [fDate, fMachine, fKind, fReading, fTimeIn, fTimeOut]) f.setError('');
      updateReadingHint();
      machine.focus();
    }

    const initial = (() => {
      onKind();
      return collect();
    })();

    async function save(m, more) {
      if (!validate()) return;
      const data = collect();
      let saved;
      if (editing) {
        const changed = diff(initial, data);
        if (!Object.keys(changed).length) {
          m.close(false);
          return;
        }
        // показание поправили по счётчику — оно больше не расчётное «≈» и становится фактом
        if (event.reading_estimated && changed.reading !== undefined) changed.reading_estimated = false;
        saved = await api.withConfirm((force) => api.put(`/events/${event.id}`, { ...changed, force }));
      } else {
        saved = await api.withConfirm((force) => api.post('/events', { ...data, force }));
      }
      if (saved === null) return;
      rememberWho(data.who);
      savedAny = true;
      ui.toast(editing ? 'Запись изменена' : `Запись добавлена: ${saved.garage_no}, ${kindLabel(saved.kind)}`);
      if (more) {
        resetForMore();
        return;
      }
      m.close(true);
    }

    async function remove(m) {
      const when = event.date_unknown ? '(дата не указана)' : `от ${ui.fmtDate(event.date)}`;
      const ok = await ui.confirm(`Удалить запись «${kindLabel(event.kind)}» ${when} (${event.garage_no})?`,
        { danger: true, okLabel: 'Удалить', title: 'Удалить запись?' });
      if (!ok) return;
      await api.del(`/events/${event.id}`);
      ui.toast('Запись удалена');
      m.close(true);
    }

    // «Смена» и «Замечания» из формы убраны, но в старой записи они могли быть заполнены — показываем строкой, чтобы не потерялись
    const kept = editing
      ? [hasValue(base.shift) ? `Смена: ${base.shift}` : null, hasValue(base.remarks) ? `Замечания: ${base.remarks}` : null]
        .filter(Boolean).join(' · ')
      : '';

    const m = ui.modal({
      title: editing ? `Запись — ${event.garage_no}` : 'Новая запись',
      width: 760,
      body: [
        h('div.form-grid-4', null,
          fMachine,
          fDate,
          fKind,
          fReading,
          fTimeIn, fDateOut, fTimeOut,
          toHint,
          fWork,
          ui.field('Масло / запчасти (использовано)', partsUsed, { cls: 'span-2' }),
          ui.field('Заказать запчасти', partsOrder, { cls: 'span-2' }),
          ui.field('Кто', who, { cls: 'span-2' })),
        kept ? h('p.dlg-note.dlg-gap', { title: 'Эти поля из прежних записей: в форме их больше нет, при сохранении они не меняются' }, kept) : null,
      ],
      buttons: [
        editing ? { label: 'Удалить запись', kind: 'ghost', icon: 'trash', align: 'left', onClick: remove } : null,
        { label: 'Отмена', value: false },
        editing ? null : { label: 'Сохранить и добавить ещё', icon: 'plus', onClick: (mm) => save(mm, true) },
        { label: 'Сохранить', kind: 'primary', primary: true, onClick: (mm) => save(mm, false) },
      ],
    });
    let carried = null; // введённое в этом окне переносится в «ТО сделано», а не теряется
    toLink.addEventListener('click', () => {
      const r = reading.getValue();
      carried = {
        kind: kind.getValue(),
        date: date.getValue(),
        reading: isNum(r) ? r : null,
        work: work.getValue(),
        parts_used: partsUsed.getValue(),
        who: who.getValue(),
      };
      m.close('to-done');
    });

    const result = await m.result;
    if (result === 'to-done') {
      const done = await openToDoneDialog(ctx, hasValue(machine.getValue()) ? machine.getValue() : undefined, carried);
      return done || savedAny;
    }
    return result === true || savedAny;
  });
}

// ───────────────────────────── «Закрыть ремонт / простой» ─────────────────────────────

/** «Закрыть ремонт/простой»: дата и время выхода; можно дополнить «что делалось» и «масло/запчасти». */
export function openCloseRepairDialog(ctx, eventOrId) {
  return guarded(async () => {
    addStyles();
    const id = eventOrId !== null && typeof eventOrId === 'object' ? eventOrId.id : eventOrId;
    if (!hasValue(id)) throw new Error('Не указана запись, которую нужно закрыть');
    const ev = await api.get(`/events/${id}`); // свежие данные: запись могли уже закрыть
    const idle = isIdle(ev.kind);
    const what = idle ? 'простой' : 'ремонт';
    const inWhat = idle ? 'В простое' : 'В ремонте';

    const dateOut = ui.dateInput({ value: ev.date_out || ui.today(), min: ev.date, onChange: updatePreview });
    const timeOut = ui.timeInput({
      value: ev.time_out || (ev.date_out ? '' : ui.nowTime()),
      autofocus: true,
      onInput: updatePreview,
      onChange: () => {
        fillDate();
        updatePreview();
      },
    });
    const fDateOut = ui.field('Вышла — дата', dateOut, { required: true });
    const fTimeOut = ui.field('Вышла — время', timeOut, { hint: 'можно 1430 или 14.30' });
    const work = ui.textArea({ rows: 2, value: ev.work || '' });
    const partsUsed = ui.textInput({ value: ev.parts_used || '' });
    const preview = h('div.readout.dlg-preview.span-all', { 'aria-live': 'polite' });
    const initial = { work: work.getValue(), parts_used: partsUsed.getValue() };

    function fillDate() {
      const t = timeOut.getValue();
      if (t && timeOut.isValid() && !dateOut.getValue()) dateOut.setValue(autoDateOut(ev.date, ev.time_in, t));
    }

    function updatePreview() {
      const d = dateOut.getValue();
      const t = timeOut.isValid() ? timeOut.getValue() : null;
      if (t === null) {
        ui.mount(preview, h('span.text-red.strong', null, 'Время — как 14:30 или 1430'));
        return;
      }
      if (!d) {
        ui.mount(preview, h('span.muted', null, t
          ? 'Дата выхода подставится сама: день заезда или следующий, если время выхода раньше времени заезда'
          : 'Укажите, когда машина вышла'));
        return;
      }
      const start = dateTimeMs(ev.date, ev.time_in);
      const end = dateTimeMs(d, t);
      if (start === null || end === null) {
        ui.clear(preview);
        return;
      }
      if (end < start) {
        ui.mount(preview, h('span.text-red.strong', null, 'Выход раньше заезда — проверьте дату и время'));
        return;
      }
      ui.mount(preview, h('span', null, `${inWhat} всего: `, h('b', null, ui.fmtDuration((end - start) / 1000))));
    }

    async function save(m) {
      if (badTime(fTimeOut, timeOut)) return;
      fillDate();
      const d = dateOut.getValue();
      fDateOut.setError(d ? '' : 'Укажите дату выхода');
      if (!d) {
        dateOut.focus();
        return;
      }
      const body = { date_out: d, time_out: timeOut.getValue() || null };
      Object.assign(body, diff(initial, { work: work.getValue(), parts_used: partsUsed.getValue() }));
      const saved = await api.withConfirm((force) => api.put(`/events/${ev.id}`, { ...body, force }));
      if (saved === null) return;
      const total = hasValue(saved.duration_seconds) && !saved.duration_error
        ? `, ${inWhat.toLowerCase()} всего ${ui.fmtDuration(saved.duration_seconds)}` : '';
      ui.toast(`${idle ? 'Простой закрыт' : 'Ремонт закрыт'}: ${saved.garage_no}${total}`);
      m.close(true);
    }

    const m = ui.modal({
      title: `Закрыть ${what} — ${ev.garage_no}`,
      body: [
        h('dl.kv.dlg-info', null,
          h('dt', null, 'Машина'), h('dd.strong', null, ev.model ? `${ev.garage_no} · ${ev.model}` : ev.garage_no),
          h('dt', null, 'Событие'), h('dd', null, kindLabel(ev.kind)),
          h('dt', null, 'Начало'), h('dd', null, ui.fmtDate(ev.date), ev.time_in ? ` ${ev.time_in}` : h('span.muted', null, ' (время заезда не указано)')),
          ev.open && hasValue(ev.duration_seconds)
            ? [h('dt', null, `${inWhat} уже`), h('dd', null, ui.fmtDuration(ev.duration_seconds))] : null),
        h('div.form-grid', null,
          fDateOut,
          fTimeOut,
          preview,
          ui.field('Что делалось', work, { cls: 'span-all' }),
          ui.field('Масло / запчасти (использовано)', partsUsed, { cls: 'span-all' })),
      ],
      buttons: [
        { label: 'Отмена', value: false },
        { label: `Закрыть ${what}`, kind: 'accent', icon: 'check', primary: true, onClick: save },
      ],
    });
    updatePreview();
    return (await m.result) === true;
  });
}

// ───────────────────────────── машина ─────────────────────────────

/** Машина: создание (без аргумента) или правка (machine — объект машины или её id). */
export function openMachineDialog(ctx, machine) {
  return guarded(async () => {
    addStyles();
    if (hasValue(machine) && typeof machine !== 'object') machine = await api.get(`/machines/${machine}/view`);
    const editing = !!(machine && hasValue(machine.id));
    const mv = machine || {};
    const auto = editing ? (mv.auto || null) : null;
    const lists = (ctx.boot && ctx.boot.lists) || {};
    const settings = (ctx.boot && ctx.boot.settings) || {};

    const garageNo = ui.textInput({ value: mv.garage_no || '', autofocus: !editing, maxLength: 40 });
    const model = ui.textInput({ value: mv.model || '', placeholder: 'SHACMAN, SANY SY215…' });
    const mtype = ui.combo({ options: lists.mtypes || [], value: mv.mtype || '', placeholder: 'Самосвал, Экскаватор…' });
    const plate = ui.textInput({ value: mv.plate || '' });
    const vin = ui.textInput({ value: mv.vin || '' });
    const site = ui.combo({ options: lists.sites || [], value: mv.site || '' });
    const status = ui.select({ options: lists.machine_statuses || ['В работе', 'На консервации', 'Списана'], value: mv.status || 'В работе' });
    const regReading = ui.numInput({ value: mv.reg_reading, decimals: 1 });
    const regDate = ui.dateInput({ value: mv.reg_date || '', max: ui.today() });
    const warranty = ui.textInput({ value: mv.warranty || '' });
    // поле photo (старая ссылка на фото) в форме убрано: фото теперь в карточке машины; ключ photo не отправляем — старое значение в базе не затрётся
    const note = ui.textInput({ value: mv.note || '' });

    const unitO = ui.select({
      options: ['км', 'м/ч'],
      placeholder: auto ? `авто — ${auto.unit}` : 'авто',
      value: mv.unit_override || '',
      onChange: updateAutoHints,
    });
    const intervalO = ui.numInput({ value: mv.interval_override, decimals: 1, placeholder: 'авто' });
    const warnO = ui.numInput({ value: mv.warn_override, decimals: 1, placeholder: 'авто' });
    const normO = ui.numInput({ value: mv.norm_override, decimals: 1, placeholder: 'авто' });

    const fGarage = ui.field('Гаражный №', garageNo, { required: true });
    const fReg = ui.field('Показание при регистрации', regReading, { hint: 'с него программа начинает счёт' });
    const fUnit = ui.field('Единица учёта', unitO);
    const fInterval = ui.field('Интервал ТО', intervalO);
    const fWarn = ui.field('Порог «скоро ТО»', warnO);
    const fNorm = ui.field('Суточная норма', normO);

    function updateAutoHints() {
      const chosen = unitO.getValue();
      if (!auto) {
        const text = 'пусто — программа определит сама';
        fUnit.setHint('пусто — по модели и типу машины');
        fInterval.setHint(text);
        fWarn.setHint(text);
        fNorm.setHint(text);
        return;
      }
      const unit = chosen || auto.unit;
      fUnit.setHint(`авто: ${auto.unit}${auto.rule_keyword ? ` (правило «${auto.rule_keyword}»)` : ''}`);
      if (unit === auto.unit) {
        fInterval.setHint(`авто: ${ui.fmtReading(auto.interval, unit)}`);
        fWarn.setHint(`авто: ${ui.fmtReading(auto.warn, unit)}`);
        fNorm.setHint(`авто: ${ui.fmtReading(auto.norm, unit)} в сутки`);
      } else if (unit === 'км') {
        // единицу сменили вручную — авто-значения берутся из общих настроек для этой единицы
        fInterval.setHint(`авто: ${ui.fmtReading(settings.default_interval_km, 'км')} (общая настройка)`);
        fWarn.setHint('авто: 10 % от интервала');
        fNorm.setHint(`авто: ${ui.fmtReading(settings.default_norm_km, 'км')} в сутки`);
      } else {
        fInterval.setHint(`авто: ${ui.fmtReading(settings.default_interval_mh, 'м/ч')} (общая настройка)`);
        fWarn.setHint(`авто: ${ui.fmtReading(settings.default_warn_mh, 'м/ч')}`);
        fNorm.setHint(`авто: ${ui.fmtReading(settings.default_norm_mh, 'м/ч')} в сутки`);
      }
    }

    function collect() {
      return {
        garage_no: garageNo.getValue(),
        model: model.getValue(),
        mtype: mtype.getValue(),
        plate: plate.getValue(),
        vin: vin.getValue(),
        site: site.getValue(),
        status: status.getValue(),
        reg_reading: regReading.getValue(),
        reg_date: regDate.getValue() || null,
        warranty: warranty.getValue(),
        note: note.getValue(),
        unit_override: unitO.getValue() || null,
        interval_override: intervalO.getValue(),
        warn_override: warnO.getValue(),
        norm_override: normO.getValue(),
      };
    }
    const initial = collect();

    async function save(m) {
      fGarage.setError(garageNo.getValue() ? '' : 'Укажите гаражный номер');
      if (!garageNo.getValue()) {
        garageNo.focus();
        return;
      }
      if (badNum(fReg, regReading, false) || badNum(fInterval, intervalO, false)
        || badNum(fWarn, warnO, false) || badNum(fNorm, normO, false)) return;
      const data = collect();
      let saved;
      if (editing) {
        const changed = diff(initial, data);
        if (!Object.keys(changed).length) {
          m.close(false);
          return;
        }
        saved = await api.withConfirm((force) => api.put(`/machines/${mv.id}`, { ...changed, force }),
          { title: 'Подтвердите изменение', okLabel: 'Всё равно сохранить' });
      } else {
        saved = await api.post('/machines', data);
      }
      if (saved === null) return;
      ui.toast(editing ? `Машина ${saved.garage_no} сохранена` : `Машина ${saved.garage_no} добавлена`);
      m.close(true);
    }

    const m = ui.modal({
      title: editing ? `Машина ${mv.garage_no}` : 'Новая машина',
      width: 780,
      body: [
        h('div.form-grid-3', null,
          fGarage,
          ui.field('Модель', model, { hint: 'по ней программа подбирает интервал ТО' }),
          ui.field('Тип машины', mtype, { hint: 'от типа зависит учёт: км или моточасы' }),
          ui.field('Госномер', plate),
          ui.field('VIN', vin),
          ui.field('Участок', site),
          ui.field('Статус', status, { hint: 'списанная машина уходит с главного экрана, история остаётся' }),
          fReg,
          ui.field('Дата этого показания', regDate, { hint: 'можно не указывать' }),
          ui.field('Гарантийный срок', warranty),
          ui.field('Примечание', note, { cls: 'span-2' })),
        h('p.dlg-note.dlg-gap', null, 'Фото машины добавляются в её карточке.'),
        h('h3.dlg-sub', null, 'Свои параметры ТО'),
        h('p.dlg-note', null, 'Обычно заполнять не нужно: пустое поле — программа считает сама, по модели и типу машины.'),
        h('div.form-grid-4', null, fUnit, fInterval, fWarn, fNorm),
      ],
      buttons: [
        { label: 'Отмена', value: false },
        { label: editing ? 'Сохранить' : 'Добавить машину', kind: 'primary', primary: true, onClick: save },
      ],
    });
    updateAutoHints();
    return (await m.result) === true;
  });
}

// ───────────────────────────── «Накинуть за день» ─────────────────────────────

/** «Накинуть за день» (R10): начислить суточную норму машинам, которые были на линии. */
export function openDailyDialog(ctx) {
  return guarded(async () => {
    addStyles();
    let data = await api.get('/daily/preview');
    const today = data.to || ui.today();
    const first = { gap: data.gap_days, last: data.last_auto_date, from: data.suggested_from };
    if (first.from && first.from < today) data = await api.get('/daily/preview', { from: first.from, to: today });

    const norms = new Map(); // нормы, исправленные человеком: id машины → значение поля
    const unchecked = new Set(); // машины, с которых человек снял галочку
    let changed = false; // что-то уже сохранено (отмена периода)
    let seq = 0;
    let tbl = null;
    let addCells = new Map();
    let m = null;

    const fromInput = ui.dateInput({ value: data.from, max: today, onChange: reload });
    const toInput = ui.dateInput({ value: data.to, max: today, onChange: reload });
    fromInput.setAttribute('data-no-enter', '');
    toInput.setAttribute('data-no-enter', '');
    const tableBox = h('div');
    const total = h('div.dlg-total', { 'aria-live': 'polite' });
    const headCheck = h('input', { type: 'checkbox', title: 'Выбрать все / снять все', 'aria-label': 'Выбрать все машины' });

    const eligible = (r) => r.days_to_add > 0;
    const isChecked = (r) => eligible(r) && !unchecked.has(r.machine_id);
    const normOf = (r) => {
      const v = norms.get(r.machine_id);
      return v === undefined || v === null ? r.norm : v;
    };
    const normOk = (r) => isNum(normOf(r)) && normOf(r) > 0;

    function addContent(r) {
      if (!eligible(r)) return h('span.muted', null, r.skip_reason || 'нечего начислять');
      if (!isChecked(r)) return h('span.muted', null, 'не выбрана');
      if (!normOk(r)) return h('span.text-red', null, 'проверьте норму');
      return h('span.strong', null, `+${ui.fmtReading(roundUnit(normOf(r) * r.days_to_add, r.unit), r.unit)}`);
    }

    function refreshRow(r) {
      const cell = addCells.get(r.machine_id);
      if (cell) ui.mount(cell, addContent(r));
    }

    function setApplyEnabled(on) {
      if (m) m.buttons[m.buttons.length - 1].disabled = !on;
    }

    function updateTotal() {
      if (!data) {
        ui.clear(total);
        setApplyEnabled(false);
        return;
      }
      const can = data.rows.filter(eligible);
      const sel = can.filter(isChecked);
      headCheck.disabled = !can.length;
      headCheck.checked = can.length > 0 && sel.length === can.length;
      headCheck.indeterminate = sel.length > 0 && sel.length < can.length;
      const skipped = data.rows.length - can.length;
      const parts = [];
      if (!data.rows.length) parts.push(h('span.muted', null, 'В парке нет машин.'));
      else if (!can.length) parts.push(h('span', null, 'За этот период начислять нечего: уже накинуто, показания внесены сегодня или машины не на линии.'));
      else parts.push(h('span.strong', null, `Будет добавлено ${sel.length} ${ui.plural(sel.length, 'машине', 'машинам', 'машинам')}`));
      if (can.length && skipped) {
        parts.push(h('span.muted', null, ` · не начисляется: ${skipped} (причина — в последней колонке)`));
      }
      ui.mount(total, parts);
      setApplyEnabled(sel.length > 0);
    }

    function draw() {
      addCells = new Map();
      tbl = ui.table({
        inputNav: true,
        maxHeight: '44vh',
        empty: 'В парке нет машин',
        rowClass: (r) => (eligible(r) ? '' : 'row-muted'),
        columns: [
          {
            title: headCheck, width: 36, sortable: false, cls: 'ta-center', headCls: 'ta-center',
            render: (r) => h('input', {
              type: 'checkbox',
              checked: isChecked(r),
              disabled: !eligible(r),
              'aria-label': `Накинуть машине ${r.garage_no}`,
              onchange: (e) => {
                if (e.target.checked) unchecked.delete(r.machine_id);
                else unchecked.add(r.machine_id);
                refreshRow(r);
                updateTotal();
              },
            }),
          },
          { key: 'garage_no', title: 'Гар. №', cls: 'strong nowrap' },
          { key: 'model', title: 'Модель', render: (r) => h('div.dlg-clip', { title: r.model || null }, r.model || '') },
          { key: 'site', title: 'Участок', render: (r) => h('div.dlg-clip', { title: r.site || null }, r.site || '') },
          {
            key: 'state', title: 'Состояние', sortValue: (r) => STATE_ORDER[r.state],
            render: (r) => ui.statusChip(r.state, r.state_label),
          },
          {
            key: 'reading', title: 'Показание', align: 'right',
            render: (r) => ui.fmtReading(r.reading, r.unit, r.reading_estimated),
          },
          {
            key: 'norm', title: 'Норма в сутки', input: true, sortable: false,
            render: (r) => {
              if (!eligible(r)) return h('span.muted', null, hasValue(r.norm) ? `${ui.fmtReading(r.norm, r.unit)}` : '—');
              const caption = h('span.muted.small', null, '');
              const setCaption = () => {
                caption.textContent = `${r.unit} · ${norms.has(r.machine_id) ? 'вручную' : (NORM_SOURCE[r.norm_source] || '')}`;
              };
              const input = ui.numInput({
                value: norms.has(r.machine_id) ? norms.get(r.machine_id) : r.norm,
                decimals: 1,
                ariaLabel: `Норма в сутки, машина ${r.garage_no}`,
                onInput: (v) => {
                  if (v === null) norms.delete(r.machine_id); // пусто — берётся норма программы
                  else norms.set(r.machine_id, v);
                  setCaption();
                  refreshRow(r);
                },
              });
              input.setAttribute('data-no-enter', '');
              setCaption();
              return h('div.dlg-norm', null, input, caption);
            },
          },
          { key: 'days_to_add', title: 'Дней', align: 'right', render: (r) => (eligible(r) ? r.days_to_add : '') },
          {
            key: 'add', title: 'Будет добавлено', align: 'right', sortable: false, cls: 'dlg-wrap',
            render: (r) => {
              const cell = h('span', null, addContent(r));
              addCells.set(r.machine_id, cell);
              return cell;
            },
          },
        ],
        rows: data.rows,
      });
      ui.mount(tableBox, tbl);
      updateTotal();
    }

    headCheck.addEventListener('change', () => {
      if (!data) return;
      for (const r of data.rows) {
        if (!eligible(r)) continue;
        if (headCheck.checked) unchecked.delete(r.machine_id);
        else unchecked.add(r.machine_id);
      }
      if (tbl) tbl.update();
      updateTotal();
    });

    async function reload() {
      seq += 1;
      const my = seq;
      const f = fromInput.getValue();
      const t = toInput.getValue();
      if (m) m.setError('');
      if (!f || !t) {
        data = null;
        ui.mount(tableBox, ui.notice('Укажите период: с какого по какое число накинуть.', 'warn'));
        updateTotal();
        return;
      }
      setApplyEnabled(false);
      ui.mount(tableBox, ui.loading('Считаю…'));
      try {
        const d = await api.get('/daily/preview', { from: f, to: t });
        if (my !== seq) return;
        data = d;
        draw();
      } catch (err) {
        if (my !== seq) return;
        data = null;
        ui.mount(tableBox, ui.errorState(err, reload, { title: 'Не получилось посчитать' }));
        updateTotal();
      }
    }

    async function apply(mm) {
      if (!data) return;
      const items = [];
      for (const r of data.rows) {
        if (!isChecked(r)) continue;
        if (!normOk(r)) {
          mm.setError(`${r.garage_no}: норма должна быть числом больше нуля`);
          return;
        }
        items.push({ machine_id: r.machine_id, norm: normOf(r) });
      }
      if (!items.length) {
        mm.setError('Не выбрано ни одной машины');
        return;
      }
      const res = await api.post('/daily/apply', { from: data.from, to: data.to, items });
      const n = res.machines || 0;
      let text = n
        ? `Накинуто ${n} ${ui.plural(n, 'машине', 'машинам', 'машинам')} (записей: ${res.created})`
        : 'Ничего не добавлено: за эти дни уже накинуто';
      if (res.skipped) text += `. Пропущено: ${res.skipped}`;
      ui.toast(text, n ? 'ok' : 'info');
      mm.close(true);
    }

    async function undo(mm) {
      const f = fromInput.getValue();
      const t = toInput.getValue();
      if (!f || !t) {
        mm.setError('Укажите период, за который отменить накинутое');
        return;
      }
      const period = f === t ? ui.fmtDate(f) : `${ui.fmtDate(f)} – ${ui.fmtDate(t)}`;
      const ok = await ui.confirm(`Отменить накинутое за ${period}?`, {
        danger: true,
        title: 'Отменить накинутое',
        details: 'Расчётные добавки всех машин за эти дни будут удалены. Показания, введённые со счётчика, останутся.',
        okLabel: 'Отменить накинутое',
        cancelLabel: 'Не отменять',
      });
      if (!ok) return;
      const res = await api.post('/daily/undo', { from: f, to: t });
      if (res.deleted) {
        changed = true;
        ui.toast(`Накинутое отменено. Удалено записей: ${res.deleted}`);
      } else {
        ui.toast('За этот период ничего не было накинуто', 'info');
      }
      mm.setBusy(false); // кнопки свободны уже сейчас: пересчёт ниже сам включит или выключит «Накинуть»
      await reload();
    }

    const notes = [];
    if (first.from && first.from < today && hasValue(first.gap)) {
      notes.push(`не накидывали ${first.gap} дн. — период подставлен с ${ui.fmtDate(first.from)}`);
    } else if (first.last) {
      notes.push(`последний раз накидывали за ${ui.fmtDate(first.last)}`);
    }
    const vahta = !first.last || (hasValue(first.gap) && first.gap > 7);

    m = ui.modal({
      title: 'Накинуть за день',
      wide: true,
      body: [
        h('p', null, 'Программа прибавит суточную норму машинам, которые в эти дни были на линии. Добавка расчётная (≈): как только введёте показание со счётчика, оно её заменит.'),
        vahta ? ui.notice('Если вы только заехали на вахту — сначала введите показания со счётчиков на главном экране, в колонке «Новое показание».', 'warn', {
          action: h('button.btn.btn-sm', {
            type: 'button',
            onclick: () => {
              m.close(changed);
              ctx.navigate('#/fleet');
            },
          }, ui.icon('road', 15), h('span', null, 'Ввести показания')),
        }) : null,
        h('div.form-row.dlg-period', null,
          ui.field('Накинуть с', fromInput),
          ui.field('по', toInput),
          notes.length ? h('div.dlg-period-note', null, notes.join(' · ')) : null),
        tableBox,
        total,
      ],
      buttons: [
        { label: 'Отменить накинутое за период', kind: 'ghost', icon: 'history', align: 'left', onClick: undo },
        { label: 'Закрыть', value: false },
        { label: 'Накинуть', kind: 'accent', icon: 'calendar-plus', primary: true, onClick: apply },
      ],
    });
    draw();
    return (await m.result) === true || changed;
  });
}
