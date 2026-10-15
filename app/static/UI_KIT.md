# UI-кит «Учёт ТО» — справочник для авторов страниц и диалогов

Всё, что нужно странице, берётся из `ctx.ui` (`js/ui.js`) и `ctx.api` (`js/api.js`). Свои компоненты, свои цвета и внешние ресурсы не заводим.
Дизайн-контракт — три правила, которые нельзя нарушать:

1. **Всё, что вводит человек, — на кремовом фоне** (класс `.input` — его ставят все поля кита). Всё, что считает программа, — на белом/сером (`.readout`, ячейки таблиц, чипы).
2. **Цвета только через переменные** `var(--…)` из `css/app.css` (`--text`, `--text-muted`, `--bg`, `--surface`, `--zebra`, `--border`, `--border-strong`, `--primary`, `--primary-dark`, `--on-primary`, `--accent`, `--accent-bright`, `--input-bg`, `--red`/`--red-bg`, `--amber`/`--amber-bg`, `--green`/`--green-bg`, `--grey`/`--grey-bg`). Ни одного `#hex` в странице.
3. **Статус никогда не передаётся одним цветом** — всегда чип со словом (`ui.statusChip`).

Содержание: [Страница](#страница) · [api.js](#apijs) · [ui.js: DOM](#uijs--dom) · [Кнопки](#кнопки) · [Поля ввода](#поля-ввода) · [Таблица](#таблица) · [Модальные окна](#модальные-окна-подтверждения-тосты) · [Статусы, вкладки, состояния](#статусы-вкладки-состояния) · [Форматтеры и даты](#форматтеры-числа-даты) · [previewTo](#previewto) · [CSS-классы](#css-классы) · [Печать](#печать) · [Иконки](#иконки)

---

## Страница

Файл `js/pages/<имя>.js` экспортирует одну функцию:

```js
export async function render(root, params, ctx) { … }
```

- `root` — пустой `<div class="page">`, в него страница кладёт своё содержимое. Заголовок экрана уже показан в верхней строке — дублировать его не нужно.
- `params` — параметры из адреса, **все значения — строки**: `#/workorder?machine_id=5` → `{machine_id: '5'}`; `#/machine/7` → `{id: '7'}`.
- `ctx`:

| Поле | Что это |
|---|---|
| `ctx.boot` | ответ `/api/bootstrap`: `{version, today, now, data_dir, first_run, settings, lists, rules, regulation}`. Объект заменяется при `ctx.refresh()` — читайте `ctx.boot` заново, не сохраняйте в переменную надолго |
| `ctx.api` | модуль `api.js` (ниже) |
| `ctx.ui` | модуль `ui.js` (ниже) |
| `ctx.dialogs` | общие диалоги: `openToDoneDialog(ctx, machineId?)`, `openReadingDialog(ctx, machineId?)`, `openEventDialog(ctx, {machineId?, event?, preset?})`, `openCloseRepairDialog(ctx, eventOrId)`, `openMachineDialog(ctx, machine?)`, `openDailyDialog(ctx)` — каждый возвращает `Promise<boolean>` (true — что-то сохранено) |
| `ctx.navigate(hash)` | перейти: `ctx.navigate('#/machine/5')`, `ctx.navigate('#/workorder?machine_id=5&date=2026-10-02')`. Тот же адрес — перерисовать экран |
| `ctx.refresh()` | `async`: перечитать `/api/bootstrap` в `ctx.boot` и перерисовать текущий экран (прокрутка сохраняется). Вызывать после сохранения настроек/справочников |
| `ctx.setTitle(text)` | сменить заголовок в верхней строке (например, «Машина 101 · SHACMAN») |
| `ctx.setLeaveGuard(fn)` | защита несохранённого ввода: `fn()` возвращает текст вопроса (или `true`), если уходить нельзя без подтверждения; `null`/`false` — можно. Снимается сама при уходе со страницы |

Что роутер делает сам:
- пока `render` не завершился дольше 250 мс — показывает «Загрузка…»;
- если `render` бросил исключение (в т. ч. ошибку `api`) — показывает текст ошибки и кнопку «Повторить»; ловить ошибки начальной загрузки в странице не нужно;
- если `render` вернул функцию — вызовет её при уходе со страницы (снять таймеры, слушатели на `window`);
- даты «сегодня» берите из `ui.today()` (часы компьютера), а не из `boot.today`: программа бывает открыта сутками.

Пример страницы целиком:

```js
export async function render(root, params, ctx) {
  const { ui, api } = ctx;
  const { h } = ui;
  root.classList.add('page-fill');                 // главная таблица займёт остаток высоты окна

  let rows = await api.get('/tyres', { machine_id: params.machine_id });

  const search = ui.textInput({ placeholder: 'Поиск', onInput: ui.debounce(apply, 200) });
  const tbl = ui.table({
    fill: true,
    columns: [
      { key: 'garage_no', title: 'Гар. №', width: 80 },
      { key: 'tyre_no', title: '№ шины' },
      { key: 'left', title: 'Остаток', align: 'right', render: (r) => ui.fmtNum(r.left) },
      { key: 'status', title: 'Статус', render: (r) => ui.statusChip(r.status, r.status_label) },
      { title: '', cls: 'cell-actions no-print', headCls: 'no-print', render: (r) => h('div.row-actions', null,
          ui.iconButton('pencil', 'Изменить', () => edit(r), { small: true }),
          ui.iconButton('trash', 'Удалить', () => remove(r), { small: true })) },
    ],
    empty: ui.emptyState('Добавьте первую шину.', { icon: 'wheel', title: 'Шин пока нет' }),
  });

  function apply() {
    const q = search.getValue().toLowerCase();
    tbl.setRows(rows.filter((r) => !q || `${r.garage_no} ${r.tyre_no}`.toLowerCase().includes(q)));
  }

  async function remove(r) {
    if (!await ui.confirm(`Удалить шину ${r.tyre_no}?`, { danger: true, okLabel: 'Удалить' })) return;
    await api.del(`/tyres/${r.id}`);
    ui.toast('Шина удалена');
    rows = await api.get('/tyres');
    apply();
  }

  root.append(
    h('div.toolbar', null, search, h('div.spacer'),
      ui.button('Добавить шину', { kind: 'primary', icon: 'plus', onClick: () => edit(null) }),
      ui.button('Печать', { icon: 'printer', onClick: () => ui.printPage('Шины') })),
    tbl,
  );
  apply();
}
```

Можно импортировать модули и напрямую: `import * as ui from '../ui.js'; import * as api from '../api.js';` — это те же самые экземпляры, что в `ctx`.

---

## api.js

Путь можно писать с `/api` и без: `'/fleet'` = `'/api/fleet'`. Все функции `async`.

| Функция | Что делает |
|---|---|
| `get(path, params?)` | GET. `params` — объект query; `null`/`undefined`/`''` пропускаются, `true/false` → `1/0` |
| `post(path, body?)` | POST с JSON |
| `put(path, body?)` | PUT с JSON (частичное обновление — передавайте только изменённые ключи) |
| `del(path, params?)` | DELETE; `params` — query, напр. `{force: 1}` |
| `upload(path, file, params?)` | POST файла (`File`/`Blob`) как `application/octet-stream`: `api.upload('/import/xlsx', file, {dry_run: 1})` |
| `download(path, params?)` | скачать файл и отдать браузеру на сохранение (имя — из заголовка сервера). Возвращает имя файла |
| `withConfirm(fn, opts?)` | запрос с подтверждением (см. ниже) |
| `onConnectionChange(cb)`, `isOnline()`, `ping()`, `startPing()` | служебное; пинг каждые 20 с запускает оболочка |

**Ошибки.** Любая неудача — исключение `ApiError`:

| Свойство | Значение |
|---|---|
| `.message` | русский текст с сервера (его и показывайте) |
| `.status` | код HTTP; `0` — нет связи с программой |
| `.needsConfirm` | `true` при 409 «нужно подтверждение» |
| `.warnings` | массив строк-предупреждений |
| `.items` | `[{machine_id, garage_no, text}]` |
| `.data` | весь JSON ответа |

**`withConfirm(fn, {title, okLabel, cancelLabel})`** — `fn(force)` вызывается с `force = false`; если сервер ответил 409 `needs_confirm`, показывается вопрос со списком `warnings` + `items`, и при согласии — `fn(true)`.
Возвращает результат `fn` **или `null`, если пользователь отказался**. Прочие ошибки пробрасываются.

```js
const saved = await api.withConfirm((force) => api.post('/events', { ...data, force }));
if (saved === null) return;            // человек решил вернуться и исправить
ui.toast('Запись сохранена');

await api.withConfirm((force) => api.del(`/machines/${id}`, { force: force ? 1 : null }),
  { title: 'Удалить машину?', okLabel: 'Удалить со всей историей' });
```

Куда девается ошибка, если её не ловить:
- внутри `onClick` кнопки модального окна (`ui.modal`) — красная строка в окне, окно остаётся открытым;
- внутри `onClick` у `ui.button` / `ui.iconButton` / пункта `ui.menu` — тост с текстом ошибки;
- в остальных местах — тост (страховка оболочки), но лучше ловить самим.

---

## ui.js — DOM

### `h(tag, attrs, ...children)` → элемент
```js
h('div.card', null, h('h2.card-title', null, 'Шины'), 'текст')
h('button.btn.btn-primary', { type: 'button', onclick: save, disabled: !ok }, 'Сохранить')
h('a', { href: '#/machine/5' }, '101')
h('td', { class: ['num', neg && 'text-red'], style: { width: '80px' }, dataset: { id: 5 } }, '−2 000')
```
- `tag`: `'div'`, `'span.muted'`, `'div#id.a.b'`.
- `attrs` (или `null`): `class` — строка или массив (ложные значения выбрасываются); `style` — строка или объект; `dataset`; `on<событие>` — обработчик; `value`, `checked`, `disabled`, `hidden`, `colSpan`, `rowSpan` и т. п. — свойства; остальное — атрибуты. `null`/`undefined`/`false` пропускаются, `true` — атрибут без значения.
- дети: строки, числа, узлы, массивы (любой вложенности); `null`/`false` пропускаются. Текст всегда вставляется как текст (экранировать не нужно, `innerHTML` не используем).

### Прочее
| Функция | Что делает |
|---|---|
| `mount(el, ...children)` | заменить содержимое элемента |
| `clear(el)` | очистить элемент |
| `icon(name, size = 18)` | SVG-иконка (цвет — `currentColor`); имена — в разделе [Иконки](#иконки) |
| `addStyle(id, css)` | один раз добавить `<style id>` со стилями страницы. Цвета — только `var(--…)`; печатные правила — внутри `@media print { … }` |
| `debounce(fn, ms = 250)` | отложенный вызов (поиск, фильтры) |
| `uid(prefix)` | уникальный id |
| `pickFile(accept, {multiple = false})` | открыть выбор файла: `const file = await ui.pickFile('.xlsx')` → `File` или `null` (отмена). С `{multiple: true}` можно выбрать несколько файлов, результат — массив `File[]`, при отмене — `[]`: `const files = await ui.pickFile('image/*', {multiple: true})` (фото в карточке машины) |
| `closePopovers()` | закрыть открытое меню/подсказки (оболочка и окна вызывают сами) |

---

## Кнопки

### `button(label, opts)` → `<button class="btn">`
`opts`: `kind` — `'default'` | `'primary'` (синяя: обычное основное действие) | `'accent'` (оранжево-коричневая: главные «сделать» — «ТО сделано», «Сохранить всё») | `'danger'` | `'ghost'` (без рамки);
`icon` — имя иконки; `onClick`; `title`; `big` (40px — главные кнопки экрана); `small` (26px — в строках таблиц); `disabled`; `cls`; `autofocus`.

`onClick` может быть `async`: на время выполнения кнопка блокируется (двойной клик не сработает); непойманная ошибка покажется тостом.

```js
ui.button('ТО сделано', { kind: 'accent', big: true, icon: 'check', onClick: () => ctx.dialogs.openToDoneDialog(ctx, m.id) })
```

### `iconButton(iconName, title, onClick, opts?)`
Кнопка-иконка без подписи; `title` обязателен. `opts` — как у `button` (по умолчанию `kind: 'ghost'`).

### `menu(anchor, items, {align = 'right'})`
Выпадающее меню у кнопки («⋯»). `items`: `{label, icon, onClick, danger, disabled, title}` или `'-'` (разделитель). Стрелки ↑↓ ходят по пунктам, Esc закрывает.
```js
ui.iconButton('dots', 'Ещё', (e) => ui.menu(e.currentTarget, [
  { label: 'Ввести показание', icon: 'gauge', onClick: () => ctx.dialogs.openReadingDialog(ctx, m.id) },
  '-',
  { label: 'Карточка машины', icon: 'truck', onClick: () => ctx.navigate(`#/machine/${m.id}`) },
  { label: 'Заказ-наряд', icon: 'clipboard-text', onClick: () => ctx.navigate(`#/workorder?machine_id=${m.id}&date=${ui.today()}`) },
]), { small: true })
```
(пункты меню строки на главной). Опасный пункт — `{ label: 'Удалить', icon: 'trash', danger: true, onClick }`.

---

## Поля ввода

Все поля — кремовые (`.input`). Каждое поле — обычный DOM-элемент (`.value`, `.disabled`, `.focus()` работают как всегда) плюс методы:
`getValue()`, `setValue(v)`, `isValid()`.

Общие `opts` у всех полей: `value`, `placeholder`, `disabled`, `readOnly`, `title`, `ariaLabel`, `id`, `name`, `maxLength`, `width` (число px или строка), `cls`, `autofocus` (фокус при открытии модального окна).

| Функция | `getValue()` | Особенности |
|---|---|---|
| `textInput(opts)` | строка без крайних пробелов | `onInput(v)`, `onChange(v)`, `onEnter(v, event)` |
| `textArea(opts)` | строка | `rows` (по умолчанию 3), `onInput`, `onChange` |
| `numInput(opts)` | **число**, `null` (пусто) или введённый текст как есть, если это не число | понимает «153 000», «1250,5»; при уходе из поля расставляет пробелы тысяч; `decimals` — макс. знаков после запятой (по умолчанию 3; `0` — целое); `thousands` (bool или функция; по умолчанию `decimals === 0`) — «153.000»/«153,000» читать как 153 000 (для км); `allowNegative`; `onInput(v)`, `onChange(v)`; при фокусе выделяет всё содержимое |
| `timeInput(opts)` | `'HH:MM'`, `''` или введённый текст, если это не время | понимает `130` → 01:30, `9` → 09:00, `1.30` → 01:30, `17:5` → 17:05 (приводит при уходе из поля и по Enter); `onInput`, `onChange` |
| `dateInput(opts)` | `'YYYY-MM-DD'` или `''` | родной календарь; `min`, `max`, `onChange(v)` |
| `select(opts)` | строка (значение пункта) | `options`: строки, `{value, label, disabled}` или `[value, label]`; `placeholder` — подпись пустого пункта со значением `''`; `onChange(v)`; `setOptions(list)`; `setValue(v)` добавит значение, которого нет в списке (старые записи не теряют значение) |
| `combo(opts)` | см. ниже | ввод с подсказками |
| `checkbox(label, opts)` | `true/false` | возвращает `<label class="checkbox">`; сам чекбокс — `.input`; `opts`: `checked`, `disabled`, `onChange(checked)` |

Если `numInput`/`timeInput` содержит не число/не время, поле подсвечивается красным, `isValid()` → `false`, а `getValue()` отдаёт текст как есть — сервер ответит понятной ошибкой, а пустым значение молча не станет. Числа можно отправлять на сервер и строкой («153 000») — он нормализует.

### `combo(opts)` — ввод с подсказками
Возвращает сам `<input>`; список подсказок открывается при фокусе и при вводе, ↑↓ — выбор, Enter — подставить, Esc — закрыть список.

- `options`: строки или `{value, label, hint, search}` (`hint` — серая подпись справа, по ней и по `search` тоже ищет);
- **обычный режим** (свободный текст: «Кто», «Что делалось»): `getValue()` → введённая строка;
- **`strict: true`** (выбор из списка: машина, справочник): `getValue()` → `value` выбранного пункта или `null`; `getOption()` → выбранный пункт; `setValue(value)` выбирает пункт по `value`; текст не из списка — поле красное, `isValid()` → `false`;
- `onChange(value, option)`, `onPick(option)` (только при выборе из списка), `onInput(text)`, `setOptions(list)`, `emptyText`, `maxItems` (60), `pickOnBlur` (в strict при уходе из поля единственное совпадение выбирается само; `false` — отключить).

```js
const machine = ui.combo({
  strict: true, placeholder: 'Гар. № или модель', autofocus: true,
  options: machines.map((m) => ({ value: m.id, label: `${m.garage_no} · ${m.model}`, hint: m.plate })),
  onChange: (id) => loadView(id),
});
const who = ui.combo({ options: ['Петров', 'Иванов'], value: localStorage.getItem('who') || '' });
```

### `field(label, control, {required, hint, cls})` — подпись + поле
Связывает `<label>` с полем, показывает звёздочку и подсказку. У результата: `setError(text)` (красная строка под полем и красная рамка; `''` — убрать), `setHint(content)`, `.control`.

```js
const reading = ui.numInput({ value: m.reading });
const f = ui.field('Показание на момент ТО', reading, { required: true, hint: 'на каком пробеге сделали' });
body.append(h('div.form-grid', null, f, ui.field('Дата ТО', ui.dateInput({ value: ui.today() }))));
if (!reading.isValid() || reading.getValue() === null) { f.setError('Введите показание'); return; }
```

Сетки форм — классы `.form-grid` (2 колонки), `.form-grid-3`, `.form-grid-4`; поле на 2 колонки — `.span-2`, на всю ширину — `.span-all`; поля в ряд по содержимому — `.form-row`.
Расчётное значение в форме («Следующее: ТО-3 на 160 000 км…») — `h('div.readout', null, текст)` (серое табло, не поле).

---

## Таблица

### `table(opts)` → `div.table-wrap`

```js
const tbl = ui.table({
  columns: [
    { key: 'garage_no', title: 'Гар. №', width: 80 },
    { key: 'reading', title: 'Показание', align: 'right',
      render: (m) => ui.fmtReading(m.reading, m.unit, m.reading_estimated) },
    { key: 'remaining', title: 'Осталось', align: 'right', cls: (m) => ['strong', m.remaining <= 0 && 'text-red'],
      render: (m) => ui.fmtNum(m.remaining) },
    { key: 'to_status', title: 'Статус ТО', sortValue: (m) => ORDER[m.to_status],
      render: (m) => ui.statusChip(m.to_status, m.to_status_label) },
  ],
  rows,
  sort: { key: 'remaining', dir: 'asc' },
  onRowClick: (m) => ctx.navigate(`#/machine/${m.id}`),
  rowClass: (m) => (m.status === 'Списана' ? 'row-muted' : ''),
  empty: 'Ничего не найдено',
  fill: true,
});
tbl.setRows(filtered);
```

**Колонка:**

| Ключ | Значение |
|---|---|
| `key` | поле строки; нужно для сортировки и значения по умолчанию |
| `title` | заголовок (строка или узел) |
| `align` | `'left'` (по умолчанию) \| `'right'` (числа: вправо, без переноса) \| `'center'` |
| `width` | ширина (px или строка) |
| `render(row, i)` | содержимое ячейки: строка, число, узел, массив; без `render` — `row[key]` |
| `sortValue(row)` | значение для сортировки (по умолчанию `row[key]`); числа сравниваются как числа, строки — по-русски с учётом чисел («9» < «20» < «101»); пустые значения всегда в конце |
| `sortable` | `false` — запретить сортировку (по умолчанию сортируется всё, у чего есть `key` или `sortValue`) |
| `cls` | класс ячейки: строка, массив или функция `(row) => …` |
| `headCls` | класс ячейки шапки (например, `'no-print'`) |
| `hint` | всплывающая подсказка в шапке |
| `group` | название группы: соседние колонки с одинаковым `group` получают общий заголовок над собой; `groupTitle` — узел вместо текста |
| `input` | `true` — колонку заполняет человек: оранжевая шапка (как в таблице заказчика) и тесные отступы под поле |
| `footer` | итог под колонкой: значение или `(rows) => …`; если задан хоть у одной колонки — появляется строка итогов |
| `hidden` | `true` — колонку не показывать |

**Опции таблицы:**

| Опция | Значение |
|---|---|
| `rows` | массив строк |
| `sort` | `{key, dir: 'asc'|'desc'}` или `null`; клик по шапке: по возрастанию → по убыванию → исходный порядок |
| `onSort(sort)` | сообщает о смене сортировки (чтобы запомнить) |
| `onRowClick(row, event)` | клик по строке (и Enter на строке); клики по кнопкам, ссылкам, полям внутри строки не считаются; элемент с атрибутом `data-no-row-click` тоже исключается. Клик по самой ячейке с полем мимо поля кит не отличает — такие ячейки страница исключает сама по `event.target` (на главной — класс `fl-noclick`) |
| `rowClass(row, i)` | классы строки: `row-red`, `row-amber`, `row-muted` или свои |
| `empty` | текст или узел (`ui.emptyState(…)`) для пустой таблицы |
| `sticky` | липкая шапка (по умолчанию `true`): таблица прокручивается внутри своей рамки, шапка остаётся на месте |
| `fill` | `true` — занять остаток высоты окна. Нужен `root.classList.add('page-fill')`. Для главной таблицы экрана |
| `maxHeight` | своя высота области прокрутки (`320`, `'50vh'`, `'none'`); без `fill` и `maxHeight` — `calc(100dvh - 200px)`, внутри модального окна — `48vh` |
| `zebra` | полосатые строки (по умолчанию `true`) |
| `inputNav` | `true` — в полях ввода внутри таблицы Enter/↓ переходят на строку ниже в той же колонке, Shift+Enter/↑ — выше (главная: «Новое показание») |
| `cls` | класс обёртки (на главной — `print-blank`: поля при печати остаются пустыми рамками) |

**Методы результата:** `setRows(rows)`, `update()` (перерисовать те же строки после изменения данных), `setColumns(columns)`, `setSort(sort)`, `getSort()`, `getRows()` (строки в показанном порядке), `setEmpty(content)`, свойство `.table` (сам `<table>`).

Перерисовка (`setRows`, `update`, сортировка) пересоздаёт ячейки — значения полей ввода внутри таблицы храните в своих данных (например, в `Map` по id машины) и подставляйте в `render`.

Колонки ввода (главная): «Новое показание» — кремовое поле у каждой машины; «Последнее ТО» — текст, а у машин без внесённого ТО — кремовые поля (класс `cell-input` на такой ячейке задаётся функцией):
```js
{ key: 'new', title: 'Новое показание', input: true, sortable: false, cls: 'fl-noclick', render: (st) => newBox(st) },
{ key: 'last_to', title: 'Последнее ТО',
  cls: (st) => (st.m.last_to ? null : 'cell-input fl-noclick'),
  render: (st) => (st.m.last_to ? lastToCell(st.m) : toBox(st)) },
