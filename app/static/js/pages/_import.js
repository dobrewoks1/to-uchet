// Загрузка из таблицы Excel (общая для экранов «Первый запуск» и «Настройки → Данные»):
// выбор файла → проверка без записи (dry_run) → отчёт простыми словами → подтверждение → загрузка → итог.
// Что делать после загрузки (перезагрузить окно, открыть главный экран), решает вызывающий экран.

/** Подсказка, как получить файл из Google-таблицы (SPEC 7.2, п. 11). */
export const GOOGLE_HINT = 'В Google-таблице: Файл → Скачать → Microsoft Excel (.xlsx), затем выберите скачанный файл.';

const MAX_FILE_BYTES = 40 * 1024 * 1024; // сервер не принимает файлы больше 40 МБ

const CSS = `
.imp-body { display: flex; flex-direction: column; gap: 12px; }
.imp-body > p, .imp-body > .notice { margin: 0; }
.imp-table { width: 100%; border-collapse: collapse; }
.imp-table td { padding: 5px 0; border-bottom: 1px solid var(--border); vertical-align: baseline; }
.imp-table tr:last-child td { border-bottom: 0; }
.imp-table td.num { width: 110px; padding-left: 16px; font-weight: 600; }
.imp-warn ul { max-height: 190px; overflow: auto; font-weight: 400; }
.imp-status { min-height: 20px; }
`;

