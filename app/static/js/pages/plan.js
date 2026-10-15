// Экран «Список на ТО» (бывший «План ТО и заказ»; SPEC 7.2 п.5, КОНЦЕПЦИЯ_v2 п.7; требование R6 из ТЗ: «список вывел, заказал масло и фильтры под эти машины»).
// Все расчёты приходят готовыми из GET /api/plan; здесь только показ, отметка «заказано» и печать.

const HORIZONS = [7, 14, 30, 60];
const HORIZON_KEY = 'to-uchet.plan.horizon';
const TAGS_SHOWN = 24; // сколько гаражных номеров показывать в блоке «нет данных», пока список свёрнут
const STATUS_ORDER = { overdue: 0, soon: 1, ok: 2 };

const STYLE = `
.plan-body { display: flex; flex-direction: column; gap: 24px; }
.plan-body.plan-busy { opacity: .6; }
.plan-body > .kpis { margin-bottom: 0; }
.plan-seg { display: inline-flex; gap: 4px; }
.plan-sub { display: block; margin-top: 2px; color: var(--text-muted); font-size: 12.5px; font-weight: 400; white-space: nowrap; }
.plan-sub.text-red { color: var(--red); font-weight: 600; }
.plan-chip { display: block; margin-top: 4px; }
.plan-reg { min-width: 210px; }
.plan-reg > div + div { margin-top: 2px; }
.plan-wrap { min-width: 150px; }
.plan-note { margin: 0 0 12px; color: var(--text-muted); }
.plan-noto-group + .plan-noto-group { margin-top: 16px; }
.plan-noto-title { font-weight: 600; }
.plan-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.plan-tag {
  padding: 1px 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
  font-size: 13px;
  text-decoration: none;
  white-space: nowrap;
}
.plan-tag:hover { border-color: var(--primary); }
.plan-more { margin-top: 8px; }
@media screen {
  .plan-tags.plan-collapsed .plan-tag:nth-child(n+${TAGS_SHOWN + 1}) { display: none; }
}
@media print {
  .plan-body { display: block; opacity: 1; }
  .plan-section { margin-top: 12pt; }
  .plan-section h2 { margin: 0 0 4pt; font-size: 12pt; }
  .plan-summary { margin: 0 0 6pt; }
  .plan-note { margin: 0 0 4pt; }
  .plan-reg, .plan-wrap { min-width: 0; }
  .plan-sub { white-space: normal; font-size: 8pt; }
  .plan-tags { display: block; margin-top: 2pt; }
  /* перенос строки — после запятой: иначе длинный список номеров уходит за край листа */
  .plan-tag { padding: 0; border: 0; white-space: normal; }
  .plan-tag::after { content: ", "; }
  .plan-tag:last-child::after { content: ""; }
  .plan-more { display: none; }
}
`;

function savedHorizon() {
  try {
    return Number(localStorage.getItem(HORIZON_KEY));
  } catch (err) {
    return 0;
  }
}

function saveHorizon(value) {
  try {
    localStorage.setItem(HORIZON_KEY, String(value));
  } catch (err) {
    // хранилище недоступно — горизонт просто не запомнится
  }
}