```
Если несколько соседних колонок ввода нужно объединить общим заголовком, у них задают одинаковый `group` (например, `group: 'Последнее ТО — если не внесено'`).

Действия в строке: колонка `{ title: '', cls: 'cell-actions', render: (r) => h('div.row-actions', null, ui.button('ТО сделано', { small: true, kind: 'accent', onClick }), …) }`.

---

## Модальные окна, подтверждения, тосты

### `modal({title, body, buttons, wide, width, onClose, closeOnBackdrop, cls})` → контроллер

- `body` — узел, строка или массив; `wide: true` — широкое окно (980px; обычное — 560px); `width` — своя ширина.
- `buttons`: `[{label, kind, icon, primary, onClick, value, align, disabled, title}]`
  - **без `onClick`** кнопка закрывает окно и отдаёт `value`;
  - **с `onClick(m)`** окно само не закрывается: обработчик делает работу и вызывает `m.close(value)`. Пока он выполняется, кнопки заблокированы. Если он бросил исключение — текст ошибки появляется красной строкой внутри окна, окно остаётся открытым;
  - `primary: true` — кнопка, которую нажимает **Enter в однострочном поле** (и Ctrl+Enter в многострочном); если не указана — первая кнопка `kind: 'primary'`/`'accent'`;
  - `align: 'left'` — прижать влево (второстепенное действие, напр. «Отменить накинутое за период»).
- Esc и крестик закрывают окно с `undefined`. Фокус заперт внутри окна и при закрытии возвращается туда, где был. Фокус при открытии — поле с `autofocus: true`, иначе первое поле.
- Контроллер: `m.result` (Promise со значением из `close`), `m.close(value)`, `m.body` (элемент содержимого), `m.buttons` (элементы кнопок по порядку), `m.setError(text)`, `m.setBusy(bool)`, `m.setTitle(text)`, `m.focusFirst()`, `m.el`.
- Поле не должно отправлять форму по Enter — поставьте ему атрибут `data-no-enter`.

Типовой диалог (возвращает `Promise<boolean>`):

```js
export function openTyreDialog(ctx, tyre) {
  const { ui, api } = ctx;
  const { h } = ui;
  const no = ui.textInput({ value: tyre?.tyre_no, autofocus: true });
  const resource = ui.numInput({ value: tyre?.resource, decimals: 0 });
  const fNo = ui.field('№ шины', no, { required: true });

  const m = ui.modal({
    title: tyre ? 'Шина' : 'Новая шина',
    body: h('div.form-grid', null, fNo, ui.field('Ресурс, км', resource)),
    buttons: [
      { label: 'Отмена', value: false },
      { label: 'Сохранить', kind: 'primary', primary: true, onClick: async () => {
          fNo.setError(no.getValue() ? '' : 'Укажите номер шины');
          if (!no.getValue()) return;
          const body = { tyre_no: no.getValue(), resource: resource.getValue() };
          if (tyre) await api.put(`/tyres/${tyre.id}`, body); else await api.post('/tyres', body);
          ui.toast('Сохранено');
          m.close(true);
        } },
    ],
  });
  return m.result.then((v) => v === true);
}
```

### `confirm(text, {danger, details, title, okLabel, cancelLabel})` → `Promise<boolean>`
Вопрос «да/нет». `details` — строка, массив строк (список) или узел. `danger: true` — красная кнопка, фокус стоит на «Отмена» (случайный Enter ничего не удалит).
```js
if (await ui.confirm('Отменить накинутое за 01.10–02.10?', { danger: true, okLabel: 'Отменить накинутое' })) { … }
```

### `alert(text, {title, details, okLabel})` → `Promise`
Сообщение с одной кнопкой «Понятно» (итог импорта и т. п.).

### `toast(text, kind = 'ok', {timeout})`
Короткое сообщение в правом нижнем углу. `kind`: `'ok'` (зелёный — «Сохранено») | `'error'` | `'warn'` | `'info'`. Исчезает само (ошибки висят дольше).

---

## Статусы, вкладки, состояния

### `statusChip(kind, label?)` → `<span class="chip …">`
`kind` | цвет | подпись по умолчанию
---|---|---
`overdue` | красный | 🔴 Просрочено
`soon` | янтарный | 🟡 Скоро ТО
`ok` | зелёный | 🟢 В норме
`none` / `no_to` / `no_reading` | серый | ⚪ Нет данных / ⚪ Внесите ТО / ⚪ Нет показаний
`line` | зелёный | 🟢 На линии
`repair` | красный | 🔴 На ремонте
`to` | синий | 🔧 На ТО
`idle` | янтарный | 🟡 Простой
`conserved` | серый | 🅿 Консервация
`red` / `yellow` / `green` / `check` | (шины, узлы) | 🔴 Менять / 🟡 Скоро менять / 🟢 Норма / ⚪ Проверьте
`amber` / `grey` / `blue` | просто цвет | — (подпись обязательна)

Значения `to_status`, `state`, `status` из API передаются как есть, вторым аргументом — подпись с сервера: `ui.statusChip(m.to_status, m.to_status_label)`, `ui.statusChip(m.state, m.state_label)`, `ui.statusChip(t.status, t.status_label)`. Пустая подпись (`''`) — чип не рисуется.

### `tabs({items, active, onChange, cls})` → `div.tabs`
`items`: `[{key, label, icon, count, disabled}]`. Содержимое вкладки рисует страница в `onChange(key)`. Методы: `setActive(key, silent?)`, `getActive()`. Стрелки ←→ переключают.
```js
const panel = h('div');
const t = ui.tabs({ items: [{ key: 'all', label: 'Все' }, { key: 'to', label: 'ТО', count: 4 }], active: 'all', onChange: draw });
root.append(t, panel);
draw('all');
```

### `emptyState(text, {icon, title, action})`
Пустое состояние: иконка, заголовок, одна фраза и одна кнопка «с чего начать». `action` — `{label, onClick, kind, icon}` или готовый узел.

### `loading(text = 'Загрузка…')`
Тихая строка загрузки — для частей экрана, которые подгружаются отдельно (первую загрузку страницы показывает роутер).

### `errorState(err, onRetry?, {title})`
Блок ошибки с кнопкой «Повторить» — для части экрана: `catch (e) { ui.mount(box, ui.errorState(e, load)); }`.

### `notice(text, kind = 'info', {action})`
Строка-сообщение над таблицей. `kind`: `'info'` | `'warn'` | `'error'` | `'ok'`. `text` — строка или узлы; `action` — `{label, onClick, icon}` или узел.
```js
ui.notice(`Карточка показана на ${ui.fmtDate(date)}: показание, статус ТО и состояние посчитаны на эту дату.`, 'info',
  { action: { label: 'Показать на сегодня', onClick: () => ctx.navigate(`#/machine/${m.id}`) } })