function count(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

/** Есть ли в отчёте хоть что-то, что изменит базу. */
function hasChanges(r) {
  return ['machines_new', 'machines_updated', 'events_new', 'events_updated', 'tyres_new', 'units_new', 'settings_updated']
    .some((key) => count(r[key]) > 0);
}

/** Отчёт проверки простыми словами: строки «что — сколько». */
function reportRows(ui, r) {
  const n = (key) => count(r[key]);
  const val = (v) => (v > 0 ? ui.fmtNum(v) : 'нет');
  const rows = [
    ['Новые машины', val(n('machines_new'))],
  ];
  if (n('machines_updated')) rows.push(['Машины, у которых обновятся данные', val(n('machines_updated'))]);
  rows.push(
    ['Записи журнала (ТО, ремонты, простои, отметки)', val(n('events_new'))],
  );
  if (n('events_updated')) rows.push(['Записи журнала, которые дополнятся (вышла, запчасти, замечания…)', val(n('events_updated'))]);
  rows.push(
    ['Шины', val(n('tyres_new'))],
    ['Узлы', val(n('units_new'))],
  );
  if (n('settings_updated')) rows.push(['Настройки, справочники, правила по маркам и регламент ТО', 'как в таблице']);
  if (n('events_skipped')) rows.push(['Записи журнала, которые уже есть в программе (второй раз не добавятся)', val(n('events_skipped'))]);
  if (n('orders_open')) rows.push(['Незакрытые заявки на запчасти', val(n('orders_open'))]);
  return rows;
}

function warningsBlock(ui, r) {
  const { h } = ui;
  const list = Array.isArray(r.warnings) ? r.warnings : [];
  if (!list.length) return null;
  const total = count(r.warnings_total) || list.length;
  return ui.notice(h('details.details.imp-warn', { open: list.length <= 4 },
    h('summary', null, `Замечания по таблице: ${ui.fmtNum(total)}. Загрузке они не мешают`),
    h('ul', null, list.map((line) => h('li', null, line)))), 'warn');
}

function reportView(ctx, file, r) {
  const { ui } = ctx;
  const { h } = ui;
  if (!hasChanges(r)) {
    return [
      ui.notice(h('div', null,
        h('div.strong', null, 'В этом файле нет ничего нового'),
        h('div', null, count(r.events_skipped)
          ? `Всё, что в нём есть, уже загружено в программу (записей журнала совпало: ${ui.fmtNum(r.events_skipped)}).`
          : 'Машин, записей журнала, шин и узлов для загрузки не нашлось. Проверьте, тот ли файл выбран.')), 'info'),
      warningsBlock(ui, r),
    ];
  }
  return [
    h('p', null, `Файл «${file.name}» прочитан. В программе пока ничего не изменилось — посмотрите, что будет загружено:`),
    h('table.imp-table', null, h('tbody', null,
      reportRows(ui, r).map(([label, value]) => h('tr', null, h('td', null, label), h('td.num', null, value))))),
    warningsBlock(ui, r),
    ctx.boot && !ctx.boot.first_run
      ? h('p.muted.small', null, 'Данные из файла добавятся к тем, что уже есть: машины с тем же гаражным номером обновятся, '
        + 'одинаковые записи журнала не задвоятся. Перед загрузкой программа сама сделает резервную копию.')
      : null,
  ];
}

/** Итог загрузки — список «что добавлено» для окна «Готово». */
function resultLines(ui, r) {
  const n = (key) => count(r[key]);
  const lines = [];
  if (n('machines_new')) lines.push(`Машин добавлено: ${ui.fmtNum(n('machines_new'))}`);
  if (n('machines_updated')) lines.push(`Машин обновлено: ${ui.fmtNum(n('machines_updated'))}`);
  if (n('events_new')) lines.push(`Записей журнала добавлено: ${ui.fmtNum(n('events_new'))}`);
  if (n('events_updated')) lines.push(`Записей журнала дополнено: ${ui.fmtNum(n('events_updated'))}`);
  if (n('tyres_new')) lines.push(`Шин добавлено: ${ui.fmtNum(n('tyres_new'))}`);
  if (n('units_new')) lines.push(`Узлов добавлено: ${ui.fmtNum(n('units_new'))}`);
  if (n('settings_updated')) lines.push('Настройки и справочники обновлены по таблице');
  if (n('events_skipped')) lines.push(`Записей журнала пропущено (уже были в программе): ${ui.fmtNum(n('events_skipped'))}`);
  const warnings = count(r.warnings_total) || (Array.isArray(r.warnings) ? r.warnings.length : 0);
  if (warnings) lines.push(`Замечаний по таблице: ${ui.fmtNum(warnings)}`);
  return lines;
}

/**
 * Окно проверки и загрузки одного файла.
 * Возвращает: объект-результат импорта (загружено), 'again' (человек хочет выбрать другой файл) или null (отмена).
 */
async function importFile(ctx, file, doneLabel) {
  const { ui, api } = ctx;
  const { h } = ui;
  ui.addStyle('import-style', CSS);

  let closed = false;
  const content = h('div.imp-body');
  const status = h('div.imp-status.muted.small', { role: 'status' });

  const m = ui.modal({
    title: 'Загрузка из таблицы Excel',
    body: [content, status],
    width: 640,
    buttons: [
      { label: 'Отмена', value: null },
      { label: 'Загрузить в программу', kind: 'accent', icon: 'upload', primary: true, disabled: true, onClick: load },
    ],
    onClose: () => {
      closed = true;
    },
  });
  const [cancelBtn, loadBtn] = m.buttons;

  async function check() {
    ui.mount(content, ui.loading(`Читаю файл «${file.name}»… Большая таблица читается до минуты.`));
    let report;
    try {
      if (file.size > MAX_FILE_BYTES) {
        throw new Error('Файл больше 40 МБ — такой большой программа не принимает. Проверьте, что выбрана таблица учёта ТО.');
      }
      report = await api.upload('/import/xlsx', file, { dry_run: 1 });
    } catch (err) {
      if (closed) return;
      // status 0 — нет связи с программой, 5xx — сбой сервера: файл тут ни при чём, можно повторить
      const notFileFault = err && typeof err.status === 'number' && (err.status === 0 || err.status >= 500);
      ui.mount(content,
        ui.notice(h('div', null,
          h('div.strong', null, notFileFault ? 'Не получилось проверить файл' : 'Файл не подошёл'),
          h('div', null, err && err.message ? err.message : String(err))), 'error'),
        notFileFault ? null
          : h('p.muted.small', null, `Нужен файл таблицы «Журнал учёта техники и ТО» в формате Excel (.xlsx). ${GOOGLE_HINT}`),
        h('div.row', null,
          notFileFault ? ui.button('Повторить', { icon: 'refresh', kind: 'primary', onClick: () => { check(); } }) : null,
          ui.button('Выбрать другой файл', { icon: 'file-spreadsheet', onClick: () => m.close('again') })));
      return;
    }
    if (closed) return;
    ui.mount(content, reportView(ctx, file, report));
    if (hasChanges(report)) {
      loadBtn.disabled = false;
      loadBtn.focus();
    } else {
      const label = cancelBtn.querySelector('span');
      if (label) label.textContent = 'Закрыть';
    }
  }

  async function load() {
    status.textContent = 'Загружаю в программу… Не закрывайте окно.';
    try {
      const result = await api.upload('/import/xlsx', file, { dry_run: 0 });
      m.close(result);
    } finally {
      status.textContent = '';
    }
  }

  check();
  const value = await m.result;
  if (value === 'again') return 'again';
  if (!value || typeof value !== 'object') return null;

  const lines = resultLines(ui, value);
  await ui.alert('Таблица загружена в программу.', {
    title: 'Готово',
    details: lines.length ? lines : null,
    okLabel: doneLabel,
  });
  return value;
}

/**
 * Загрузить данные из таблицы Excel: выбор файла → отчёт проверки → подтверждение → загрузка → окно «Готово».
 * Возвращает Promise: результат импорта ({machines_new, events_new, …}), если данные загружены, или null, если человек отказался.
 * После результата вызывающий экран обязан перечитать данные (location.reload()).
 * doneLabel — подпись кнопки в окне «Готово» («Открыть главную», «Продолжить»).
 */
export async function importFromExcel(ctx, { doneLabel = 'Продолжить' } = {}) {
  for (;;) {
    const file = await ctx.ui.pickFile('.xlsx');
    if (!file) return null;
    const outcome = await importFile(ctx, file, doneLabel);
    if (outcome !== 'again') return outcome;
  }
}
