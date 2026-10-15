// Экран «Машины» (SPEC 7.2 п.9): справочник техники — все машины, включая списанные (серым).
// Добавление и правка — общим диалогом ctx.dialogs.openMachineDialog; единицу учёта, интервал и порог ТО
// по каждой машине считает сервер (поля unit, interval, warn) — здесь они только показываются.

const RETIRED = 'Списана';

/** Откуда у машины параметры ТО: заданы вручную, по правилу для марки или общие из настроек. */
function paramsSource(m) {
  const own = [
    m.unit_override ? 'единица' : '',
    m.interval_override !== null && m.interval_override !== undefined ? 'интервал' : '',
    m.warn_override !== null && m.warn_override !== undefined ? 'порог' : '',
    m.norm_override !== null && m.norm_override !== undefined ? 'норма' : '',
  ].filter(Boolean);
  if (own.length) return `свои: ${own.join(', ')}`;
  if (m.rule_keyword) return `по марке ${m.rule_keyword}`;
  return 'общие';
}

export async function render(root, params, ctx) {
  const { ui, api, dialogs } = ctx;
  const { h } = ui;
  root.classList.add('page-fill');

  // при 1366 px фильтры, счётчик и обе кнопки — в одну строку; длинные модели обрезаются (целиком — в подсказке)
  ui.addStyle('machines-css', `
.mc-toolbar { gap: 8px; }
.mc-model { max-width: 170px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media print { .mc-model { max-width: none; white-space: normal; } }
`);
  const filter = { q: '', site: '', status: '', unit: '' };
  let rows = await api.get('/machines'); // ошибку первой загрузки покажет оболочка (текст + «Повторить»)

  async function reload() {
    try {
      rows = await api.get('/machines');
    } catch (err) {
      ui.toast(`Не получилось обновить список машин: ${err.message}`, 'error');
      return;
    }
    if (!root.isConnected) return;
    // первая машина добавлена или последняя удалена — оболочке нужно узнать, что база (не) пустая
    if (!!ctx.boot.first_run !== (rows.length === 0)) {
      await ctx.refresh();
      return;
    }
    siteFilter.setOptions(siteOptions());
    apply();
  }

  const add = async () => {
    if (await dialogs.openMachineDialog(ctx)) await reload();
  };
  const edit = async (m) => {
    if (await dialogs.openMachineDialog(ctx, m)) await reload();
  };

  async function remove(m) {
    const name = `${m.garage_no}${m.model ? ` ${m.model}` : ''}`;
    const first = await ui.confirm(`Удалить машину ${name} из справочника?`, {
      danger: true, title: 'Удалить машину?', okLabel: 'Удалить',
      details: m.status === RETIRED ? null
        : 'Если машина просто больше не работает — не удаляйте её, а поставьте статус «Списана»: она уйдёт с главного экрана и из «Списка на ТО», а история сохранится.',
    });
    if (!first) return;
    try {
      await api.del(`/machines/${m.id}`);
    } catch (err) {
      if (!err.needsConfirm) throw err;
      // у машины есть записи, шины или узлы — сервер перечисляет, что удалится вместе с ней
      const second = await ui.confirm(err.message, {
        danger: true, title: 'Удалить вместе с историей?', details: err.warnings,
        okLabel: 'Удалить со всей историей', cancelLabel: 'Не удалять',
      });
      if (!second) return;
      await api.del(`/machines/${m.id}`, { force: 1 });
    }
    ui.toast(`Машина ${m.garage_no} удалена`);
    await reload();
  }

  function siteOptions() {
    const known = (ctx.boot.lists && ctx.boot.lists.sites) || [];
    const used = rows.map((m) => m.site).filter(Boolean);
    return [...new Set([...known, ...used])];
  }

  const onFilter = (key) => (v) => {
    filter[key] = v;
    apply();
  };
  const search = ui.textInput({
    placeholder: 'Поиск: гар. №, модель, госномер, VIN', ariaLabel: 'Поиск по машинам', width: 280,
    onInput: ui.debounce(onFilter('q'), 200),
  });
  const siteFilter = ui.select({ options: siteOptions(), placeholder: 'Все участки', ariaLabel: 'Участок', onChange: onFilter('site') });
  const statusFilter = ui.select({
    options: (ctx.boot.lists && ctx.boot.lists.machine_statuses) || ['В работе', 'На консервации', RETIRED],
    placeholder: 'Все статусы', ariaLabel: 'Статус машины', onChange: onFilter('status'),
  });
  const unitFilter = ui.select({
    options: [['км', 'По пробегу, км'], ['м/ч', 'По моточасам, м/ч']],
    placeholder: 'Любой учёт', ariaLabel: 'Единица учёта', onChange: onFilter('unit'),
  });
  const counter = h('span.toolbar-label');

  const emptyAll = ui.emptyState(
    'Впишите технику: гаражный №, модель, госномер, VIN, участок. Список из таблицы Excel можно загрузить в «Настройках», раздел «Данные».',
    { icon: 'truck', title: 'Машин пока нет', action: { label: 'Добавить машину', icon: 'plus', onClick: add } });
  const emptyFiltered = h('div.table-empty-text', null, 'Под выбранные условия машин нет. ',
    h('button.link', { type: 'button', onclick: () => {
      Object.assign(filter, { q: '', site: '', status: '', unit: '' });
      search.setValue('');
      siteFilter.setValue('');
      statusFilter.setValue('');
      unitFilter.setValue('');
      apply();
    } }, 'Показать все машины'));
  let emptyShown = null;

  const tbl = ui.table({
    fill: true,
    rows: [],
    empty: '',
    onRowClick: edit,
    rowClass: (m) => (m.status === RETIRED ? 'row-muted' : ''),
    columns: [
      { key: 'garage_no', title: 'Гар. №', width: 84,
        render: (m) => h('a.strong', { href: `#/machine/${m.id}`, title: 'Открыть карточку машины' }, m.garage_no) },
      { key: 'model', title: 'Модель', render: (m) => h('div.mc-model', { title: m.model }, m.model) },
      { key: 'mtype', title: 'Тип' },
      { key: 'plate', title: 'Госномер', cls: 'nowrap' },
      { key: 'vin', title: 'VIN', cls: 'nowrap' },
      { key: 'site', title: 'Участок' },
      { key: 'status', title: 'Статус', cls: 'nowrap',
        render: (m) => (m.status === 'В работе' ? m.status : ui.statusChip('grey', m.status)) },
      { key: 'unit', title: 'Учёт', align: 'center', width: 60, hint: 'В чём считается ТО этой машины: километры или моточасы' },
      { key: 'interval', title: 'Интервал ТО', align: 'right', hint: 'Через сколько после последнего ТО делать следующее',
        render: (m) => ui.fmtReading(m.interval, m.unit) },
      { key: 'warn', title: 'Порог «скоро»', align: 'right', hint: 'За сколько до срока машина попадает в «Скоро ТО»',
        render: (m) => ui.fmtReading(m.warn, m.unit) },
      { key: 'params', title: 'Параметры ТО', sortValue: paramsSource,
        hint: 'Откуда взяты единица, интервал и порог: заданы у машины, по марке (правила в настройках) или общие',
        cls: 'nowrap', render: (m) => paramsSource(m) },
      { key: 'reading', title: 'Показание', align: 'right',
        hint: 'Текущее показание счётчика; «≈» — расчётное (накинуто за дни без ввода)',
        render: (m) => (m.reading === null || m.reading === undefined ? h('span.muted', null, 'нет')
          : ui.fmtReading(m.reading, m.unit, m.reading_estimated)) },
      { key: 'note', title: 'Примечание', cls: 'ellipsis',
        sortValue: (m) => noteText(m),
        render: (m) => h('span', { title: noteText(m) }, noteText(m)) },
      { title: '', cls: 'cell-actions no-print', headCls: 'no-print',
        render: (m) => h('div.row-actions', null,
          ui.iconButton('pencil', 'Изменить машину', () => edit(m), { small: true }),
          ui.iconButton('trash', 'Удалить машину', () => remove(m), { small: true })) },
    ],
  });

  function noteText(m) {
    return [m.warranty ? `гарантия: ${m.warranty}` : '', m.note].filter(Boolean).join('; ');
  }

  function apply() {
    const q = filter.q.toLowerCase();
    const shown = rows.filter((m) => (!filter.site || m.site === filter.site)
      && (!filter.status || m.status === filter.status)
      && (!filter.unit || m.unit === filter.unit)
      && (!q || `${m.garage_no} ${m.model} ${m.mtype} ${m.plate} ${m.vin} ${m.site} ${m.warranty} ${m.note}`.toLowerCase().includes(q)));
    const empty = rows.length ? emptyFiltered : emptyAll;
    if (empty !== emptyShown) {
      emptyShown = empty;
      tbl.setEmpty(empty);
    }
    tbl.setRows(shown);
    const retired = rows.filter((m) => m.status === RETIRED).length;
    counter.textContent = `${shown.length} из ${rows.length}${retired ? ` (списано: ${retired})` : ''}`;
  }

  root.append(
    h('div.toolbar.mc-toolbar', null,
      search, siteFilter, statusFilter, unitFilter, counter,
      h('div.spacer'),
      ui.button('Добавить машину', { kind: 'primary', icon: 'plus', onClick: add }),
      ui.button('Печать', { icon: 'printer', onClick: () => ui.printPage(`Машины на ${ui.fmtDate(ui.today())}`, { landscape: true }) })),
    tbl,
  );
  apply();
}