```
Раскрывающийся список предупреждений — обычный `<details class="details">` внутри текста: `ui.notice(h('details.details', null, h('summary', null, 'Есть замечания по данным: 3'), h('ul', null, …)), 'warn')`.

### `kpi({label, value, kind, active, onClick, hint})` — плитка-счётчик
`kind` — цвет числа: `red` | `amber` | `green` | `grey` | `blue`. С `onClick` плитка становится кнопкой-фильтром; `active` — подсветка выбранной. Методы: `setActive(bool)`, `setValue(v)`. Плитки кладутся в `h('div.kpis', null, …)`.

---

## Форматтеры, числа, даты

| Функция | Пример |
|---|---|
| `fmtNum(v, digits = 2)` | `fmtNum(1234567)` → «1 234 567»; `fmtNum(1250.5)` → «1 250,5»; `fmtNum(-2000)` → «−2 000»; пусто → `''` |
| `fmtReading(v, unit, estimated)` | `fmtReading(153000, 'км', true)` → «≈ 153 000 км»; км — целые, м/ч — до 0,1; пусто → «—» |
| `fmtDate(iso)` | `'2026-10-02'` → «02.10.2026» |
| `fmtEventDate(o)` | `{date, date_unknown}` → «02.10.2026» или «дата не указана» (событие/`last_to` без даты) |
| `eventDateCell(e)` | ячейка «Дата» журнала: дата или серое «дата не указана» + мелко «внесено 02.10.2026» |
| `fmtDateShort(iso)` | `'2026-11-12'` → «12.11» |
| `fmtDateTime(iso)` | `'2026-10-02T14:30:05'` → «02.10.2026 14:30» |
| `fmtDuration(seconds)` | `98400` → «1 д 3 ч 20 мин» |
| `fmtHours(hours, digits = 1)` | `7.5` → «7,5 ч» |
| `fmtPct(ratio, digits = 0)` | `0.874` → «87 %» |
| `plural(n, one, few, many)` | `plural(5, 'машина', 'машины', 'машин')` → «машин» |
| `parseNum(text)` | «153 000», «1250,5», «153000 км» → число; пусто/не число → `null` |
| `parseTime(text)` | «130» → `'01:30'`; пусто → `''`; не время → `null` |
| `today()` | сегодняшняя дата `'YYYY-MM-DD'` по часам компьютера |
| `nowTime()` | текущее время `'HH:MM'` |
| `addDays(iso, n)` | `addDays('2026-10-31', 1)` → `'2026-11-01'` |
| `daysBetween(a, b)` | число дней от `a` до `b` |

Числа выводятся с неразрывными пробелами (не переносятся посреди числа). Все пустые значения (`null`, `undefined`, `''`) дают пустую строку (у `fmtReading` — «—»).

### `printPage(title?, {landscape})`
Печать текущего экрана: `ui.printPage('Машины и ТО на 02.10.2026', {landscape: true})`. Заголовок печатается сверху листа и подставляется в имя PDF; без заголовка — печатается только содержимое (заказ-наряд). `landscape: true` — альбомная ориентация для широких таблиц. См. [Печать](#печать).

---

## previewTo

`previewTo(machine, {kind, reading, current}, settings)` → `{next_kind, next_at, remaining, status}` — **единственный расчёт, разрешённый на фронте** (живая подсказка в диалогах; всё остальное считает сервер).

- `machine` — MachineView (`interval`, `warn`, `unit`, `reading`); `kind` — вид вносимого ТО; `reading` — показание на момент ТО; `current` — показание сейчас (пусто → `machine.reading`); `settings` — `ctx.boot.settings`.
- `next_at = reading + interval`; `remaining = next_at − current`; `next_kind` — следующий после `kind` по кругу `settings.to_cycle`; `status`: `'overdue'` (≤ 0) | `'soon'` (≤ `warn`) | `'ok'` | `'none'` (не хватает данных, тогда `next_at`/`remaining` могут быть `null`).
- `reading`/`current` можно передавать прямо из `numInput.getValue()`.

```js
const p = ui.previewTo(m, { kind: kind.getValue(), reading: atTo.getValue(), current: now.getValue() }, ctx.boot.settings);
ui.mount(hint, p.next_at === null ? 'Введите показание на момент ТО'
  : `Следующее: ${p.next_kind} на ${ui.fmtReading(p.next_at, m.unit)}, останется ${ui.fmtNum(p.remaining)}`);
