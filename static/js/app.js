(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const csrf = $('meta[name="csrf-token"]')?.content || '';
  const user = window.ITP_USER || {};

  const I18N = window.ITP_LOCALES || {ru:{}};

  const STATUS_LABELS = {
    ru:{NOT_ANALYZED:'Точные предложения не проверены',NO_OTHER_SELLERS:'Других продавцов не найдено',INSUFFICIENT_DATA:'Недостаточно точных предложений',REVIEW_REQUIRED:'Требует ручной проверки',EXACT_LOWEST:'Самая низкая цена среди продавцов',EXACT_BELOW:'Ниже медианы продавцов',EXACT_IN_MARKET:'В рыночном диапазоне',EXACT_ABOVE:'Выше медианы продавцов',EXACT_HIGHEST:'Самая высокая цена среди продавцов',EXACT_COMPETITIVE:'В рыночном диапазоне',DATA_COLLECTED:'Данные собраны',DATA_ERROR:'Ошибка получения точных предложений',COMPARABLE_LOWEST:'Ниже сопоставимого рынка',COMPARABLE_BELOW:'Ниже медианы бренда и размера',COMPARABLE_IN_MARKET:'В диапазоне бренда и размера',COMPARABLE_ABOVE:'Выше медианы бренда и размера',COMPARABLE_HIGHEST:'Выше сопоставимого рынка'},
    kk:{NOT_ANALYZED:'Нақты ұсыныстар тексерілмеген',NO_OTHER_SELLERS:'Басқа сатушылар табылмады',INSUFFICIENT_DATA:'Нақты ұсыныстар жеткіліксіз',REVIEW_REQUIRED:'Қолмен тексеру қажет',EXACT_LOWEST:'Сатушылар арасындағы ең төмен баға',EXACT_BELOW:'Сатушылар медианасынан төмен',EXACT_IN_MARKET:'Нарық диапазонында',EXACT_ABOVE:'Сатушылар медианасынан жоғары',EXACT_HIGHEST:'Сатушылар арасындағы ең жоғары баға',EXACT_COMPETITIVE:'Нарық диапазонында',DATA_COLLECTED:'Дерек жиналды',DATA_ERROR:'Нақты ұсыныстарды алу қатесі',COMPARABLE_LOWEST:'Салыстырмалы нарықтан төмен',COMPARABLE_BELOW:'Бренд және өлшем медианасынан төмен',COMPARABLE_IN_MARKET:'Бренд және өлшем диапазонында',COMPARABLE_ABOVE:'Бренд және өлшем медианасынан жоғары',COMPARABLE_HIGHEST:'Салыстырмалы нарықтан жоғары'},
    en:{NOT_ANALYZED:'Exact offers not checked',NO_OTHER_SELLERS:'No other sellers found',INSUFFICIENT_DATA:'Insufficient exact offers',REVIEW_REQUIRED:'Manual review required',EXACT_LOWEST:'Lowest price among sellers',EXACT_BELOW:'Below seller median',EXACT_IN_MARKET:'Within market range',EXACT_ABOVE:'Above seller median',EXACT_HIGHEST:'Highest price among sellers',EXACT_COMPETITIVE:'Within market range',DATA_COLLECTED:'Data collected',DATA_ERROR:'Exact-offer collection error',COMPARABLE_LOWEST:'Below comparable market',COMPARABLE_BELOW:'Below brand-size median',COMPARABLE_IN_MARKET:'Within brand-size range',COMPARABLE_ABOVE:'Above brand-size median',COMPARABLE_HIGHEST:'Above comparable market'}
  };


  const ACTIONS = {
    kaspi:[['sync_catalog','Синхронизация каталога'],['update_own_prices','Обновление цен Unityre'],['scan_market','Точные предложения всех продавцов'],['refresh_market','Обновить устаревшие точные цены'],['retry_errors','Повтор ошибок точных карточек']],
    ozon:[['ozon_discover','Обнаружение товаров'],['ozon_enrich','Характеристики новых товаров'],['ozon_market_search','Поиск рыночных предложений'],['ozon_refresh_prices','Обновление цен'],['ozon_refresh_stale','Обновление устаревших характеристик'],['ozon_retry','Повтор ошибок'],['ozon_full_sync','Полная синхронизация'],['ozon_export','Экспорт реестра']],
    system:[['export_report','Сводный отчёт'],['audit_catalog','Аудит Kaspi'],['backup_database','Резервная копия']]
  };

  const state = {lang:'ru',theme:'system',page:'dashboard',products:{page:1,pages:1,pageSize:30,scope:'all',items:[],requestStartedAt:0,lastDurationMs:0},selected:new Set(),tasks:[],currentTask:null,settings:null};

  async function api(path, options={}) {
    const opts = {...options, headers:{...(options.headers||{})}};
    if (opts.body && typeof opts.body !== 'string') { opts.headers['Content-Type']='application/json'; opts.body=JSON.stringify(opts.body); }
    if (opts.method && opts.method !== 'GET') opts.headers['X-CSRF-Token']=csrf;
    const res = await fetch(path, opts);
    const data = await res.json().catch(()=>({ok:false,error:`HTTP ${res.status}`}));
    if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }
  const esc = v => String(v ?? '').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const icon = (name, cls='ui-icon') => `<img class="${cls}" src="/static/icons/${encodeURIComponent(name)}.svg" alt="">`;
  const money = v => v===null||v===undefined||v===''?'—':`${Math.round(Number(v)).toLocaleString('ru-RU')} ₸`;
  const number = v => Number(v||0).toLocaleString(state.lang==='en'?'en-GB':state.lang==='kk'?'kk-KZ':'ru-RU');
  const t = (key,fallback='') => window.ITPUI?.t(key,fallback) ?? I18N[state.lang]?.[key] ?? I18N.ru?.[key] ?? fallback ?? key;
  const dateText = v => { if(!v)return '—'; const d=new Date(v); return isNaN(d)?String(v):d.toLocaleString(state.lang==='en'?'en-GB':state.lang==='kk'?'kk-KZ':'ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}); };
  const statusLabel = code => STATUS_LABELS[state.lang]?.[code] || STATUS_LABELS.ru[code] || code || '—';
  const toast = (text,error=false) => { const e=document.createElement('div');e.className=`toast${error?' error':''}`;e.textContent=text;$('#toasts').append(e);setTimeout(()=>e.remove(),4200); };
  const taskStatus = v=>t(`task_${v}`,({running:'Выполняется',completed:'Окончено',failed:'Ошибка',stopped:'Остановлено',interrupted:'Прервано'}[v]||v));
  const taskStatusTone = v=>({running:'info',completed:'success',failed:'danger',stopped:'warning',interrupted:'warning'}[v]||'neutral');
  const durationText = seconds => { const total=Math.max(0, Math.round(Number(seconds||0))); if(total<60) return `${total} сек`; const mins=Math.floor(total/60); const secs=total%60; if(mins<60) return secs?`${mins} мин ${secs} сек`:`${mins} мин`; const hours=Math.floor(mins/60); const rest=mins%60; return rest?`${hours} ч ${rest} мин`:`${hours} ч`; };
  const formatEta = task => { const percent=Number(task?.progress?.percent||0); if(!(percent>0 && percent<100) || !task?.started_at) return ''; const elapsed=Math.max(1, (Date.now()-new Date(task.started_at).getTime())/1000); const total=elapsed/(percent/100); const remaining=Math.max(0,total-elapsed); return remaining>0 ? `Осталось ~${durationText(remaining)}` : ''; };
  const looksGarbled = text => { const value=String(text||''); if(!value) return false; const suspicious=(value.match(/[РСÐÑ]{2,}/g)||[]).length + (value.match(/Ã|Â|�/g)||[]).length; const cyr=(value.match(/[А-Яа-яЁё]/g)||[]).length; return suspicious>0 && suspicious>=Math.max(2, Math.floor(cyr/6)); };
  const taskProgressText = task => { const p=task?.progress; if(!p) return task?.message || ''; if(p.current!=null && p.total!=null) return `${p.current} из ${p.total} · ${Number(p.percent||0).toFixed(0)}%`; return `${Number(p.percent||0).toFixed(0)}%`; };
  const taskSecondaryText = task => { const line=String(task?.last_line||'').trim(); if(line && !looksGarbled(line) && line.length<220) return line; const eta=formatEta(task); const progress=taskProgressText(task); if(task?.running && eta && progress) return `${progress} · ${eta}`; if(task?.running && progress) return progress; return task?.message || ''; };

  function applyI18n(lang,{persist=true}={}) {
    state.lang = I18N[lang] ? lang : 'ru';
    window.ITPUI?.setLocale(state.lang,{store:persist,emit:false});
    $$('[data-lang]').forEach(b=>b.classList.toggle('active',b.dataset.lang===state.lang));
    window.ITPUI?.translateTree(document.body);
    const roleNode=$('#profileRole');if(roleNode)roleNode.textContent=roleLabel(user.role);
    renderPageHeading();updateHelpButton();
    if(state.products.items.length)renderProducts(state.products.items);
    if(state.page==='operations'&&state.tasks.length)renderTasks();
    if(state.page==='schedules')loadSchedules();
    if(state.page==='users')loadUsers();
  }
  function renderPageHeading(){ const node=$('#pageTitle'); if(!node)return; const key=`${state.page}_page_title`; node.textContent=t(key,t(`nav_${state.page}`,'Spyon')); }
  function navigate(page){ state.page=page; $$('.page').forEach(x=>x.classList.toggle('active',x.id===`page-${page}`)); $$('.nav').forEach(x=>x.classList.toggle('active',x.dataset.page===page)); renderPageHeading(); if(page==='products')loadProducts(); if(page==='operations')loadTasks(); if(page==='reports')loadReports(); if(page==='schedules')loadSchedules(); if(page==='settings')loadSettings(); if(page==='users')loadUsers(); $('#sidebar').classList.remove('open');$('#mobileMenu')?.setAttribute('aria-expanded','false');closeHelp();updateHelpButton(); }

  async function loadOverview(){
    try{
      const data=await api('/api/overview'); const o=data.overview; state.tasks=data.tasks||[];
      const dataCoverage=Number(o.data_coverage_pct ?? o.scan_coverage_pct ?? 0);
      const readyCount=Number(o.data_ready_count ?? o.scanned_count ?? 0);
      $('#navProductCount').textContent=number(o.catalog_count); $('#heroProducts').textContent=number(o.catalog_count);$('#heroAnalyzed').textContent=`${dataCoverage.toFixed(0)}%`;$('#heroRisks').textContent=number(o.risk_count);
      $('#metricProducts').textContent=number(o.catalog_count);$('#metricPlatforms').textContent=`Kaspi ${number(o.kaspi_count)} · Ozon ${number(o.ozon_count)}`;$('#metricAnalyzed').textContent=`${dataCoverage.toFixed(1)}%`;$('#metricAnalyzedSub').textContent=`Kaspi ${number(o.kaspi_market_analyzed_count)}/${number(o.kaspi_count)} · Ozon ${number(o.ozon_data_ready_count)}/${number(o.ozon_count)}`;$('#metricRisks').textContent=number(o.risk_count);$('#metricOpportunity').textContent=money(o.price_potential_monthly_kzt ?? o.potential_margin_monthly_kzt);$('#metricOpportunitySub').textContent=Number(o.potential_position_count||0)>0?`${number(o.potential_position_count)} поз. · ${number(o.potential_units_total)} ед./мес.`:'нет подтверждённых возможностей';
      $('#metricOpportunity').closest('article').title='Оценка возможного роста выручки: для товаров Kaspi с точными предложениями рассчитывается разница до нижнего квартиля цен продавцов и умножается на заданный месячный объём. Это не бухгалтерская маржа и не прогноз прибыли.';
      renderStatusChart(o.status_distribution||[]); renderHealth(o.health||{},o); renderActivity(o.recent_events||[]); renderRunningBadge(data.tasks||[]);
      if(o.preferences?.locale && !localStorage.getItem('itp_lang')) applyI18n(o.preferences.locale);
    }catch(e){toast(e.message,true)}
  }
  function renderStatusChart(rows){ const total=rows.reduce((a,b)=>a+Number(b.count||0),0)||1; $('#statusChart').innerHTML=rows.length?rows.slice(0,8).map(r=>`<div class="status-row"><label>${esc(statusLabel(r.status))}</label><div class="status-bar"><i style="width:${Math.max(2,Number(r.count)*100/total)}%"></i></div><b>${number(r.count)}</b></div>`).join(''):`<div class="empty">${esc(t('empty_data','Нет данных'))}</div>`; }
  function renderHealth(h,o){ const coverage=Number(o.data_coverage_pct ?? o.scan_coverage_pct ?? 0);const items=[['catalog','Каталог',`${number(o.kaspi_count)} Kaspi · ${number(o.ozon_count)} Ozon`,'catalog'],['prices','Цены',`${Number(o.price_coverage_pct||0).toFixed(1)}% покрытия`,'currency'],['market','Обработка данных',`${coverage.toFixed(1)}% · Kaspi ${number(o.kaspi_market_analyzed_count)}/${number(o.kaspi_count)} · Ozon ${number(o.ozon_data_ready_count)}/${number(o.ozon_count)}`,'chart']];$('#healthList').innerHTML=items.map(([k,n,d,ic])=>`<div class="health-item ${esc(h[k]||'empty')}"><span>${icon(ic)}</span><div><b>${n}</b><small>${d}</small></div><i></i></div>`).join(''); }
  function renderActivity(rows){ $('#activityList').innerHTML=rows.length?rows.map(r=>`<div class="activity"><span>${icon('operations')}</span><div><b>${esc(eventLabel(r.event_type))}</b><small>${esc(r.display_name||'Система')}</small></div><time>${dateText(r.created_at)}</time></div>`).join(''):'<div class="empty">Нет событий</div>'; }
  const eventLabel = v => ({task_started:'Операция запущена',task_stopped:'Операция остановлена',task_deleted:'Операция удалена',settings_updated:'Настройки обновлены',product_state_updated:'Карточки обновлены'}[v]||v||'Событие');

  async function loadOptions(){ try{ const d=await api('/api/products/options'); $('#brandFilter').innerHTML=`<option value="">${t('all_brands','Все бренды')}</option>`+(d.brands||[]).map(v=>`<option>${esc(v)}</option>`).join(''); $('#statusFilter').innerHTML=`<option value="">${t('all_statuses','Все статусы')}</option>`+(d.statuses||[]).map(v=>`<option value="${esc(v.value)}">${esc(statusLabel(v.value))}</option>`).join(''); }catch(e){toast(e.message,true)} }
  function productQuery(){ const q=new URLSearchParams({page:state.products.page,page_size:$('#pageSize').value,query:$('#productSearch').value,platform:$('#platformFilter').value,brand:$('#brandFilter').value,status:$('#statusFilter').value,freshness:$('#freshnessFilter')?$('#freshnessFilter').value:'',scope:state.products.scope,sort:$('#sortProducts').value,direction:'desc'});return q; }
  async function loadProducts(){
    state.products.requestStartedAt=Date.now();
    $('#productsLoadInfo').textContent=t('loading_catalog','Загружаем каталог…');
    $('#productsBody').innerHTML=`<tr><td colspan="9"><div class="loader">${esc(t('loading_catalog','Загрузка каталога…'))}</div></td></tr>`;
    try{const d=await api(`/api/products?${productQuery()}`);const r=d.result;state.products={...state.products,page:r.page,pages:r.pages,pageSize:r.page_size,items:r.items,lastDurationMs:Date.now()-state.products.requestStartedAt};$('#productsFound').textContent=number(r.total);$('#pageInfo').textContent=`${r.page} / ${r.pages}`;$('#productsLoadInfo').textContent=`Обновлено за ${(state.products.lastDurationMs/1000).toFixed(2)} сек`;renderProducts(r.items);}catch(e){$('#productsLoadInfo').textContent=t('load_error','Ошибка загрузки');$('#productsBody').innerHTML=`<tr><td colspan="9"><div class="empty">${esc(e.message)}</div></td></tr>`;}
  }
  function renderProducts(items){
    $('#productsBody').innerHTML=items.length?items.map(p=>{
      const kaspi=p.platform==='kaspi';
      const price=kaspi?p.own_price_kzt:p.price_kzt;
      const sellerMeta=p.seller_name?`<small class="cell-meta">${esc(p.seller_name)}</small>`:'';
      const original=!kaspi&&p.price_original?`<small class="cell-meta">${number(p.price_original)} ${esc(p.currency_original)}</small>`:'';
      const range=p.market_median_price_kzt?`<div class="range"><span>${money(p.market_min_price_kzt)}</span><span class="median">${money(p.market_median_price_kzt)}</span><span>${money(p.market_max_price_kzt)}</span></div>`:'<span class="muted">—</span>';
      const pot=Number(p.potential_margin_monthly_kzt||0)>0?`<b class="positive">${money(p.potential_margin_monthly_kzt)}</b><small>${money(p.potential_margin_per_unit_kzt)} / ед.</small>`:'<span class="muted">—</span>';
      return `<tr><td><input class="row-check" type="checkbox" data-code="${esc(p.product_code)}" ${state.selected.has(p.product_code)?'checked':''}></td><td><div class="product-cell"><img src="${esc(p.image_url||'')}" onerror="this.style.visibility='hidden'"><div><b>${esc(p.title)}</b><small>${esc(p.source_product_code)} · ${esc(p.brand||'')} · ${esc(p.size||'')}</small></div></div></td><td><div class="cell-stack"><span class="badge ${p.platform}">${esc(p.platform_label)}</span>${sellerMeta}</div></td><td><div class="cell-stack price-stack"><span class="money">${money(price)}</span>${original}</div></td><td>${range}</td><td><div class="position-stack"><span class="status ${esc(p.status_tone||'neutral')}">${esc(statusLabel(p.price_status))}</span>${p.price_rank?`<small class="rank-meta">${p.price_rank} / ${p.price_rank_total}</small>`:''}</div></td><td>${pot}</td><td><div class="updated-cell"><span>${dateText(p.updated_at)}</span></div></td><td><button class="open-row" data-open-product="${esc(p.product_code)}">${icon('chevron-right')}</button></td></tr>`;
    }).join(''):'<tr><td colspan="9"><div class="empty">Позиции не найдены</div></td></tr>';
    bindProductRows(); updateSelectionBar();
  }
  function bindProductRows(){ $$('.row-check').forEach(x=>x.onchange=()=>{x.checked?state.selected.add(x.dataset.code):state.selected.delete(x.dataset.code);updateSelectionBar();}); $$('[data-open-product]').forEach(x=>x.onclick=()=>openProduct(x.dataset.openProduct)); }
  function updateSelectionBar(){ const n=state.selected.size;$('#selectionBar').hidden=!n;$('#selectionCount').textContent=n; if($('#operationScope')) $('#operationScope').value = n ? $('#operationScope').value : 'all'; }
  async function openProduct(code){ $('#backdrop').hidden=false;$('#productDrawer').classList.add('open');$('#productDrawer').setAttribute('aria-hidden','false');$('#drawerBody').innerHTML='<div class="loader">Загрузка…</div>';try{const d=await api(`/api/products/${encodeURIComponent(code)}`);renderDrawer(d.product);}catch(e){$('#drawerBody').innerHTML=`<div class="empty">${esc(e.message)}</div>`;} }
  function closeDrawer(){ $('#productDrawer').classList.remove('open');$('#backdrop').hidden=true;$('#productDrawer').setAttribute('aria-hidden','true'); }
  function renderDrawer(p){
    $('#drawerTitle').textContent=p.title||t('product','Товар'); const current=p.platform==='kaspi'?p.own_price_kzt:p.price_kzt;
    const lowLink=p.lowest_product_url?`<a href="${esc(p.lowest_product_url)}" target="_blank" rel="noreferrer">${esc(p.lowest_product_title||t('open_catalog_button','Открыть карточку'))}</a>`:''; const highLink=p.highest_product_url?`<a href="${esc(p.highest_product_url)}" target="_blank" rel="noreferrer">${esc(p.highest_product_title||t('open_catalog_button','Открыть карточку'))}</a>`:'';
    const specs=(p.specifications||[]).map(s=>`<div class="spec"><small>${esc(s.name)}</small><b>${esc(s.value)}</b></div>`).join('')||'<div class="empty">Характеристики не получены</div>';
    const offers=(p.offers||[]).map(o=>{const relation=p.platform==='ozon'?(o.match_method_label||'Точный товар Ozon'):(o.is_own?'Unityre':'Тот же product_code');const linkText=p.platform==='ozon'?'Открыть карточку Ozon ↗':'Открыть ту же карточку Kaspi ↗';return `<div class="candidate"><div><b>${esc(o.merchant_name||t('seller','Продавец'))}</b><small>${money(o.price_kzt)}${o.merchant_rating?` · рейтинг ${Number(o.merchant_rating).toFixed(1)}`:''}</small>${o.product_url?`<a href="${esc(o.product_url)}" target="_blank" rel="noreferrer">${linkText}</a>`:''}</div><span class="relation">${esc(relation)}</span></div>`}).join('')||'<div class="empty">Точные предложения конкурентов ещё не найдены</div>';
    const method=p.match_method_label?`<p><small>Метод сопоставления</small><b>${esc(p.match_method_label)}</b></p>`:'';
    $('#drawerBody').innerHTML=`<div class="drawer-product"><img src="${esc(p.image_url||'')}" onerror="this.style.visibility='hidden'"><div><h3>${esc(p.title)}</h3><p><span class="badge ${esc(p.platform)}">${esc(p.platform_label)}</span> ${esc(p.source_product_code)} · ${esc(p.brand||'')} · ${esc(p.size||'')}</p><p><span class="status ${esc(p.status_tone||'neutral')}">${esc(statusLabel(p.price_status))}</span></p>${method}</div></div>
      <div class="position-grid"><div class="position-card"><small>Текущая цена</small><b>${money(current)}</b>${p.currency_original!=='KZT'?`<span>${number(p.price_original)} ${esc(p.currency_original)}</span>`:''}</div><div class="position-card"><small>Минимум продавцов</small><b>${money(p.market_min_price_kzt)}</b>${lowLink}</div><div class="position-card"><small>Медиана продавцов</small><b>${money(p.market_median_price_kzt)}</b><span>${p.reference_count||0} продавцов</span></div><div class="position-card"><small>Максимум продавцов</small><b>${money(p.market_max_price_kzt)}</b>${highLink}</div></div>
      ${p.platform==='kaspi'?`<div class="opportunity-card"><div><small>Потенциал маржи до нижнего квартиля точных предложений</small><b>${money(p.potential_margin_monthly_kzt)}</b></div><div><small>На единицу</small><b>${money(p.potential_margin_per_unit_kzt)}</b></div></div>`:''}
      <section class="drawer-section"><h4>История цены</h4><div class="history-chart">${historySvg(p.history||[])}</div></section>
      <section class="drawer-section"><h4>Характеристики</h4><div class="spec-grid">${specs}</div></section>
      <section class="drawer-section"><h4>${p.platform==='kaspi'?'Продавцы этой же карточки Kaspi':'Рыночные предложения Ozon'}</h4><div class="candidate-list">${offers}</div></section>`;
  }

  const relationLabel = v => ({KASPI_SAME_CARD:'Та же карточка Kaspi',EXACT_MODEL:'Точный товар',REVIEW:'Требует проверки',accepted:'Подтверждено',review:'Проверка',rejected:'Отклонено'}[v]||v||'Точный кандидат');
  function historySvg(points){ const vals=points.map(x=>Number(x.price_kzt??x.price)).filter(x=>x>0);if(vals.length<2)return '<span class="muted">Недостаточно данных для графика</span>';const min=Math.min(...vals),max=Math.max(...vals),w=700,h=170,pad=18;const pts=vals.map((v,i)=>`${pad+i*(w-pad*2)/(vals.length-1)},${h-pad-(v-min)/(max-min||1)*(h-pad*2)}`).join(' ');return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#06a9e7" stop-opacity=".32"/><stop offset="1" stop-color="#06a9e7" stop-opacity="0"/></linearGradient></defs><polyline points="${pts} ${w-pad},${h-pad} ${pad},${h-pad}" fill="url(#g)" stroke="none"/><polyline points="${pts}" fill="none" stroke="#06a9e7" stroke-width="3" vector-effect="non-scaling-stroke"/></svg>`; }

  function updateOperationActions(){ const platform=$('#operationPlatform').value;$('#operationAction').innerHTML=(ACTIONS[platform]||[]).filter(([id])=>id!=='backup_database'||user.platform_role==='superadmin').map(([id,label])=>`<option value="${id}">${esc(label)}</option>`).join('');$('#operationScope').disabled=platform!=='kaspi';if(platform!=='kaspi')$('#operationScope').value='all'; }
  async function startTask(action,scope='all',codes=[]){ try{const d=await api('/api/tasks/start',{method:'POST',body:{action,scope,codes}});toast(`Запущено: ${d.task.label}`);navigate('operations');loadTasks();}catch(e){toast(e.message,true)} }
  async function loadTasks(){ try{const d=await api('/api/tasks');state.tasks=d.tasks||[];renderTasks();renderRunningBadge(state.tasks);}catch(e){toast(e.message,true)} }
  function renderRunningBadge(tasks){const n=tasks.filter(t=>t.running).length;$('#navRunning').hidden=!n;$('#navRunning').textContent=n;$('#opsRunning')&&($('#opsRunning').textContent=n);}
  function renderTasks(){const tasks=state.tasks;$('#opsRunning').textContent=tasks.filter(t=>t.running).length;$('#opsCompleted').textContent=tasks.filter(t=>t.status==='completed').length;$('#opsFailed').textContent=tasks.filter(t=>['failed','interrupted'].includes(t.status)).length;$('#operationsList').innerHTML=tasks.length?tasks.map(t=>{const percent=Number(t?.progress?.percent||0);const eta=formatEta(t);const progressText=taskProgressText(t);const secondary=taskSecondaryText(t);const stateIcon=t.running?'operations':t.status==='completed'?'check':t.status==='failed'?'risk':'info';return `<article class="operation-card"><span class="op-icon ${esc(taskStatusTone(t.status))}">${icon(stateIcon)}</span><div><div class="operation-head"><h4>${esc(t.label)}</h4><span class="status ${esc(taskStatusTone(t.status))}">${esc(taskStatus(t.status))}</span></div><small><span class="badge ${esc(t.metadata?.platform||'kaspi')}">${esc((t.metadata?.platform||'kaspi').toUpperCase())}</span> ${dateText(t.started_at)}</small>${t.progress?`<div class="progress"><i style="width:${percent}%"></i></div><div class="progress-meta"><small>${esc(progressText)}</small>${eta?`<small>${esc(eta)}</small>`:''}</div>`:''}<small class="task-preview">${esc(secondary||'')}</small></div><div class="op-actions"><button data-log="${esc(t.id)}">${icon('eye')}<span>Журнал</span></button>${t.running?`<button data-stop="${esc(t.id)}">${icon('stop')}</button>`:`<button data-delete="${esc(t.id)}">${icon('trash')}</button>`}</div></article>`}).join(''):'<div class="empty">История операций пуста</div>';$$('[data-log]').forEach(x=>x.onclick=()=>openLog(x.dataset.log));$$('[data-stop]').forEach(x=>x.onclick=()=>stopTask(x.dataset.stop));$$('[data-delete]').forEach(x=>x.onclick=()=>deleteTask(x.dataset.delete));}

  async function openLog(id){state.currentTask=id;showModal('logModal');await refreshLog();}
  async function refreshLog(){if(!state.currentTask)return;try{const d=await api(`/api/tasks/${encodeURIComponent(state.currentTask)}/log?lines=800`);$('#logTitle').textContent=d.task.label;$('#logContent').textContent=d.log||t('log_empty','Журнал пуст');$('#stopTask').hidden=!d.task.running;}catch(e){toast(e.message,true)}}
  async function stopTask(id){try{await api(`/api/tasks/${encodeURIComponent(id)}/stop`,{method:'POST'});toast('Операция остановлена');loadTasks();refreshLog();}catch(e){toast(e.message,true)}}
  async function deleteTask(id){if(!confirm('Удалить операцию и её журнал?'))return;try{await api(`/api/tasks/${encodeURIComponent(id)}`,{method:'DELETE'});loadTasks();}catch(e){toast(e.message,true)}}

  function renderAnalyticsKpis(k){const coverage=Number(k.data_coverage_pct ?? k.analysis_coverage_pct ?? 0);const items=[['catalog','Всего товаров',number(k.total_products),`Kaspi ${number(k.kaspi_products)} · Ozon ${number(k.ozon_products)}`],['coverage','Покрытие данных',`${coverage.toFixed(1)}%`,`Kaspi ${number(k.kaspi_market_analyzed_count)}/${number(k.kaspi_products)} · Ozon ${number(k.ozon_data_ready_count)}/${number(k.ozon_products)}`],['risk','Ценовые риски Kaspi',number(k.risk_count),`${number(k.review_count)} требуют проверки`],['potential','Ценовой потенциал Kaspi',money(k.price_potential_monthly_kzt ?? k.potential_margin_monthly_kzt),`${number(k.potential_position_count)} поз. · ${number(k.potential_units_total)} ед./мес.`]];$('#reportKpis').innerHTML=items.map(([ic,label,value,sub])=>`<article><span>${icon(ic)}</span><div><small>${esc(label)}</small><b>${esc(value)}</b><em>${esc(sub)}</em></div></article>`).join('');}
  function renderAnalyticsStatus(rows){const total=rows.reduce((a,b)=>a+Number(b.count||0),0)||1;const top=rows.slice(0,6);let cursor=0;const colors=['#08a8e8','#31c9e8','#4d85e8','#6fd5d7','#43b7a2','#8aa6ba'];const stops=top.map((r,i)=>{const start=cursor;cursor+=Number(r.count||0)/total*100;return `${colors[i%colors.length]} ${start}% ${cursor}%`}).join(',');$('#reportStatusChart').innerHTML=`<div class="analytics-donut" style="background:conic-gradient(${stops||'#e8eef2 0 100%'})"><div><b>${number(total)}</b><small>позиций</small></div></div><div class="analytics-legend">${top.map((r,i)=>`<div><i style="background:${colors[i%colors.length]}"></i><span>${esc(statusLabel(r.status))}</span><b>${number(r.count)}</b></div>`).join('')}</div>`;}
  function renderPlatformChart(rows){const max=Math.max(1,...rows.map(r=>Number(r.total||0)));$('#reportPlatformChart').innerHTML=rows.map(r=>`<div class="platform-row"><div><b>${esc(r.platform)}</b><small>${number(r.priced)} из ${number(r.total)} с ценой</small></div><div class="platform-track"><i style="width:${Number(r.coverage_pct||0)}%"></i></div><strong>${Number(r.coverage_pct||0).toFixed(1)}%</strong></div>`).join('')||`<div class="empty">${esc(t('empty_data','Нет данных'))}</div>`;}
  function renderBrandRisks(rows){const max=Math.max(1,...rows.map(r=>Number(r.risks||0)+Number(r.review||0)));$('#reportBrandRisks').innerHTML=rows.slice(0,10).map(r=>{const risk=Number(r.risks||0),review=Number(r.review||0);return `<div class="brand-risk-row"><label>${esc(r.brand)}</label><div class="brand-risk-track"><i style="width:${risk/max*100}%"></i><em style="width:${review/max*100}%"></em></div><b>${number(risk)}</b><small>${number(review)} на проверке</small></div>`}).join('')||`<div class="empty">${esc(t('empty_data','Нет данных'))}</div>`;}
  function renderPriceBands(rows){const max=Math.max(1,...rows.map(r=>Number(r.count||0)));$('#reportPriceBands').innerHTML=rows.map(r=>`<div class="price-band-row"><label>${esc(r.label)}</label><div><i style="height:${Math.max(8,Number(r.count||0)/max*100)}%"></i></div><b>${number(r.count)}</b></div>`).join('')||`<div class="empty">${esc(t('empty_data','Нет данных'))}</div>`;}
  function renderQuality(k){const total=Number(k.total_products||0);const rows=[['Данные обработаны',k.data_ready_count??k.analyzed_count,total],['Kaspi: точный рынок',k.kaspi_market_analyzed_count,k.kaspi_products],['Ozon: карточки готовы',k.ozon_data_ready_count,k.ozon_products],['Требуют проверки Kaspi',k.review_count,k.kaspi_products]];$('#reportQuality').innerHTML=rows.map(([label,value,rowTotal])=>{const pct=rowTotal?Number(value||0)/Number(rowTotal)*100:0;return `<div class="quality-row"><div><span>${esc(label)}</span><b>${number(value||0)}</b></div><div class="quality-track"><i style="width:${Math.min(100,pct)}%"></i></div></div>`}).join('');}
  function analyticsTable(rows,type){if(!rows.length)return '<tr><td colspan="4"><div class="empty">Нет данных</div></td></tr>';return rows.map(r=>type==='risk'?`<tr data-open-analytics="${esc(r.product_code)}"><td><b>${esc(r.title)}</b><small>${esc(r.brand||'')} · ${esc(r.size||'')}</small></td><td>${money(r.current_price_kzt)}</td><td>${money(r.market_median_price_kzt)}</td><td><strong class="negative">${r.difference_pct==null?'—':`${Number(r.difference_pct).toFixed(1)}%`}</strong></td></tr>`:`<tr data-open-analytics="${esc(r.product_code)}"><td><b>${esc(r.title)}</b><small>${esc(r.brand||'')} · ${esc(r.size||'')}</small></td><td>${money(r.current_price_kzt)}</td><td>${money(r.potential_margin_per_unit_kzt)}</td><td><strong class="positive">${money(r.potential_margin_monthly_kzt)}</strong></td></tr>`).join('');}
  function bindAnalyticsRows(){$$('[data-open-analytics]').forEach(row=>row.onclick=()=>openProduct(row.dataset.openAnalytics));}
  async function loadReports(){try{const [analyticsData,reportsData]=await Promise.all([api('/api/analytics/dashboard'),api('/api/reports')]);const a=analyticsData.analytics||{};const k=a.kpis||{};renderAnalyticsKpis(k);renderAnalyticsStatus(a.status_distribution||[]);renderPlatformChart(a.platforms||[]);renderBrandRisks(a.brand_risks||[]);renderPriceBands(a.price_bands||[]);renderQuality(k);$('#reportRiskTable').innerHTML=analyticsTable(a.top_risks||[],'risk');$('#reportOpportunityTable').innerHTML=analyticsTable(a.top_opportunities||[],'opportunity');bindAnalyticsRows();$('#reportsList').innerHTML=(reportsData.reports||[]).length?reportsData.reports.map(r=>`<article class="report-item"><span>${icon('file')}</span><div><b>${esc(r.file_name)}</b><small>${esc(r.report_type)} · ${number(r.rows_count)} строк · ${dateText(r.created_at)}</small></div><a href="/api/reports/${r.id}/download">${icon('download')}<span>Скачать</span></a></article>`).join(''):'<div class="empty">Отчёты ещё не сформированы</div>';}catch(e){toast(e.message,true);$('#reportKpis').innerHTML=`<div class="empty">${esc(e.message)}</div>`;}}

  async function loadSettings(){try{const d=await api('/api/settings');state.settings=d;const p=d.preferences||{};$('#prefLocale').value=p.locale||'ru';state.theme=p.theme||window.ITPUI?.getTheme()||'system';window.ITPUI?.setTheme(state.theme,{store:true,emit:false});$('#prefCurrency').value=p.display_currency||'KZT';$('#prefUnits').value=p.default_monthly_units??1;$('#prefRub').value=p.rub_to_kzt??5.5;$('#prefUsd').value=p.usd_to_kzt??520;$('#prefEur').value=p.eur_to_kzt??565;const tenant=d.tenant||{};if($('#tenantName')){$('#tenantName').value=tenant.name||'';$('#tenantRegistrationNumber').value=tenant.registration_number||'';$('#tenantContactEmail').value=tenant.contact_email||'';$('#tenantContactPhone').value=tenant.contact_phone||'';}const n=d.network||{};if($('#cfgLocalUrl')){$('#cfgLocalUrl').value=n.local_url||'';$('#cfgLanUrl').value=(n.lan_urls||[]).join(', ');$('#cfgLanPort').value=n.port||'';$('#cfgLanState').value=n.lan_enabled?'Включён':'Выключен';}if(d.config){const c=d.config;$('#cfgSellerId').value=c.kaspi?.seller_id||'';$('#cfgSellerName').value=c.kaspi?.seller_name||'';$('#cfgCityId').value=c.kaspi?.city_id||'';$('#cfgExpected').value=c.kaspi?.expected_count||0;$('#cfgDiscover').value=c.analysis?.discover_workers||1;$('#cfgPrice').value=c.analysis?.price_workers||1;if($('#cfgOzonClientUrls')){$('#cfgOzonClientUrls').value=c.ozon?.client_catalog_urls||c.ozon?.seller_catalog_url||'';$('#cfgOzonMarketUrls').value=c.ozon?.market_category_urls||c.ozon?.category_urls||'';$('#cfgOzonExpectedSeller').value=c.ozon?.expected_seller||'';$('#cfgOzonCurrentSeller').value=c.ozon?.current_seller_name||'';$('#cfgOzonCurrentSellerUrl').value=c.ozon?.current_seller_url||'';$('#cfgOzonCurrentProducts').value=c.ozon?.current_products??0;$('#cfgOzonMarketProducts').value=c.ozon?.current_market_products??0;$('#cfgOzonCurrentOffers').value=c.ozon?.current_offers??0;}}}catch(e){toast(e.message,true)}}
  async function saveSettings(){const preferences={locale:$('#prefLocale').value,theme:window.ITPUI?.getTheme()||'system',display_currency:$('#prefCurrency').value,default_monthly_units:Number($('#prefUnits').value),rub_to_kzt:Number($('#prefRub').value),usd_to_kzt:Number($('#prefUsd').value),eur_to_kzt:Number($('#prefEur').value)};const body={preferences};if(user.role==='admin'&&$('#tenantName'))body.tenant={name:$('#tenantName').value,registration_number:$('#tenantRegistrationNumber').value,contact_email:$('#tenantContactEmail').value,contact_phone:$('#tenantContactPhone').value};if(user.role==='admin'&&state.settings?.config)body.config={kaspi:{...state.settings.config.kaspi,seller_id:$('#cfgSellerId').value,seller_name:$('#cfgSellerName').value,city_id:$('#cfgCityId').value,expected_count:Number($('#cfgExpected').value)},analysis:{...state.settings.config.analysis,discover_workers:Number($('#cfgDiscover').value),price_workers:Number($('#cfgPrice').value)},app:state.settings.config.app,ozon:{...state.settings.config.ozon,client_catalog_urls:$('#cfgOzonClientUrls')?$('#cfgOzonClientUrls').value:'',market_category_urls:$('#cfgOzonMarketUrls')?$('#cfgOzonMarketUrls').value:'',expected_seller:$('#cfgOzonExpectedSeller')?$('#cfgOzonExpectedSeller').value:''}};try{const d=await api('/api/settings',{method:'PUT',body});if(d.tenant)state.settings.tenant=d.tenant;applyI18n(preferences.locale);toast(t('settings_saved','Настройки сохранены'));loadOverview();loadProducts();}catch(e){toast(e.message,true)}}

  const scheduleStatus=v=>({
    completed:t('task_completed','Окончено'),
    failed:t('task_failed','Ошибка'),
    running:t('task_running','Выполняется'),
    queued:t('task_queued','В очереди'),
    stopped:t('task_stopped','Остановлено')
  }[v]||v||'—');

  const weekdayKeys=['weekday_mon','weekday_tue','weekday_wed','weekday_thu','weekday_fri','weekday_sat','weekday_sun'];
  const weekdayFallback=['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];

  function scheduleWeekdayName(day){
    const index=Number(day);
    return t(weekdayKeys[index],weekdayFallback[index]||String(day));
  }

  function recurrenceText(s){
    const type=String(s.recurrence_type||'daily');
    if(type==='once'){
      const raw=s.run_date?`${s.run_date}T${s.time_of_day||'03:00'}`:s.next_run_at;
      return raw?`${t('schedule_once','Однократно')} · ${dateText(raw)}`:t('schedule_once','Однократно');
    }
    if(type==='interval'){
      const hours=Math.max(1,Number(s.interval_minutes||60)/60);
      return `${t('schedule_every','Каждые')} ${Number.isInteger(hours)?hours:hours.toFixed(1)} ${t('hours_short','ч')}`;
    }
    if(type==='weekly'){
      const days=(s.weekdays||[]).map(scheduleWeekdayName).join(' · ');
      return `${days||t('schedule_weekly','По дням недели')} · ${s.time_of_day||'03:00'}`;
    }
    return `${t('schedule_daily','Ежедневно')} · ${s.time_of_day||'03:00'}`;
  }

  function scheduleRuleHtml(s){
    const type=String(s.recurrence_type||'daily');
    if(type==='weekly'){
      return `<div class="schedule-days">${(s.weekdays||[]).map(day=>`<span>${esc(scheduleWeekdayName(day))}</span>`).join('')}</div>`;
    }
    if(type==='once'){
      return `<div class="schedule-rule-badge once">${esc(t('schedule_no_repeat','Без повторения'))}</div>`;
    }
    if(type==='interval'){
      return `<div class="schedule-rule-badge">${esc(t('schedule_repeats','Повторяется'))}</div>`;
    }
    return `<div class="schedule-rule-badge">${esc(t('schedule_every_day','Каждый день'))}</div>`;
  }

  async function loadSchedules(){
    try{
      const d=await api('/api/schedules');
      const schedules=d.schedules||[],runs=d.runs||[];
      state.scheduleActions=d.actions||[];
      state.schedules=schedules;
      const enabled=schedules.filter(x=>x.is_enabled);
      const next=enabled.map(x=>x.next_run_at).filter(Boolean).sort()[0];
      const last=runs[0];

      $('#scheduleSummary').innerHTML=`
        <article><small>${esc(t('schedule_active','Активных заданий'))}</small><b>${enabled.length}</b></article>
        <article><small>${esc(t('schedule_next','Следующий запуск'))}</small><b>${next?dateText(next):'—'}</b></article>
        <article><small>${esc(t('schedule_last','Последний результат'))}</small><b>${last?esc(scheduleStatus(last.status)):'—'}</b></article>`;

      $('#scheduleList').innerHTML=schedules.length?schedules.map(x=>`
        <article class="schedule-card">
          <div class="schedule-card-main">
            <div class="schedule-card-title-row">
              <h3>${esc(x.name)}</h3>
              <span class="schedule-platform">${esc((state.scheduleActions.find(a=>a.code===x.action)||{}).platform||x.platform||'')}</span>
            </div>
            <p>${esc((state.scheduleActions.find(a=>a.code===x.action)||{}).name||x.action)}</p>
            <div class="schedule-rule-row">
              ${scheduleRuleHtml(x)}
              <strong>${esc(recurrenceText(x))}</strong>
            </div>
            <div class="schedule-card-meta">
              <span><small>${esc(t('schedule_next','Следующий запуск'))}</small><b>${x.next_run_at?dateText(x.next_run_at):'—'}</b></span>
              <span><small>${esc(t('schedule_last_run','Последний запуск'))}</small><b>${x.last_run_at?dateText(x.last_run_at):esc(t('schedule_never_run','Не запускалось'))}</b></span>
              <span><small>${esc(t('status','Статус'))}</small><b>${esc(scheduleStatus(x.last_status))}</b></span>
            </div>
          </div>
          <div class="schedule-card-actions">
            <button class="secondary edit" data-edit-schedule="${x.id}">${esc(t('edit','Изменить'))}</button>
            <button class="toggle ${x.is_enabled?'active':''}" data-toggle-schedule="${x.id}" data-enabled="${x.is_enabled?1:0}">${x.is_enabled?esc(t('enabled','Включено')):esc(t('disabled','Выключено'))}</button>
            ${user.role==='admin'?`<button class="delete" data-delete-schedule="${x.id}">${esc(t('delete','Удалить'))}</button>`:''}
          </div>
        </article>`).join(''):`<div class="empty">${esc(t('schedule_empty','Расписания ещё не созданы'))}</div>`;

      $('#scheduleRuns').innerHTML=runs.length?runs.map(r=>`
        <div class="schedule-run">
          <div><b>${esc(r.schedule_name)}</b><small>${dateText(r.started_at)}${r.message?` · ${esc(r.message)}`:''}</small></div>
          <span class="schedule-run-status ${esc(r.status)}">${esc(scheduleStatus(r.status))}</span>
        </div>`).join(''):`<div class="empty">${esc(t('schedule_runs_empty','Плановых запусков ещё не было'))}</div>`;

      $$('[data-edit-schedule]').forEach(b=>b.onclick=()=>openScheduleModal(Number(b.dataset.editSchedule)));
      $$('[data-toggle-schedule]').forEach(b=>b.onclick=()=>toggleSchedule(Number(b.dataset.toggleSchedule),b.dataset.enabled!=='1'));
      $$('[data-delete-schedule]').forEach(b=>b.onclick=()=>deleteSchedule(Number(b.dataset.deleteSchedule)));
    }catch(e){toast(e.message,true)}
  }

  function openScheduleModal(id=null){
    const select=$('#scheduleAction');
    select.innerHTML=(state.scheduleActions||[]).map(x=>`<option value="${esc(x.code)}">${esc(x.platform)} — ${esc(x.name)}</option>`).join('');
    const form=$('#scheduleForm');
    form.reset();
    form.dataset.editId=id?String(id):'';
    form.elements.is_enabled.checked=true;
    const today=new Date();
    const minDate=`${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
    $('#scheduleRunDate').min=minDate;

    const item=id?(state.schedules||[]).find(x=>Number(x.id)===Number(id)):null;
    $('#scheduleModalTitle').textContent=item?t('schedule_modal_edit','Изменить расписание'):t('schedule_modal_new','Новое расписание');
    if(item){
      form.elements.name.value=item.name||'';
      form.elements.action.value=item.action||'';
      form.elements.recurrence_type.value=item.recurrence_type||'daily';
      form.elements.run_date.value=item.run_date||'';
      form.elements.time_of_day.value=item.time_of_day||'03:00';
      form.elements.interval_minutes.value=String(item.interval_minutes||360);
      form.elements.is_enabled.checked=Boolean(item.is_enabled);
      const days=new Set((item.weekdays||[]).map(Number));
      form.querySelectorAll('[name="weekdays"]').forEach(node=>node.checked=days.has(Number(node.value)));
    }else{
      form.elements.recurrence_type.value='weekly';
      form.elements.time_of_day.value='03:00';
    }
    updateScheduleFields();
    showModal('scheduleModal');
  }

  async function toggleSchedule(id,is_enabled){
    try{
      await api(`/api/schedules/${id}`,{method:'PUT',body:{is_enabled}});
      loadSchedules();
    }catch(e){toast(e.message,true)}
  }

  async function deleteSchedule(id){
    if(!confirm(t('schedule_delete_confirm','Удалить расписание?')))return;
    try{
      await api(`/api/schedules/${id}`,{method:'DELETE'});
      toast(t('schedule_deleted','Расписание удалено'));
      loadSchedules();
    }catch(e){toast(e.message,true)}
  }

  function updateScheduleFields(){
    const type=$('#scheduleRecurrence').value;
    $('#scheduleDateField').hidden=type!=='once';
    $('#scheduleTimeField').hidden=type==='interval';
    $('#scheduleIntervalField').hidden=type!=='interval';
    $('#scheduleWeekdays').hidden=type!=='weekly';
  }

  async function createSchedule(e){
    e.preventDefault();
    const form=e.target,fd=new FormData(form),body=Object.fromEntries(fd);
    body.weekdays=fd.getAll('weekdays').map(Number);
    body.is_enabled=fd.has('is_enabled');
    body.interval_minutes=Number(body.interval_minutes||360);
    const editId=Number(form.dataset.editId||0);
    try{
      if(editId){
        await api(`/api/schedules/${editId}`,{method:'PUT',body});
        toast(t('schedule_updated','Расписание обновлено'));
      }else{
        await api('/api/schedules',{method:'POST',body});
        toast(t('schedule_created','Расписание создано'));
      }
      hideModals();
      form.reset();
      form.dataset.editId='';
      loadSchedules();
    }catch(err){toast(err.message,true)}
  }

  const roleLabel=v=>({admin:t('role_admin','Администратор'),operator:t('role_operator','Оператор'),viewer:t('role_viewer','Наблюдатель')}[v]||v);
  const marketplaceNames={kaspi:'Kaspi',ozon:'Ozon',forte_market:'Forte Market',halyk_market:'Halyk Market'};
  async function loadUsers(){if(user.role!=='admin')return;try{const d=await api('/api/users');$('#usersGrid').innerHTML=(d.users||[]).map(u=>{const access=u.marketplaces||{};return `<article class="user-card" data-user-id="${u.id}"><div class="user-card-head"><span class="user-avatar">${esc((u.display_name||'?')[0].toUpperCase())}</span><div><h4>${esc(u.display_name)}</h4><small>${esc(u.email)}</small></div></div><div class="user-access-grid"><label><span>${esc(t('role','Роль'))}</span><select data-user-role="${u.id}"><option value="admin" ${u.role==='admin'?'selected':''}>Администратор</option><option value="operator" ${u.role==='operator'?'selected':''}>Оператор</option><option value="viewer" ${u.role==='viewer'?'selected':''}>Наблюдатель</option></select></label><div class="user-access-state"><span>${esc(t('access','Доступ'))}</span><label class="access-switch"><input type="checkbox" data-user-active="${u.id}" ${u.is_active?'checked':''} ${Number(u.id)===Number(user.id)?'disabled':''}><i></i><em>${u.is_active?t('active','Активен'):t('disabled','Отключён')}</em></label></div></div><div class="user-marketplaces"><span>${esc(t('platforms','Площадки'))}</span><div>${['kaspi','ozon','forte_market','halyk_market'].map(code=>`<label class="marketplace-check ${['forte_market','halyk_market'].includes(code)?'coming':''}"><input type="checkbox" data-user-marketplace="${u.id}" value="${code}" ${access[code]?'checked':''} ${['forte_market','halyk_market'].includes(code)?'disabled':''}><span>${marketplaceNames[code]}${['forte_market','halyk_market'].includes(code)?` · ${t('coming_soon','скоро')}`:''}</span></label>`).join('')}</div></div><div class="user-actions"><button class="secondary" data-save-user="${u.id}">${esc(t('save','Сохранить'))}</button><button class="secondary" data-recovery-user="${u.id}">${esc(t('new_code','Новый код'))}</button>${Number(u.id)!==Number(user.id)?`<button class="danger-soft" data-delete-user="${u.id}" data-user-name="${esc(u.display_name)}">${esc(t('delete','Удалить'))}</button>`:''}</div></article>`}).join('');$$('[data-save-user]').forEach(b=>b.onclick=()=>saveUserAccess(Number(b.dataset.saveUser)));$$('[data-recovery-user]').forEach(b=>b.onclick=()=>regenerateUserRecovery(Number(b.dataset.recoveryUser)));$$('[data-delete-user]').forEach(b=>b.onclick=()=>deleteUser(Number(b.dataset.deleteUser),b.dataset.userName));$$('[data-user-active]').forEach(input=>input.onchange=()=>{const em=input.closest('.access-switch').querySelector('em');em.textContent=input.checked?t('active','Активен'):t('disabled','Отключён')});}catch(e){toast(e.message,true)}}
  async function saveUserAccess(id){try{const role=$(`[data-user-role="${id}"]`).value;const activeNode=$(`[data-user-active="${id}"]`);const is_active=activeNode?activeNode.checked:true;const marketplaces=$$(`[data-user-marketplace="${id}"]:checked`).map(x=>x.value);await api(`/api/users/${id}`,{method:'PUT',body:{role,is_active,marketplaces}});toast('Права пользователя сохранены');loadUsers();}catch(e){toast(e.message,true)}}
  async function regenerateUserRecovery(id){try{const d=await api(`/api/users/${id}/recovery`,{method:'POST'});alert(`Новый код восстановления:
${d.recovery_code}`);}catch(e){toast(e.message,true)}}
  async function deleteUser(id,name){if(!confirm(`Удалить пользователя «${name}»? Действие нельзя отменить.`))return;try{await api(`/api/users/${id}`,{method:'DELETE'});toast('Пользователь удалён');loadUsers();}catch(e){toast(e.message,true)}}

  function showModal(id){$('#'+id).hidden=false} function hideModals(){$$('.modal').forEach(m=>m.hidden=true)}
  async function setWatch(){const codes=[...state.selected].filter(c=>!c.startsWith('ozon:'));if(!codes.length)return toast('Для наблюдения выберите позиции Kaspi',true);try{await api('/api/products/state',{method:'PUT',body:{codes,watched:true}});toast('Позиции добавлены в наблюдение');state.selected.clear();loadProducts();}catch(e){toast(e.message,true)}}

  function updateHelpButton(){const button=$('#helpButton');if(!button)return;button.dataset.page=state.page;button.title=t('help_open','Открыть помощь по текущему разделу');}
  function openHelp(){const content=window.ITPUI?.helpFor(state.page)||window.ITPUI?.helpFor('dashboard');if(!content)return;$('#helpTitle').textContent=content.title||t('help','Помощь');$('#helpBody').innerHTML=`<p class="help-intro">${esc(content.intro||'')}</p>${(content.sections||[]).map(section=>`<section><h3>${esc(section.title)}</h3><ul>${(section.items||[]).map(item=>`<li>${esc(item)}</li>`).join('')}</ul></section>`).join('')}${content.tip?`<aside><b>${esc(t('help_tip','Подсказка'))}</b><span>${esc(content.tip)}</span></aside>`:''}`;$('#helpBackdrop').hidden=false;$('#helpDrawer').classList.add('open');$('#helpDrawer').setAttribute('aria-hidden','false');$('#helpButton').setAttribute('aria-expanded','true');document.body.classList.add('overlay-open');}
  function closeHelp(){const drawer=$('#helpDrawer');if(!drawer)return;drawer.classList.remove('open');drawer.setAttribute('aria-hidden','true');$('#helpBackdrop').hidden=true;$('#helpButton')?.setAttribute('aria-expanded','false');if(!$('#productDrawer')?.classList.contains('open'))document.body.classList.remove('overlay-open');}
  async function persistUiPreference(values){try{await api('/api/settings',{method:'PUT',body:{preferences:values}});}catch(e){toast(e.message,true)}}
  function handleThemeChange(pref){state.theme=pref;persistUiPreference({theme:pref});}

  function bind(){
    $$('.nav').forEach(b=>b.onclick=()=>navigate(b.dataset.page));$$('[data-page-link]').forEach(b=>b.onclick=()=>navigate(b.dataset.pageLink));$('#mobileMenu').onclick=()=>{const nav=$('#sidebar');nav.classList.toggle('open');$('#mobileMenu').setAttribute('aria-expanded',String(nav.classList.contains('open')))}; 
    $('#profileButton').onclick=e=>{e.stopPropagation();$('#profileMenu').hidden=!$('#profileMenu').hidden};$('#profileMenu').onclick=e=>e.stopPropagation();$('#openPassword').onclick=()=>showModal('passwordModal');$$('.modal-close').forEach(b=>b.onclick=hideModals);$('#closeDrawer').onclick=closeDrawer;$('#backdrop').onclick=closeDrawer;
    $$('[data-lang]').forEach(b=>b.onclick=()=>{applyI18n(b.dataset.lang);persistUiPreference({locale:b.dataset.lang})});if($('#helpButton'))$('#helpButton').onclick=e=>{e.stopPropagation();openHelp()};if($('#closeHelp'))$('#closeHelp').onclick=closeHelp;if($('#helpBackdrop'))$('#helpBackdrop').onclick=closeHelp;window.ITPUI?.onTheme(handleThemeChange);window.ITPUI?.onLocale(lang=>{state.lang=lang;applyI18n(lang,{persist:false})});
    $$('[data-quick-action]').forEach(b=>b.onclick=()=>startTask(b.dataset.quickAction));
    $('#refreshProducts').onclick=loadProducts;$('#productSearch').oninput=debounce(()=>{state.products.page=1;loadProducts()},350);['platformFilter','brandFilter','statusFilter','pageSize','sortProducts'].forEach(id=>$('#'+id).onchange=()=>{state.products.page=1;loadProducts()});$('#resetFilters').onclick=()=>{$('#productSearch').value='';$('#platformFilter').value='';$('#brandFilter').value='';$('#statusFilter').value='';if($('#freshnessFilter'))$('#freshnessFilter').value='';state.products.scope='all';$$('#scopeTabs button').forEach(b=>b.classList.toggle('active',b.dataset.scope==='all'));loadProducts()};
    $$('#scopeTabs button').forEach(b=>b.onclick=()=>{state.products.scope=b.dataset.scope;state.products.page=1;$$('#scopeTabs button').forEach(x=>x.classList.toggle('active',x===b));loadProducts()});$('#prevPage').onclick=()=>{if(state.products.page>1){state.products.page--;loadProducts()}};$('#nextPage').onclick=()=>{if(state.products.page<state.products.pages){state.products.page++;loadProducts()}};$('#selectPage').onchange=e=>{state.products.items.forEach(p=>e.target.checked?state.selected.add(p.product_code):state.selected.delete(p.product_code));renderProducts(state.products.items)};$('#clearSelection').onclick=()=>{state.selected.clear();renderProducts(state.products.items)};$('#watchSelected').onclick=setWatch;$('#analyzeSelected').onclick=()=>startTask('scan_market','selected',[...state.selected].filter(c=>!c.startsWith('ozon:')));$('#exportSelected').onclick=()=>startTask('export_report','selected',[...state.selected]);$('#selectedReport').onclick=()=>startTask('export_report',state.selected.size?'selected':'all',[...state.selected]);
    $('#operationPlatform').onchange=updateOperationActions;$('#launchOperation').onclick=()=>{const scope=$('#operationScope').value,codes=scope==='selected'?[...state.selected]:[];startTask($('#operationAction').value,scope,codes)};$('#clearOperations').onclick=async()=>{if(!confirm('Удалить историю завершённых операций и журналы?'))return;try{await api('/api/tasks',{method:'DELETE'});loadTasks();}catch(e){toast(e.message,true)}};$('#refreshLog').onclick=refreshLog;$('#stopTask').onclick=()=>stopTask(state.currentTask);
    $('#generateReport').onclick=()=>startTask('export_report');$('#saveSettings').onclick=saveSettings;if($('#addSchedule'))$('#addSchedule').onclick=openScheduleModal;if($('#scheduleRecurrence'))$('#scheduleRecurrence').onchange=updateScheduleFields;if($('#scheduleForm'))$('#scheduleForm').onsubmit=createSchedule;
    $('#passwordForm').onsubmit=async e=>{e.preventDefault();const f=Object.fromEntries(new FormData(e.target));try{await api('/api/account/password',{method:'POST',body:f});toast(t('password_changed','Пароль изменён'));hideModals();e.target.reset();}catch(err){toast(err.message,true)}};
    if($('#addUser'))$('#addUser').onclick=()=>showModal('userModal');if($('#userForm'))$('#userForm').onsubmit=async e=>{e.preventDefault();try{const fd=new FormData(e.target),body=Object.fromEntries(fd);body.marketplaces=fd.getAll('marketplaces');const d=await api('/api/users',{method:'POST',body});toast(`Пользователь создан. Код восстановления: ${d.recovery_code}`);hideModals();e.target.reset();loadUsers();}catch(err){toast(err.message,true)}};
    document.addEventListener('click',e=>{const menu=$('#profileMenu'),button=$('#profileButton');if(menu&&!menu.hidden&&!menu.contains(e.target)&&!button.contains(e.target))menu.hidden=true;const nav=$('#sidebar'),mobile=$('#mobileMenu');if(nav?.classList.contains('open')&&!nav.contains(e.target)&&!mobile?.contains(e.target)){nav.classList.remove('open');mobile?.setAttribute('aria-expanded','false')};$$('.modal:not([hidden])').forEach(modal=>{if(e.target===modal)modal.hidden=true});});document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeDrawer();closeHelp();hideModals();if($('#profileMenu'))$('#profileMenu').hidden=true;$('#sidebar')?.classList.remove('open');$('#mobileMenu')?.setAttribute('aria-expanded','false')}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();navigate('products');$('#productSearch').focus()}});
  }
  function debounce(fn,ms){let t;return(...a)=>{clearTimeout(t);t=setTimeout(()=>fn(...a),ms)}}

  function applyMarketplaceAccess(){const access=user.marketplaces&&Object.keys(user.marketplaces).length?user.marketplaces:{kaspi:true,ozon:true};['kaspi','ozon'].forEach(code=>{const allowed=Boolean(access[code]);$$(`#platformFilter option[value="${code}"],#operationPlatform option[value="${code}"]`).forEach(option=>option.hidden=!allowed)});const visible=$$('#operationPlatform option').filter(option=>!option.hidden);if(visible.length&&!visible.some(option=>option.value===$('#operationPlatform').value))$('#operationPlatform').value=visible[0].value;const filterVisible=$$('#platformFilter option').filter(option=>!option.hidden);if(filterVisible.length&&!filterVisible.some(option=>option.value===$('#platformFilter').value))$('#platformFilter').value=filterVisible[0].value;}

  async function init(){
    bind();applyMarketplaceAccess();state.lang=window.ITPUI?.getLocale()||localStorage.getItem('itp_lang')||'ru';state.theme=window.ITPUI?.getTheme()||'system';window.ITPUI?.setTheme(state.theme,{store:false,emit:false});applyI18n(state.lang,{persist:false});updateHelpButton();updateOperationActions();await Promise.all([loadOptions(),loadOverview(),loadTasks()]);$('#app').hidden=false;setTimeout(()=>$('#boot').classList.add('hide'),150);setInterval(()=>{loadOverview();if(state.page==='operations')loadTasks()},15000);
  }
  init().catch(e=>{console.error(e);$('#boot').innerHTML=`<strong>Ошибка запуска</strong><span>${esc(e.message)}</span>`});
})();
