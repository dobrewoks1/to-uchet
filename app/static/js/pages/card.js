// Экран «Карточка машины» (docs/КОНЦЕПЦИЯ_v2.md, раздел 5; маршрут #/machine/{id}): всё об одной машине на одном экране —
// кнопка «← Все машины», блок ТО, состояние сегодня, машина с фото, замечания по данным, история, шины и узлы.
// Все числа и статусы считает сервер (GET /api/machines/{id}/card, там же список фото); здесь только показ и вызов общих диалогов.
// Фото — API из п. 9.2 концепции: POST/DELETE /api/machines/{id}/photos[/{name}].

import { tyreColumns, openTyreDialog, removeTyre } from './tyres.js';
import { unitColumns, openUnitDialog, removeUnit } from './units.js';

const KIND_MARK = 'Отметка моточасов';
const RETIRED = 'Списана';

// вкладки-фильтры истории по виду события
const HISTORY_TABS = [
  { key: 'all', label: 'Все', test: () => true },
  { key: 'to', label: 'ТО', test: (e) => !!e.is_to },
  { key: 'repair', label: 'Ремонт', test: (e) => String(e.kind).startsWith('Ремонт') },
  { key: 'idle', label: 'Простой', test: (e) => String(e.kind).startsWith('Простой') },
  { key: 'note', label: 'Заметки', test: (e) => String(e.kind).startsWith('Заметка') },
  { key: 'mark', label: 'Отметки показаний', test: (e) => e.kind === KIND_MARK },
];
// «Прочее» — виды событий, которые добавили в настройках
HISTORY_TABS.push({ key: 'other', label: 'Прочее', test: (e) => !HISTORY_TABS.slice(1, 6).some((t) => t.test(e)) });

// откуда взята норма для кнопки «Накинуть за день» (daily_norm_source)
const NORM_SOURCE = {
  override: 'своя норма машины',
  fact: 'фактическая средняя по показаниям',
  rule: 'норма для марки из настроек',
  default: 'общая норма из настроек',
};

// фото машины: что принимает сервер (КОНЦЕПЦИЯ_v2 п. 9.2)
const PHOTO_MAX_BYTES = 25 * 1024 * 1024;
const PHOTO_EXT = /\.(jpe?g|png|webp|gif)$/i;
const PHOTO_MIME = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif' };

const CSS = `
.mc-top { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.3fr) minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 1279px) {
  .mc-top { grid-template-columns: minmax(0, 1fr) minmax(0, 1.3fr); }
  .mc-top > .mc-today { grid-column: 1 / -1; }
}
.mc-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin-bottom: 12px; }
.mc-head .card-title { margin: 0; }
.mc-no { font-size: 20px; font-weight: 700; line-height: 1.2; }
.mc-model { margin: -8px 0 12px; color: var(--text-muted); }
.mc-cap { color: var(--text-muted); font-size: 13px; }
.mc-big { font-size: 28px; font-weight: 700; line-height: 1.15; }
.mc-big-text { font-size: 20px; font-weight: 600; line-height: 1.25; }
.mc-sub { margin-top: 4px; }
.mc-block { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }
.mc-block-title { margin-bottom: 8px; font-weight: 600; }
.mc-sec-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin: 24px 0 12px; }
.mc-sec-head h2 { margin: 0; }
.mc-hist td { vertical-align: top; }
.mc-text { min-width: 110px; overflow-wrap: anywhere; }
.mc-how > summary { color: var(--text-muted); }
.mc-how[open] > summary { margin-bottom: 8px; }
.mc-photos-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin-bottom: 8px; }
.mc-photos-head .mc-block-title { margin: 0; }
.mc-photos.mc-drop { outline: 2px dashed var(--accent-bright); outline-offset: 4px; border-radius: var(--radius); }
.mc-thumbs { display: flex; flex-wrap: wrap; gap: 8px; }
.mc-thumb {
  display: flex; flex-direction: column; gap: 4px; width: 96px; margin: 0; padding: 0;
  border: 0; background: none; color: inherit; font: inherit; text-align: left; cursor: zoom-in;
}
.mc-thumb img {
  display: block; width: 96px; height: 96px; object-fit: cover;
  border: 1px solid var(--border); border-radius: var(--radius); background: var(--grey-bg);
}
.mc-thumb:hover img { border-color: var(--border-strong); }
.mc-thumb:focus-visible { outline: 2px solid var(--accent-bright); outline-offset: 2px; border-radius: var(--radius); }
.mc-thumb-name { overflow: hidden; color: var(--text-muted); font-size: 13px; white-space: nowrap; text-overflow: ellipsis; }
.modal.mc-photo-modal { width: auto; min-width: min(360px, 90vw); max-width: 90vw; }
.mc-photo-modal .modal-title { overflow-wrap: anywhere; }
.mc-photo-modal .modal-body { padding: 12px; }
.mc-photo-full {
  display: block; max-width: calc(90vw - 24px); max-height: min(90vh, calc(100dvh - 230px)); margin: 0 auto;
  object-fit: contain; background: var(--grey-bg);
}
.mc-photo-meta { margin-top: 8px; }
@media print {
  .mc-top { display: block; }
  .mc-top > .card { margin-bottom: 10pt; }
  .mc-thumb img { border-color: currentColor; }
}
`;