```

---

## CSS-классы

**Кнопки:** `.btn` (обычная) · `.btn-primary` · `.btn-accent` · `.btn-danger` · `.btn-ghost` · размеры `.btn-big` (40px), `.btn-sm` (26px) · `.btn-icon` (квадратная) · `button.link` (кнопка-ссылка в тексте). Ссылка, выглядящая кнопкой: `h('a.btn.btn-primary', { href: '#/plan' }, ui.icon('calendar-check'), h('span', null, 'Список на ТО'))` (так сделана кнопка «Список на ТО» на главной).

**Раскладка:**
| Класс | Назначение |
|---|---|
| `.page-fill` | на `root`: экран с одной главной таблицей (`table({fill: true})`) |
| `.toolbar` | ряд действий и фильтров над таблицей (на печати скрыт); поля внутри — фиксированной ширины; `.spacer` — распорка, `.toolbar-label` — серая подпись |
| `.card`, `.card-title` | белая карточка с рамкой и её заголовок |
| `.cards` | сетка карточек (автоколонки от 320px) |
| `.stack` | колонка с отступом 16px между блоками |
| `.row` | ряд с отступом 12px и переносом |
| `.section-title` (или просто `h2` на странице) | заголовок раздела 16px |
| `.kpis`, `.kpi`, `.kpi-value`, `.kpi-label`, `.kpi-red/-amber/-green/-grey/-blue`, `.kpi.active` | плитки-счётчики (см. `ui.kpi`) |
| `.kv` | список «подпись — значение»: `h('dl.kv', null, h('dt', null, 'VIN'), h('dd', null, m.vin))` |
| `.form-grid`, `.form-grid-3`, `.form-grid-4`, `.span-2`, `.span-3`, `.span-all`, `.form-row` | сетки форм |
| `.field`, `.field-label`, `.field-hint`, `.field-error` | части `ui.field` |

**Поля:** `.input` (кремовое поле; ставится китом) · `.input-num` · `.input-time` · `.input-date` · `.textarea` · `.select` · `.checkbox` · `.invalid` (красная рамка) · `.readout` (серое табло для расчётного значения).

**Таблица:** `.table-wrap`, `.table` (делает `ui.table`) · строки: `.row-red`, `.row-amber`, `.row-muted` · ячейки: `.cell-input` (ячейка с полем), `.cell-actions` + `.row-actions` (кнопки в строке).

**Чипы:** `.chip` + `.chip-red` | `.chip-amber` | `.chip-green` | `.chip-grey` | `.chip-blue` (обычно через `ui.statusChip`; дополнительно ставится `chip-<kind>`: `chip-overdue`, `chip-repair`…).

**Текст и ячейки:** `.muted` (серый второстепенный) · `.num` (вправо, без переноса — числа) · `.strong` (600) · `.small` (13px) · `.nowrap` · `.ta-center` · `.ta-right` · `.text-red` · `.text-amber` · `.text-green` · `.ellipsis` (обрезка с «…», макс. 280px).

**Сообщения и состояния:** `.notice` + `.notice-warn` | `.notice-error` | `.notice-ok` · `.empty` · `.loading` · `.error-state` · `details.details`.

**Чипы-фильтры** (главная; не путать со статус-чипами `.chip`): `.filter-chips` — ряд (flex, перенос, отступ снизу 12px; на печати скрыт) из кнопок `.filter-chip` — переключателей в форме таблетки высотой 30px.
Внутри чипа подпись, `.filter-chip-sep` (точка-разделитель) и `.filter-chip-count` (число). Цвет — модификатор `.filter-chip-red` | `-amber` | `-grey` | `-blue` (как у статус-чипа), у «Все» — `.filter-chip-all`; `.filter-chip-zero` — чип с нулём, без цвета (тревожить нечем).
Включённость задаёт только атрибут `aria-pressed="true"`: чип заливается своим цветом, «Все» получает синюю обводку. Ряд принято завершать `h('div.spacer')`, счётчиком «N из M» и кнопкой «Сбросить».
```js
h('div.filter-chips', { role: 'group', 'aria-label': 'Отбор машин' },
  h('button.filter-chip.filter-chip-red', { type: 'button', 'aria-pressed': on ? 'true' : 'false', onclick: () => pick('overdue') },
    h('span', null, '🔴 Просрочено ТО'), h('span.filter-chip-sep', { 'aria-hidden': 'true' }, '·'), h('span.filter-chip-count', null, '12')),
  h('div.spacer'), ui.button('Сбросить', { kind: 'ghost', small: true, icon: 'x', onClick: reset }))