export async function render(root, params, ctx) {
  const { ui, api } = ctx;
  const { h } = ui;
  ui.addStyle('page-plan-style', STYLE);

  const days = (n) => `${n} ${ui.plural(n, 'день', 'дня', 'дней')}`;
  const machinesWord = (n) => `${n} ${ui.plural(n, 'машина', 'машины', 'машин')}`;

  let horizon = [Number(params.horizon), savedHorizon(), 30].find((v) => HORIZONS.includes(v));
  let data = null;
  let loadSeq = 0;

  // ── панель: горизонт и печать ──

  const horizonButtons = HORIZONS.map((n) => {
    const btn = ui.button(days(n), { onClick: () => setHorizon(n) });
    btn.dataset.horizon = n;
    return btn;
  });
  const printButton = ui.button('Печать', { icon: 'printer', onClick: printPlan, disabled: true });
  const body = h('div.plan-body');

  function paintHorizon() {
    for (const btn of horizonButtons) {
      const on = Number(btn.dataset.horizon) === horizon;
      btn.classList.toggle('btn-primary', on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
  }

  function setHorizon(n) {
    if (n === horizon && data) return undefined;
    horizon = n;
    saveHorizon(n);
    paintHorizon();
    return load();
  }

  function printPlan() {
    if (!data) return;
    ui.printPage(`Список на ТО на ${ui.fmtDate(data.date)} — ближайшие ${days(data.horizon_days)}`, { landscape: true });
  }

  // ── загрузка ──

  async function load() {
    loadSeq += 1;
    const seq = loadSeq;
    if (data) body.classList.add('plan-busy');
    else ui.mount(body, ui.loading());
    try {
      const res = await api.get('/plan', { horizon_days: horizon });
      if (seq !== loadSeq) return;
      data = res;
      draw();
    } catch (err) {
      if (seq !== loadSeq) return;
      data = null;
      printButton.disabled = true;
      ui.mount(body, ui.errorState(err, load));
    } finally {
      if (seq === loadSeq) body.classList.remove('plan-busy');
    }
  }

  // ── ячейки ──

  const cardLink = (m) => h('a', { href: `#/machine/${m.machine_id || m.id}`, title: 'Открыть карточку машины' }, m.garage_no);

  function plateVinCell(m) {
    if (!m.plate && !m.vin) return h('span.muted', null, '—');
    return [
      m.plate ? h('div.nowrap', null, m.plate) : null,
      m.vin ? h('div.plan-sub', { title: 'VIN' }, m.vin) : null,
    ];
  }

  function kindCell(m) {
    return [
      h('span.strong', null, m.next_to_kind || '—'),
      m.next_to_at !== null && m.next_to_at !== undefined
        ? h('span.plan-sub', null, `на ${ui.fmtReading(m.next_to_at, m.unit)}`) : null,
    ];
  }

  function remainingCell(m) {
    const overdue = m.to_status === 'overdue';
    return [
      h('span', { class: ['strong', overdue && 'text-red'] }, ui.fmtReading(m.remaining, m.unit)),
      overdue && m.missed > 1 ? h('span.plan-sub.text-red', null, `пропущено ТО: ${m.missed}`) : null,
      h('span.plan-chip', null, ui.statusChip(m.to_status, m.to_status_label)),
    ];
  }

  function rateHint(m) {
    if (m.rate === null || m.rate === undefined) return 'Средняя наработка неизвестна';
    const src = m.rate_source === 'fact'
      ? `по факту${m.rate_days ? ` за ${days(m.rate_days)}` : ''}` : 'по норме';
    return `Средняя наработка: ${ui.fmtReading(m.rate, m.unit)} в сутки (${src})`;
  }

  function forecastCell(m) {
    if (m.to_status === 'overdue') return h('span.strong.text-red', { title: rateHint(m) }, 'пора сейчас');
    if (!m.forecast_date) return h('span.muted', { title: rateHint(m) }, '—');
    return h('span', { title: rateHint(m) },
      h('span.nowrap', null, `≈ ${ui.fmtDate(m.forecast_date)}`),
      h('span.plan-sub', null, `через ${days(m.forecast_days)}`));
  }

  function regulationCell(m) {
    const reg = m.regulation;
    if (!reg) return h('span.muted', null, `В регламенте нет строки «${m.next_to_kind || 'ТО'}»`);
    const line = (label, text) => (text ? h('div', null, h('span.muted', null, `${label}: `), text) : null);
    const lines = [line('Масло', reg.oil), line('Фильтры', reg.filters), line('Работы', reg.works)].filter(Boolean);
    return lines.length ? h('div.plan-reg', null, lines) : h('span.muted', null, '—');
  }

  async function toDone(m) {
    const saved = await ctx.dialogs.openToDoneDialog(ctx, m.id);
    if (saved) await load();
  }

  // ── блоки экрана ──

  function summaryBlock(rows, noTo, orders) {
    const overdue = rows.filter((m) => m.to_status === 'overdue').length;
    const soon = rows.filter((m) => m.to_status === 'soon').length;
    const byForecast = rows.length - overdue - soon;
    const text = `К ТО в ближайшие ${days(data.horizon_days)}: ${machinesWord(rows.length)} — просрочено ${overdue}, скоро ${soon}, `
      + `по прогнозу ${byForecast}. Нет данных о последнем ТО: ${noTo.length}. Заявок на запчасти: ${orders.length}.`;
    return [
      h('div.kpis', null,
        ui.kpi({ label: 'ТО просрочено', value: overdue, kind: overdue ? 'red' : 'grey' }),
        ui.kpi({ label: 'Скоро ТО', value: soon, kind: soon ? 'amber' : 'grey' }),
        ui.kpi({ label: `По прогнозу за ${days(data.horizon_days)}`, value: byForecast, kind: 'blue',
          hint: 'Пока в норме, но по средней наработке ТО подойдёт в выбранный срок' }),
        ui.kpi({ label: 'Нет данных о ТО', value: noTo.length, kind: 'grey' }),
        ui.kpi({ label: 'Заявки на запчасти', value: orders.length, kind: orders.length ? 'blue' : 'grey' })),
      h('p.plan-summary.print-only', null, text),
    ];
  }

  function planTable(rows, noTo) {
    const emptyText = `В ближайшие ${days(data.horizon_days)} ни одной машине ТО не подходит.`
      + (noTo.length ? ` Машины без данных о последнем ТО (${noTo.length}) в план не попадают — см. ниже.` : '');
    return h('section.plan-section', null,
      h('h2', null, `Машины, которым пора на ТО — ${rows.length}`),
      ui.table({
        maxHeight: 'none',
        rows,
        onRowClick: (m) => ctx.navigate(`#/machine/${m.id}`),
        rowClass: (m) => (m.to_status === 'overdue' ? 'row-red' : m.to_status === 'soon' ? 'row-amber' : ''),
        empty: emptyText,
        columns: [
          { key: 'garage_no', title: 'Гар. №', cls: 'strong nowrap', render: cardLink },
          { key: 'model', title: 'Модель' },
          { key: 'plate', title: 'Госномер, VIN', render: plateVinCell },
          { key: 'site', title: 'Участок' },
          { key: 'next_to_kind', title: 'Вид ТО', render: kindCell },
          { key: 'remaining', title: 'Осталось', align: 'right', render: remainingCell,
            hint: 'Сколько осталось до следующего ТО; с минусом — на сколько просрочено' },
          { key: 'forecast_days', title: 'Прогноз', render: forecastCell,
            sortValue: (m) => (m.to_status === 'overdue' ? -1 : m.forecast_days),
            hint: 'Когда подойдёт ТО при средней наработке машины' },
          // короткая шапка: в ячейке и так подписано «Масло:», «Фильтры:», «Работы:»; длинная не давала таблице влезть в 1366 px
          { key: 'regulation', title: 'По регламенту', hint: 'Что нужно на это ТО: масло, фильтры, работы',
            sortable: false, render: regulationCell },
          { title: '', cls: 'cell-actions no-print', headCls: 'no-print', render: (m) => h('div.row-actions', null,
            ui.button('ТО сделано', { kind: 'accent', small: true, onClick: () => toDone(m),
              title: `Отметить, что ${m.next_to_kind || 'ТО'} на ${m.garage_no} сделано` })) },
        ],
      }));
  }

  function tagList(list) {
    const tags = h('div.plan-tags', { class: list.length > TAGS_SHOWN && 'plan-collapsed' },
      list.map((m) => h('a.plan-tag', {
        href: `#/machine/${m.id}`,
        title: [m.model, m.site].filter(Boolean).join(' · ') || null,
      }, m.garage_no)));
    if (list.length <= TAGS_SHOWN) return tags;
    const more = h('button.link.plan-more', { type: 'button' }, `Показать все ${list.length}`);
    more.addEventListener('click', () => {
      const collapsed = tags.classList.toggle('plan-collapsed');
      more.textContent = collapsed ? `Показать все ${list.length}` : 'Свернуть список';
    });
    return [tags, more];
  }

  function noToBlock(noTo) {
    if (!noTo.length) return null;
    const noToList = noTo.filter((m) => m.to_status !== 'no_reading');
    const noReading = noTo.filter((m) => m.to_status === 'no_reading');
    const group = (title, list) => (list.length
      ? h('div.plan-noto-group', null, h('div.plan-noto-title', null, `${title} — ${list.length}`), tagList(list)) : null);
    return h('section.plan-section.card', null,
      h('h2', null, `Нет данных о последнем ТО — ${machinesWord(noTo.length)}`),
      h('p.plan-note', null,
        'Без последнего ТО и показания счётчика программа не знает, когда следующее ТО, — этих машин в плане нет. ',
        'Внести последнее ТО можно на главном экране — в колонке «Последнее ТО» (кремовые поля), ',
        'показание счётчика — в колонке «Новое показание».'),
      h('p.no-print', null,
        h('a.btn.btn-primary', { href: '#/fleet' }, ui.icon('truck', 18), h('span', null, 'На главную'))),
      group('Не внесено последнее ТО', noToList),
      group('Нет показаний счётчика', noReading));
  }

  function materialsBlock(materials) {
    return h('section.plan-section', null,
      h('h2', null, 'Что заказать'),
      h('p.plan-note', null, 'Масло, фильтры и запчасти по регламенту — под машины из списка выше, по видам ТО и моделям.'),
      ui.table({
        maxHeight: 'none',
        rows: materials,
        empty: 'Заказывать под ТО пока нечего: в плане нет машин.',
        columns: [
          { key: 'to_kind', title: 'Вид ТО', cls: 'strong nowrap' },
          { key: 'model', title: 'Модель' },
          { key: 'count', title: 'Машин', align: 'right', cls: 'strong',
            footer: (list) => list.reduce((sum, r) => sum + (Number(r.count) || 0), 0) },
          { key: 'garage_nos', title: 'Гаражные №', sortable: false, cls: 'plan-wrap',
            render: (r) => (r.garage_nos || []).join(', '), footer: 'всего машин' },
          { key: 'oil', title: 'Масло', cls: 'plan-wrap' },
          { key: 'filters', title: 'Фильтры', cls: 'plan-wrap' },
          { key: 'parts', title: 'Запчасти', cls: 'plan-wrap' },
        ],
      }));
  }

  function ordersBlock(orders) {
    const tbl = ui.table({
      maxHeight: 'none',
      rows: orders,
      rowClass: (e) => (e.order_done ? 'row-muted' : ''),
      empty: 'Незакрытых заявок нет. Заявка появляется, когда в записи по машине заполнено «Заказать запчасти».',
      columns: [
        { key: 'date', title: 'Дата', cls: 'nowrap', render: (e) => ui.eventDateCell(e) },
        { key: 'garage_no', title: 'Гар. №', cls: 'strong nowrap', render: cardLink },
        { key: 'model', title: 'Модель' },
        { key: 'kind', title: 'Событие', cls: 'nowrap', render: (e) => e.kind_title || e.kind },
        { key: 'work', title: 'Что делалось', cls: 'plan-wrap' },
        { key: 'parts_order', title: 'Что заказать', cls: 'plan-wrap strong' },
        { key: 'who', title: 'Кто записал' },
        { key: 'order_done', title: 'Заказано', input: true, width: 120, render: (e) => orderCheck(e) },
      ],
    });

    function orderCheck(e) {
      const box = ui.checkbox('заказано', {
        checked: !!e.order_done,
        title: 'Поставьте галочку, когда запчасти заказаны, — заявка уйдёт из списка',
        onChange: async (done) => {
          box.input.disabled = true;
          try {
            const saved = await api.post(`/events/${e.id}/order-done`, { done });
            e.order_done = !!saved.order_done;
            ui.toast(e.order_done ? `Заявка по ${e.garage_no} отмечена: заказано` : `Отметка «заказано» по ${e.garage_no} снята`);
          } catch (err) {
            ui.toast(err && err.message ? err.message : String(err), 'error');
          }
          tbl.update(); // перерисовать строку по тому, что сохранил сервер (при ошибке галочка вернётся)
        },
      });
      return box;
    }

    return h('section.plan-section', null,
      h('h2', null, `Заявки на запчасти из записей по машинам — ${orders.length}`),
      h('p.plan-note.no-print', null, 'Отмеченные «заказано» заявки останутся на экране серыми, пока вы не уйдёте с него, — галочку можно снять.'),
      tbl);
  }

  function draw() {
    const rows = data.rows || [];
    const noTo = data.no_to || [];
    const orders = data.parts_orders || [];
    printButton.disabled = false;

    if (!rows.length && !noTo.length && !orders.length) {
      const wider = HORIZONS.find((n) => n > horizon);
      ui.mount(body, ui.emptyState(
        `В ближайшие ${days(data.horizon_days)} ни одной машине ТО не подходит, заявок на запчасти нет. `
        + 'Здесь появится список машин на ТО и что под них заказать.',
        {
          icon: 'calendar-check',
          title: 'ТО пока не требуется',
          action: wider
            ? { label: `Посмотреть на ${days(HORIZONS[HORIZONS.length - 1])} вперёд`, onClick: () => setHorizon(HORIZONS[HORIZONS.length - 1]) }
            : { label: 'На главную', onClick: () => ctx.navigate('#/fleet') },
        }));
      return;
    }

    ui.mount(body,
      summaryBlock(rows, noTo, orders),
      planTable(rows, noTo),
      noToBlock(noTo),
      materialsBlock(data.materials || []),
      ordersBlock(orders));
  }

  // ── сборка экрана ──

  root.append(
    h('div.toolbar', null,
      h('span.toolbar-label', null, 'Показать ТО на ближайшие:'),
      h('div.plan-seg', { role: 'group', 'aria-label': 'На сколько дней вперёд показывать план' }, horizonButtons),
      h('div.spacer'),
      printButton),
    body,
  );
  paintHorizon();
  await load();
}