function isEmpty(v) {
  return v === null || v === undefined || v === '';
}

export async function render(root, params, ctx) {
  const { ui, api, dialogs } = ctx;
  const { h } = ui;
  ui.addStyle('page-card-style', CSS);

  const machineId = encodeURIComponent(params.id);
  // карточку можно открыть на дату отчёта: #/machine/5?date=2026-10-01
  const onDate = /^\d{4}-\d{2}-\d{2}$/.test(params.date || '') && params.date !== ui.today() ? params.date : null;
  const hist = { kind: 'all', showAuto: false, sort: null }; // фильтр истории переживает обновление данных
  let howOpen = false; // «Как считается» в блоке ТО раскрыт — переживает перерисовку
  const photoLoad = { busy: false, text: '' }; // идёт загрузка фото — кнопка «Добавить фото» заблокирована
  let photoBtn = null; // кнопка «Добавить фото» последней отрисовки (draw() создаёт новую)
  let card = null;

  function notFound() {
    ctx.setTitle('Карточка машины');
    ui.mount(root, ui.emptyState('Такой машины в справочнике нет — возможно, её удалили.', {
      icon: 'truck', title: 'Машина не найдена',
      action: { label: 'Открыть список машин', onClick: () => ctx.navigate('#/machines') },
    }));
  }

  try {
    card = await api.get(`/machines/${machineId}/card`, { date: onDate });
  } catch (err) {
    if (err.status !== 404) throw err; // прочие ошибки покажет оболочка (текст + «Повторить»)
    if (root.isConnected) notFound();
    return;
  }
  if (!root.isConnected) return; // пока грузили, человек ушёл на другой экран
  draw();

  // фото: вставка снимка из буфера (Ctrl+V) и защита от файла, брошенного мимо блока «Фото»
  document.addEventListener('paste', onPaste);
  document.addEventListener('dragover', onDocDragOver);
  document.addEventListener('drop', onDocDrop);
  const cleanup = () => {
    document.removeEventListener('paste', onPaste);
    document.removeEventListener('dragover', onDocDragOver);
    document.removeEventListener('drop', onDocDrop);
  };

  /** Перечитать карточку после любого сохранения и перерисовать экран. */
  async function reload() {
    let fresh;
    try {
      fresh = await api.get(`/machines/${machineId}/card`, { date: onDate });
    } catch (err) {
      if (!root.isConnected) return;
      if (err.status === 404) notFound();
      else ui.toast(`Не получилось обновить карточку: ${err.message}`, 'error');
      return;
    }
    if (!root.isConnected) return;
    card = fresh;
    draw();
  }

  /** Диалог вернул true (что-то сохранено) — обновляем карточку. */
  async function after(saving) {
    if (await saving) await reload();
  }

  function draw() {
    const m = card.machine;
    ctx.setTitle(`${m.garage_no}${m.model ? ` · ${m.model}` : ''}`);
    ui.mount(root,
      actionBar(m),
      m.status === RETIRED ? ui.notice(
        'Машина списана: в парк, отчёты, план ТО и ввод показаний она не попадает, история сохранена. '
        + 'Вернуть в работу — «Изменить машину», поле «Статус».') : null,
      onDate ? ui.notice(`Карточка показана на ${ui.fmtDate(onDate)}: показание, статус ТО и состояние посчитаны на эту дату.`, 'info',
        { action: { label: 'Показать на сегодня', onClick: () => ctx.navigate(`#/machine/${m.id}`) } }) : null,
      warningsNotice(m),
      h('div.mc-top', null, machineCard(m), toCard(m), todayCard(m)),
      historySection(m),
      tyresSection(m),
      unitsSection(m));
  }

  // ───────────── кнопки действий ─────────────

  function openEvent(id) {
    return (card.events || []).find((e) => e.id === id) || null;
  }

  function actionBar(m) {
    const active = m.status !== RETIRED;
    const stateEvent = openEvent(m.state_event_id);
    const canClose = active && (m.state === 'repair' || m.state === 'idle') && stateEvent && stateEvent.open;
    return h('div.toolbar', null,
      // фильтры главной живут в памяти её модуля — список откроется в том же виде
      ui.button('Все машины', { big: true, icon: 'arrow-left', title: 'Вернуться на главную — к списку всех машин',
        onClick: () => ctx.navigate('#/fleet') }),
      active ? ui.button('ТО сделано', { kind: 'accent', big: true, icon: 'check',
        onClick: () => after(dialogs.openToDoneDialog(ctx, m.id)) }) : null,
      active ? ui.button('Показание', { big: true, icon: 'gauge', title: 'Ввести текущее показание счётчика',
        onClick: () => after(dialogs.openReadingDialog(ctx, m.id)) }) : null,
      active ? ui.button('Запись', { big: true, icon: 'plus', title: 'Запись: ремонт, простой, заметка',
        onClick: () => after(dialogs.openEventDialog(ctx, { machineId: m.id })) }) : null,
      canClose ? ui.button(m.state === 'repair' ? 'Закрыть ремонт' : 'Закрыть простой', { big: true, icon: 'circle-check',
        onClick: () => after(dialogs.openCloseRepairDialog(ctx, stateEvent)) }) : null,
      h('div.spacer'),
      ui.button('Заказ-наряд', { icon: 'clipboard-text',
        onClick: () => ctx.navigate(`#/workorder?machine_id=${m.id}&date=${onDate || ui.today()}`) }),
      ui.button('Изменить машину', { icon: 'pencil', onClick: () => after(dialogs.openMachineDialog(ctx, m)) }),
      ui.button('Печать', { icon: 'printer',
        onClick: () => ui.printPage(`Карточка машины ${m.garage_no} на ${ui.fmtDate(onDate || ui.today())}`) }));
  }

  // ───────────── замечания по данным ─────────────

  function warningsNotice(m) {
    const items = Array.isArray(m.data_warning_items) && m.data_warning_items.length
      ? m.data_warning_items
      : (m.data_warnings || []).map((text) => ({ text }));
    if (!items.length) return null;
    const n = items.length;
    return ui.notice([
      h('div.strong', null, `Проверьте записи: ${n} ${ui.plural(n, 'замечание', 'замечания', 'замечаний')} по данным этой машины`),
      h('ul', null, items.map((w) => {
        const ev = openEvent(w.event_id);
        return h('li', null, w.text, ev && ev.source !== 'auto'
          ? [' ', h('button.link.no-print', { type: 'button', onclick: () => after(dialogs.openEventDialog(ctx, { event: ev })) }, 'открыть запись')]
          : null);
      })),
    ], 'warn');
  }

  // ───────────── верхние блоки: машина, ТО, сегодня ─────────────

  /** Список «подпись — значение»; пустое значение показывается прочерком. */
  function kv(pairs) {
    return h('dl.kv', null, pairs.filter(Boolean).map(([label, value]) => [
      h('dt', null, label),
      h('dd', null, isEmpty(value) ? h('span.muted', null, '—') : value),
    ]));
  }

  function machineCard(m) {
    // старое поле «Фото» (ссылка) — показываем, только если его заполняли; новые фото — блок ниже
    const photoLink = m.photo && /^https?:\/\//i.test(m.photo)
      ? h('a', { href: m.photo, target: '_blank', rel: 'noopener' }, 'открыть ссылку')
      : m.photo;
    return h('div.card', null,
      h('div.mc-head', null,
        h('div.mc-no', null, m.garage_no),
        m.status !== 'В работе' ? ui.statusChip('grey', m.status) : null),
      h('div.mc-model', null, m.model || 'модель не указана'),
      kv([
        ['Тип', m.mtype],
        ['Госномер', m.plate],
        ['VIN', m.vin],
        ['Участок', m.site],
        ['Гарантия', m.warranty],
        m.note ? ['Примечание', m.note] : null,
        photoLink ? ['Ссылка на фото', photoLink] : null,
      ]),
      photoBlock());
  }

  // ───────────── фото машины (КОНЦЕПЦИЯ_v2 п. 5.1; API — п. 9.2) ─────────────

  /** Список фото из ответа карточки; поля нет (сервер ещё старый) — просто пусто. */
  function photoList() {
    return Array.isArray(card.photos) ? card.photos : [];
  }

  /** Кнопка «Добавить фото» показывает, что идёт загрузка (переживает перерисовку карточки). */
  function paintPhotoBtn() {
    if (!photoBtn) return;
    photoBtn.disabled = photoLoad.busy;
    photoBtn.classList.toggle('busy', photoLoad.busy);
    const label = photoBtn.querySelector('span');
    if (label) label.textContent = photoLoad.busy ? photoLoad.text : 'Добавить фото';
  }

  /** Перетаскивают файлы (а не текст или картинку со страницы). */
  function hasFiles(e) {
    const types = e.dataTransfer && e.dataTransfer.types;
    return !!types && Array.from(types).includes('Files');
  }

  function pad2(n) {
    return String(n).padStart(2, '0');
  }

  /** Имя для снимка из буфера (там у файла имя «image.png»): foto-ГГГГММДД-ЧЧММСС — как у сервера для пустого имени. */
  function stampName() {
    const d = new Date();
    return `foto-${d.getFullYear()}${pad2(d.getMonth() + 1)}${pad2(d.getDate())}-${pad2(d.getHours())}${pad2(d.getMinutes())}${pad2(d.getSeconds())}`;
  }

  /** Файл, который примет сервер: jpg/png/webp/gif по расширению; без расширения, но с типом фото — допишем его. Иначе null. */
  function photoFile(file) {
    const name = file.name || '';
    if (PHOTO_EXT.test(name)) return file;
    const ext = PHOTO_MIME[file.type];
    if (!ext) return null;
    return new File([file], `${name.replace(/\.[^.]*$/, '') || stampName()}.${ext}`, { type: file.type });
  }

  function fmtSize(bytes) {
    const n = Number(bytes);
    if (!Number.isFinite(n) || n <= 0) return '';
    return n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} КБ` : `${ui.fmtNum(n / (1024 * 1024), 1)} МБ`;
  }

  /** mtime с сервера: секунды/миллисекунды или ISO-строка → «02.10.2026 14:30»; непонятное — ''. */
  function fmtMtime(mtime) {
    if (isEmpty(mtime)) return '';
    const d = typeof mtime === 'number' ? new Date(mtime < 1e12 ? mtime * 1000 : mtime) : new Date(String(mtime).replace(' ', 'T'));
    if (Number.isNaN(d.getTime())) return '';
    return ui.fmtDateTime(`${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`);
  }

  async function pickPhotos() {
    const files = await ui.pickFile('image/*', { multiple: true });
    if (files.length) await uploadPhotos(files);
  }

  /**
   * Загрузить фото по одному (выбор, перетаскивание, вставка из буфера) и перечитать карточку.
   * Ошибки — тостом: не фото, больше 25 МБ, отказ сервера (его текст).
   */
  async function uploadPhotos(files) {
    if (photoLoad.busy) {
      ui.toast('Подождите: предыдущие фото ещё загружаются', 'info');
      return;
    }
    const problems = [];
    const queue = [];
    for (const f of files) {
      const pf = photoFile(f);
      if (!pf) problems.push(`«${f.name || 'без имени'}» — такой файл не подходит (можно фото jpg, png, webp, gif)`);
      else if (pf.size > PHOTO_MAX_BYTES) problems.push(`«${pf.name}» — файл больше 25 МБ`);
      else queue.push(pf);
    }
    let added = 0;
    if (queue.length) {
      photoLoad.busy = true;
      try {
        for (let i = 0; i < queue.length; i += 1) {
          photoLoad.text = queue.length > 1 ? `Загружаю ${i + 1} из ${queue.length}…` : 'Загружаю…';
          paintPhotoBtn();
          const f = queue[i];
          try {
            await api.upload(`/machines/${machineId}/photos`, f, { name: f.name });
            added += 1;
          } catch (err) {
            problems.push(`«${f.name}» — ${err.message}`);
            if (err.status === 0) break; // связи с программой нет — остальные тоже не уйдут
          }
        }
      } finally {
        photoLoad.busy = false;
        photoLoad.text = '';
        paintPhotoBtn();
      }
    }
    if (added) ui.toast(added === 1 ? 'Фото добавлено' : `Добавлено фото: ${added}`);
    if (problems.length) {
      ui.toast(`${problems.length === 1 ? 'Фото не добавлено' : `Не добавлено фото: ${problems.length}`}. ${problems.join('; ')}`, 'error');
    }
    if (added && root.isConnected) await reload();
  }

  /** Фото во весь размер: имя, размер, «Удалить». */
  function openPhoto(p) {
    const meta = [fmtSize(p.size), fmtMtime(p.mtime) ? `добавлено ${fmtMtime(p.mtime)}` : ''].filter(Boolean).join(' · ');
    const viewer = ui.modal({
      title: p.name,
      cls: 'mc-photo-modal',
      body: [
        h('img.mc-photo-full', { src: p.url, alt: p.name }),
        h('div.mc-photo-meta.muted.small', null,
          meta ? `${meta} · ` : null,
          h('a', { href: p.url, target: '_blank', rel: 'noopener' }, 'открыть в отдельной вкладке')),
      ],
      buttons: [
        { label: 'Удалить', kind: 'danger', icon: 'trash', align: 'left', onClick: async (mm) => {
          const ok = await ui.confirm(`Удалить фото «${p.name}»?`, {
            danger: true, title: 'Удалить фото?', okLabel: 'Удалить',
            details: 'Файл удалится из папки данных программы, вернуть его будет нельзя.',
          });
          if (!ok) return;
          await api.del(`/machines/${machineId}/photos/${encodeURIComponent(p.name)}`);
          mm.close(true);
        } },
        { label: 'Закрыть', value: false },
      ],
    });
    viewer.buttons[1].focus();
    viewer.result.then(async (deleted) => {
      if (deleted !== true) return;
      ui.toast('Фото удалено');
      if (root.isConnected) await reload();
    });
  }

  function photoBlock() {
    const list = photoList();
    photoBtn = ui.button('Добавить фото', {
      small: true, icon: 'upload', cls: 'no-print',
      title: 'Выбрать фото на компьютере. Ещё можно перетащить файлы на этот блок или вставить снимок из буфера (Ctrl+V)',
      onClick: () => {
        pickPhotos(); // не возвращаем обещание: занятость кнопки ведёт paintPhotoBtn, ошибки ловит uploadPhotos
      },
    });
    paintPhotoBtn();

    let depth = 0; // dragenter/dragleave приходят и от вложенных элементов — считаем глубину
    const zone = h('div.mc-block.mc-photos', {
      class: !list.length && 'no-print',
      ondragenter: (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        depth += 1;
        zone.classList.add('mc-drop');
      },
      ondragover: (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
      },
      ondragleave: (e) => {
        if (!hasFiles(e)) return;
        depth = Math.max(0, depth - 1);
        if (!depth) zone.classList.remove('mc-drop');
      },
      ondrop: (e) => {
        if (!hasFiles(e)) return;
        e.preventDefault();
        depth = 0;
        zone.classList.remove('mc-drop');
        uploadPhotos(Array.from(e.dataTransfer.files || []));
      },
    },
    h('div.mc-photos-head', null,
      h('div.mc-block-title', null, 'Фото'),
      list.length ? h('span.muted.small', null, `${list.length} шт.`) : null,
      h('div.spacer'),
      photoBtn),
    list.length
      ? h('div.mc-thumbs', null, list.map((p) => h('button.mc-thumb', { type: 'button', title: p.name, onclick: () => openPhoto(p) },
        h('img', { src: p.url, alt: p.name, loading: 'lazy', decoding: 'async' }),
        h('span.mc-thumb-name', null, p.name))))
      : h('div.muted', null, 'Фото пока нет — например, табличка с VIN или документы.',
        h('div.small', null, 'Можно перетащить файлы сюда или вставить снимок из буфера (Ctrl+V).')));
    return zone;
  }

  /** Курсор стоит в поле, куда вставляют текст, — вставку не перехватываем. */
  function isTypingTarget(t) {
    if (!(t instanceof Element)) return false;
    if (t.isContentEditable || t instanceof HTMLTextAreaElement) return true;
    return t instanceof HTMLInputElement
      && !['checkbox', 'radio', 'button', 'submit', 'reset', 'file', 'range', 'color'].includes(t.type);
  }

  /** Ctrl+V со снимком в буфере — добавить фото (если курсор не в поле ввода и не открыт диалог). */
  function onPaste(e) {
    if (!photoBtn || !photoBtn.isConnected || document.querySelector('.modal-overlay')) return;
    if (isTypingTarget(e.target)) return;
    const files = Array.from((e.clipboardData && e.clipboardData.files) || []).filter((f) => String(f.type).startsWith('image/'));
    if (!files.length) return;
    e.preventDefault();
    // снимок экрана приходит как «image.png» — даём имя с датой; файл, скопированный в Проводнике, оставляем со своим именем
    const stamp = stampName();
    uploadPhotos(files.map((f, i) => {
      const ext = PHOTO_MIME[f.type];
      const generic = !f.name || /^image\.\w+$/i.test(f.name);
      return ext && generic ? new File([f], `${stamp}${i ? `-${i + 1}` : ''}.${ext}`, { type: f.type }) : f;
    }));
  }

  // файл, брошенный мимо блока «Фото», браузер открыл бы вместо программы — не даём (курсор «нельзя»)
  function onDocDragOver(e) {
    if (e.defaultPrevented || !hasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'none';
  }

  function onDocDrop(e) {
    if (e.defaultPrevented || !hasFiles(e)) return;
    e.preventDefault();
  }

  /** Откуда у машины единица, интервал и порог ТО. */
  function paramsSource(m) {
    const own = [
      m.unit_override ? 'единица' : '',
      !isEmpty(m.interval_override) ? 'интервал' : '',
      !isEmpty(m.warn_override) ? 'порог' : '',
    ].filter(Boolean);
    if (own.length) return `задано у этой машины: ${own.join(', ')}`;
    if (m.rule_keyword) return `по правилу для марки ${m.rule_keyword} (настройки)`;
    return 'общие значения из настроек';
  }

  /** Простыми словами: как получена средняя наработка в сутки. */
  function rateSource(m) {
    if (m.rate_source === 'fact') {
      return `посчитано по вашим показаниям за ${m.rate_days} ${ui.plural(m.rate_days, 'день', 'дня', 'дней')}`;
    }
    const norm = !isEmpty(m.norm_override) ? 'своя норма машины'
      : m.daily_norm_source === 'rule' ? 'норма для марки из настроек' : 'общая норма из настроек';
    return `фактических показаний пока мало, поэтому взята ${norm}`;
  }

  function toCard(m) {
    const u = m.unit;
    const est = !!m.reading_estimated;
    const active = m.status !== RETIRED; // у списанной машины кнопок «ТО сделано» и «Показание» нет
    const reg = card.regulation_next;
    let head;
    if (m.to_status === 'overdue') {
      head = [
        h('div.mc-cap', null, 'ТО просрочено на'),
        h('div.mc-big.text-red', null, ui.fmtReading(m.overdue_by, u, est)),
        m.missed > 1 ? h('div.mc-sub.text-red.strong', null, `пропущено ТО подряд: ${m.missed}`) : null,
      ];
    } else if (m.to_status === 'soon' || m.to_status === 'ok') {
      head = [
        h('div.mc-cap', null, 'До ТО осталось'),
        h('div.mc-big', { class: m.to_status === 'soon' ? 'text-amber' : 'text-green' }, ui.fmtReading(m.remaining, u, est)),
      ];
    } else if (m.to_status === 'no_to') {
      head = [
        h('div.mc-big-text', null, 'Последнее ТО не внесено'),
        h('div.mc-sub.muted', null, 'Без него программа не знает, когда следующее.',
          active ? ' Нажмите «ТО сделано» и укажите, на каком показании и когда делали последнее ТО.' : null),
      ];
    } else {
      head = [
        h('div.mc-big-text', null, 'Нет показаний счётчика'),
        h('div.mc-sub.muted', null, 'Без показания программа не может считать срок ТО.',
          active ? ' Введите текущее показание — кнопка «Показание».' : null),
      ];
    }

    const next = isEmpty(m.next_to_at)
      ? [m.next_to_kind, h('span.muted', null, ' — срок пока неизвестен')]
      : h('span.strong', null, `${m.next_to_kind} на ${ui.fmtReading(m.next_to_at, u)}`);
    const forecast = m.forecast_date
      ? `≈ ${ui.fmtDate(m.forecast_date)}, через ${m.forecast_days} ${ui.plural(m.forecast_days, 'день', 'дня', 'дней')}`
      : m.to_status === 'overdue' ? 'срок уже прошёл' : h('span.muted', null, 'пока нет данных');
    const last = m.last_to
      ? [`${m.last_to.kind} на ${ui.fmtReading(m.last_to.reading, u)}, ${ui.fmtEventDate(m.last_to)}`,
        h('div.muted.small', null, `всего ТО в журнале: ${m.to_count}`)]
      : h('span.muted', null, 'не внесено');
    const rate = isEmpty(m.rate) ? null
      : [`${ui.fmtNum(m.rate, 1)} ${u} в сутки`, h('div.muted.small', null, rateSource(m))];
    const norm = isEmpty(m.daily_norm) ? null
      : [`${ui.fmtNum(m.daily_norm, 1)} ${u}`,
        h('div.muted.small', null, `столько добавляет «Накинуть за день» — ${NORM_SOURCE[m.daily_norm_source] || 'норма из настроек'}`)];
    const interval = [`${ui.fmtReading(m.interval, u)}; «скоро ТО» — за ${ui.fmtReading(m.warn, u)}`,
      h('div.muted.small', null, paramsSource(m))];

    return h('div.card', null,
      h('div.mc-head', null, h('h3.card-title', null, 'Техобслуживание'), ui.statusChip(m.to_status, m.to_status_label)),
      head,
      h('div.mc-block', null, kv([
        ['Следующее ТО', next],
        ['Прогноз', forecast],
        ['Последнее ТО', last],
      ])),
      h('div.mc-block', null,
        h('div.mc-block-title', null, `Что делать на ${m.next_to_kind} — по регламенту`),
        reg
          ? kv([
            ['Работы', reg.works],
            ['Масло', reg.oil],
            ['Фильтры', reg.filters],
            ['Запчасти', reg.parts],
            reg.note ? ['Примечание', reg.note] : null,
          ])
          : h('div.muted', null, `В регламенте нет строки для ${m.next_to_kind}. Её можно добавить в `,
            h('a', { href: '#/settings' }, 'настройках'), ', раздел «Регламент ТО».')),
      // откуда берутся сроки — нужно редко, поэтому свёрнуто
      h('div.mc-block', null,
        h('details.details.mc-how', { open: howOpen, ontoggle: (e) => {
          howOpen = e.currentTarget.open;
        } },
          h('summary', null, 'Как считается'),
          kv([
            ['Средняя наработка', rate],
            ['Норма за день', norm],
            ['Интервал ТО', interval],
          ]))));
  }

  function todayCard(m) {
    const u = m.unit;
    const retired = m.status === RETIRED; // списанная машина в парке не участвует — состояние «на линии» ей не показываем
    let since = null;
    if (!retired && m.state_since && (m.state === 'repair' || m.state === 'idle')) {
      // событие без времени заезда начинается в 00:00 — тогда показываем только дату
      const start = ui.fmtDateTime(m.state_since);
      since = h('div.mc-sub', null, `с ${start.endsWith(' 00:00') ? ui.fmtDate(m.state_since) : start}`,
        isEmpty(m.state_seconds) ? null : h('span.strong', null, ` — ${ui.fmtDuration(m.state_seconds)}`));
    }

    let readingNote;
    if (isEmpty(m.reading)) readingNote = 'показаний ещё нет';
    else {
      const base = m.reading_base_date ? `от ${ui.fmtDate(m.reading_base_date)}` : 'при регистрации машины';
      if (m.reading_estimated) {
        readingNote = `расчётное: по счётчику ${ui.fmtReading(m.reading_base, u)} ${base} + накинуто ${ui.fmtReading(m.reading_auto_add, u)}`;
      } else {
        readingNote = m.reading_base_date ? `по счётчику, внесено ${ui.fmtDate(m.reading_base_date)}` : 'показание при регистрации машины';
      }
    }

    const hours = retired || isEmpty(m.line_hours) ? null
      : `на линии ${ui.fmtHours(m.line_hours)} · в ремонте ${ui.fmtHours(m.repair_hours)} · простой ${ui.fmtHours(m.idle_hours)}`;

    return h('div.card.mc-today', null,
      h('div.mc-head', null,
        h('h3.card-title', null, onDate ? `Состояние на ${ui.fmtDate(onDate)}` : 'Сегодня'),
        retired ? ui.statusChip('grey', m.status) : ui.statusChip(m.state, m.state_label)),
      !retired && m.state_work ? h('div', null, m.state_work) : null,
      since,
      h('div.mc-block', null,
        h('div.mc-cap', null, 'Показание счётчика'),
        h('div.mc-big', null, ui.fmtReading(m.reading, u, m.reading_estimated)),
        h('div.mc-sub.muted.small', null, readingNote)),
      h('div.mc-block', null, kv([
        hours ? [onDate ? 'За эти сутки' : 'За сутки', hours] : null,
        !isEmpty(m.last_repair_seconds) ? ['Последний ремонт', `длился ${ui.fmtDuration(m.last_repair_seconds)}`] : null,
        ['Заказать запчасти', m.parts_order ? h('span.strong.text-amber', null, m.parts_order) : h('span.muted', null, 'заявок нет')],
        m.tyres_red ? ['Шины', h('span.strong.text-red', null, `пора менять: ${m.tyres_red}`)] : null,
        m.units_attention ? ['Узлы', h('span.strong.text-amber', null, `требуют внимания: ${m.units_attention}`)] : null,
      ])));
  }

  // ───────────── история ─────────────

  function eventTitle(e) {
    return e.kind_title || (e.kind === KIND_MARK ? 'Отметка показаний' : e.kind);
  }

  function eventReading(e) {
    if (e.source === 'auto') return isEmpty(e.delta) ? '' : `+${ui.fmtReading(e.delta, e.unit)}`;
    return isEmpty(e.reading) ? '' : ui.fmtReading(e.reading, e.unit, e.reading_estimated);
  }

  /** Ячейка «В ремонте / простое»: длительность и строкой ниже — заезд и выезд. */
  function eventTime(e) {
    const lines = [];
    if (e.duration_error) lines.push(h('div.text-red', null, 'ошибка во времени'));
    else if (!isEmpty(e.duration_seconds)) {
      lines.push(h('div', null, ui.fmtDuration(e.duration_seconds), e.open ? h('span.muted', null, ' — идёт') : null));
    }
    const sameYear = e.date_out && String(e.date_out).slice(0, 4) === String(e.date).slice(0, 4);
    const marks = [
      e.time_in ? `заезд ${e.time_in}` : '',
      e.date_out ? `выезд ${sameYear ? ui.fmtDateShort(e.date_out) : ui.fmtDate(e.date_out)}${e.time_out ? ` ${e.time_out}` : ''}` : '',
    ].filter(Boolean);
    if (marks.length) lines.push(h('div.muted.small', null, marks.join(' · ')));
    return lines;
  }

  async function removeEvent(e) {
    const isAuto = e.source === 'auto';
    const text = isAuto
      ? `Удалить накинутое за ${ui.fmtDate(e.date)} (${eventReading(e)})?`
      : `Удалить запись «${eventTitle(e)}» ${e.date_unknown ? '(дата не указана)' : `от ${ui.fmtDate(e.date)}`}?`;
    const details = e.is_to ? 'Срок следующего ТО после удаления пересчитается от предыдущего ТО.'
      : !isAuto && !isEmpty(e.reading) ? 'Показание из этой записи перестанет учитываться.' : null;
    if (!await ui.confirm(text, { danger: true, title: 'Удалить запись?', okLabel: 'Удалить', details })) return;
    await api.del(`/events/${e.id}`);
    ui.toast('Запись удалена');
    await reload();
  }

  function historySection(m) {
    const events = card.events || [];
    const active = m.status !== RETIRED;
    const autoCount = events.filter((e) => e.source === 'auto').length;
    const pool = () => events.filter((e) => hist.showAuto || e.source !== 'auto');
    const addEvent = () => after(dialogs.openEventDialog(ctx, { machineId: m.id }));
    const editEvent = (e) => after(dialogs.openEventDialog(ctx, { event: e }));
    const textCell = (key) => ({ key, cls: 'mc-text' });
    // «Замечания» больше не вводятся (КОНЦЕПЦИЯ_v2 п. 6) — колонка видна, только если они есть в старых записях
    const hasRemarks = events.some((e) => !isEmpty(e.remarks) && String(e.remarks).trim() !== '');

    const tbl = ui.table({
      cls: 'mc-hist',
      maxHeight: 520,
      rows: [],
      empty: '',
      sort: hist.sort,
      onSort: (s) => {
        hist.sort = s;
      },
      onRowClick: (e) => {
        if (e.source !== 'auto') editEvent(e);
      },
      rowClass: (e) => (e.source === 'auto' ? 'row-muted'
        : e.open ? (String(e.kind).startsWith('Ремонт') ? 'row-red' : 'row-amber') : ''),
      columns: [
        { key: 'date', title: 'Дата', width: 92, sortValue: (e) => `${e.date} ${String(e.id).padStart(9, '0')}`,
          render: (e) => ui.eventDateCell(e) },
        { key: 'kind', title: 'Событие', sortValue: eventTitle,
          render: (e) => [
            h('span', { class: e.is_to && 'strong' }, eventTitle(e)),
            e.source === 'auto' ? h('div.muted.small', null, 'накинуто за день') : null,
            e.open ? h('div', null, ui.statusChip(String(e.kind).startsWith('Ремонт') ? 'red' : 'amber', 'не закрыт')) : null,
          ] },
        { key: 'reading', title: 'Показание', align: 'right', hint: '«≈» — расчётное показание; «+» — накинуто за день',
          sortValue: (e) => (e.source === 'auto' ? null : e.reading), render: eventReading },
        { key: 'duration_seconds', title: 'В ремонте / простое', hint: 'Сколько длился ремонт или простой — от заезда до выезда',
          render: eventTime },
        { ...textCell('work'), title: 'Что делалось' },
        { ...textCell('parts_used'), title: 'Масло / запчасти' },
        { ...textCell('parts_order'), title: 'Заказать',
          render: (e) => (e.parts_order ? [e.parts_order,
            h('div.small', { class: e.order_done ? 'muted' : 'text-amber' }, e.order_done ? 'заказано' : 'ещё не заказано')] : '') },
        { ...textCell('remarks'), title: 'Замечания', hidden: !hasRemarks },
        { key: 'who', title: 'Кто' },
        { title: '', cls: 'cell-actions no-print', headCls: 'no-print',
          render: (e) => h('div.row-actions', null,
            e.open && e.source !== 'auto' ? ui.button('Закрыть', { small: true,
              title: String(e.kind).startsWith('Ремонт') ? 'Закрыть ремонт: машина вышла' : 'Закрыть простой',
              onClick: () => after(dialogs.openCloseRepairDialog(ctx, e)) }) : null,
            e.source !== 'auto' ? ui.iconButton('pencil', 'Изменить запись', () => editEvent(e), { small: true }) : null,
            ui.iconButton('trash', e.source === 'auto' ? 'Удалить накинутое' : 'Удалить запись', () => removeEvent(e), { small: true })) },
      ],
    });

    const tabsBox = h('div');

    function emptyNode() {
      if (!events.length) {
        return ui.emptyState('Здесь появятся ТО, ремонты, простои и отметки показаний этой машины.', {
          icon: 'history', title: 'Записей пока нет',
          action: active ? { label: 'Добавить запись', icon: 'plus', onClick: addEvent } : null,
        });
      }
      if (!pool().length) return 'По этой машине есть только накинутые показания — включите галочку «Показывать накинутое за день».';
      return 'Таких записей у этой машины нет.';
    }

    function showRows() {
      const tab = HISTORY_TABS.find((t) => t.key === hist.kind) || HISTORY_TABS[0];
      const list = pool().filter(tab.test);
      if (!list.length) tbl.setEmpty(emptyNode());
      tbl.setRows(list);
    }

    // вкладки пересобираются при смене галочки «накинутое»: меняются счётчики
    function fill() {
      const base = pool();
      const items = HISTORY_TABS.map((t) => ({ key: t.key, label: t.label, count: base.filter(t.test).length }))
        .filter((it) => it.key !== 'other' || it.count > 0);
      if (!items.some((it) => it.key === hist.kind)) hist.kind = 'all';
      // записей нет совсем — фильтровать нечего, вкладки не показываем
      ui.mount(tabsBox, !events.length ? null : ui.tabs({
        items, active: hist.kind,
        onChange: (key) => {
          hist.kind = key;
          showRows();
        },
      }));
      showRows();
    }

    const autoBox = ui.checkbox(`Показывать накинутое за день${autoCount ? ` (${autoCount})` : ''}`, {
      checked: hist.showAuto, disabled: !autoCount && !hist.showAuto, cls: 'no-print',
      title: autoCount ? 'Расчётная наработка, добавленная кнопкой «Накинуть за день»' : 'Накинутых показаний у этой машины нет',
      onChange: (on) => {
        hist.showAuto = on;
        fill();
      },
    });

    fill();
    return [
      h('div.mc-sec-head', null,
        h('h2', null, 'История'),
        h('span.muted', null, `${events.length - autoCount} ${ui.plural(events.length - autoCount, 'запись', 'записи', 'записей')}`),
        h('div.spacer'),
        autoBox,
        active ? ui.button('Добавить запись', { small: true, icon: 'plus', onClick: addEvent }) : null),
      tabsBox,
      tbl,
    ];
  }

  // ───────────── шины и узлы машины ─────────────

  function tyresSection(m) {
    const list = card.tyres || [];
    const edit = (t) => after(openTyreDialog(ctx, { tyre: t }));
    const remove = async (t) => {
      if (await removeTyre(ctx, t)) await reload();
    };
    return [
      h('div.mc-sec-head', null,
        h('h2', null, 'Шины'),
        h('span.muted', null, list.length ? `${list.length} шт.` : ''),
        h('div.spacer'),
        ui.button('Добавить шину', { small: true, icon: 'plus', onClick: () => after(openTyreDialog(ctx, { machineId: m.id })) })),
      ui.table({
        columns: tyreColumns(ctx, { withMachine: false, onEdit: edit, onRemove: remove }),
        rows: list,
        maxHeight: 360,
        onRowClick: edit,
        empty: 'Шины этой машины не внесены. Добавьте шину — программа будет считать её пробег и подскажет, когда менять.',
      }),
    ];
  }

  function unitsSection(m) {
    const list = card.units || [];
    const edit = (u) => after(openUnitDialog(ctx, { unit: u }));
    const remove = async (u) => {
      if (await removeUnit(ctx, u)) await reload();
    };
    return [
      h('div.mc-sec-head', null,
        h('h2', null, 'Узлы и агрегаты'),
        h('span.muted', null, list.length ? `${list.length} шт.` : ''),
        h('div.spacer'),
        ui.button('Добавить узел', { small: true, icon: 'plus', onClick: () => after(openUnitDialog(ctx, { machineId: m.id })) })),
      ui.table({
        columns: unitColumns(ctx, { withMachine: false, onEdit: edit, onRemove: remove }),
        rows: list,
        maxHeight: 360,
        onRowClick: edit,
        empty: 'Узлы этой машины не внесены. Добавьте узел с ресурсом — программа будет считать его наработку.',
      }),
    ];
  }

  return cleanup; // роутер вызовет при уходе с карточки — снимет слушатели с document
}