```

**Панель сохранения** `.save-bar` (главная): липкая (`position: sticky; bottom: 0`) полоса внизу страницы с оранжевой меткой слева — как шапка колонок ввода; на печати скрыта. Кладётся последним ребёнком `.page-fill` и показывается (`hidden = false`), пока в таблице есть несохранённый ввод.
Внутри: `.save-bar-count` (жирный счётчик «Показаний: 3 · последних ТО: 2»), подпись `.toolbar-label`, `dateInput` (в панели его ширина 150px), `.spacer`, кнопки «Очистить» (`ghost`) и «Сохранить всё» (`accent`, `big`).
```js
const bar = h('div.save-bar.no-print', { hidden: true },
  h('span.save-bar-count'), h('span.toolbar-label', null, 'Дата показаний'), dateInp, h('div.spacer'),
  ui.button('Очистить', { kind: 'ghost', icon: 'x', onClick: clearAll }),
  ui.button('Сохранить всё', { kind: 'accent', big: true, icon: 'check', onClick: save }));
root.append(toolbar, chips, tbl, bar);
```

**Боковая панель:** `.nav-caption` — подпись-заголовок группы пунктов («Служебное»): 11.5px, заглавные, разрядка, цвет `--sidebar-caption` (токен боковой панели: тише пунктов, контраст 4.9:1 на фоне панели), не ссылка. Оболочка строит её из строки в массиве `NAV` (`app.js`); `.nav-sep` — простой разделитель.

**Печать:** `.no-print` (скрыть на бумаге) · `.print-only` (показать только на бумаге) · `.print-blank` (на таблице или её обёртке: кремовые поля печатаются **пустыми рамками**, а не подчёркнутым текстом — бланк, куда вписывают от руки; на главной таблица печатается именно так).

Размеры из контракта: шаг 4px (4/8/12/16/24/32), строка таблицы 36px, поля и кнопки 34px, главные кнопки 40px, радиус 6px, шрифт 14px (в таблицах 13px), заголовок раздела 16px. Тени — только у окон и выпадающих списков. Анимаций у таблиц и карточек нет.

---

## Печать

`css/print.css` при печати сам:
- скрывает боковую панель, верхнюю строку, `.toolbar`, `.tabs`, `.kpis`, `.filter-chips`, `.save-bar`, все `.btn`, тосты, окна;
- делает всё чёрным по белому, таблицы — с рамками, шапка повторяется на каждом листе, прокрутка таблиц раскрывается целиком;
- чипы печатает обычным текстом, кремовые поля — подчёркнутым текстом; внутри `.print-blank` кремовые поля остаются пустыми рамками (бланк для обхода);
- формат A4 книжный, поля 12 мм (`printPage(title, {landscape: true})` — альбомный).

Что делает страница: всё, что не нужно на бумаге, помечает `.no-print` (колонки действий: `cls: 'cell-actions no-print', headCls: 'no-print'`); то, что нужно только на бумаге (подписи, дата отчёта), — `.print-only`. Таблицу, которую печатают как бланк для ручного заполнения (главная), помечают `cls: 'print-blank'`. Свои печатные правила — через `ui.addStyle(id, '@media print { … }')`.

---

## Иконки

Один набор — Tabler outline, файлы `app/static/icons/*.svg`, встроены в `js/icons.js`. `ui.icon(name, size)`; список имён — `ui.iconNames`:

`alert-triangle`, `arrow-left`, `arrow-right`, `calendar-check`, `calendar-plus`, `check`, `chevron-down`, `chevron-right`, `chevron-up`, `circle-check`, `clipboard-text`, `clock`, `database`, `dots`, `download`, `engine`, `external-link`, `file-spreadsheet`, `filter`, `gauge`, `history`, `info-circle`, `list-details`, `notebook`, `pencil`, `player-play`, `plus`, `printer`, `refresh`, `report`, `road`, `search`, `settings`, `tool`, `trash`, `truck`, `upload`, `wheel`, `x`.

Эмодзи — только кружки статусов и значки состояний, перенесённые из таблицы (их ставит `statusChip` и сервер в `*_label`); в остальных местах — иконки.

`js/icons.js` сгенерирован из папки `icons/`. После добавления нового svg (Tabler, `viewBox="0 0 24 24"`, `stroke="currentColor"`) пересоберите модуль из корня проекта:

```
.venv\Scripts\python.exe -c "import re,json,pathlib as p;s=p.Path('app/static');j=s/'js'/'icons.js';t=j.read_text(encoding='utf-8');a=t.index('const ICONS = {');b=t.index('};',a);d={f.stem:re.match(r'<svg\b[^>]*>(.*)</svg>\s*$',f.read_text(encoding='utf-8').strip(),re.S).group(1).strip() for f in sorted((s/'icons').glob('*.svg'))};j.write_text(t[:a]+'const ICONS = {\n'+''.join('  {}: {},\n'.format(json.dumps(k),json.dumps(v,ensure_ascii=False)) for k,v in d.items())+t[b:],encoding='utf-8',newline='\n')"
```
(команда заново заполняет объект `ICONS` в `icons.js`, остальной код файла не трогает).
