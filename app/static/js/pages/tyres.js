// Экран «Шины» (SPEC 7.2 п.8): учёт шин по всему парку — ресурс, пробег, остаток, износ, статус.
// Пробег, остаток, износ и статус считает сервер (SPEC 4.7); здесь только показ и ввод.
// Колонки таблицы, форму и удаление шины использует ещё и карточка машины (card.js).

const RETIRED = 'Списана';

// порядок статусов для сортировки: сначала те, что требуют действий
const STATUS_ORDER = { red: 0, yellow: 1, green: 2, none: 3 };

// плитки-счётчики над таблицей (они же — фильтр по статусу)
const STATUS_TILES = [
  { key: 'red', label: 'Менять', kind: 'red' },
  { key: 'yellow', label: 'Скоро менять', kind: 'amber' },
  { key: 'green', label: 'Норма', kind: 'green' },
  { key: 'none', label: 'Не хватает данных', kind: 'grey', hint: 'Не указан ресурс или пробег — статус посчитать нельзя' },
];

function isEmpty(v) {
  return v === null || v === undefined || v === '';
}

function sameId(a, b) {
  return !isEmpty(a) && !isEmpty(b) && String(a) === String(b);
}

function machineOption(m) {
  return {
    value: m.id,
    label: m.model ? `${m.garage_no} · ${m.model}` : String(m.garage_no),
    hint: [m.plate, m.status === RETIRED ? 'списана' : ''].filter(Boolean).join(' · '),
  };
}

/** Короткое название шины для вопросов и сообщений: «№ A-100, 12.00R20, Перед. левая». */
function tyreName(t) {
  return [t.tyre_no ? `№ ${t.tyre_no}` : '', t.ttype, t.position].filter(Boolean).join(', ') || 'без номера';
}

/** Почему у шины нет статуса (подпись вместо чипа). */
function whyNoStatus(t) {
  if (!t.resource) return 'не указан ресурс';
  if (isEmpty(t.run_eff)) return isEmpty(t.install_reading) ? 'не указан пробег' : 'у машины нет показаний';
  return '';
}

/**
 * Колонки таблицы шин. withMachine: false — без колонок машины (в карточке машины).
 * onEdit(t), onRemove(t) — действия в строке (без них колонки действий нет).
 */
export function tyreColumns(ctx, { withMachine = true, onEdit, onRemove } = {}) {
  const { ui } = ctx;
  const { h } = ui;
  // Примечания из таблицы заказчика бывают длинными (список серийных номеров на 8 строк) — на экране не выше
  // трёх строк, целиком во всплывающей подсказке и на печати. Колонки плотнее — чтобы при 1366 px не было прокрутки вбок.
  ui.addStyle('tyres-css', `
.tyre-table .table th, .tyre-table .table td { padding-left: 7px; padding-right: 7px; }
.table td.tyre-note-cell { min-width: 150px; }
.tyre-note { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; overflow-wrap: anywhere; }
@media print {
  .tyre-note { display: block; -webkit-line-clamp: none; overflow: visible; }
  .table td.tyre-note-cell { min-width: 0; }
}
`);
  const amount = (v, t) => (isEmpty(v) ? '' : ui.fmtReading(v, t.unit));
  return [
    { key: 'garage_no', title: 'Гар. №', width: 84, hidden: !withMachine,
      render: (t) => h('a.strong', { href: `#/machine/${t.machine_id}`, title: 'Открыть карточку машины' }, t.garage_no) },
    { key: 'model', title: 'Модель', hidden: !withMachine, cls: 'ellipsis', render: (t) => h('span', { title: t.model }, t.model) },
    { key: 'tyre_no', title: '№ шины' },
    { key: 'ttype', title: 'Тип / размер' },
    { key: 'position', title: 'Позиция' },
    { key: 'install_date', title: 'Установлена', width: 96, render: (t) => ui.fmtDate(t.install_date) },
    { key: 'resource', title: 'Ресурс', align: 'right', render: (t) => amount(t.resource, t) },
    { key: 'run_eff', title: 'Пробег', align: 'right',
      hint: 'Считается сам от показания машины при установке; если оно не указано — берётся пробег, введённый вручную',
      render: (t) => amount(t.run_eff, t) },
    { key: 'left', title: 'Остаток', align: 'right',
      cls: (t) => ['strong', !isEmpty(t.left) && t.left <= 0 && 'text-red'],
      render: (t) => amount(t.left, t) },
    { key: 'wear', title: 'Износ', align: 'right', width: 70, render: (t) => ui.fmtPct(t.wear) },
    { key: 'status', title: 'Статус', sortValue: (t) => STATUS_ORDER[t.status] ?? 9,
      render: (t) => (t.status_label ? ui.statusChip(t.status, t.status_label) : h('span.muted.small', null, whyNoStatus(t))) },
    { key: 'note', title: 'Примечание', cls: 'tyre-note-cell',
      render: (t) => (t.note ? h('div.tyre-note', { title: t.note }, t.note) : '') },
    { title: '', cls: 'cell-actions no-print', headCls: 'no-print', hidden: !onEdit && !onRemove,
      render: (t) => h('div.row-actions', null,
        onEdit ? ui.iconButton('pencil', 'Изменить шину', () => onEdit(t), { small: true }) : null,
        onRemove ? ui.iconButton('trash', 'Удалить шину', () => onRemove(t), { small: true }) : null) },
  ];
}

