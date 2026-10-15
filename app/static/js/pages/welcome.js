// Экран «Первый запуск» (SPEC 7.2, п. 11): база пустая — два пути.
// 1) Загрузить всё из таблицы Excel (выбор файла → отчёт проверки → подтверждение → главный экран).
// 2) Начать с пустой базы — добавить первую машину вручную.

import { importFromExcel, GOOGLE_HINT } from './_import.js';

const CSS = `
.welcome { max-width: 940px; margin: 0 auto; padding-top: 8px; }
.welcome-lead { max-width: 640px; margin-bottom: 16px; color: var(--text-muted); }
.welcome-cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; align-items: stretch; }
.welcome-card { display: flex; flex-direction: column; gap: 12px; }
.welcome-card .card-title { display: flex; align-items: center; gap: 10px; margin: 0; }
.welcome-card .card-title .icon { color: var(--primary); }
.welcome-card p { margin: 0; }
.welcome-steps { margin: 0; padding-left: 20px; }
.welcome-steps li + li { margin-top: 6px; }
.welcome-action { margin-top: auto; padding-top: 4px; }
.welcome-foot { margin-top: 16px; }
.welcome-foot p { margin: 0 0 4px; }
.welcome-path { overflow-wrap: anywhere; user-select: all; }
`;

export async function render(root, params, ctx) {
  const { ui } = ctx;
  const { h } = ui;
  ui.addStyle('welcome-style', CSS);

  // Загрузка из таблицы: после успеха окно перезагружается и открывается «Главная».
  function fromExcel() {
    importFromExcel(ctx, { doneLabel: 'Открыть главную' })
      .then((result) => {
        if (!result) return;
        history.replaceState(null, '', '#/fleet');
        location.reload();
      })
      .catch((err) => ui.toast(err && err.message ? err.message : String(err), 'error'));
  }

  // Пустая база: диалог «Машина»; сохранили — идём на главный экран.
  async function firstMachine() {
    const saved = await ctx.dialogs.openMachineDialog(ctx);
    if (saved) ctx.navigate('#/fleet');
  }

  const excelCard = h('section.card.welcome-card', null,
    h('h3.card-title', null, ui.icon('file-spreadsheet', 22), 'Загрузить из таблицы Excel'),
    h('p', null, 'Если вы уже ведёте таблицу «Журнал учёта техники и ТО» — программа заберёт из неё машины, журнал, шины, узлы и настройки.'),
    h('ol.welcome-steps', null,
      h('li', null, GOOGLE_HINT),
      h('li', null, 'Программа покажет, что нашла в файле, и загрузит только после вашего подтверждения.')),
    h('div.welcome-action', null,
      ui.button('Выбрать файл таблицы (.xlsx)', { kind: 'accent', big: true, icon: 'upload', onClick: fromExcel })));

  const emptyCard = h('section.card.welcome-card', null,
    h('h3.card-title', null, ui.icon('truck', 22), 'Начать с пустой базы'),
    h('p', null, 'Таблицы нет или хотите начать заново — добавьте первую машину вручную: гаражный номер, модель, госномер, участок.'),
    h('p.muted', null, 'Остальные машины можно внести позже на экране «Машины», а показания и последние ТО — на главном экране.'),
    h('div.welcome-action', null,
      ui.button('Добавить первую машину', { kind: 'primary', big: true, icon: 'plus', onClick: firstMachine })));

  const dataDir = ctx.boot && ctx.boot.data_dir ? ctx.boot.data_dir : '';

  root.append(h('div.welcome', null,
    h('h2', null, 'База пустая — в программе пока нет ни одной машины'),
    h('p.welcome-lead', null, 'Выберите, с чего начать. Таблицу можно загрузить и позже: «Настройки» → «Данные».'),
    h('div.welcome-cards', null, excelCard, emptyCard),
    h('div.welcome-foot.muted.small', null,
      dataDir ? h('p', null, 'Данные программы хранятся на этом компьютере, в папке: ', h('span.welcome-path', null, dataDir)) : null,
      h('p', null, 'Раньше уже работали в программе и осталась резервная копия? ',
        h('a', { href: '#/settings?tab=data' }, 'Восстановить из копии')))));
}
