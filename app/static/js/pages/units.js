// Экран «Узлы» (SPEC 7.2 п.8): ресурс и износ узлов и агрегатов по всему парку.
// Наработку, остаток, износ и статус считает сервер от текущего показания машины (SPEC 4.7); здесь только показ и ввод.
// Колонки таблицы, форму и удаление узла использует ещё и карточка машины (card.js).

const RETIRED = 'Списана';

// порядок статусов для сортировки: сначала те, что требуют действий
const STATUS_ORDER = { red: 0, yellow: 1, check: 2, green: 3, none: 4 };

// плитки-счётчики над таблицей (они же — фильтр по статусу)
const STATUS_TILES = [
  { key: 'red', label: 'Скоро замена или ремонт', kind: 'red' },
  { key: 'yellow', label: 'Контроль', kind: 'amber' },
  { key: 'green', label: 'Норма', kind: 'green' },
  { key: 'check', label: 'Проверить показание установки', kind: 'grey',
    hint: 'Показание при установке больше текущего показания машины — проверьте, что введено' },
  { key: 'none', label: 'Не хватает данных', kind: 'grey', hint: 'Не указан ресурс или показание при установке — статус посчитать нельзя' },
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

/** Почему у узла нет статуса (подпись вместо чипа). */
function whyNoStatus(u) {
  if (!u.resource) return 'не указан ресурс';
  if (isEmpty(u.install_reading)) return 'не указано показание при установке';
  if (isEmpty(u.run)) return 'у машины нет показаний';
  return '';
}

/**
 * Колонки таблицы узлов. withMachine: false — без колонок машины (в карточке машины).
 * onEdit(u), onRemove(u) — действия в строке (без них колонки действий нет).
 */
export function unitColumns(ctx, { withMachine = true, onEdit, onRemove } = {}) {
  const { ui } = ctx;
  const { h } = ui;
  const amount = (v, u) => (isEmpty(v) ? '' : ui.fmtReading(v, u.unit_of_measure));
  // длинные шапки («Показание при установке») переносятся на две строки: иначе они съедают ширину,
  // а название узла и статус сжимаются в столбик по слову
  ui.addStyle('units-css', '.table th.th-wrap { white-space: normal; line-height: 1.2; }');
  const wrap = 'th-wrap';
  return [
    { key: 'garage_no', title: 'Гар. №', width: 84, hidden: !withMachine,
      render: (u) => h('a.strong', { href: `#/machine/${u.machine_id}`, title: 'Открыть карточку машины' }, u.garage_no) },
    { key: 'model', title: 'Модель', hidden: !withMachine, cls: 'ellipsis', render: (u) => h('span', { title: u.model }, u.model) },
    { key: 'unit', title: 'Узел' },
    { key: 'resource', title: 'Ресурс / гарантия', align: 'right', headCls: wrap, render: (u) => amount(u.resource, u) },
    { key: 'install_reading', title: 'Показание при установке', align: 'right', headCls: wrap,
      hint: 'Показание счётчика машины в момент установки узла (0 — узел с завода)',
      render: (u) => amount(u.install_reading, u) },
    { key: 'run', title: 'Наработка узла', align: 'right', headCls: wrap,
      hint: 'Считается сама: текущее показание машины минус показание при установке',
      cls: (u) => (!isEmpty(u.run) && u.run < 0 ? 'text-red' : null),
      render: (u) => amount(u.run, u) },
    { key: 'left', title: 'Остаток', align: 'right',
      cls: (u) => ['strong', u.status !== 'check' && !isEmpty(u.left) && u.left <= 0 && 'text-red'],
      render: (u) => (u.status === 'check' ? '' : amount(u.left, u)) },
    { key: 'wear', title: 'Износ', align: 'right', width: 70, render: (u) => (u.status === 'check' ? '' : ui.fmtPct(u.wear)) },
    { key: 'status', title: 'Статус', sortValue: (u) => STATUS_ORDER[u.status] ?? 9,
      render: (u) => (u.status_label ? ui.statusChip(u.status, u.status_label) : h('span.muted.small', null, whyNoStatus(u))) },
    { key: 'note', title: 'Рекомендация / запчасти', headCls: wrap },
    { title: '', cls: 'cell-actions no-print', headCls: 'no-print', hidden: !onEdit && !onRemove,
      render: (u) => h('div.row-actions', null,
        onEdit ? ui.iconButton('pencil', 'Изменить узел', () => onEdit(u), { small: true }) : null,
        onRemove ? ui.iconButton('trash', 'Удалить узел', () => onRemove(u), { small: true }) : null) },
  ];
}

/** Удалить узел с вопросом. Возвращает true, если удалён. */
export async function removeUnit(ctx, unit) {
  const { ui, api } = ctx;
  const ok = await ui.confirm(`Удалить узел «${unit.unit}» (машина ${unit.garage_no}) из учёта?`,
    { danger: true, title: 'Удалить узел?', okLabel: 'Удалить' });
  if (!ok) return false;
  await api.del(`/units/${unit.id}`);
  ui.toast('Узел удалён');
  return true;
}

/**
 * Форма узла: новый (можно сразу указать машину — machineId) или правка (unit — строка из /api/units).
 * machines — список из /api/machines, если он уже загружен. Возвращает Promise<boolean> (true — сохранено).
 */
export async function openUnitDialog(ctx, { unit = null, machineId = null, machines = null } = {}) {
  const { ui, api } = ctx;
  const { h } = ui;
  const lists = (ctx.boot && ctx.boot.lists) || {};
  const all = machines || await api.get('/machines');
  const currentId = unit ? unit.machine_id : machineId;
  // списанные машины в выбор не попадают — кроме той, на которой узел уже стоит
  const choice = all.filter((m) => m.status !== RETIRED || sameId(m.id, currentId));
  const machineOf = (id) => choice.find((m) => sameId(m.id, id)) || null;

  const machine = ui.combo({
    strict: true, options: choice.map(machineOption), value: machineOf(currentId) ? currentId : null,
    placeholder: 'Гаражный № или модель', emptyText: 'Такой машины нет',
    autofocus: !machineOf(currentId), onChange: showMachineInfo,
  });
  const name = ui.combo({
    options: lists.unit_names || [], value: unit ? unit.unit : '',
    placeholder: 'выберите из списка или впишите', autofocus: !!machineOf(currentId),
  });
  const resource = ui.numInput({ value: unit ? unit.resource : null, decimals: 1 });
  const installReading = ui.numInput({ value: unit ? unit.install_reading : null, decimals: 1 });
  const note = ui.textArea({ value: unit && unit.note, rows: 2, maxLength: 500 });

  // единица (км или м/ч) в подписях полей — по выбранной машине
  const unitMarks = [h('span.muted'), h('span.muted')];
  const fMachine = ui.field('Машина', machine, { required: true });
  const fName = ui.field('Узел', name, { required: true });
  const fResource = ui.field(['Ресурс / гарантия', unitMarks[0]], resource, { hint: 'сколько узел должен отработать' });
  const fInstall = ui.field(['Показание машины при установке', unitMarks[1]], installReading);
  const numFields = [[fResource, resource], [fInstall, installReading]];

  function showMachineInfo() {
    const m = machineOf(machine.getValue());
    for (const mark of unitMarks) mark.textContent = m ? `, ${m.unit}` : '';
    const base = '0 — узел стоит с завода. Наработку узла программа считает сама.';
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
    fName.setError(name.getValue() ? '' : 'Укажите узел');
    if (!name.getValue()) ok = false;
    for (const [f, input] of numFields) {
      f.setError(input.isValid() ? '' : 'Введите число, например 6 000');
      if (!input.isValid()) ok = false;
    }
    if (!ok) return;
    const body = {
      machine_id: mid,
      unit: name.getValue(),
      resource: resource.getValue(),
      install_reading: installReading.getValue(),
      note: note.getValue(),
    };
    if (unit) await api.put(`/units/${unit.id}`, body);
    else await api.post('/units', body);
    ui.toast(unit ? 'Узел сохранён' : 'Узел добавлен');
    dlg.close(true);
  }

  const dlg = ui.modal({
    title: unit ? `Узел «${unit.unit}»` : 'Новый узел',
    width: 640,
    body: h('div.form-grid', null,
      h('div.span-all', null, fMachine),
      h('div.span-all', null, fName),
      fResource,
      fInstall,
      h('div.span-all', null, ui.field('Рекомендация / запчасти / стоимость', note))),
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
  let retiredLoaded = false; // сейчас загружены узлы одной списанной машины (в общий список сервер их не отдаёт)

  const machineOf = (id) => machines.find((m) => sameId(m.id, id)) || null;

  async function load() {
    machines = await api.get('/machines');
    const chosen = machineOf(filter.machineId);
    if (!chosen) filter.machineId = null;
    retiredLoaded = !!chosen && chosen.status === RETIRED;
    rows = await api.get('/units', retiredLoaded ? { machine_id: chosen.id } : null);
  }

  await load(); // ошибку первой загрузки покажет оболочка (текст + «Повторить»)

  if (!machines.length) {
    root.append(ui.emptyState('Узлы записываются на машины. Сначала добавьте технику в справочник «Машины».', {
      icon: 'engine', title: 'Сначала добавьте машины',
      action: { label: 'Открыть «Машины»', onClick: () => ctx.navigate('#/machines') },
    }));
    return;
  }

  async function reload() {
    try {
      await load();
    } catch (err) {
      ui.toast(`Не получилось обновить список узлов: ${err.message}`, 'error');
      return;
    }
    if (!root.isConnected) return;
    machineFilter.setOptions(machines.map(machineOption));
    machineFilter.setValue(filter.machineId);
    apply();
  }

  const add = async () => {
    if (await openUnitDialog(ctx, { machineId: filter.machineId, machines })) await reload();
  };
  const edit = async (u) => {
    if (await openUnitDialog(ctx, { unit: u, machines })) await reload();
  };
  const remove = async (u) => {
    if (await removeUnit(ctx, u)) await reload();
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
    placeholder: 'Поиск: узел, примечание', ariaLabel: 'Поиск по узлам', width: 240,
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
  const tileAll = ui.kpi({ label: 'Всего узлов', value: 0, kind: 'blue', onClick: () => setStatus('') });
  const tiles = STATUS_TILES.map((s) => ({
    key: s.key,
    el: ui.kpi({ label: s.label, value: 0, kind: s.kind, hint: s.hint, onClick: () => setStatus(s.key) }),
  }));

  const emptyAll = ui.emptyState(
    'Здесь ведётся учёт узлов и агрегатов: ресурс или гарантия и показание машины при установке. Наработку, остаток и статус программа считает сама.',
    { icon: 'engine', title: 'Узлов пока нет', action: { label: 'Добавить узел', icon: 'plus', onClick: add } });
  const emptyFiltered = h('div.table-empty-text', null, 'Под выбранные условия узлов нет. ',
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
    } }, 'Показать все узлы'));
  let emptyShown = null;

  const tbl = ui.table({
    fill: true,
    columns: unitColumns(ctx, { onEdit: edit, onRemove: remove }),
    rows: [],
    onRowClick: edit,
    empty: '',
  });

  function apply() {
    const q = filter.q.toLowerCase();
    const base = rows.filter((u) => (!filter.machineId || sameId(u.machine_id, filter.machineId))
      && (!q || `${u.garage_no} ${u.model} ${u.unit} ${u.note}`.toLowerCase().includes(q)));
    tileAll.setValue(base.length);
    tileAll.setActive(!filter.status);
    for (const tile of tiles) {
      tile.el.setValue(base.filter((u) => u.status === tile.key).length);
      tile.el.setActive(filter.status === tile.key);
    }
    const shown = filter.status ? base.filter((u) => u.status === filter.status) : base;
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
      ui.button('Добавить узел', { kind: 'primary', icon: 'plus', onClick: add }),
      ui.button('Печать', { icon: 'printer', onClick: () => ui.printPage(printTitle(), { landscape: true }) })),
    tbl,
  );
  apply();

  // заголовок листа: что именно напечатано (фильтры на бумаге не видны)
  function printTitle() {
    const m = machineOf(filter.machineId);
    const tile = STATUS_TILES.find((s) => s.key === filter.status);
    return ['Узлы', m ? `машина ${m.garage_no}` : '', tile ? tile.label.toLowerCase() : '', `на ${ui.fmtDate(ui.today())}`]
      .filter(Boolean).join(' — ');
  }
}