/** Удалить шину с вопросом. Возвращает true, если удалена. */
export async function removeTyre(ctx, tyre) {
  const { ui, api } = ctx;
  const ok = await ui.confirm(`Удалить шину ${tyreName(tyre)} (машина ${tyre.garage_no})?`,
    { danger: true, title: 'Удалить шину?', okLabel: 'Удалить' });
  if (!ok) return false;
  await api.del(`/tyres/${tyre.id}`);
  ui.toast('Шина удалена');
  return true;
}

/**
 * Форма шины: новая (можно сразу указать машину — machineId) или правка (tyre — строка из /api/tyres).
 * machines — список из /api/machines, если он уже загружен. Возвращает Promise<boolean> (true — сохранено).
 */
export async function openTyreDialog(ctx, { tyre = null, machineId = null, machines = null } = {}) {
  const { ui, api } = ctx;
  const { h } = ui;
  const lists = (ctx.boot && ctx.boot.lists) || {};
  const all = machines || await api.get('/machines');
  const currentId = tyre ? tyre.machine_id : machineId;
  // списанные машины в выбор не попадают — кроме той, на которой шина уже стоит
  const choice = all.filter((m) => m.status !== RETIRED || sameId(m.id, currentId));
  const machineOf = (id) => choice.find((m) => sameId(m.id, id)) || null;

  const machine = ui.combo({
    strict: true, options: choice.map(machineOption), value: machineOf(currentId) ? currentId : null,
    placeholder: 'Гаражный № или модель', emptyText: 'Такой машины нет',
    autofocus: !machineOf(currentId), onChange: showMachineInfo,
  });
  const no = ui.textInput({ value: tyre && tyre.tyre_no, maxLength: 60, autofocus: !!machineOf(currentId) });
  const ttype = ui.combo({ options: lists.tyre_types || [], value: tyre ? tyre.ttype : '', placeholder: 'например, 12.00R20' });
  const position = ui.combo({ options: lists.positions || [], value: tyre ? tyre.position : '', placeholder: 'где стоит' });
  const installDate = ui.dateInput({ value: tyre ? tyre.install_date : '' });
  const resource = ui.numInput({ value: tyre ? tyre.resource : null, decimals: 1 });
  const installReading = ui.numInput({ value: tyre ? tyre.install_reading : null, decimals: 1 });
  const run = ui.numInput({ value: tyre ? tyre.run : null, decimals: 1 });
  const note = ui.textInput({ value: tyre && tyre.note, maxLength: 300 });

  // единица (км или м/ч) в подписях полей — по выбранной машине
  const unitMarks = [h('span.muted'), h('span.muted'), h('span.muted')];
  const fMachine = ui.field('Машина', machine, { required: true });
  const fNo = ui.field('№ шины', no);
  const fDate = ui.field('Дата установки', installDate);
  const fResource = ui.field(['Ресурс шины', unitMarks[0]], resource, { hint: 'сколько шина должна пройти' });
  const fInstall = ui.field(['Показание машины при установке', unitMarks[1]], installReading);
  const fRun = ui.field(['Пробег шины вручную', unitMarks[2]], run,
    { hint: 'Заполняйте, только если не знаете показание при установке. Если оно указано — это поле не используется.' });
  const numFields = [[fResource, resource], [fInstall, installReading], [fRun, run]];

  function showMachineInfo() {
    const m = machineOf(machine.getValue());
    for (const mark of unitMarks) mark.textContent = m ? `, ${m.unit}` : '';
    const base = 'Тогда пробег шины программа считает сама.';
    if (!m) fInstall.setHint(base);
    else if (isEmpty(m.reading)) fInstall.setHint(`${base} У машины пока нет показаний.`);
    else {
      fInstall.setHint([`${base} Сейчас на счётчике ${ui.fmtReading(m.reading, m.unit, m.reading_estimated)} — `,
        h('button.link', { type: 'button', onclick: () => {
          installReading.setValue(m.reading);
          fInstall.setError('');
        } }, 'подставить')]);
    }
  }
  showMachineInfo();

  async function save(dlg) {
    let ok = true;
    const mid = machine.getValue();
    fMachine.setError(mid ? '' : 'Выберите машину из списка');
    if (!mid) ok = false;
    const named = no.getValue() || ttype.getValue() || position.getValue();
    fNo.setError(named ? '' : 'Укажите номер шины, тип или позицию');
    if (!named) ok = false;
    fDate.setError(installDate.isValid() ? '' : 'Дата введена не полностью');
    if (!installDate.isValid()) ok = false;
    for (const [f, input] of numFields) {
      f.setError(input.isValid() ? '' : 'Введите число, например 80 000');
      if (!input.isValid()) ok = false;
    }
    if (!ok) return;
    const body = {
      machine_id: mid,
      tyre_no: no.getValue(),
      ttype: ttype.getValue(),
      position: position.getValue(),
      install_date: installDate.getValue() || null,
      resource: resource.getValue(),
      install_reading: installReading.getValue(),
      run: run.getValue(),
      note: note.getValue(),
    };
    if (tyre) await api.put(`/tyres/${tyre.id}`, body);
    else await api.post('/tyres', body);
    ui.toast(tyre ? 'Шина сохранена' : 'Шина добавлена');
    dlg.close(true);
  }

  const dlg = ui.modal({
    title: tyre ? `Шина ${tyreName(tyre)}` : 'Новая шина',
    width: 640,
    body: h('div.form-grid', null,
      h('div.span-all', null, fMachine),
      fNo,
      ui.field('Тип / размер', ttype),
      ui.field('Позиция', position),
      fDate,
      fResource,
      fInstall,
      h('div.span-all', null, fRun),
      h('div.span-all', null, ui.field('Примечание', note))),
    buttons: [
      { label: 'Отмена', value: false },
      { label: 'Сохранить', kind: 'primary', primary: true, onClick: save },
    ],
  });
  return dlg.result.then((v) => v === true);
}

