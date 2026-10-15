// Экран «Заказ-наряд» (SPEC 7.2 п.7 и блок «Заказ-наряд» раздела 5).
// Форма повторяет лист «Заказ-наряд» исходной таблицы v8: заполняется из GET /api/workorder,
// перед печатью правится любое поле; на бумагу (A4, книжная) уходит только сама форма.
// Правки нигде не сохраняются — это бланк для печати, а не запись в базе.

const MIN_WORK_ROWS = 4; // как в образце: четыре строки работ + ИТОГО
const MIN_PART_ROWS = 5; // как в образце: пять строк запчастей

const STYLE = `
.wo-dates { margin: 0 0 12px; }
.wo-dates button.link { margin-left: 8px; }
.wo-dates .wo-date-current { margin-left: 8px; color: var(--text); font-weight: 600; }
.wo-sheet {
  max-width: 880px;
  padding: 28px 36px 36px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
}
.wo-contract { display: flex; justify-content: flex-end; }
.wo-contract .input { width: 360px; text-align: right; }
.wo-forma { margin: 8px 0 0 48px; font-weight: 600; letter-spacing: .04em; }
.wo-title {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin: 8px 0 12px;
  font-size: 18px;
  font-weight: 700;
}
.wo-title .input { width: 130px; font-weight: 600; }
.wo-line { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px; }
.wo-line .input { width: auto; }
.wo-kv {
  display: grid;
  grid-template-columns: 180px minmax(0, 380px);
  align-items: center;
  gap: 6px 10px;
  margin: 16px 0 20px;
}
.wo-table { width: 100%; border-collapse: collapse; table-layout: fixed; }
.wo-table th, .wo-table td {
  padding: 3px 4px;
  border: 1px solid var(--border-strong);
  vertical-align: middle;
}
.wo-table th { padding: 6px 8px; font-weight: 600; text-align: center; }
.wo-table .wo-n { width: 56px; text-align: center; }
.wo-table th.wo-n { padding-left: 2px; padding-right: 2px; white-space: nowrap; } /* «№ п/п» не рвётся на «№ п/» и «п» */
.wo-table .wo-h { width: 92px; text-align: center; }
.wo-table .wo-cat { width: 170px; }
.wo-table .wo-unit { width: 84px; }
.wo-table .wo-qty { width: 84px; }
.wo-table th.wo-del, .wo-table td.wo-del { width: 38px; padding: 0 0 0 6px; border: 0; }
.wo-table .input { height: 30px; padding: 0 8px; }
.wo-table .wo-h .input, .wo-table .wo-unit .input, .wo-table .wo-qty .input { text-align: center; }
.wo-table .wo-total td { height: 38px; font-weight: 600; }
.wo-table .wo-total .wo-label { padding-left: 12px; }
.wo-edit {
  height: auto;
  min-height: 30px;
  padding: 5px 8px;
  line-height: 1.35;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  cursor: text;
}
.wo-table .wo-edit { height: auto; padding: 5px 8px; }
.wo-edit:empty::before { content: attr(data-placeholder); color: var(--text-muted); }
.wo-add { margin: 6px 0 0; }
.wo-issued { margin: 14px 0 0; align-items: flex-start; }
.wo-issued > span { line-height: 34px; }
.wo-sign { display: inline-flex; flex-direction: column; align-items: center; }
.wo-sign-line { width: 170px; height: 30px; border-bottom: 1px solid currentColor; }
.wo-sign-cap { font-size: 12px; }
.wo-heading { margin: 26px 0 8px; font-weight: 700; }
.wo-times {
  display: grid;
  grid-template-columns: max-content max-content;
  align-items: center;
  justify-content: center;
  gap: 6px 12px;
  margin: 18px 0 10px;
}
.wo-repair { margin: 10px 0 0; }
.wo-signs-title { margin: 36px 0 14px; font-weight: 700; text-align: center; }
.wo-signs { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 56px; }
.wo-signs > div { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
.wo-signs .wo-sign-line { width: 220px; }
@media print {
  .wo-sheet { max-width: none; padding: 0; border: 0; font-size: 11pt; }
  .wo-sheet .input { height: auto; min-height: 0; padding: 0 2pt; border: 0; font-size: inherit; }
  .wo-sheet input.input:placeholder-shown { min-height: 14pt; border-bottom: 1px solid currentColor; }
  .wo-table input.input:placeholder-shown { border-bottom: 0; }
  .wo-edit:empty::before { content: none; }
  .wo-title { font-size: 14pt; }
  .wo-kv { grid-template-columns: 46mm minmax(0, 1fr); gap: 3pt 8pt; margin: 10pt 0 12pt; }
  .wo-table th, .wo-table td { padding: 3pt 4pt; border: 1px solid currentColor; }
  .wo-table .wo-total td { height: auto; }
  .wo-table tr { break-inside: avoid; }
  .wo-table .wo-del { display: none; }
  .wo-issued > span { line-height: inherit; }
  /* должность и ФИО — по ширине текста, чтобы строка «выдал … (подпись)» уместилась на листе одной строкой */
  .wo-issued .input { field-sizing: content; width: auto !important; min-width: 20mm; }
  .wo-repair .input { field-sizing: content; width: auto !important; min-width: 30mm; }
  .wo-issued, .wo-signs { break-inside: avoid; }
  .wo-sign-line { height: 16pt; }
  .wo-heading { margin: 16pt 0 5pt; }
  .wo-times { margin: 12pt 0 6pt; }
  .wo-signs-title { margin: 22pt 0 10pt; break-after: avoid; }
}
`;

