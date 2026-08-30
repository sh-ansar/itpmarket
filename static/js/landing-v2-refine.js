(() => {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  const money = value => new Intl.NumberFormat('ru-RU').format(value) + ' ₸';

  const cleanRadar = () => {
    const label = $('[data-radar-label]');
    if (label) label.textContent = label.textContent.replace(/^DEMO\s*[·.]\s*/i, '');
  };
  $('.v2-hero .hero-copy > .kicker')?.remove();
  $('.radar-signal span:first-child')?.remove();
  cleanRadar();
  document.querySelectorAll('[data-radar]').forEach(node => {
    node.addEventListener('mouseenter', () => queueMicrotask(cleanRadar));
    node.addEventListener('focus', () => queueMicrotask(cleanRadar));
  });

  const iconFiles = ['catalog.svg', 'chart.svg', 'operations.svg', 'users.svg'];
  document.querySelectorAll('.feature-grid i').forEach((node, index) => {
    node.innerHTML = `<img src="/static/icons/${iconFiles[index]}" alt="">`;
  });

  const channelHost = $('.channels .v2-wrap');
  if (channelHost && !$('.spyon-mark-story', channelHost)) {
    const mark = document.createElement('div');
    mark.className = 'spyon-mark-story';
    mark.setAttribute('aria-hidden', 'true');
    mark.innerHTML = '<span class="mark-spy" aria-hidden="true"><span class="mark-letter mark-s">S</span><span class="mark-letter mark-p">p</span><span class="mark-letter mark-y">y</span></span><svg class="story-eye" viewBox="0 0 220 112" fill="none" aria-hidden="true"><defs><linearGradient id="spyonEyeGradient" x1="16" y1="15" x2="204" y2="98" gradientUnits="userSpaceOnUse"><stop stop-color="#082E4D"/><stop offset=".42" stop-color="#59B3D9"/><stop offset="1" stop-color="#0A5985"/></linearGradient><radialGradient id="spyonPupilGradient" cx="0" cy="0" r="1" gradientTransform="translate(110 56) rotate(90) scale(37)"><stop stop-color="#2F9ED0"/><stop offset="1" stop-color="#083859"/></radialGradient></defs><path d="M12 56C43 21 72 10 110 10c39 0 68 12 98 46-30 35-59 46-98 46-38 0-67-11-98-46Z" stroke="url(#spyonEyeGradient)" stroke-width="9" stroke-linejoin="round"/><circle cx="110" cy="56" r="35" fill="url(#spyonPupilGradient)" stroke="url(#spyonEyeGradient)" stroke-width="5"/><circle cx="124" cy="42" r="8" fill="#EDF8FF"/></svg><span class="mark-on" aria-hidden="true"><span class="mark-letter mark-o">O</span><span class="mark-letter mark-n">n</span></span><img class="story-final-mark" src="/static/images/spyon-logo.svg" alt="">';
    channelHost.prepend(mark);
  }

  const words = {
    ru: {
      kicker: 'ТОВАРЫ В РАБОТЕ', title: 'Товары, риски и потенциал — в одном обзоре',
      text: 'Интерактивный фрагмент раздела товаров. Данные демонстрационные и не связаны с компаниями.',
      all: 'Все', risks: 'Риски', potential: 'Потенциал', review: 'Требуют проверки', watched: 'Наблюдение',
      refresh: 'Обновить', report: 'Скачать отчёт', sort: 'Сортировать по', updatedSort: 'Обновлению', priceSort: 'Цене', titleSort: 'Названию',
      product: 'Товар', platform: 'Площадка', current: 'Текущая цена', range: 'Рынок: мин / средняя / макс', status: 'Статус', statusHint: '(позиция цены)', potentialHead: 'Потенциал', updated: 'Обновлено', demo: 'Демо-позиция',
      statusLabels: { risk: 'Выше рынка', potential: 'Есть потенциал', review: 'Нужно проверить', watched: 'Под наблюдением' },
      products: ['Беспроводная зарядная станция 3‑в‑1', 'Портативный проектор Full HD', 'Умная лампа Wi‑Fi E27', 'Механическая клавиатура 75%', 'Робот-пылесос с лидаром']
    },
    en: {
      kicker: 'PRODUCTS IN ACTION', title: 'Products, risks and potential — in one view',
      text: 'An interactive slice of the Products view. All entries are sample data and are not tied to companies.',
      all: 'All', risks: 'Risks', potential: 'Potential', review: 'Review needed', watched: 'Watching',
      refresh: 'Refresh', report: 'Download report', sort: 'Sort by', updatedSort: 'Updated', priceSort: 'Price', titleSort: 'Name',
      product: 'Product', platform: 'Channel', current: 'Current price', range: 'Market: min / average / max', status: 'Status', statusHint: '(price position)', potentialHead: 'Potential', updated: 'Updated', demo: 'Sample position',
      statusLabels: { risk: 'Above market', potential: 'Potential found', review: 'Needs review', watched: 'Watching' },
      products: ['3-in-1 wireless charging station', 'Portable Full HD projector', 'Smart Wi-Fi bulb E27', '75% mechanical keyboard', 'LiDAR robot vacuum']
    },
    kk: {
      kicker: 'ТАУАРЛАР ЖҰМЫСТА', title: 'Тауарлар, тәуекелдер және әлеует — бір шолуда',
      text: 'Тауарлар бөлімінің интерактивті көрінісі. Барлық дерек демонстрациялық және компанияларға тиесілі емес.',
      all: 'Барлығы', risks: 'Тәуекелдер', potential: 'Әлеует', review: 'Тексеру қажет', watched: 'Бақылауда',
      refresh: 'Жаңарту', report: 'Есепті жүктеу', sort: 'Сұрыптау', updatedSort: 'Жаңартылуы', priceSort: 'Бағасы', titleSort: 'Атауы',
      product: 'Тауар', platform: 'Арна', current: 'Ағымдағы баға', range: 'Нарық: мин / орташа / макс', status: 'Мәртебе', statusHint: '(баға позициясы)', potentialHead: 'Әлеует', updated: 'Жаңартылды', demo: 'Демо-позиция',
      statusLabels: { risk: 'Нарықтан жоғары', potential: 'Әлеует бар', review: 'Тексеру қажет', watched: 'Бақылауда' },
      products: ['3‑в‑1 сымсыз қуаттау станциясы', 'Full HD портативті проектор', 'Wi‑Fi E27 смарт шамы', '75% механикалық пернетақта', 'Лидарлы робот шаңсорғыш']
    }
  };

  const catalog = [
    { code: 'SPY-18421', channel: 'Kaspi', kind: 'risk', price: 24990, min: 21990, avg: 22650, max: 23990, rank: '1 / 8', potential: null, updated: '29.08, 13:02', image: 'charge' },
    { code: 'SPY-19381', channel: 'Ozon.ru', kind: 'risk', price: 32990, min: 28490, avg: 30120, max: 31500, rank: '1 / 6', potential: null, updated: '29.08, 12:36', image: 'projector' },
    { code: 'SPY-20744', channel: 'Wildberries', kind: 'potential', price: 12490, min: 12490, avg: 13990, max: 15190, rank: '5 / 7', potential: 180000, updated: '29.08, 12:55', image: 'lamp' },
    { code: 'SPY-21409', channel: 'Halyk Market', kind: 'review', price: 18900, min: 17690, avg: 18650, max: 20490, rank: '—', potential: null, updated: '29.08, 12:18', image: 'keyboard' },
    { code: 'SPY-22602', channel: 'Forte Market', kind: 'watched', price: 89990, min: 86500, avg: 88300, max: 92500, rank: '3 / 5', potential: null, updated: '29.08, 11:48', image: 'vacuum' }
  ];

  let lang = window.ITP_PUBLIC_LOCALE || localStorage.getItem('itp_lang') || 'ru';
  let scope = 'all';
  let sort = 'updated';
  const section = document.createElement('section');
  section.className = 'product-preview';
  $('#how')?.insertAdjacentElement('afterend', section);

  const filtered = () => catalog.filter(item => scope === 'all' || item.kind === scope).sort((a, b) => {
    if (sort === 'price') return a.price - b.price;
    if (sort === 'title') return a.code.localeCompare(b.code);
    return b.updated.localeCompare(a.updated);
  });

  const download = () => {
    const t = words[lang] || words.ru;
    const rows = filtered().map(item => [item.code, t.products[catalog.indexOf(item)], item.channel, item.price, t.statusLabels[item.kind]]);
    const csv = [[t.product, t.platform, t.current, t.status].join(';'), ...rows.map(row => row.slice(1).join(';'))].join('\n');
    const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a'); link.href = url; link.download = 'spyon-demo-catalog.csv'; link.click();
    URL.revokeObjectURL(url);
  };

  const render = () => {
    const t = words[lang] || words.ru;
    const visible = filtered();
    section.innerHTML = `<div class="v2-wrap">
      <div class="product-preview-head"><div><p class="kicker">${t.kicker}</p><h2>${t.title}</h2><p>${t.text}</p></div></div>
      <div class="product-demo-command">
        <div class="product-scope-tabs" role="tablist" aria-label="${t.status}">
          ${[['all', t.all], ['risk', t.risks], ['potential', t.potential], ['review', t.review], ['watched', t.watched]].map(([value, label]) => `<button type="button" role="tab" aria-selected="${scope === value}" class="${scope === value ? 'active' : ''}" data-scope="${value}">${label}</button>`).join('')}
        </div>
        <div class="product-command-actions"><button type="button" class="demo-refresh"><span aria-hidden="true">↻</span>${t.refresh}</button><button type="button" class="demo-report"><span aria-hidden="true">↓</span>${t.report}</button><label>${t.sort}<select class="demo-sort"><option value="updated" ${sort === 'updated' ? 'selected' : ''}>${t.updatedSort}</option><option value="price" ${sort === 'price' ? 'selected' : ''}>${t.priceSort}</option><option value="title" ${sort === 'title' ? 'selected' : ''}>${t.titleSort}</option></select></label></div>
      </div>
      <div class="product-table-wrap product-demo-table"><table><thead><tr><th><input type="checkbox" aria-label="${t.demo}"></th><th>${t.product}</th><th>${t.platform}</th><th>${t.current}</th><th>${t.range}</th><th>${t.status}<small>${t.statusHint}</small></th><th>${t.potentialHead}<button type="button" class="potential-info" aria-label="${t.potentialHead}">i</button></th><th>${t.updated}</th><th></th></tr></thead><tbody>
        ${visible.map(item => { const index = catalog.indexOf(item); return `<tr data-kind="${item.kind}"><td><input type="checkbox" aria-label="${item.code}"></td><td><div class="demo-product-cell"><span class="demo-thumb ${item.image}" aria-hidden="true"><img src="/static/icons/products.svg" alt=""></span><div><b>${t.products[index]}</b><small>${item.code} · ${t.demo}</small></div></div></td><td><div class="demo-platform"><b>${item.channel}</b><small>${t.demo}</small></div></td><td><b class="demo-price">${money(item.price)}</b></td><td><div class="demo-range"><span><small>МИН</small><b>${money(item.min)}</b></span><span><small>СР</small><b>${money(item.avg)}</b></span><span><small>МАКС</small><b>${money(item.max)}</b></span></div></td><td><div class="demo-status"><span class="${item.kind}">${t.statusLabels[item.kind]}</span><small>${item.rank}</small></div></td><td>${item.potential ? `<div class="demo-potential"><b>+${money(item.potential)}</b><small>/ месяц</small></div>` : '<span class="demo-empty">—</span>'}</td><td><span class="demo-updated">${item.updated}</span></td><td><button type="button" class="demo-open" aria-label="${t.product}">›</button></td></tr>`; }).join('')}
      </tbody></table></div>
    </div>`;
    section.querySelectorAll('[data-scope]').forEach(button => button.addEventListener('click', () => { scope = button.dataset.scope; render(); }));
    $('.demo-sort', section).addEventListener('change', event => { sort = event.target.value; render(); });
    $('.demo-report', section).addEventListener('click', download);
    $('.demo-refresh', section).addEventListener('click', event => { event.currentTarget.classList.add('is-refreshing'); setTimeout(() => event.currentTarget.classList.remove('is-refreshing'), 600); });
  };
  render();
  document.addEventListener('itp:public-locale', event => { lang = event.detail.lang; render(); });
})();