export async function render(root, params, ctx) {
  const { ui, api } = ctx;
  const { h } = ui;
  root.classList.add('page-fill');

  const filter = { machineId: params.machine_id || null, status: '', q: '' };
  let machines = [];
  let rows = [];
  let retiredLoaded = false; // сейчас загружены шины одной списанной машины (в общий список сервер их не отдаёт)

  const machineOf = (id) => machines.find((m) => sameId(m.id, id)) || null;

  async function load() {
    machines = await api.get('/machines');
    const chosen = machineOf(filter.machineId);
    if (!chosen) filter.machineId = null;
    retiredLoaded = !!chosen && chosen.status === RETIRED;
    rows = await api.get('/tyres', retiredLoaded ? { machine_id: chosen.id } : null);
  }

  await load(); // ошибку первой загрузки покажет оболочка (текст + «Повторить»)

  if (!machines.length) {
    root.append(ui.emptyState('Шины записываются на машины. Сначала добавьте технику в справочник «Машины».', {
      icon: 'wheel', title: 'Сначала добавьте машины',
      action: { label: 'Открыть «Машины»', onClick: () => ctx.navigate('#/machines') },
    }));
    return;
  }

  async function reload() {
    try {
      await load();
    } catch (err) {
      ui.toast(`Не получилось обновить список шин: ${err.message}`, 'error');
      return;
    }
    if (!root.isConnected) return;
    machineFilter.setOptions(machines.map(machineOption));
    machineFilter.setValue(filter.machineId);
    apply();
  }

  const add = async () => {
    if (await openTyreDialog(ctx, { machineId: filter.machineId, machines })) await reload();
  };
  const edit = async (t) => {
    if (await openTyreDialog(ctx, { tyre: t, machines })) await reload();
  };
  const remove = async (t) => {
    if (await removeTyre(ctx, t)) await reload();
  };

  const machineFilter = ui.combo({
    strict: true, options: machines.map(machineOption), value: filter.machineId,
    placeholder: 'Все машины', ariaLabel: 'Фильтр по машине', emptyText: 'Такой машины нет', width: 260,
    onChange: (id) => {
      filter.machineId = id;
      const m = machineOf(id);
      if ((m && m.status === RETIRED) || retiredLoaded) reload();
      else apply();
    },
  });
  const search = ui.textInput({
    placeholder: 'Поиск: № шины, тип, позиция', ariaLabel: 'Поиск по шинам', width: 240,
    onInput: ui.debounce((v) => {
      filter.q = v;
      apply();
    }, 200),
  });
  const counter = h('span.toolbar-label');

  const setStatus = (key) => {
    filter.status = filter.status === key ? '' : key;
    apply();
  };
  const tileAll = ui.kpi({ label: 'Всего шин', value: 0, kind: 'blue', onClick: () => setStatus('') });
  const tiles = STATUS_TILES.map((s) => ({
    key: s.key,
    el: ui.kpi({ label: s.label, value: 0, kind: s.kind, hint: s.hint, onClick: () => setStatus(s.key) }),
  }));

  const emptyAll = ui.emptyState(
    'Здесь ведётся учёт шин: когда поставили, какой ресурс, сколько прошла. Остаток, износ и статус «менять» программа считает сама.',
    { icon: 'wheel', title: 'Шин пока нет', action: { label: 'Добавить шину', icon: 'plus', onClick: add } });
  const emptyFiltered = h('div.table-empty-text', null, 'Под выбранные условия шин нет. ',
    h('button.link', { type: 'button', onclick: () => {
      filter.status = '';
      filter.q = '';
      search.setValue('');
      if (filter.machineId) {
        machineFilter.setValue(null);
        filter.machineId = null;
        if (retiredLoaded) {
          reload();
          return;
        }
      }
      apply();
    } }, 'Показать все шины'));
  let emptyShown = null;

  const tbl = ui.table({
    fill: true,
    cls: 'tyre-table',
    columns: tyreColumns(ctx, { onEdit: edit, onRemove: remove }),
    rows: [],
    onRowClick: edit,
    empty: '',
  });

  function apply() {
    const q = filter.q.toLowerCase();
    const base = rows.filter((t) => (!filter.machineId || sameId(t.machine_id, filter.machineId))
      && (!q || `${t.garage_no} ${t.model} ${t.tyre_no} ${t.ttype} ${t.position} ${t.note}`.toLowerCase().includes(q)));
    tileAll.setValue(base.length);
    tileAll.setActive(!filter.status);
    for (const tile of tiles) {
      tile.el.setValue(base.filter((t) => t.status === tile.key).length);
      tile.el.setActive(filter.status === tile.key);
    }
    const shown = filter.status ? base.filter((t) => t.status === filter.status) : base;
    const empty = rows.length || filter.machineId ? emptyFiltered : emptyAll;
    if (empty !== emptyShown) {
      emptyShown = empty;
      tbl.setEmpty(empty);
    }
    tbl.setRows(shown);
    counter.textContent = `Показано ${shown.length} из ${rows.length}`;
  }

  root.append(
    h('div.kpis', null, tileAll, tiles.map((t) => t.el)),
    h('div.toolbar', null,
      machineFilter, search, counter,
      h('div.spacer'),
      ui.button('Добавить шину', { kind: 'primary', icon: 'plus', onClick: add }),
      ui.button('Печать', { icon: 'printer', onClick: () => ui.printPage(printTitle(), { landscape: true }) })),
    tbl,
  );
  apply();

  // заголовок листа: что именно напечатано (фильтры на бумаге не видны)
  function printTitle() {
    const m = machineOf(filter.machineId);
    const tile = STATUS_TILES.find((s) => s.key === filter.status);
    return ['Шины', m ? `машина ${m.garage_no}` : '', tile ? tile.label.toLowerCase() : '', `на ${ui.fmtDate(ui.today())}`]
      .filter(Boolean).join(' — ');
  }
}