export async function render(root, params, ctx) {
  const { ui, api } = ctx;
  const { h } = ui;
  ui.addStyle('page-workorder-style', STYLE);

  const isIso = (s) => /^\d{4}-\d{2}-\d{2}$/.test(String(s || ''));
  const machineId = /^\d+$/.test(String(params.machine_id || '')) ? Number(params.machine_id) : null;
  const date = isIso(params.date) ? params.date : ui.today();
  let dirty = false; // в форме есть правки, которые ещё не распечатаны
  let printForm = null; // задаётся, когда форма построена

  const machines = await api.get('/machines');

  // ── выбор машины и даты ──

  function go(id, d) {
    ctx.setLeaveGuard(null); // про правки уже спросили сами
    ctx.navigate(id ? `#/workorder?machine_id=${id}&date=${d}` : `#/workorder?date=${d}`);
  }

  async function canDropEdits() {
    if (!dirty) return true;
    return ui.confirm('В заказ-наряде есть правки. Они нигде не сохраняются и пропадут, если заполнить форму заново.', {
      title: 'Заполнить форму заново?',
      okLabel: 'Заполнить заново',
      cancelLabel: 'Оставить как есть',
      danger: true,
    });
  }

  const machineCombo = ui.combo({
    strict: true,
    pickOnBlur: false,
    width: 300,
    placeholder: 'Машина: гар. № или модель',
    ariaLabel: 'Машина',
    emptyText: 'Такой машины нет',
    options: (machines || []).map((m) => ({
      value: m.id,
      label: m.model ? `${m.garage_no} · ${m.model}` : String(m.garage_no),
      hint: [m.plate, m.status === 'Списана' ? 'списана' : ''].filter(Boolean).join(' · '),
      search: `${m.plate || ''} ${m.vin || ''}`,
    })),
    value: machineId,
    onPick: async (opt) => {
      if (Number(opt.value) === machineId) return;
      if (!await canDropEdits()) {
        machineCombo.setValue(machineId);
        return;
      }
      go(opt.value, dateInput.getValue() || date);
    },
  });
  machineCombo.addEventListener('blur', () => {
    // стёрли или не выбрали из списка — возвращаем машину, по которой заполнена форма
    setTimeout(() => {
      if (document.activeElement !== machineCombo && machineCombo.getValue() === null) machineCombo.setValue(machineId);
    }, 0);
  });

  // Поле даты сообщает об изменении на каждой набранной цифре — ждём, пока человек допечатает.
  const applyDate = ui.debounce(async () => {
    const v = dateInput.getValue();
    if (!root.isConnected || !v || v === date || v < '2000-01-01') return;
    if (!await canDropEdits()) {
      dateInput.setValue(date);
      return;
    }
    go(machineId, v);
  }, 600);
  const dateInput = ui.dateInput({ value: date, ariaLabel: 'Дата работ', onChange: applyDate });
  dateInput.addEventListener('blur', () => {
    if (!dateInput.getValue()) dateInput.setValue(date); // дату стёрли или не дописали — возвращаем прежнюю
  });

  const refillButton = ui.button('Заполнить заново', {
    icon: 'refresh',
    title: 'Заново подтянуть работы, запчасти и время из истории машины (правки в форме пропадут)',
    disabled: true,
    onClick: async () => {
      if (!await canDropEdits()) return;
      go(machineId, date);
    },
  });
  const printButton = ui.button('Печать', {
    kind: 'primary',
    icon: 'printer',
    disabled: true,
    title: 'Напечатать форму на листе А4 или сохранить в PDF',
    onClick: () => printForm && printForm(),
  });

  const body = h('div');
  root.append(
    h('div.toolbar', null,
      h('span.toolbar-label', null, 'Машина'),
      machineCombo,
      h('span.toolbar-label', null, 'Дата работ'),
      dateInput,
      h('div.spacer'),
      refillButton,
      printButton),
    body,
  );

  if (!machineId) {
    ui.mount(body, ui.emptyState(
      'Выберите машину и дату работ — форма сама заполнится по записям в истории машины (ТО и ремонты за этот день). '
      + 'Перед печатью любое поле можно поправить.',
      {
        icon: 'clipboard-text',
        title: 'Заказ-наряд на печать',
        action: { label: 'Выбрать машину', icon: 'search', onClick: () => machineCombo.focus() },
      }));
    return;
  }

  // ── данные формы ──

  /** Даты последних ТО и ремонтов машины — подсказка, за какой день есть что печатать. Не обязательна: ошибка не мешает форме. */
  async function recentDates() {
    try {
      const [to, repair] = await Promise.all([
        api.get('/events', { machine_id: machineId, kind: 'ТО', limit: 30 }),
        api.get('/events', { machine_id: machineId, kind: 'Ремонт', limit: 30 }),
      ]);
      const all = [...((to && to.rows) || []), ...((repair && repair.rows) || [])].map((e) => e.date).filter(Boolean);
      return [...new Set(all)].sort().reverse().slice(0, 8);
    } catch (err) {
      return [];
    }
  }

  let wo;
  let dates;
  try {
    [wo, dates] = await Promise.all([api.get('/workorder', { machine_id: machineId, date }), recentDates()]);
  } catch (err) {
    ui.mount(body, ui.errorState(err, () => ctx.navigate(location.hash), { title: 'Не получилось заполнить заказ-наряд' }));
    return;
  }

  const machine = wo.machine || {};
  const works = (wo.works || []).map((w) => ({ name: w.name || '', hours: w.hours }));
  const parts = (wo.parts || []).map((p) => ({ name: p.name || '', catalog: '', unit: '', qty: '' }));
  const hadWorks = works.length > 0;
  while (works.length < MIN_WORK_ROWS) works.push({ name: '', hours: null });
  while (parts.length < MIN_PART_ROWS) parts.push({ name: '', catalog: '', unit: '', qty: '' });

  const touch = () => {
    dirty = true;
  };

  // ── поля ──

  /** Однострочное поле формы. Пустое поле на бумаге печатается чертой — чтобы вписать от руки. */
  const text = (value, label, opts = {}) => ui.textInput({
    value: value === null || value === undefined ? '' : value, placeholder: ' ', ariaLabel: label, ...opts,
  });

  /**
   * Многострочная ячейка (названия работ и запчастей). В ui.js такого поля нет: обычное поле ввода на бумаге
   * обрезает длинный текст, поэтому здесь редактируемый блок — он переносит строки и на экране, и на печати.
   */
  function editable(value, { label, placeholder = '', onInput } = {}) {
    const el = h('div.input.wo-edit', {
      role: 'textbox', 'aria-multiline': 'true', 'aria-label': label, spellcheck: 'false', dataset: { placeholder },
    });
    el.setAttribute('contenteditable', 'plaintext-only');
    if (el.contentEditable !== 'plaintext-only') {
      // старый браузер: обычное редактирование, но вставляем только текст
      el.setAttribute('contenteditable', 'true');
      el.addEventListener('paste', (e) => {
        e.preventDefault();
        const pasted = e.clipboardData ? e.clipboardData.getData('text/plain') : '';
        document.execCommand('insertText', false, pasted);
      });
    }
    el.textContent = value || '';
    el.getValue = () => el.innerText.replace(/ /g, ' ').trim();
    el.addEventListener('input', () => {
      if (!el.textContent.trim()) el.replaceChildren(); // после стирания браузер оставляет пустой перенос
      if (onInput) onInput(el.getValue());
    });
    return el;
  }

  const plain = (v, digits) => ui.fmtNum(v, digits).replace(/ /g, ' ');
  const unitShort = machine.unit === 'км' ? 'км' : 'мч';

  const fContract = text(wo.contract, 'Шапка: приложение к договору');
  const fNumber = text('', 'Номер заказ-наряда');
  const fDate = text(ui.fmtDate(wo.date), 'Дата', { width: 130 });
  const fType = text(machine.mtype, 'Тип машины');
  const fModel = text(machine.model, 'Модель');
  const fPlate = text(machine.plate, 'Заводской номер'); // соответствие полей — как в таблице v8 (SPEC разд. 5)
  const fVin = text(machine.vin, 'Инвентарный номер');
  const fGarage = text(machine.garage_no, 'Бортовой номер');
  const fReading = text(plain(machine.reading, machine.unit === 'км' ? 0 : 1), `Наработка, ${unitShort}`);
  const fPost = text(wo.mechanic_post, 'Должность механика', { width: 130 });
  const fName = text(wo.mechanic_name, 'ФИО механика', { width: 220 });
  const fStart = ui.timeInput({ value: wo.time_start || '', ariaLabel: 'Время начала работ' });
  const fEnd = ui.timeInput({ value: wo.time_end || '', ariaLabel: 'Время завершения работ' });
  const fRepairIn = text(ui.fmtDateTime(wo.repair_in), 'В ремонте с', { width: 170 });
  const fRepairOut = text(ui.fmtDateTime(wo.repair_out), 'Вышла из ремонта', { width: 170 });
  const fRepairTotal = text(ui.fmtDuration(wo.repair_total_seconds).replace(/ /g, ' '), 'Всего в ремонте', { width: 170 });

  // ── таблица «Перечень выполненных работ» ──

  const worksBody = h('tbody');
  const totalCell = h('td.wo-h');

  function drawTotal() {
    const nums = works.map((w) => w.hours).filter((v) => typeof v === 'number' && Number.isFinite(v));
    totalCell.textContent = nums.length ? plain(nums.reduce((a, b) => a + b, 0), 2) : '';
  }

  function drawWorks(focusLast) {
    const rows = works.map((w, i) => h('tr', null,
      h('td.wo-n', null, i + 1),
      h('td', null, editable(w.name, {
        label: `Работа ${i + 1}: наименование операции`,
        onInput: (v) => {
          w.name = v;
        },
      })),
      h('td.wo-h', null, ui.numInput({
        value: w.hours,
        decimals: 2,
        ariaLabel: `Работа ${i + 1}: нормо-часы`,
        onInput: (v) => {
          w.hours = v;
          drawTotal();
        },
      })),
      h('td.wo-del.no-print', null, ui.iconButton('trash', 'Удалить строку', () => {
        works.splice(i, 1);
        touch();
        drawWorks();
      }, { small: true }))));
    ui.mount(worksBody, rows,
      h('tr.wo-total', null,
        h('td.wo-n', null, works.length + 1),
        h('td.wo-label', null, 'ИТОГО'),
        totalCell,
        h('td.wo-del.no-print')));
    drawTotal();
    if (focusLast && rows.length) rows[rows.length - 1].querySelector('.wo-edit').focus();
  }

  const worksTable = h('table.wo-table', null,
    h('thead', null,
      h('tr', null,
        h('th.wo-n'),
        h('th', { scope: 'col' }, 'Перечень выполненных работ'),
        h('th.wo-h', { scope: 'col' }, 'Кол-во'),
        h('th.wo-del.no-print')),
      h('tr', null,
        h('th.wo-n', { scope: 'col' }, '№ п/п'),
        h('th', { scope: 'col' }, 'Наименование операций'),
        h('th.wo-h', { scope: 'col' }, 'н/час'),
        h('th.wo-del.no-print'))),
    worksBody);

  // ── таблица «Используемые запчасти и материалы» ──

  const partsBody = h('tbody');

  function drawParts(focusLast) {
    const rows = parts.map((p, i) => h('tr', null,
      h('td.wo-n', null, i + 1),
      h('td', null, editable(p.name, {
        label: `Запчасть ${i + 1}: наименование`,
        onInput: (v) => {
          p.name = v;
        },
      })),
      h('td.wo-cat', null, text(p.catalog, `Запчасть ${i + 1}: каталожный номер`, { onInput: (v) => {
        p.catalog = v;
      } })),
      h('td.wo-unit', null, text(p.unit, `Запчасть ${i + 1}: единица измерения`, { onInput: (v) => {
        p.unit = v;
      } })),
      h('td.wo-qty', null, text(p.qty, `Запчасть ${i + 1}: количество`, { onInput: (v) => {
        p.qty = v;
      } })),
      h('td.wo-del.no-print', null, ui.iconButton('trash', 'Удалить строку', () => {
        parts.splice(i, 1);
        touch();
        drawParts();
      }, { small: true }))));
    ui.mount(partsBody, rows);
    if (focusLast && rows.length) rows[rows.length - 1].querySelector('.wo-edit').focus();
  }

  const partsTable = h('table.wo-table', null,
    h('thead', null,
      h('tr', null,
        h('th.wo-n', { scope: 'col' }, '№ п/п'),
        h('th', { scope: 'col' }, 'Наименование'),
        h('th.wo-cat', { scope: 'col' }, 'Каталожный номер'),
        h('th.wo-unit', { scope: 'col' }, 'Ед. изм'),
        h('th.wo-qty', { scope: 'col' }, 'Кол-во'),
        h('th.wo-del.no-print'))),
    partsBody);

  const addRowButton = (onClick) => h('div.wo-add', null, ui.button('Добавить строку', { icon: 'plus', small: true, onClick }));
  const signature = () => h('span.wo-sign', null, h('span.wo-sign-line'), h('span.wo-sign-cap', null, '(подпись)'));

  // ── лист формы ──

  const sheet = h('div.wo-sheet', null,
    h('div.wo-contract', null, fContract),
    h('div.wo-forma', null, 'ФОРМА'),
    h('div.wo-title', null, h('span', null, 'Заказ - наряд №'), fNumber),
    h('div.wo-line', null, h('span', null, 'Дата:'), fDate),
    h('div.wo-kv', null,
      h('span', null, 'Тип машины:'), fType,
      h('span', null, 'Модель:'), fModel,
      h('span', null, 'Заводской номер:'), fPlate,
      h('span', null, 'Инвентарный номер:'), fVin,
      h('span', null, 'Бортовой номер:'), fGarage,
      h('span', null, `Наработка, ${unitShort}:`), fReading),
    machine.reading_estimated
      ? h('div.no-print', null, ui.notice('Наработка расчётная (≈): на счётчик не смотрели. Проверьте число и поправьте перед печатью.', 'warn'))
      : null,
    worksTable,
    addRowButton(() => {
      works.push({ name: '', hours: null });
      touch();
      drawWorks(true);
    }),
    h('div.wo-line.wo-issued', null,
      h('span', null, 'Заказ-наряд выдал: (механик)'), fPost,
      h('span', null, '(ФИО)'), fName,
      signature()),
    h('div.wo-heading', null, 'ИСПОЛЬЗУЕМЫЕ ЗАПЧАСТИ И МАТЕРИАЛЫ'),
    partsTable,
    addRowButton(() => {
      parts.push({ name: '', catalog: '', unit: '', qty: '' });
      touch();
      drawParts(true);
    }),
    h('div.wo-times', null,
      h('span', null, 'Время начала работ:'), fStart,
      h('span', null, 'Время завершения работ:'), fEnd),
    h('div.wo-line.wo-repair', null,
      h('span', null, 'В ремонте:'), fRepairIn,
      h('span', null, 'вышла:'), fRepairOut,
      h('span', null, 'всего в ремонте:'), fRepairTotal),
    h('div.wo-signs-title', null, 'ПОДПИСИ СТОРОН'),
    h('div.wo-signs', null,
      h('div', null, text('', 'Первая сторона: должность, ФИО', { placeholder: 'Должность, ФИО' }), signature()),
      h('div', null, text('', 'Вторая сторона: должность, ФИО', { placeholder: 'Должность, ФИО' }), signature())));

  sheet.addEventListener('input', touch);
  drawWorks();
  drawParts();

  // ── подсказки над формой (на бумагу не идут) ──

  const datesLine = h('p.wo-dates.small.muted.no-print', null,
    dates.length ? 'ТО и ремонты этой машины в истории:' : 'По этой машине в истории ещё нет ни ТО, ни ремонтов.',
    dates.map((d) => (d === date
      ? h('span.wo-date-current', { title: 'Форма заполнена за эту дату' }, ui.fmtDate(d))
      : h('button.link', {
        type: 'button',
        title: 'Заполнить заказ-наряд за эту дату',
        onclick: async () => {
          if (await canDropEdits()) go(machineId, d);
        },
      }, ui.fmtDate(d)))));

  const emptyNote = hadWorks ? null : h('div.no-print', null, ui.notice(
    `За ${ui.fmtDate(date)} в истории нет ТО и ремонтов по машине ${machine.garage_no || ''} — работы и запчасти не подтянулись. `
    + 'Впишите их в форму вручную или выберите другую дату.', 'info'));

  ui.mount(body,
    emptyNote,
    datesLine,
    sheet,
    h('p.small.muted.no-print', { style: { marginTop: '12px' } },
      'Кремовые поля можно править. Правки в заказ-наряде не сохраняются в программе — распечатайте форму или сохраните её в PDF через «Печать».'));

  // ── печать ──

  printForm = () => {
    const oldTitle = document.title;
    const name = `Заказ-наряд ${fGarage.getValue() || machine.garage_no || ''} ${fDate.getValue() || ui.fmtDate(date)}`;
    document.title = name.replace(/[\\/:*?"<>|]+/g, '-').trim(); // имя файла при сохранении в PDF
    window.addEventListener('afterprint', () => {
      setTimeout(() => {
        document.title = oldTitle;
      }, 0);
    }, { once: true });
    dirty = false; // распечатали — уходить с экрана можно без вопросов
    ui.printPage(); // без заголовка: на листе только сама форма
  };
  refillButton.disabled = false;
  printButton.disabled = false;

  ctx.setLeaveGuard(() => (dirty
    ? 'В заказ-наряде есть правки, которые ещё не распечатаны. Они нигде не сохраняются. Уйти с экрана?'
    : null));
}
