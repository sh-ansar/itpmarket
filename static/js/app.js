(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const csrf = $('meta[name="csrf-token"]')?.content || '';
  const user = window.ITP_USER || {};
  const can = code => Boolean(user.platform_role==='superadmin'||user.permissions?.[code]);

  const I18N = window.ITP_LOCALES || {ru:{}};
  const PERMISSION_LABELS={view_dashboard:'Обзор',view_products:'Просмотр товаров',manage_products:'Изменение товаров',view_inventory:'Просмотр остатков и закупочных цен',manage_inventory:'Изменение остатков и закупочных цен',manage_product_matching:'Подтверждение сопоставления товаров',view_operations:'Просмотр операций',run_operations:'Запуск операций',manage_operations:'Остановка и удаление операций',view_reports:'Просмотр отчётов',create_reports:'Создание отчётов',view_settings:'Просмотр настроек',manage_company:'Профиль компании',manage_marketplaces:'Подключения marketplace',manage_filters:'Фильтры каталога',manage_users:'Сотрудники и права',view_help:'Справка'};

  const STATUS_LABELS = {
    ru:{NOT_ANALYZED:'Точные предложения не проверены',NO_OTHER_SELLERS:'Других продавцов не найдено',INSUFFICIENT_DATA:'Недостаточно точных предложений',REVIEW_REQUIRED:'Требует ручной проверки',EXACT_LOWEST:'Единственная минимальная цена',EXACT_TIED_LOWEST:'Делит минимальную цену с другими продавцами',EXACT_BELOW:'Ниже медианы продавцов',EXACT_IN_MARKET:'В рыночном диапазоне',EXACT_ABOVE:'Выше медианы продавцов',EXACT_HIGHEST:'Единственная максимальная цена',EXACT_TIED_HIGHEST:'Делит максимальную цену с другими продавцами',EXACT_COMPETITIVE:'В рыночном диапазоне',DATA_COLLECTED:'Данные собраны',DATA_ERROR:'Ошибка получения точных предложений',COMPARABLE_LOWEST:'Ниже сопоставимого рынка',COMPARABLE_BELOW:'Ниже медианы бренда и размера',COMPARABLE_IN_MARKET:'В диапазоне бренда и размера',COMPARABLE_ABOVE:'Выше медианы бренда и размера',COMPARABLE_HIGHEST:'Выше сопоставимого рынка'},
    kk:{NOT_ANALYZED:'Нақты ұсыныстар тексерілмеген',NO_OTHER_SELLERS:'Басқа сатушылар табылмады',INSUFFICIENT_DATA:'Нақты ұсыныстар жеткіліксіз',REVIEW_REQUIRED:'Қолмен тексеру қажет',EXACT_LOWEST:'Бірегей ең төмен баға',EXACT_TIED_LOWEST:'Басқа сатушылармен ең төмен бағаны бөліседі',EXACT_BELOW:'Сатушылар медианасынан төмен',EXACT_IN_MARKET:'Нарық диапазонында',EXACT_ABOVE:'Сатушылар медианасынан жоғары',EXACT_HIGHEST:'Бірегей ең жоғары баға',EXACT_TIED_HIGHEST:'Басқа сатушылармен ең жоғары бағаны бөліседі',EXACT_COMPETITIVE:'Нарық диапазонында',DATA_COLLECTED:'Дерек жиналды',DATA_ERROR:'Нақты ұсыныстарды алу қатесі',COMPARABLE_LOWEST:'Салыстырмалы нарықтан төмен',COMPARABLE_BELOW:'Бренд және өлшем медианасынан төмен',COMPARABLE_IN_MARKET:'Бренд және өлшем диапазонында',COMPARABLE_ABOVE:'Бренд және өлшем медианасынан жоғары',COMPARABLE_HIGHEST:'Салыстырмалы нарықтан жоғары'},
    en:{NOT_ANALYZED:'Exact offers not checked',NO_OTHER_SELLERS:'No other sellers found',INSUFFICIENT_DATA:'Insufficient exact offers',REVIEW_REQUIRED:'Manual review required',EXACT_LOWEST:'Unique lowest price',EXACT_TIED_LOWEST:'Tied for the lowest price',EXACT_BELOW:'Below seller median',EXACT_IN_MARKET:'Within market range',EXACT_ABOVE:'Above seller median',EXACT_HIGHEST:'Unique highest price',EXACT_TIED_HIGHEST:'Tied for the highest price',EXACT_COMPETITIVE:'Within market range',DATA_COLLECTED:'Data collected',DATA_ERROR:'Exact-offer collection error',COMPARABLE_LOWEST:'Below comparable market',COMPARABLE_BELOW:'Below brand-size median',COMPARABLE_IN_MARKET:'Within brand-size range',COMPARABLE_ABOVE:'Above brand-size median',COMPARABLE_HIGHEST:'Above comparable market'}
  };


  const ACTIONS = {
    kaspi:[['kaspi_catalog_collect','Сбор каталога'],['kaspi_price_actualize','Актуализация цен'],['kaspi_full_sync','Полная синхронизация'],['audit_catalog','Аудит каталога']],
    ozon:[['ozon_catalog_collect','Сбор каталога'],['ozon_price_actualize','Актуализация цен'],['ozon_full_sync','Полная синхронизация']],
    ozon_kz:[['ozon_kz_catalog_collect','Сбор каталога'],['ozon_kz_price_actualize','Актуализация цен'],['ozon_kz_full_sync','Полная синхронизация']],
    halyk_market:[['halyk_catalog_collect','Сбор каталога'],['halyk_price_actualize','Актуализация цен'],['halyk_full_sync','Полная синхронизация']],
    forte_market:[['forte_catalog_collect','Сбор каталога'],['forte_price_actualize','Актуализация цен'],['forte_full_sync','Полная синхронизация']],
    wildberries:[['wb_catalog_collect','Сбор каталога'],['wb_price_actualize','Актуализация цен'],['wb_full_sync','Полная синхронизация']],
    system:[['full_sync_all','Полная синхронизация доступных площадок'],['export_report','Сводный отчёт'],['backup_database','Резервная копия']]
  };
  const LEGACY_ACTIONS = {
    sync_catalog:['Синхронизация каталога','kaspi'],update_own_prices:['Обновление цен компании','kaspi'],scan_market:['Точные предложения всех продавцов','kaspi'],refresh_market:['Обновить устаревшие точные цены','kaspi'],retry_errors:['Повтор ошибок точных карточек','kaspi'],
    ozon_open_browser:['Ozon.ru: открыть браузер','ozon'],ozon_discover:['Ozon.ru: обнаружение товаров','ozon'],ozon_enrich:['Ozon.ru: характеристики новых товаров','ozon'],ozon_market_search:['Ozon.ru: поиск рыночных предложений','ozon'],ozon_refresh_prices:['Ozon.ru: обновление цен','ozon'],ozon_refresh_stale:['Ozon.ru: обновление характеристик','ozon'],ozon_retry:['Ozon.ru: повтор ошибок','ozon'],ozon_export:['Ozon.ru: экспорт реестра','ozon'],
    halyk_sync_catalog:['Halyk Market: синхронизация каталога','halyk_market'],halyk_refresh_offers:['Halyk Market: точные предложения продавцов','halyk_market'],
    forte_sync_catalog:['Forte Market: синхронизация каталога','forte_market'],forte_refresh_offers:['Forte Market: точные предложения продавцов','forte_market']
  };
  const RELATED_ACTIONS = {
    kaspi_catalog_collect:['kaspi_catalog_collect','sync_catalog'],
    kaspi_price_actualize:['kaspi_price_actualize','update_own_prices','scan_market','refresh_market','retry_errors'],
    kaspi_full_sync:['kaspi_full_sync'],
    ozon_catalog_collect:['ozon_catalog_collect','ozon_discover','ozon_enrich'],
    ozon_price_actualize:['ozon_price_actualize','ozon_market_search','ozon_refresh_prices','ozon_refresh_stale','ozon_retry'],
    ozon_full_sync:['ozon_full_sync'],
    ozon_kz_catalog_collect:['ozon_kz_catalog_collect'],
    ozon_kz_price_actualize:['ozon_kz_price_actualize','ozon_kz_refresh_prices'],
    ozon_kz_full_sync:['ozon_kz_full_sync'],
    halyk_catalog_collect:['halyk_catalog_collect','halyk_sync_catalog'],
    halyk_price_actualize:['halyk_price_actualize','halyk_refresh_offers'],
    halyk_full_sync:['halyk_full_sync'],
    forte_catalog_collect:['forte_catalog_collect','forte_sync_catalog'],
    forte_price_actualize:['forte_price_actualize','forte_refresh_offers'],
    forte_full_sync:['forte_full_sync'],
    wb_catalog_collect:['wb_catalog_collect'],
    wb_price_actualize:['wb_price_actualize'],
    wb_full_sync:['wb_full_sync'],
    full_sync_all:['full_sync_all'],export_report:['export_report'],audit_catalog:['audit_catalog'],backup_database:['backup_database']
  };
  const FILTERABLE_ACTIONS = new Set(['kaspi_price_actualize','ozon_price_actualize','ozon_kz_price_actualize','halyk_price_actualize','forte_price_actualize','export_report']);


  const state = {lang:'ru',theme:'system',page:'dashboard',overview:null,overviewLoading:false,products:{page:1,pages:1,pageSize:30,scope:'all',items:[],requestStartedAt:0,lastDurationMs:0},selected:new Set(),tasks:[],tasksLoading:false,currentTask:null,currentProductCode:'',settings:null,catalogConfig:null,reportRequest:0,notifications:[],notificationsInitialized:false,lastNotificationId:0,inventoryLoaded:false,inventoryLoading:false,telegram:null};
  const multiSelectRegistry = new Map();
  let helpReturnFocus = null;
  let productsRequestController = null;
  let productsRequestSerial = 0;
  let productDrawerController = null;

  function multiValues(target){
    const node=typeof target==='string'?$(target):target;
    if(!node)return [];
    return [...node.selectedOptions].map(option=>option.value).filter(Boolean);
  }
  function multiLabels(target){
    const node=typeof target==='string'?$(target):target;
    if(!node)return [];
    return [...node.selectedOptions].filter(option=>option.value).map(option=>option.textContent.trim());
  }
  function closeMultiSelects(except=null){
    multiSelectRegistry.forEach((control,node)=>{if(node!==except)control.close();});
  }
  function refreshMultiSelect(target){
    const node=typeof target==='string'?$(target):target;
    multiSelectRegistry.get(node)?.refresh();
  }
  function refreshAllMultiSelects(){multiSelectRegistry.forEach(control=>control.refresh());}
  function clearMultiSelect(target,{emit=false}={}){
    const node=typeof target==='string'?$(target):target;
    if(!node)return;
    [...node.options].forEach(option=>{option.selected=false;});
    refreshMultiSelect(node);
    if(emit)node.dispatchEvent(new Event('change',{bubbles:true}));
  }
  function initMultiSelect(select){
    if(!select||multiSelectRegistry.has(select))return multiSelectRegistry.get(select);
    const wrapper=document.createElement('div');wrapper.className='multi-select';
    select.parentNode.insertBefore(wrapper,select);wrapper.appendChild(select);select.classList.add('multi-select-native');
    const button=document.createElement('button');button.type='button';button.className='multi-select-button';button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-expanded','false');button.innerHTML='<span></span><b aria-hidden="true">0</b><i aria-hidden="true"></i>';
    const popover=document.createElement('div');popover.className='multi-select-popover';popover.hidden=true;
    popover.innerHTML='<label class="multi-select-search"><span aria-hidden="true">⌕</span><input type="search" autocomplete="off" placeholder="Найти"></label><div class="multi-select-options" role="listbox" aria-multiselectable="true"></div><div class="multi-select-footer"><small></small><button type="button">Очистить</button></div>';
    wrapper.append(button,popover);
    const summary=$('span',button),badge=$('b',button),search=$('input',popover),optionsBox=$('.multi-select-options',popover),footerCount=$('.multi-select-footer small',popover),clearButton=$('.multi-select-footer button',popover);
    const close=()=>{popover.hidden=true;button.setAttribute('aria-expanded','false');wrapper.classList.remove('open');};
    const open=()=>{if(select.disabled)return;closeMultiSelects(select);popover.hidden=false;button.setAttribute('aria-expanded','true');wrapper.classList.add('open');if(!search.closest('label').hidden){search.value='';filterOptions('');setTimeout(()=>search.focus(),0);}};
    const filterOptions=value=>{const query=String(value||'').trim().toLowerCase();$$('.multi-select-option',optionsBox).forEach(row=>{row.hidden=Boolean(query)&&!row.dataset.search.includes(query);});};
    const refresh=()=>{
      const options=[...select.options].filter(option=>option.value&&!option.hidden);
      const selected=options.filter(option=>option.selected&&!option.disabled);
      const placeholder=select.dataset.placeholder||'Выберите значения';
      const labels=selected.map(option=>option.textContent.trim());
      summary.textContent=!labels.length?placeholder:labels.length<=2?labels.join(', '):`${labels.length} выбрано`;
      badge.textContent=String(labels.length);badge.hidden=labels.length<2;
      button.classList.toggle('has-value',labels.length>0);button.disabled=select.disabled;
      button.title=labels.join(', ');
      optionsBox.innerHTML=options.map(option=>`<label class="multi-select-option${option.disabled?' disabled':''}" data-search="${esc(option.textContent.trim().toLowerCase())}"><input type="checkbox" value="${esc(option.value)}"${option.selected?' checked':''}${option.disabled?' disabled':''}><span>${esc(option.textContent.trim())}</span></label>`).join('')||'<div class="multi-select-empty">Нет доступных значений</div>';
      $$('input[type="checkbox"]',optionsBox).forEach(input=>input.onchange=()=>{const option=[...select.options].find(item=>item.value===input.value);if(option)option.selected=input.checked;select.dispatchEvent(new Event('change',{bubbles:true}));});
      search.closest('label').hidden=options.length<8;
      footerCount.textContent=labels.length?`Выбрано: ${labels.length}`:`Доступно: ${options.length}`;
      clearButton.hidden=!labels.length;
      filterOptions(search.value);
    };
    button.onclick=event=>{event.stopPropagation();popover.hidden?open():close();};
    popover.onclick=event=>event.stopPropagation();search.oninput=()=>filterOptions(search.value);
    clearButton.onclick=()=>{clearMultiSelect(select,{emit:true});};
    select.addEventListener('change',refresh);
    const control={refresh,close,open};multiSelectRegistry.set(select,control);refresh();return control;
  }
  function initMultiSelects(){$$('select[data-multi-select]').forEach(initMultiSelect);}

  async function api(path, options={}) {
    const timeoutMs=Number(options.timeoutMs||25000);
    const externalSignal=options.signal||null;
    const controller=new AbortController();
    let externallyAborted=false;
    const forwardAbort=()=>{externallyAborted=true;controller.abort()};
    if(externalSignal){if(externalSignal.aborted)forwardAbort();else externalSignal.addEventListener('abort',forwardAbort,{once:true})}
    const opts = {...options, headers:{...(options.headers||{})},signal:controller.signal};
    delete opts.timeoutMs;
    if (opts.body && typeof opts.body !== 'string') { opts.headers['Content-Type']='application/json'; opts.body=JSON.stringify(opts.body); }
    if (opts.method && opts.method !== 'GET') opts.headers['X-CSRF-Token']=csrf;
    const timer=controller?setTimeout(()=>controller.abort(),timeoutMs):0;
    try{
      const res = await fetch(path, opts);
      const data = await res.json().catch(()=>({ok:false,error:`HTTP ${res.status}`}));
      if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
      return data;
    }catch(error){
      if(error?.name==='AbortError'&&externallyAborted)throw error;
      if(error?.name==='AbortError'){const timeoutError=new Error('Запрос занял слишком много времени. Текущие данные сохранены — попробуйте обновить ещё раз.');timeoutError.code='REQUEST_TIMEOUT';throw timeoutError}
      throw error;
    }finally{if(timer)clearTimeout(timer);externalSignal?.removeEventListener('abort',forwardAbort)}
  }
  const esc = v => String(v ?? '').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const externalHref = v => {
    const raw=String(v||'').trim();
    if(!raw || !/^https?:\/\//i.test(raw)) return '';
    try{return encodeURI(raw)}catch{return raw}
  };
  const icon = (name, cls='ui-icon') => `<img class="${cls}" src="/static/icons/${encodeURIComponent(name)}.svg" alt="">`;
  const money = v => v===null||v===undefined||v===''?'—':`${Math.round(Number(v)).toLocaleString('ru-RU')} ₸`;
  const number = v => Number(v||0).toLocaleString(state.lang==='en'?'en-GB':state.lang==='kk'?'kk-KZ':'ru-RU');
  const t = (key,fallback='') => window.ITPUI?.t(key,fallback) ?? I18N[state.lang]?.[key] ?? I18N.ru?.[key] ?? fallback ?? key;
  const statusClass = tone => `status status-${esc(tone||'neutral')}`;
  const productCodeText = code => code ? `${t('product_code_label','Код товара')}: ${code}` : '';
  const dateText = v => { if(!v)return '—'; const d=new Date(v); return isNaN(d)?String(v):d.toLocaleString(state.lang==='en'?'en-GB':state.lang==='kk'?'kk-KZ':'ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}); };
  const statusLabel = code => STATUS_LABELS[state.lang]?.[code] || STATUS_LABELS.ru[code] || code || '—';
  const toast = (text,error=false) => { const e=document.createElement('div');e.className=`toast${error?' error':''}`;e.textContent=text;$('#toasts').append(e);setTimeout(()=>e.remove(),4200); };
  function renderNotifications(){
    const list=$('#notificationList'),badge=$('#notificationBadge'),summary=$('#notificationSummary');if(!list)return;
    const unread=state.notifications.filter(item=>!item.read_at).length;
    if(badge){badge.textContent=String(Math.min(unread,99));badge.hidden=!unread;}
    if(summary)summary.textContent=unread?`${unread} непрочитанных`:'Нет новых уведомлений';
    list.innerHTML=state.notifications.length?state.notifications.map(item=>`<article class="notification-item ${esc(item.level)}${item.read_at?' read':''}" data-notification-id="${Number(item.id)}"><div><span>${esc(item.category==='billing'?'Оплата':item.category==='operations'?'Операции':'Система')}</span><time>${esc(dateText(item.created_at))}</time></div><b>${esc(item.title)}</b><p>${esc(item.message||'')}</p></article>`).join(''):`<div class="empty">Уведомлений пока нет.</div>`;
  }
  async function loadNotifications({announce=true}={}){
    try{
      const data=await api('/api/notifications?limit=60');const items=data.items||[];
      const newest=items.reduce((value,item)=>Math.max(value,Number(item.id||0)),0);
      if(state.notificationsInitialized&&announce)items.filter(item=>!item.read_at&&Number(item.id)>state.lastNotificationId).reverse().forEach(item=>toast(`${item.title}${item.message?`: ${item.message}`:''}`,item.level==='danger'));
      state.notifications=items;state.lastNotificationId=Math.max(state.lastNotificationId,newest);state.notificationsInitialized=true;renderNotifications();
    }catch(error){console.error(error)}
  }
  function closeNotifications(){const drawer=$('#notificationDrawer');if(drawer)drawer.hidden=true;$('#notificationButton')?.setAttribute('aria-expanded','false');}
  const taskStatus = v=>t(`task_${v}`,({running:'Выполняется',completed:'Окончено',failed:'Ошибка',stopped:'Остановлено',interrupted:'Прервано'}[v]||v));
  const marketplaceLabel = v => ({kaspi:'Kaspi',ozon:'Ozon.ru',ozon_kz:'Ozon.kz',halyk_market:'Halyk Market',forte_market:'Forte Market',wildberries:'Wildberries',system:'Spyon'}[v]||v||'Spyon');
  const visibleMarketplaceCodes = () => ['kaspi','ozon','ozon_kz','halyk_market','forte_market','wildberries'].filter(code=>Boolean(user.marketplaces?.[code]));
  const platformCounts = o => visibleMarketplaceCodes().map(code=>`${marketplaceLabel(code)} ${number(({kaspi:o.kaspi_count||o.kaspi_products,ozon:o.ozon_count||o.ozon_products,ozon_kz:o.ozon_kz_count||o.ozon_kz_products,halyk_market:o.halyk_count||o.halyk_products,forte_market:o.forte_count||o.forte_products,wildberries:o.wildberries_count||o.wildberries_products})[code])}`).join(' · ') || '—';
  const platformReady = o => visibleMarketplaceCodes().map(code=>`${marketplaceLabel(code)} ${number(({kaspi:o.kaspi_market_analyzed_count,ozon:o.ozon_data_ready_count,ozon_kz:o.ozon_kz_data_ready_count,halyk_market:o.halyk_market_analyzed_count,forte_market:o.forte_market_analyzed_count,wildberries:o.wildberries_data_ready_count})[code])}/${number(({kaspi:o.kaspi_count||o.kaspi_products,ozon:o.ozon_count||o.ozon_products,ozon_kz:o.ozon_kz_count||o.ozon_kz_products,halyk_market:o.halyk_count||o.halyk_products,forte_market:o.forte_count||o.forte_products,wildberries:o.wildberries_count||o.wildberries_products})[code])}`).join(' · ') || '—';
  const taskStatusTone = v=>({running:'info',completed:'success',failed:'danger',stopped:'warning',interrupted:'warning'}[v]||'neutral');
  const durationText = seconds => { const total=Math.max(0, Math.round(Number(seconds||0))); if(total<60) return `${total} сек`; const mins=Math.floor(total/60); const secs=total%60; if(mins<60) return secs?`${mins} мин ${secs} сек`:`${mins} мин`; const hours=Math.floor(mins/60); const rest=mins%60; return rest?`${hours} ч ${rest} мин`:`${hours} ч`; };
  const operationLaunchers = [
    {platform:'operationPlatform', seller:'operationSeller', action:'operationAction', scope:'operationScope', launch:'launchOperation'},
    {platform:'opsOperationPlatform', seller:'opsOperationSeller', action:'opsOperationAction', scope:'opsOperationScope', launch:'opsLaunchOperation'}
  ];
  const flatActions = () => Object.entries(ACTIONS).flatMap(([platform,items])=>items.map(([id,label])=>({id,label,platform})));
  const actionInfo = id => flatActions().find(item=>item.id===id) || (LEGACY_ACTIONS[id]?{id,label:LEGACY_ACTIONS[id][0],platform:LEGACY_ACTIONS[id][1]}:{id,label:id,platform:'system'});
  const taskPlatform = task => flatActions().find(item=>item.id===task?.name)?.platform || LEGACY_ACTIONS[task?.name]?.[1] || task?.metadata?.platform || task?.platform || 'system';
  const tenantLabel = () => String(user.tenant_name || user.company_name || '').trim() || t('company_name','компании');
  const actionLabel = (id,fallback='') => id === 'update_own_prices'
    ? `${t('updated','Обновление')} ${tenantLabel()}`
    : t(`action_${id}`, actionInfo(id).label || fallback || id);
  const formatEta = task => { const percent=Number(task?.progress?.percent||0); if(!(percent>0 && percent<100) || !task?.started_at) return ''; const elapsed=Math.max(1, (Date.now()-new Date(task.started_at).getTime())/1000); const total=elapsed/(percent/100); const remaining=Math.max(0,total-elapsed); return remaining>0 ? `Осталось ~${durationText(remaining)}` : ''; };
  const looksGarbled = text => {
    const value=String(text||'');
    if(!value) return false;
    const mojibake=(value.match(/(?:Р[°±ІіЇїЅѕµ¶·ё№º»ј¼½ѕ¿Єє„…†‡€‰Љ‹ЊЌЋЏЎў]|С[‚ѓ„…†‡€‰Љ‹ЊЌЋЏ]|вЂ|в„|В·|Ð|Ñ|�)/g)||[]).length;
    const suspicious=(value.match(/[РСÐÑ]{2,}/g)||[]).length + (value.match(/Ã|Â|�/g)||[]).length;
    const cyr=(value.match(/[А-Яа-яЁё]/g)||[]).length;
    return mojibake>=2 || (suspicious>0 && suspicious>=Math.max(2, Math.floor(cyr/6)));
  };
  const taskProgressText = task => { const p=task?.progress; if(!p) return task?.message || ''; if(p.current!=null && p.total!=null) return `${p.current} из ${p.total} · ${Number(p.percent||0).toFixed(0)}%`; return `${Number(p.percent||0).toFixed(0)}%`; };
  const technicalLogLine = line => /(?:[A-Z]:\\|\\\\|\/Users\/|\/tmp\/|\.py\b|\.bat\b|\.ps1\b|\.csv\b|\.json\b|\.html\b|Traceback|^\s*File ")/i.test(line);
  const cleanLogLine = line => {
    const raw=String(line||'').trim();
    if(!raw || looksGarbled(raw) || /^[=\-_\s]{12,}$/.test(raw)) return '';
    if(technicalLogLine(raw)) return t('report_file_ready','Файл отчёта подготовлен');
    return raw
      .replace(/^\[([^\]]+)\]\s*/, '$1: ')
      .replace(/\bproduct_code\b/gi, t('product_code_label','Код товара').toLowerCase())
      .replace(/\bseller\b/gi, t('seller','Продавец').toLowerCase())
      .replace(/\bupdated=(\d+)/gi, `${t('updated_count_label','Обновлено')}: $1`)
      .replace(/\berrors?=(\d+)/gi, `${t('errors_count_label','Ошибок')}: $1`)
      .replace(/\bblocked=(\d+)/gi, `${t('blocked_count_label','Заблокировано')}: $1`)
      .replace(/\bresponse=(\d+)/gi, `${t('responses_count_label','Ответов')}: $1`)
      .replace(/\bno offers=(\d+)/gi, `${t('no_offers_count_label','Без предложения')}: $1`)
      .replace(/\s+/g, ' ');
  };
  const friendlyLog = text => {
    const lines=String(text||'').split(/\r?\n/).map(cleanLogLine).filter(line=>line && !looksGarbled(line) && !/^[=\-_\s]{12,}$/.test(line));
    return lines.length ? lines.join('\n') : t('log_empty','Журнал пуст');
  };
  const taskSecondaryText = task => {
    const eta=formatEta(task);
    const progress=taskProgressText(task);
    if(task?.progress?.current!=null && task?.progress?.total!=null){
      return eta ? `${progress} · ${eta}` : progress;
    }
    const line=cleanLogLine(task?.last_line||'');
    if(line && line.length<160) return line;
    if(task?.running && eta && progress) return `${progress} · ${eta}`;
    if(task?.running && progress) return progress;
    const message=cleanLogLine(task?.message || '');
    if(message) return message;
    return {
      completed:t('task_completed_message','Операция завершена'),
      failed:t('task_failed_message','Операция завершилась с ошибкой'),
      interrupted:t('task_interrupted_message','Операция прервана'),
      stopped:t('task_stopped_message','Операция остановлена')
    }[task?.status] || '';
  };
  const logSummaryHtml = task => {
    const tone=taskStatusTone(task.status);
    const p=task?.progress||{};
    const rows=[
      [t('status','Статус'), `<span class="${statusClass(tone)}">${esc(taskStatus(task.status))}</span>`],
      [t('marketplace','Площадка'), esc(marketplaceLabel(taskPlatform(task)))],
      [t('updated','Обновлено'), esc(dateText(task?.updated_at||task?.started_at))]
    ];
    if(p.current!=null && p.total!=null) rows.splice(1,0,[t('progress_label','Прогресс'), esc(taskProgressText(task))]);
    return `<div class="log-summary-grid">${rows.map(([label,value])=>`<div><small>${esc(label)}</small><b>${value}</b></div>`).join('')}</div>`;
  };

  function applyI18n(lang,{persist=true}={}) {
    state.lang = I18N[lang] ? lang : 'ru';
    window.ITPUI?.setLocale(state.lang,{store:persist,emit:false});
    $$('[data-lang]').forEach(b=>b.classList.toggle('active',b.dataset.lang===state.lang));
    if($('#languageSelect')) $('#languageSelect').value=state.lang;
    window.ITPUI?.translateTree(document.body);
    $$('[data-company-price-label]').forEach(node=>{node.textContent=`Обновить цены ${tenantLabel()}`;});
    const roleNode=$('#profileRole');if(roleNode)roleNode.textContent=roleLabel(user.role);
    renderPageHeading();updateHelpButton();
    if(state.page==='dashboard'&&state.overview){renderStatusChart(state.overview.status_distribution||[]);renderHealth(state.overview.health||{},state.overview);}
    if(state.products.items.length)renderProducts(state.products.items);
    $$('[data-i18n-tooltip]').forEach(el=>{const value=t(el.dataset.i18nTooltip,el.getAttribute('aria-label')||'');el.dataset.tooltip=value;el.title=value;});
    updateOperationActions();
    refreshAllMultiSelects();
    if($('#helpDrawer')?.classList.contains('open'))openHelp({preserveFocus:true});
    if(state.page==='operations'&&state.tasks.length)renderTasks();
    if(state.page==='schedules')loadSchedules();
    if(state.page==='users')loadUsers();
  }
  function renderPageHeading(){ const node=$('#pageTitle'),wrap=node?.closest('.page-section-header'); if(!node||!wrap)return; wrap.hidden=['dashboard','products','operations','reports','schedules','users'].includes(state.page); const key=`${state.page}_page_title`; node.textContent=t(key,t(`nav_${state.page}`,'Spyon')); }
  const PAGE_PERMISSIONS={dashboard:'view_dashboard',products:'view_products',operations:'view_operations',schedules:'view_operations',reports:'view_reports',users:'manage_users',settings:'view_settings'};
  function navigate(page){
    if(PAGE_PERMISSIONS[page]&&!can(PAGE_PERMISSIONS[page]))return toast('Недостаточно прав.',true);
    state.page=page;$$('.page').forEach(x=>x.classList.toggle('active',x.id===`page-${page}`));$$('.nav').forEach(x=>x.classList.toggle('active',x.dataset.page===page));renderPageHeading();
    if(page==='products'){loadProducts();if(can('view_inventory'))loadInventorySummary()}
    if(page==='operations')loadTasks();if(page==='reports')loadReports();if(page==='schedules')loadSchedules();if(page==='settings')loadSettings();if(page==='users')loadUsers();
    $('#sidebar').classList.remove('open');$('#mobileMenu')?.setAttribute('aria-expanded','false');closeHelp();updateHelpButton();
  }
  function applyPermissions(){
    Object.entries(PAGE_PERMISSIONS).forEach(([page,permission])=>{
      $$(`[data-page="${page}"],[data-page-link="${page}"]`).forEach(node=>node.hidden=!can(permission));
    });
    const operationsAllowed=can('run_operations')&&(user.tenant_status==='approved'||user.platform_role==='superadmin')&&(user.tenant_profile_complete||user.platform_role==='superadmin');
    ['#productsOperationBar','#opsOperationBar','#addSchedule','#generateReport','#selectedReport','#analyzeSelected','#exportSelected'].forEach(selector=>{const node=$(selector);if(node)node.hidden=!operationsAllowed;});
    $$('.hero-quick-actions,[data-quick-action]').forEach(node=>node.hidden=!operationsAllowed);
  }

  async function loadOverview(){
    if(state.overviewLoading)return;state.overviewLoading=true;
    try{
      const data=await api('/api/overview',{timeoutMs:60000}); const o=data.overview; state.overview=o; state.tasks=data.tasks||[];
      const dataCoverage=Number(o.data_coverage_pct ?? o.scan_coverage_pct ?? 0);
      const readyCount=Number(o.data_ready_count ?? o.scanned_count ?? 0);
      $('#navProductCount').textContent=number(o.catalog_count); $('#heroProducts').textContent=number(o.catalog_count);$('#heroAnalyzed').textContent=`${dataCoverage.toFixed(0)}%`;$('#heroRisks').textContent=number(o.risk_count);
      $('#metricProducts').textContent=number(o.catalog_count);$('#metricPlatforms').textContent=platformCounts(o);$('#metricAnalyzed').textContent=`${dataCoverage.toFixed(1)}%`;$('#metricAnalyzedSub').textContent=platformReady(o);$('#metricRisks').textContent=number(o.risk_count);$('#metricOpportunity').textContent=money(o.price_potential_monthly_kzt ?? o.potential_margin_monthly_kzt);$('#metricOpportunitySub').textContent=Number(o.potential_position_count||0)>0?`${number(o.potential_position_count)} поз. · ${number(o.potential_units_total)} ед./мес.`:t('no_confirmed_opportunities','нет подтверждённых возможностей');
      $('#metricOpportunity').closest('article').title='Оценка возможного роста выручки: для товаров Kaspi с точными предложениями рассчитывается разница до нижнего квартиля цен продавцов и умножается на заданный месячный объём. Это не бухгалтерская маржа и не прогноз прибыли.';
      renderStatusChart(o.status_distribution||[]); renderHealth(o.health||{},o); if($('#activityList')) renderActivity(o.recent_events||[]); renderRunningBadge(data.tasks||[]);
      if(o.preferences?.locale && !localStorage.getItem('itp_lang')) applyI18n(o.preferences.locale);
    }catch(e){toast(e.message,true)}finally{state.overviewLoading=false}
  }
  function renderStatusChart(rows){ const total=rows.reduce((a,b)=>a+Number(b.count||0),0)||1; $('#statusChart').innerHTML=rows.length?rows.slice(0,8).map(r=>{const count=Number(r.count||0),pct=Math.min(100,count*100/total);return `<div class="status-row"><div class="status-row-head"><label>${esc(statusLabel(r.status))}</label><b>${number(count)}</b></div><div class="status-bar"><i style="width:${Math.max(2,pct)}%"></i></div><small>${esc(t('status_scale_summary','Из {total} товаров · {pct}% по шкале из 100%').replace('{total}',number(total)).replace('{pct}',pct.toFixed(1)))}</small></div>`}).join(''):`<div class="empty">${esc(t('empty_data','Нет данных'))}</div>`; }
  function renderHealth(h,o){ const coverage=Number(o.data_coverage_pct ?? o.scan_coverage_pct ?? 0);const items=[['catalog','Каталог',platformCounts(o),'catalog'],['prices','Цены',`${Number(o.price_coverage_pct||0).toFixed(1)}% покрытия`,'currency'],['market','Обработка данных',`${coverage.toFixed(1)}% · ${platformReady(o)}`,'chart']];$('#healthList').innerHTML=items.map(([k,n,d,ic])=>`<div class="health-item ${esc(h[k]||'empty')}"><span>${icon(ic)}</span><div><b>${n}</b><small>${d}</small></div><i></i></div>`).join(''); }
  function renderActivity(rows){ $('#activityList').innerHTML=rows.length?rows.map(r=>`<div class="activity"><span>${icon('operations')}</span><div><b>${esc(eventLabel(r.event_type))}</b><small>${esc(r.display_name||'Система')}</small></div><time>${dateText(r.created_at)}</time></div>`).join(''):'<div class="empty">Нет событий</div>'; }
  const eventLabel = v => ({task_started:'Операция запущена',task_stopped:'Операция остановлена',task_deleted:'Операция удалена',settings_updated:'Настройки обновлены',product_state_updated:'Карточки обновлены'}[v]||v||'Событие');

  async function loadOptions(){
    try{
      const d=await api('/api/products/options',{timeoutMs:60000});
      const fill=(selector,placeholder,items,labelFn=v=>v,valueFn=v=>v)=>{
        const node=$(selector);if(!node)return;
        const current=new Set(node.multiple?multiValues(node):[node.value].filter(Boolean));
        node.dataset.placeholder=placeholder;
        node.innerHTML=(node.multiple?'':`<option value="">${esc(placeholder)}</option>`)+(items||[]).map(v=>`<option value="${esc(valueFn(v))}">${esc(labelFn(v))}</option>`).join('');
        [...node.options].forEach(option=>{if(current.has(option.value))option.selected=true;});
        refreshMultiSelect(node);
      };
      fill('#brandFilter',t('all_brands','Все бренды'),d.brands);
      fill('#statusFilter',t('all_statuses','Все статусы'),d.statuses,v=>statusLabel(v.value),v=>v.value);
      fill('#productTypeFilter','Все типы товаров',d.product_types,v=>v.label,v=>v.value);
      fill('#sizeFilter','Все размеры',d.sizes);
      fill('#seasonFilter','Любая сезонность',d.seasons,v=>v.label,v=>v.value);
      fill('#characteristicGroupFilter','Общие группы характеристик',d.characteristic_groups,v=>`${v.label} · ${v.platform_count} пл. · ${number(v.count)}`,v=>v.value);
      fill('#reportBrand','Все бренды',d.brands);
      fill('#reportProductType','Все типы товаров',d.product_types,v=>v.label,v=>v.value);
      fill('#reportSize','Все размеры',d.sizes);
      fill('#reportSeason','Любая сезонность',d.seasons,v=>v.label,v=>v.value);
      fill('#reportCharacteristicGroup','Все общие группы',d.characteristic_groups,v=>`${v.label} · ${v.platform_count} пл. · ${number(v.count)}`,v=>v.value);
      refreshAllMultiSelects();
    }catch(e){toast(e.message,true)}
  }
  async function loadCatalogConfiguration({settings=false}={}){
    if(!can('view_products'))return;
    const platforms=settings?[]:multiValues('#platformFilter');
    const suffix=platforms.length?`?platforms=${encodeURIComponent(platforms.join(','))}`:'';
    try{
      const d=await api(`/api/catalog/filters${suffix}`);
      state.catalogConfig=d;
      const byKey=new Map((d.attributes||[]).map(item=>[item.attribute_key,item]));
      const enabled=(d.filters||[]).filter(item=>item.is_enabled&&!['title','marketplace'].includes(item.attribute_key)&&byKey.has(item.attribute_key));
      const host=$('#dynamicAttributeFilters');
      if(host){
        host.innerHTML=enabled.map(item=>{
          const definition=byKey.get(item.attribute_key)||{};
          return `<select class="dynamic-filter-select" multiple data-multi-select data-attribute-key="${esc(item.attribute_key)}" data-placeholder="${esc(item.display_name)}">${(definition.values||[]).map(value=>`<option value="${esc(value)}">${esc(value)}</option>`).join('')}</select>`;
        }).join('');
        initMultiSelects();
        $$('[data-attribute-key]',host).forEach(node=>node.onchange=()=>{state.products.page=1;updateFilterResetVisibility();loadProducts()});
      }
      if(settings&&$('#catalogFilterSettings')){
        $('#catalogFilterSettings').innerHTML=(d.filters||[]).filter(item=>!['title','marketplace'].includes(item.attribute_key)).map(item=>{
          const definition=byKey.get(item.attribute_key)||{};
          const sourceCodes=[...new Set((definition.sources||[]).map(source=>String(source.marketplace_code||'')).filter(Boolean))];
          const sourceNames=(definition.sources||[]).map(source=>String(source.source_attribute||'')).join(' ');
          const searchText=[item.display_name,sourceNames,...(definition.values||[]).slice(0,20)].join(' ').toLocaleLowerCase('ru');
          return `<label class="catalog-filter-option" data-filter-platforms="${esc(sourceCodes.join(','))}" data-filter-common="${sourceCodes.length>1?'1':'0'}" data-filter-search="${esc(searchText)}"><input type="checkbox" data-catalog-filter-key="${esc(item.attribute_key)}" ${item.is_enabled?'checked':''}><span><b>${esc(item.display_name)}</b><small>${number(definition.product_count||0)} товаров · ${sourceCodes.map(marketplaceLabel).map(esc).join(', ')||'—'}</small></span></label>`;
        }).join('')||'<div class="empty">Характеристики появятся после первого импорта каталога.</div>';
        $$('[data-catalog-filter-key]').forEach(node=>node.onchange=queueCatalogFilterSave);
        applyCatalogSettingsSearch();
      }
    }catch(e){toast(e.message,true)}
  }
  let catalogFilterSaveTimer=0;
  function applyCatalogSettingsSearch(){
    const query=String($('#catalogAttributeSearch')?.value||'').trim().toLocaleLowerCase('ru');
    const platform=String($('#catalogAttributeMarketplace')?.value||'all');
    $$('.catalog-filter-option').forEach(node=>{
      const sources=String(node.dataset.filterPlatforms||'').split(',').filter(Boolean);
      const platformMatches=platform==='all'||(platform==='common'&&node.dataset.filterCommon==='1')||sources.includes(platform);
      node.hidden=!(platformMatches&&(!query||String(node.dataset.filterSearch||'').includes(query)));
    });
  }
  function queueCatalogFilterSave(){
    const status=$('#catalogFilterSaveStatus');if(status)status.textContent='Сохраняем…';
    clearTimeout(catalogFilterSaveTimer);catalogFilterSaveTimer=setTimeout(()=>saveCatalogFilters({silent:true}),350);
  }
  async function saveCatalogFilters({silent=false}={}){
    const filters=(state.catalogConfig?.filters||[]).map(item=>({attribute_key:item.attribute_key,is_enabled:['title','marketplace'].includes(item.attribute_key)||Boolean($(`[data-catalog-filter-key="${CSS.escape(item.attribute_key)}"]`)?.checked)}));
    try{
      const saved=await api('/api/catalog/filters',{method:'PUT',body:{filters}});
      state.catalogConfig=saved;
      const status=$('#catalogFilterSaveStatus');if(status)status.textContent='Сохранено автоматически';
      await loadCatalogConfiguration({settings:false});
      if(!silent)toast('Фильтры каталога сохранены');
    }catch(e){const status=$('#catalogFilterSaveStatus');if(status)status.textContent='Ошибка сохранения';toast(e.message,true)}
  }
  function attributeFilterValues(){const result={};$$('[data-attribute-key]').forEach(node=>{const values=multiValues(node);if(values.length)result[node.dataset.attributeKey]=values;});return result;}
  function productFiltersPayload(){return {query:$('#productSearch')?.value||'',platforms:multiValues('#platformFilter'),brand:multiValues('#brandFilter'),status:multiValues('#statusFilter'),freshness:multiValues('#freshnessFilter'),product_type:multiValues('#productTypeFilter'),size:multiValues('#sizeFilter'),season:multiValues('#seasonFilter'),characteristic_group:multiValues('#characteristicGroupFilter'),attributes:attributeFilterValues(),scope:state.products.scope,sort:$('#sortProducts')?.value||'updated',direction:'desc'};}
  function productQuery(){const q=filtersQuery(productFiltersPayload());q.set('page',String(state.products.page));q.set('page_size',$('#pageSize').value);return q;}
  function hasProductFilters(){const f=productFiltersPayload();return Boolean(f.query.trim()||f.platforms.length||f.brand.length||f.status.length||f.freshness.length||f.product_type.length||f.size.length||f.season.length||f.characteristic_group.length||Object.keys(f.attributes).length||f.scope!=='all');}
  function updateFilterResetVisibility(){const button=$('#resetFilters'),filters=$('#productFilters');const active=hasProductFilters();if(button)button.hidden=!active;if(filters)filters.classList.toggle('has-reset',active);}

  function productSkeletonRows(){
    return Array.from({length:6},()=>`<tr class="product-skeleton-row" aria-hidden="true"><td><i></i></td><td><i class="wide"></i><i class="medium"></i></td><td><i class="medium"></i></td><td><i class="short"></i></td><td><i class="wide"></i></td><td><i class="medium"></i></td><td><i class="short"></i></td><td><i class="medium"></i></td><td><i class="short"></i></td></tr>`).join('');
  }
  function setProductsLoading(active,{initial=false}={}){
    const card=$('#page-products .table-card'),info=$('#productsLoadInfo'),refresh=$('#refreshProducts');
    card?.classList.toggle('is-loading',active);card?.setAttribute('aria-busy',String(active));info?.classList.toggle('is-loading',active);if(refresh)refresh.disabled=active;
    if(active&&initial)$('#productsBody').innerHTML=productSkeletonRows();
  }
  function renderProductsError(message){
    $('#productsBody').innerHTML=`<tr><td colspan="9"><div class="catalog-error"><b>${esc(t('load_error','Не удалось загрузить каталог'))}</b><span>${esc(message)}</span><button class="secondary" id="retryProducts" type="button">${esc(t('refresh','Повторить'))}</button></div></td></tr>`;
    $('#retryProducts').onclick=()=>loadProducts();
  }

  async function loadProducts(){
    const requestSerial=++productsRequestSerial;
    productsRequestController?.abort();
    const controller=new AbortController();productsRequestController=controller;
    const startedAt=Date.now();state.products.requestStartedAt=startedAt;
    updateFilterResetVisibility();
    $('#productsLoadInfo').textContent=t('loading_catalog','Загружаем каталог…');
    setProductsLoading(true,{initial:!state.products.items.length});
    try{
      const d=await api(`/api/products?${productQuery()}`,{signal:controller.signal,timeoutMs:45000});
      if(requestSerial!==productsRequestSerial)return;
      const r=d.result,lastDurationMs=Date.now()-startedAt;
      state.products={...state.products,page:r.page,pages:r.pages,pageSize:r.page_size,total:r.total,items:r.items,lastDurationMs};
      $('#productsFound').textContent=number(r.total);$('#pageInfo').textContent=`${r.page} / ${r.pages}`;$('#productsLoadInfo').textContent=t('products_updated_in','Обновлено за {seconds} сек').replace('{seconds}',(lastDurationMs/1000).toFixed(2));renderProducts(r.items);
    }catch(e){
      if(e?.name==='AbortError'||requestSerial!==productsRequestSerial)return;
      $('#productsLoadInfo').textContent=t('load_error','Ошибка загрузки');
      if(state.products.items.length){toast(e.message,true)}else renderProductsError(e.message);
    }finally{
      if(requestSerial===productsRequestSerial){setProductsLoading(false);productsRequestController=null}
    }
  }
  async function loadInventorySummary({force=false}={}){
    const host=$('#inventorySummary');if(!host)return;
    if(state.inventoryLoading||(!force&&state.inventoryLoaded))return;
    state.inventoryLoading=true;host.classList.add('is-loading');host.setAttribute('aria-busy','true');
    try{
      const data=await api('/api/inventory/summary'),summary=data.summary||{};
      $('#inventoryItems').textContent=number(summary.inventory_products);
      $('#inventoryQuantity').textContent=number(summary.quantity_on_hand);
      $('#inventoryValue').textContent=money(summary.stock_value_kzt);
      $('#inventoryValueNote').textContent=summary.stock_value_complete?'по закупочной цене':`частичная сумма · без цены: ${number(summary.unpriced_inventory_products)}`;
      $('#inventoryUnmatched').textContent=number(summary.unmatched_listings);
      host.classList.remove('has-error');state.inventoryLoaded=true;
    }catch(error){host.classList.add('has-error');}
    finally{state.inventoryLoading=false;host.classList.remove('is-loading');host.setAttribute('aria-busy','false')}
  }
  function renderProducts(items){
    $('#productsBody').innerHTML=items.length?items.map(p=>{
      const isOzon = p.platform==='ozon';
      // Only Ozon.ru uses RUB. Kaspi and Halyk Market are always displayed in KZT.
      const pricePrimary = isOzon && p.price_original ? `${number(p.price_original)} ₽` : money(p.own_price_kzt??p.price_kzt);
      const priceSecondary = isOzon && p.price_original && p.price_kzt ? `<small class="cell-meta">${money(p.price_kzt)}</small>` : '';
      const sellerMeta=p.seller_name?`<small class="cell-meta">${esc(p.seller_name)}</small>`:'';
      // Market range: show in RUB for Ozon.ru, in KZT for others
      const range = !isOzon && p.market_median_price_kzt ?
        `<div class="range price-range"><span class="range-min"><small>${esc(t('min_short','мин'))}</small><b>${money(p.market_min_price_kzt)}</b></span><span class="range-mid"><small>${esc(t('avg_short','ср'))}</small><b>${money(p.market_median_price_kzt)}</b></span><span class="range-max"><small>${esc(t('max_short','макс'))}</small><b>${money(p.market_max_price_kzt)}</b></span></div>` :
        (p.platform==='ozon' && p.market_median_price_original ? `<div class="range price-range"><span class="range-min"><small>${esc(t('min_short','мин'))}</small><b>${number(p.market_min_price_original)} ₽</b></span><span class="range-mid"><small>${esc(t('avg_short','ср'))}</small><b>${number(p.market_median_price_original)} ₽</b></span><span class="range-max"><small>${esc(t('max_short','макс'))}</small><b>${number(p.market_max_price_original)} ₽</b></span></div>` : '<span class="muted">—</span>');
      const pot=Number(p.potential_margin_monthly_kzt||0)>0?`<div class="potential-stack"><b class="positive">${money(p.potential_margin_monthly_kzt)}</b><small>${money(p.potential_margin_per_unit_kzt)}<br>${esc(t('per_unit_short','ед.'))}</small></div>`:'<span class="muted">—</span>';
      const selected=state.selected.has(p.product_code);
      return `<tr class="${selected?'is-selected':''}"><td><input class="row-check" type="checkbox" data-code="${esc(p.product_code)}" ${selected?'checked':''} aria-label="${esc(t('selected','выбрано'))}"></td><td><div class="product-cell"><img src="${esc(p.image_url||'')}" onerror="this.remove()"><div><b>${esc(p.title)}</b><small class="product-characteristics">${esc([p.product_type_label,p.size,p.load_index&&p.speed_index?`${p.load_index}${p.speed_index}`:"",p.season_label].filter(Boolean).join(" · "))}</small></div></div></td><td><div class="cell-stack"><span class="badge ${p.platform}">${esc(p.platform_label)}</span>${sellerMeta}</div></td><td><div class="cell-stack price-stack"><span class="money">${pricePrimary}</span>${priceSecondary}</div></td><td>${range}</td><td><div class="position-stack"><span class="${statusClass(p.status_tone)}">${esc(statusLabel(p.price_status))}</span>${p.price_rank?`<small class="rank-meta">${p.price_rank} / ${p.price_rank_total}</small>`:''}</div></td><td>${pot}</td><td><div class="updated-cell"><span>${dateText(p.updated_at)}</span></div></td><td><div class="row-actions"><button class="open-row" data-open-product="${esc(p.product_code)}">${icon('chevron-right')}</button></div></td></tr>`;
    }).join(''):'<tr><td colspan="9"><div class="empty">Позиции не найдены</div></td></tr>';
    bindProductRows(); updateSelectionBar(); updateFilterResetVisibility();
  }
  function bindProductRows(){ $$('.row-check').forEach(x=>x.onchange=()=>{x.checked?state.selected.add(x.dataset.code):state.selected.delete(x.dataset.code);x.closest('tr')?.classList.toggle('is-selected',x.checked);updateSelectionBar();}); $$('[data-open-product]').forEach(x=>x.onclick=()=>openProduct(x.dataset.openProduct)); }
  function updateSelectionBar(){ const n=state.selected.size;$('#selectionBar').hidden=!n;$('#selectionCount').textContent=n; if($('#operationScope')) $('#operationScope').value = n ? $('#operationScope').value : 'all'; }

  // Export every product matching the active filters. The API intentionally
  // caps a single response at 200 rows, so collect all pages before building XLS.
  async function exportVisibleProducts(){
    const button=$('#selectedReport');
    const originalHtml=button?.innerHTML||'';
    try{
      if(button){button.disabled=true;button.classList.add('loading');}
      const baseQ=productQuery();
      baseQ.set('page_size','200');
      const byCode=new Map();
      let page=1;
      let pages=1;
      do{
        const q=new URLSearchParams(baseQ.toString());
        q.set('page',String(page));
        const d=await api(`/api/products?${q.toString()}`);
        const result=d.result||{};
        (result.items||[]).forEach(item=>byCode.set(item.product_code,item));
        pages=Math.max(1,Number(result.pages||1));
        if(button){
          const span=button.querySelector('span');
          if(span)span.textContent=`Экспорт ${Math.min(page,pages)} / ${pages}`;
        }
        page+=1;
      }while(page<=pages);

      const items=[...byCode.values()];
      if(!items.length)return toast(t('no_data_export','Нет данных для экспорта'),true);
      const escHtml=v=>String(v??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
      const cell=v=>v===null||v===undefined?'':escHtml(v);
      const link=url=>{const href=externalHref(url);return href?`<a href="${escHtml(href)}">${escHtml(href)}</a>`:'';};
      const yesNo=v=>v?'Да':'Нет';
      const headers=[
        'Код товара','Товар','Бренд','Модель','Размер','Площадка','Продавец',
        'Текущая цена, ₸','Исходная цена','Валюта','Рынок мин., ₸','Рынок медиана, ₸','Рынок макс., ₸',
        'Отклонение, ₸','Отклонение, %','Статус','Позиция цены','Количество предложений',
        'Риск','Требует проверки','Потенциал','Потенциал на единицу, ₸','Ожидаемый объём, ед./мес.',
        'Потенциал за месяц, ₸','Актуальность','Наблюдение','Приоритет','Примечание','Обновлено',
        'Ссылка на товар','Ссылка на минимум','Ссылка на максимум'
      ];
      const rows=items.map(p=>{
        const risk=['EXACT_ABOVE','EXACT_HIGHEST','DATA_ERROR'].includes(String(p.price_status||''));
        const review=['NOT_ANALYZED','INSUFFICIENT_DATA','REVIEW_REQUIRED'].includes(String(p.price_status||''));
        const opportunity=['EXACT_LOWEST','EXACT_BELOW'].includes(String(p.price_status||''));
        const rank=p.price_rank&&p.price_rank_total?`${p.price_rank} / ${p.price_rank_total}`:'';
        const values=[
          p.product_code,p.title,p.brand,p.model,p.size,p.platform_label||p.platform,p.seller_name,
          p.price_kzt??p.own_price_kzt,p.price_original,p.currency_original,p.market_min_price_kzt,
          p.market_median_price_kzt,p.market_max_price_kzt,p.difference_kzt,p.difference_pct,
          p.status_label||statusLabel(p.price_status),rank,p.reference_count,yesNo(risk),yesNo(review),
          yesNo(opportunity),p.potential_margin_per_unit_kzt,p.expected_monthly_units,
          p.potential_margin_monthly_kzt,p.freshness_label||p.freshness_status,yesNo(p.watched),
          p.priority,p.note,p.updated_at
        ];
        return `<tr>${values.map(v=>`<td>${cell(v)}</td>`).join('')}<td>${link(p.product_url)}</td><td>${link(p.lowest_product_url)}</td><td>${link(p.highest_product_url)}</td></tr>`;
      });
      const filterDescription=[
        $('#productSearch').value&&`Поиск: ${$('#productSearch').value}`,
        multiLabels('#platformFilter').length&&`Площадки: ${multiLabels('#platformFilter').join(', ')}`,
        multiLabels('#brandFilter').length&&`Бренды: ${multiLabels('#brandFilter').join(', ')}`,
        multiLabels('#statusFilter').length&&`Статусы: ${multiLabels('#statusFilter').join(', ')}`,
        multiLabels('#freshnessFilter').length&&`Актуальность: ${multiLabels('#freshnessFilter').join(', ')}`,
        multiLabels('#sizeFilter').length&&`Размеры: ${multiLabels('#sizeFilter').join(', ')}`,
        state.products.scope!=='all'&&`Раздел: ${state.products.scope}`
      ].filter(Boolean).join(' · ')||'Все товары';
      const html=`<!doctype html><html><head><meta charset="utf-8"/><style>table{border-collapse:collapse}th,td{border:1px solid #bbb;padding:5px;vertical-align:top}th{font-weight:700;background:#eef5f8}.meta{margin:0 0 10px;font-family:Arial,sans-serif}</style></head><body><div class="meta"><b>Фильтры:</b> ${escHtml(filterDescription)}<br><b>Позиций:</b> ${items.length}</div><table><thead><tr>${headers.map(h=>`<th>${escHtml(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></body></html>`;
      const blob=new Blob(['\ufeff',html],{type:'application/vnd.ms-excel;charset=utf-8;'});
      const url=URL.createObjectURL(blob);
      const a=document.createElement('a');
      a.href=url;
      a.download=`products_filtered_${new Date().toISOString().slice(0,10)}_${items.length}.xls`;
      document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);
      toast(`Отчёт готов: ${number(items.length)} позиций`);
    }catch(e){console.error(e);toast(e.message,true)}
    finally{
      if(button){button.disabled=false;button.classList.remove('loading');button.innerHTML=originalHtml;}
    }
  }

async function stopProduct(code){ try{ const d=await api('/api/tasks/stop_by_product',{method:'POST',body:{product_code:code}}); if(d && d.stopped && d.stopped.length) toast(t('stopped_ok','Остановлено')) ; else if(d && d.stopped && d.stopped.length===0) toast(t('stopped_none','Связанные выполняемые операции не найдены'), true); if(typeof loadTasks==='function') loadTasks(); }catch(e){toast(e.message,true)} }
  function drawerLoadingMarkup(){return '<div class="drawer-loading" aria-label="Загрузка товара"><div class="drawer-skeleton-product"><i></i><span><b></b><b></b><b></b></span></div><div class="drawer-skeleton-grid"><i></i><i></i><i></i><i></i></div><div class="drawer-skeleton-section"></div><div class="drawer-skeleton-section"></div></div>'}
  async function openProduct(code){
    productDrawerController?.abort();const controller=new AbortController();productDrawerController=controller;
    state.currentProductCode=code;$('#backdrop').hidden=false;$('#productDrawer').classList.add('open');$('#productDrawer').setAttribute('aria-hidden','false');$('#drawerBody').innerHTML=drawerLoadingMarkup();
    try{const d=await api(`/api/products/${encodeURIComponent(code)}`,{signal:controller.signal,timeoutMs:45000});if(state.currentProductCode===code&&!controller.signal.aborted)renderDrawer(d.product)}catch(e){if(e?.name==='AbortError')return;$('#drawerBody').innerHTML=`<div class="catalog-error drawer-error"><b>${esc(t('load_error','Не удалось загрузить товар'))}</b><span>${esc(e.message)}</span><button class="secondary" id="retryProduct" type="button">${esc(t('refresh','Повторить'))}</button></div>`;$('#retryProduct').onclick=()=>openProduct(code)}finally{if(productDrawerController===controller)productDrawerController=null}
  }
  function closeDrawer(){ productDrawerController?.abort();productDrawerController=null;$('#productDrawer').classList.remove('open');$('#backdrop').hidden=true;$('#productDrawer').setAttribute('aria-hidden','true'); }
  function renderDrawer(p){
    $('#drawerTitle').textContent=p.title||t('product','Товар');
    // Only Ozon.ru uses RUB. Kaspi and Halyk Market are always displayed in KZT.
    const isOzon = p.platform==='ozon';
    const currentPrimary = isOzon && p.price_original ? `${number(p.price_original)} ₽` : money(p.own_price_kzt??p.price_kzt);
    const currentSecondary = isOzon && p.price_original && p.price_kzt ? `<span>${money(p.price_kzt)}</span>` : '';
    const productLink=externalHref(p.product_url)?`<a class="drawer-product-link" href="${esc(externalHref(p.product_url))}" target="_blank" rel="noopener noreferrer">${esc(t('open_catalog_button','Открыть карточку'))}</a>`:'';
    const lowLink=externalHref(p.lowest_product_url)?`<a href="${esc(externalHref(p.lowest_product_url))}" target="_blank" rel="noopener noreferrer">${esc(p.lowest_product_title||t('open_catalog_button','Открыть карточку'))}</a>`:''; const highLink=externalHref(p.highest_product_url)?`<a href="${esc(externalHref(p.highest_product_url))}" target="_blank" rel="noopener noreferrer">${esc(p.highest_product_title||t('open_catalog_button','Открыть карточку'))}</a>`:'';
    const specs=(p.specifications||[]).map(s=>`<div class="spec"><small>${esc(s.name)}</small><b>${esc(s.value)}</b></div>`).join('')||`<div class="empty">${esc(t('specs_empty','Характеристики не получены'))}</div>`;
    const offers=(p.offers||[]).map(o=>{const relation=p.platform==='ozon'?(o.match_method_label||t('exact_product','Точный товар')):(o.is_own?t('own_offer','Свой продавец'):t('same_card','Та же карточка'));const linkText=p.platform==='ozon'?t('open_ozon_card','Открыть карточку Ozon.ru ↗'):p.platform==='halyk_market'?t('open_halyk_card','Открыть карточку Halyk Market ↗'):p.platform==='forte_market'?t('open_forte_card','Открыть карточку Forte Market ↗'):t('open_kaspi_card','Открыть карточку Kaspi ↗');const offerUrl=externalHref(o.product_url);return `<div class="candidate"><div><b>${esc(o.merchant_name||t('seller','Продавец'))}</b><small>${money(o.price_kzt)}${o.merchant_rating?` · ${esc(t('rating','рейтинг'))} ${Number(o.merchant_rating).toFixed(1)}`:''}</small>${offerUrl?`<a href="${esc(offerUrl)}" target="_blank" rel="noopener noreferrer">${esc(linkText)}</a>`:''}</div><span class="relation">${esc(relation)}</span></div>`}).join('')||`<div class="empty">${esc(t('offers_empty','Точные предложения конкурентов ещё не найдены'))}</div>`;
    const method=p.match_method_label?`<p><small>${esc(t('match_method','Метод сопоставления'))}</small><b>${esc(p.match_method_label)}</b></p>`:'';
    const tiedDetail=Number(p.price_rank_tie_count||0)>1?`<small class="price-tie-note">Такая же цена ещё у ${number(Number(p.price_rank_tie_count)-1)} продавц.</small>`:'';
    const context=p.inventory_context||{};
    const inventory=inventorySectionMarkup(context,p);
    const matching=matchingSectionMarkup(context);
    $('#drawerBody').innerHTML=`<div class="drawer-product"><img src="${esc(p.image_url||'')}" onerror="this.remove()"><div><h3>${esc(p.title)}</h3><p><span class="badge ${esc(p.platform)}">${esc(p.platform_label)}</span> ${esc([productCodeText(p.source_product_code),p.brand,p.size].filter(Boolean).join(' · '))}</p><p><span class="${statusClass(p.status_tone)}">${esc(statusLabel(p.price_status))}</span></p>${method}${productLink}</div></div>
      <div class="position-grid"><div class="position-card"><small>${esc(t('current_price_label','Текущая цена'))}</small><b>${currentPrimary}</b>${currentSecondary}${tiedDetail}</div><div class="position-card"><small>${esc(p.reference_type==='KASPI_SAME_CARD'?'Минимум других продавцов':t('seller_min','Минимум рынка'))}</small><b>${p.platform==='ozon' ? (p.market_min_price_original ? `${number(p.market_min_price_original)} ₽` : '<span class="muted">—</span>') : money(p.market_min_price_kzt)}</b>${lowLink}</div><div class="position-card"><small>${esc(p.reference_type==='KASPI_SAME_CARD'?'Медиана других продавцов':'Медиана рынка')}</small><b>${p.platform==='ozon' ? (p.market_median_price_original ? `${number(p.market_median_price_original)} ₽` : '<span class="muted">—</span>') : money(p.market_median_price_kzt)}</b><span>${esc(t('offers_count','Предложений'))}: ${number(p.reference_count||0)}</span></div><div class="position-card"><small>${esc(p.reference_type==='KASPI_SAME_CARD'?'Максимум других продавцов':t('seller_max','Максимум рынка'))}</small><b>${p.platform==='ozon' ? (p.market_max_price_original ? `${number(p.market_max_price_original)} ₽` : '<span class="muted">—</span>') : money(p.market_max_price_kzt)}</b>${highLink}</div></div>
      ${p.platform==='kaspi'?`<div class="opportunity-card"><div><small>${esc(t('opportunity_monthly','Потенциал за месяц'))}</small><b>${money(p.potential_margin_monthly_kzt)}</b></div><div><small>${esc(t('per_unit','На единицу'))}</small><b>${money(p.potential_margin_per_unit_kzt)}</b></div></div>`:''}
      ${inventory}${matching}
      <section class="drawer-section"><h4>${esc(t('price_history','История цены'))}</h4><div class="history-chart">${historySvg(p.history||[])}</div></section>
      <section class="drawer-section"><h4>${esc(t('specifications','Характеристики'))}</h4><div class="spec-grid">${specs}</div></section>
      <section class="drawer-section"><h4>${esc(p.platform==='ozon'?t('ozon_market_offers','Рыночные предложения Ozon.ru'):t('same_card_sellers','Продавцы этой же карточки'))}</h4><div class="candidate-list">${offers}</div></section>`;
    $('#inventoryForm')?.addEventListener('submit',saveInventoryFromDrawer);
    $$('[data-match-decision]',$('#drawerBody')).forEach(button=>button.onclick=()=>decideProductMatch(button));
  }

  function inventorySectionMarkup(context,p){
    if(!context.can_view_inventory)return '';
    const item=context.inventory||{},editable=Boolean(context.can_manage_inventory),locked=editable?'':' readonly';
    const pricing=item.id?`<div class="inventory-pricing"><article><small>Вложено в остаток</small><b>${money(item.stock_value_kzt)}</b></article><article><small>Рекомендованная цена</small><b>${money(item.recommended_min_price_kzt)}</b></article><article><small>Валовая прибыль / ед.</small><b>${money(item.gross_profit_per_unit_kzt)}</b></article><article><small>Валовая прибыль остатка</small><b>${money(item.gross_profit_on_hand_kzt)}</b></article></div><p class="calculation-note">Расчёт до комиссий маркетплейса, логистики, налогов и возвратов. Рекомендация = закупочная цена + целевая наценка.</p>`:'';
    const links=(item.linked_listings||[]).map(link=>`<span class="inventory-link-chip">${esc(link.platform_label||link.marketplace_code)} · ${esc(productCodeText(link.source_product_code))}</span>`).join('');
    return `<section class="drawer-section inventory-section"><div class="drawer-section-title"><div><small>ЕДИНЫЙ СКЛАДСКОЙ ТОВАР</small><h4>Остаток и закупочная цена</h4></div>${item.id?`<span class="relation">ID ${number(item.id)}</span>`:''}</div><form id="inventoryForm" class="inventory-form"><label><span>Внутренний SKU</span><input name="internal_sku" maxlength="120" value="${esc(item.internal_sku||'')}"${locked}></label><label><span>Количество, ед.</span><input name="quantity_on_hand" type="number" min="0" max="1000000000" step="1" value="${esc(item.quantity_on_hand??0)}"${locked}></label><label><span>Закупочная цена, ₸</span><input name="purchase_price_kzt" type="number" min="0" max="1000000000000" step="0.01" value="${esc(item.purchase_price_kzt??'')}"${locked}></label><label><span>Целевая наценка, %</span><input name="target_markup_percent" type="number" min="0" max="1000" step="0.01" value="${esc(item.target_markup_percent??20)}"${locked}></label><label class="wide"><span>Примечание</span><textarea name="notes" maxlength="2000" rows="2"${locked}>${esc(item.notes||'')}</textarea></label>${editable?'<button class="primary wide" type="submit">Сохранить складские данные</button>':'<p class="calculation-note wide">Доступ только для просмотра. Право изменения выдаёт администратор компании.</p>'}</form>${links?`<div class="inventory-links">${links}</div>`:''}${pricing}</section>`;
  }

  function matchingSectionMarkup(context){
    const matching=context.matching||{},items=matching.suggestions||[];
    const rows=items.map(item=>{const status=item.status||'suggested',actions=context.can_manage_matching&&status==='suggested'?`<div class="match-actions"><button class="secondary" type="button" data-match-decision="rejected" data-candidate-code="${esc(item.listing_code)}">Не совпадает</button><button class="primary" type="button" data-match-decision="confirmed" data-candidate-code="${esc(item.listing_code)}">Объединить</button></div>`:`<span class="match-state ${esc(status)}">${status==='confirmed'?'Уже объединено':status==='conflict'?'Связано с другим товаром':'Ожидает решения'}</span>`;return `<article class="match-candidate"><div><span class="badge ${esc(item.platform)}">${esc(item.platform_label||item.platform)}</span><b>${esc(item.title)}</b><small>${esc(item.match_reason||'')} · уверенность ${number(item.match_score||0)}%</small><small>${item.price_kzt===null||item.price_kzt===undefined?'Цена не получена':money(item.price_kzt)}</small></div>${actions}</article>`;}).join('');
    return `<section class="drawer-section matching-section"><div class="drawer-section-title"><div><small>ДИНАМИЧЕСКИЙ МАТЧИНГ КАТАЛОГОВ</small><h4>Тот же товар на других площадках</h4></div><span class="relation">без автообъединения</span></div><p class="calculation-note">Сначала строгие артикулы и характеристики, затем похожая модель. Количество и закупочная сумма станут общими только после подтверждения.</p><div class="match-candidates">${rows||'<div class="empty compact-empty">Подходящих карточек на других площадках пока нет.</div>'}</div></section>`;
  }

  async function saveInventoryFromDrawer(event){
    event.preventDefault();const form=event.currentTarget,button=form.querySelector('button[type="submit"]');
    const values=Object.fromEntries(new FormData(form).entries());
    try{if(button)button.disabled=true;await api(`/api/products/${encodeURIComponent(state.currentProductCode)}/inventory`,{method:'PUT',body:values});toast('Остаток и закупочная цена сохранены');await Promise.all([openProduct(state.currentProductCode),loadInventorySummary({force:true})]);}catch(error){toast(error.message,true)}finally{if(button)button.disabled=false}
  }

  async function decideProductMatch(button){
    const decision=button.dataset.matchDecision,candidateCode=button.dataset.candidateCode,code=state.currentProductCode;
    try{button.disabled=true;await api(`/api/products/${encodeURIComponent(code)}/match`,{method:'POST',body:{candidate_code:candidateCode,decision}});toast(decision==='confirmed'?'Карточки объединены в один складской товар':'Предложение отклонено');await Promise.all([openProduct(code),loadInventorySummary({force:true})]);}catch(error){toast(error.message,true)}finally{button.disabled=false}
  }

  const relationLabel = v => ({KASPI_SAME_CARD:'Та же карточка Kaspi',EXACT_MODEL:'Точный товар',REVIEW:'Требует проверки',accepted:'Подтверждено',review:'Проверка',rejected:'Отклонено'}[v]||v||'Точный кандидат');
  function historySvg(points){ const vals=points.map(x=>Number(x.price_kzt??x.price)).filter(x=>x>0);if(vals.length<2)return '<span class="muted">Недостаточно данных для графика</span>';const min=Math.min(...vals),max=Math.max(...vals),w=700,h=170,pad=18;const pts=vals.map((v,i)=>`${pad+i*(w-pad*2)/(vals.length-1)},${h-pad-(v-min)/(max-min||1)*(h-pad*2)}`).join(' ');return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#06a9e7" stop-opacity=".32"/><stop offset="1" stop-color="#06a9e7" stop-opacity="0"/></linearGradient></defs><polyline points="${pts} ${w-pad},${h-pad} ${pad},${h-pad}" fill="url(#g)" stroke="none"/><polyline points="${pts}" fill="none" stroke="#06a9e7" stroke-width="3" vector-effect="non-scaling-stroke"/></svg>`; }

  function updateOperationScope(ids){
    const actionNode=$('#'+ids.action),scopeNode=$('#'+ids.scope);
    if(!actionNode||!scopeNode)return;
    const filterable=FILTERABLE_ACTIONS.has(actionNode.value);
    scopeNode.disabled=!filterable;
    if(!filterable)scopeNode.value='all';
  }
  function updateOperationLauncher(ids){
    const platformNode=$('#'+ids.platform), actionNode=$('#'+ids.action), scopeNode=$('#'+ids.scope);
    if(!platformNode||!actionNode||!scopeNode)return;
    const platform=platformNode.value;
    const sellerNode=$('#'+ids.seller),sellerField=$('#'+ids.seller+'Field');
    const sellers=(user.marketplace_sellers?.[platform]||[]);
    if(sellerNode){
      const previousSeller=sellerNode.value;
      sellerNode.innerHTML=sellers.map(item=>`<option value="${Number(item.id)}">${esc(item.display_name||item.external_seller_id||`#${item.id}`)}</option>`).join('');
      if([...sellerNode.options].some(option=>option.value===previousSeller))sellerNode.value=previousSeller;
      if(sellerField)sellerField.hidden=sellers.length<=1||platform==='system';
    }
    const previous=actionNode.value;
    actionNode.innerHTML=(ACTIONS[platform]||[]).filter(([id])=>(id!=='backup_database'||user.platform_role==='superadmin')&&(id!=='full_sync_all'||visibleMarketplaceCodes().length>1)).map(([id,label])=>`<option value="${id}">${esc(actionLabel(id,label))}</option>`).join('');
    if([...actionNode.options].some(option=>option.value===previous))actionNode.value=previous;
    actionNode.onchange=()=>updateOperationScope(ids);
    updateOperationScope(ids);
  }
  function updateOperationActions(){ operationLaunchers.forEach(updateOperationLauncher); }
  async function startTask(action,scope='all',codes=[],filters=null,options={}){
    try{
      const body={action,scope,codes};if(filters)body.filters=filters;
      const platform=actionInfo(action).platform;
      const sellers=user.marketplace_sellers?.[platform]||[];
      const inferred=[...new Set((codes||[]).map(code=>String(code).match(/^[^:]+:s(\d+):/)?.[1]).filter(Boolean))];
      const sellerId=Number(options.tenantSellerId||((inferred.length===1)&&inferred[0])||(sellers.length===1&&sellers[0].id)||0);
      if(sellers.length>1&&!sellerId)throw new Error('Выберите продавца для запуска операции.');
      if(sellerId)body.tenant_seller_id=sellerId;
      const d=await api('/api/tasks/start',{method:'POST',body});
      toast(`Запущено: ${d.task?.label || action}`);
      if(d&&d.task){const idx=state.tasks.findIndex(t=>t.id===d.task.id);if(idx>=0)state.tasks[idx]=d.task;else state.tasks.unshift(d.task);renderTasks();}else{loadTasks();}
      if(options.navigate!==false)navigate('operations');
    }catch(e){toast(e.message,true)}
  }
  async function loadTasks(){
    if(state.tasksLoading)return;
    state.tasksLoading=true;
    const hadRunningReport=state.tasks.some(task=>task.running&&task.name==='export_report');
    try{
      const d=await api('/api/tasks');state.tasks=d.tasks||[];renderTasks();renderRunningBadge(state.tasks);
      if(hadRunningReport&&!state.tasks.some(task=>task.running&&task.name==='export_report')&&state.page==='reports')loadReports();
    }
    catch(e){toast(e.message,true)}
    finally{state.tasksLoading=false;}
  }

  function renderRunningBadge(tasks){const n=tasks.filter(t=>t.running).length;$('#navRunning').hidden=!n;$('#navRunning').textContent=n;$('#opsRunning')&&($('#opsRunning').textContent=n);}
  function renderOperationActionGrid(tasks){
    const node=$('#operationActionGrid');
    if(!node)return;
    const groups=[
      ['kaspi','Kaspi'],
      ['ozon','Ozon.ru'],
      ['ozon_kz','Ozon.kz'],
      ['halyk_market','Halyk Market'],
      ['forte_market','Forte Market'],
      ['wildberries','Wildberries'],
      ['system',t('system','Система')]
    ];
    const actionCard = item => {
      const names=new Set(RELATED_ACTIONS[item.id]||[item.id]);
      const related=tasks.filter(task=>names.has(task.name)&&String(taskPlatform(task))===String(item.platform)).sort((a,b)=>String(b.started_at||'').localeCompare(String(a.started_at||'')));
      const running=related.filter(task=>task.running);
      const active=running[0]||related[0];
      const lastStatus=active?taskStatus(active.status):t('schedule_never_run','Не запускалось');
      const lastTime=active?dateText(active.updated_at||active.started_at):t('schedule_never_run','Не запускалось');
      const tone=active?taskStatusTone(active.status):'neutral';
      const toneClass = tone==='success'?'success':tone==='danger'?'danger':tone==='warning'?'warning':'info';
      const runningIds = running.map(t=>t.id||'').filter(Boolean).join(',');
      const startBtn = !running.length?`<button class="primary start-op" data-action="${esc(item.id)}">${esc(t('start','Запустить'))}</button>`:'';
      const stopBtn = running.length?`<button class="danger stop-op" data-task-ids="${esc(runningIds)}">${esc(t('stop','Остановить'))}</button>`:'';
      const percent=active?.progress?.percent==null?null:Math.max(0,Math.min(100,Number(active.progress.percent)||0));
      const progressText=active?taskSecondaryText(active):'';
      const progressHtml=running.length?`<div class="operation-card-progress"><div class="operation-progress-track ${percent==null?'indeterminate':''}"><i style="${percent==null?'':`width:${percent}%`}"></i></div><div class="operation-progress-meta"><span>${esc(progressText||t('task_running','Выполняется'))}</span>${percent==null?'':`<b>${Math.round(percent)}%</b>`}</div></div>`:'';
      return `
        <section class="operation-launch-card ${esc(toneClass)} ${running.length?'is-running':''}" ${active?.id?`data-open-task="${esc(active.id)}"`:''}>
          <div class="operation-card-body">
            <div class="operation-info"><b>${esc(actionLabel(item.id,item.label))}</b><small class="operation-meta">${esc(lastStatus)} · ${esc(lastTime)}</small></div>
            <div class="operation-actions">${startBtn}${stopBtn}</div>
          </div>
          ${progressHtml}
        </section>`;
    };
    const access=user.marketplaces&&typeof user.marketplaces==='object'?user.marketplaces:{};
    node.innerHTML=groups.filter(([platform])=>platform==='system'||Boolean(access[platform])).map(([platform,label])=>{
      const items=(ACTIONS[platform]||[]).map(([id,fallback])=>({id,label:fallback,platform})).filter(item=>item.id!=='backup_database'||user.platform_role==='superadmin');
      return `<section class="operation-platform-column"><div class="operation-platform-head"><small>${esc(label)}</small><b>${number(items.length)}</b></div><div>${items.map(actionCard).join('')}</div></section>`;
    }).join('');

    $$('.start-op').forEach(b=>b.onclick=(e)=>{ e.stopPropagation(); const action = b.dataset.action || b.closest('[data-start-action]')?.dataset.startAction; if(!action) return; startTask(action,'all',[]); });
    $$('.stop-op').forEach(b=>b.onclick=(e)=>{e.stopPropagation();const ids=String(b.getAttribute('data-task-ids')||'').split(',').map(x=>x.trim()).filter(Boolean);stopTasks(ids);});
    $$('[data-open-task]').forEach(card=>card.onclick=e=>{if(e.target.closest('button'))return;openLog(card.dataset.openTask);});
  }
  function renderTasks(){const tasks=state.tasks;const running=tasks.filter(task=>task.running).length,completed=tasks.filter(task=>task.status==='completed').length,failed=tasks.filter(task=>['failed','interrupted'].includes(task.status)).length;if($('#opsRunning'))$('#opsRunning').textContent=running;if($('#opsSummary'))$('#opsSummary').textContent=`${number(completed)} ${t('completed','завершено')} · ${number(failed)} ${t('failed','с ошибкой')}`;renderOperationActionGrid(tasks);}

  async function openLog(id){state.currentTask=id;showModal('logModal');await refreshLog();}
  async function refreshLog(){if(!state.currentTask)return;try{const d=await api(`/api/tasks/${encodeURIComponent(state.currentTask)}/log?lines=800`);$('#logTitle').textContent=d.task.label;$('#logSummary').innerHTML=logSummaryHtml(d.task);$('#logContent').textContent=friendlyLog(d.log);$('#stopTask').hidden=!d.task.running;}catch(e){toast(e.message,true)}}
  async function stopTask(id,{silent=false}={}){try{await api(`/api/tasks/${encodeURIComponent(id)}/stop`,{method:'POST'});if(!silent)toast(t('stopped_ok','Остановлено'));const idx=state.tasks.findIndex(task=>task.id===id);if(idx>=0){state.tasks[idx].running=false;state.tasks[idx].status='stopped';state.tasks[idx].message='Операция остановлена';renderTasks();}else{await loadTasks();}if(state.currentTask===id)refreshLog();return true;}catch(e){if(!silent)toast(e.message,true);return false;}}
  async function stopTasks(ids){if(!ids.length)return toast(t('stopped_none','Связанные выполняемые операции не найдены'),true);const results=await Promise.all(ids.map(id=>stopTask(id,{silent:true})));if(results.some(Boolean))toast(t('stopped_ok','Остановлено'));if(results.some(value=>!value))toast('Не все операции удалось остановить',true);await loadTasks();}
  async function deleteTask(id){if(!confirm('Удалить операцию и её журнал?'))return;try{await api(`/api/tasks/${encodeURIComponent(id)}`,{method:'DELETE'});loadTasks();}catch(e){toast(e.message,true)}}

  function renderAnalyticsKpis(k){const coverage=Number(k.data_coverage_pct ?? k.analysis_coverage_pct ?? 0);const items=[['catalog',t('total_products','Всего товаров'),number(k.total_products),platformCounts(k)],['coverage',t('analyzed','Данные обработаны'),`${coverage.toFixed(1)}%`,platformReady(k)],['risk',t('price_risks','Ценовые риски'),number(k.risk_count),`${number(k.review_count)} ${t('needs_review_short','на проверке')}`],['potential',t('margin_potential','Ценовой потенциал / месяц'),money(k.price_potential_monthly_kzt ?? k.potential_margin_monthly_kzt),`${number(k.potential_position_count)} поз. · ${number(k.potential_units_total)} ед./мес.`]];$('#reportKpis').innerHTML=items.map(([ic,label,value,sub])=>`<article><span>${icon(ic)}</span><div><small>${esc(label)}</small><b>${esc(value)}</b><em>${esc(sub)}</em></div></article>`).join('');}
  function renderAnalyticsStatus(rows){const total=rows.reduce((a,b)=>a+Number(b.count||0),0)||1;const top=rows.slice(0,6);let cursor=0;const colors=['#08a8e8','#31c9e8','#4d85e8','#6fd5d7','#43b7a2','#8aa6ba'];const stops=top.map((r,i)=>{const start=cursor;cursor+=Number(r.count||0)/total*100;return `${colors[i%colors.length]} ${start}% ${cursor}%`}).join(',');$('#reportStatusChart').innerHTML=`<div class="analytics-donut" style="background:conic-gradient(${stops||'#e8eef2 0 100%'})"><div><b>${number(total)}</b><small>позиций</small></div></div><div class="analytics-legend">${top.map((r,i)=>`<div><i style="background:${colors[i%colors.length]}"></i><span>${esc(statusLabel(r.status))}</span><b>${number(r.count)}</b></div>`).join('')}</div>`;}
  function renderPlatformChart(rows){const max=Math.max(1,...rows.map(r=>Number(r.total||0)));$('#reportPlatformChart').innerHTML=rows.map(r=>`<div class="platform-row"><div><b>${esc(r.platform)}</b><small>${number(r.priced)} из ${number(r.total)} с ценой</small></div><div class="platform-track"><i style="width:${Number(r.coverage_pct||0)}%"></i></div><strong>${Number(r.coverage_pct||0).toFixed(1)}%</strong></div>`).join('')||`<div class="empty">${esc(t('empty_data','Нет данных'))}</div>`;}
  function renderBrandRisks(rows){const max=Math.max(1,...rows.map(r=>Number(r.risks||0)+Number(r.review||0)));$('#reportBrandRisks').innerHTML=rows.slice(0,10).map(r=>{const risk=Number(r.risks||0),review=Number(r.review||0);return `<div class="brand-risk-row"><label>${esc(r.brand)}</label><div class="brand-risk-track"><i style="width:${risk/max*100}%"></i><em style="width:${review/max*100}%"></em></div><b>${number(risk)}</b><small>${number(review)} на проверке</small></div>`}).join('')||`<div class="empty">${esc(t('empty_data','Нет данных'))}</div>`;}
  function renderPriceBands(rows){const max=Math.max(1,...rows.map(r=>Number(r.count||0)));$('#reportPriceBands').innerHTML=rows.map(r=>`<div class="price-band-row"><label>${esc(r.label)}</label><div><i style="height:${Math.max(8,Number(r.count||0)/max*100)}%"></i></div><b>${number(r.count)}</b></div>`).join('')||`<div class="empty">${esc(t('empty_data','Нет данных'))}</div>`;}
  function renderQuality(k){const total=Number(k.total_products||0);const rows=[['Данные обработаны',k.data_ready_count??k.analyzed_count,total],['Kaspi: точный рынок',k.kaspi_market_analyzed_count,k.kaspi_products],['Ozon.ru: карточки готовы',k.ozon_data_ready_count,k.ozon_products],['Halyk Market: точный рынок',k.halyk_market_analyzed_count,k.halyk_products],['Forte Market: точный рынок',k.forte_market_analyzed_count,k.forte_products],['Wildberries: цены готовы',k.wildberries_data_ready_count,k.wildberries_products],['Требуют проверки',k.review_count,total]];$('#reportQuality').innerHTML=rows.map(([label,value,rowTotal])=>{const pct=rowTotal?Number(value||0)/Number(rowTotal)*100:0;return `<div class="quality-row"><div><span>${esc(label)}</span><b>${number(value||0)}</b></div><div class="quality-track"><i style="width:${Math.min(100,pct)}%"></i></div></div>`}).join('');}
  function analyticsTable(rows,type){if(!rows.length)return '<tr><td colspan="4"><div class="empty">Нет данных</div></td></tr>';return rows.map(r=>type==='risk'?`<tr data-open-analytics="${esc(r.product_code)}"><td><b>${esc(r.title)}</b><small>${esc(r.brand||'')} · ${esc(r.size||'')}</small></td><td>${money(r.current_price_kzt)}</td><td>${money(r.market_median_price_kzt)}</td><td><strong class="negative">${r.difference_pct==null?'—':`${Number(r.difference_pct).toFixed(1)}%`}</strong></td></tr>`:`<tr data-open-analytics="${esc(r.product_code)}"><td><b>${esc(r.title)}</b><small>${esc(r.brand||'')} · ${esc(r.size||'')}</small></td><td>${money(r.current_price_kzt)}</td><td>${money(r.potential_margin_per_unit_kzt)}</td><td><strong class="positive">${money(r.potential_margin_monthly_kzt)}</strong></td></tr>`).join('');}
  function bindAnalyticsRows(){$$('[data-open-analytics]').forEach(row=>row.onclick=()=>openProduct(row.dataset.openAnalytics));}
  function reportFiltersPayload(){
    return {
      platforms:multiValues('#reportPlatforms'),
      scope:$('#reportScope')?.value||'all',
      brand:multiValues('#reportBrand'),
      freshness:multiValues('#reportFreshness'),
      product_type:multiValues('#reportProductType'),
      size:multiValues('#reportSize'),
      season:multiValues('#reportSeason'),
      characteristic_group:multiValues('#reportCharacteristicGroup'),
      sort:'updated',direction:'desc'
    };
  }
  function filtersQuery(filters){const q=new URLSearchParams();Object.entries(filters||{}).forEach(([key,value])=>{if(Array.isArray(value)){if(value.length)q.set(key,value.join(','));}else if(value&&typeof value==='object'){if(Object.keys(value).length)q.set(key,JSON.stringify(value));}else if(value!==''&&value!=null)q.set(key,String(value));});return q;}
  function renderReportPreview(result){
    const rows=result?.items||[];$('#reportPreviewCount').textContent=`${number(result?.total||0)} позиций`;
    $('#reportPreviewBody').innerHTML=rows.length?rows.map(p=>{
      const price=p.platform==='ozon'&&p.price_original?`${number(p.price_original)} ₽`:money(p.own_price_kzt??p.price_kzt);
      const characteristics=p.characteristic_group_label||[p.product_type_label,p.size,p.season_label].filter(Boolean).join(' · ')||'—';
      return `<tr data-open-analytics="${esc(p.product_code)}"><td><b>${esc(p.title)}</b><small>${esc(p.brand||'')} · ${esc(p.source_product_code||'')}</small></td><td><span class="badge ${esc(p.platform)}">${esc(p.platform_label)}</span></td><td>${esc(characteristics)}</td><td>${esc(price)}</td><td><span class="${statusClass(p.status_tone)}">${esc(statusLabel(p.price_status))}</span></td><td>${esc(p.freshness_label||'—')}</td></tr>`;
    }).join(''):'<tr><td colspan="6"><div class="empty">По выбранным фильтрам позиции не найдены</div></td></tr>';
  }
  function setReportsLoading(loading){
    const page=$('#page-reports'),indicator=$('#reportsLoading'),refresh=$('#refreshReportPreview');
    page?.setAttribute('aria-busy',String(Boolean(loading)));
    page?.classList.toggle('reports-loading',Boolean(loading));
    if(indicator)indicator.hidden=!loading;
    if(refresh){refresh.disabled=Boolean(loading);refresh.classList.toggle('loading',Boolean(loading));}
  }
  async function generateFilteredReport(){const filters=reportFiltersPayload();if(!filters.platforms.length)return toast('Выберите хотя бы одну площадку.',true);await startTask('export_report','filtered',[],filters,{navigate:false});}
  async function loadReports(){
    const requestId=++state.reportRequest;
    const filters=reportFiltersPayload();
    if(!filters.platforms.length){
      setReportsLoading(false);$('#reportPreviewCount').textContent='0 позиций';$('#reportPreviewBody').innerHTML='<tr><td colspan="6"><div class="empty">Выберите хотя бы одну площадку</div></td></tr>';
      $('#reportKpis').innerHTML='<div class="empty">Выберите хотя бы одну площадку</div>';return;
    }
    setReportsLoading(true);
    const query=filtersQuery(filters);
    const previewQuery=new URLSearchParams(query.toString());previewQuery.set('page','1');previewQuery.set('page_size','100');
    try{
      const [analyticsData,reportsData,previewData]=await Promise.all([api(`/api/analytics/dashboard?${query}`),api('/api/reports'),api(`/api/products?${previewQuery}`)]);
      if(requestId!==state.reportRequest)return;
      const a=analyticsData.analytics||{},k=a.kpis||{};
      renderAnalyticsKpis(k);renderAnalyticsStatus(a.status_distribution||[]);renderPlatformChart(a.platforms||[]);renderBrandRisks(a.brand_risks||[]);renderPriceBands(a.price_bands||[]);renderQuality(k);
      $('#reportRiskTable').innerHTML=analyticsTable(a.top_risks||[],'risk');$('#reportOpportunityTable').innerHTML=analyticsTable(a.top_opportunities||[],'opportunity');renderReportPreview(previewData.result||{});bindAnalyticsRows();
      const reports=reportsData.reports||[];
      $('#reportsList').innerHTML=reports.length?reports.map(r=>{const excel=String(r.report_type||'').includes('xlsx');return `<article class="report-item ${excel?'excel-report':''}"><span>${icon(excel?'file':'reports')}</span><div><b>${esc(r.file_name)}</b><small>${excel?'Excel · ':''}${esc(r.report_type)} · ${number(r.rows_count)} строк · ${dateText(r.created_at)}</small></div><a href="/api/reports/${r.id}/download">${icon('download')}<span>Скачать</span></a></article>`}).join(''):'<div class="empty">Отчёты ещё не сформированы</div>';
    }catch(e){if(requestId!==state.reportRequest)return;toast(e.message,true);$('#reportKpis').innerHTML=`<div class="empty">${esc(e.message)}</div>`;}
    finally{if(requestId===state.reportRequest)setReportsLoading(false);}
  }
  const queueReportLoad=debounce(loadReports,220);



  function renderSubscriptionSettings(snapshot){
    const host=$('#subscriptionSettings');if(!host)return;
    const data=snapshot||{},ent=data.entitlement||{},current=ent.subscription||{};
    const active=Boolean(ent.active),pending=(data.requests||[]).find(item=>item.status==='pending');
    const quotas=Object.entries(ent.marketplaces||{}).map(([code,q])=>`<article class="subscription-quota ${q.enabled?'':'disabled'}"><b>${esc(marketplaceLabel(code))}</b><small>${q.enabled?'Доступна':'Выключена в пакете'}</small><span>Позиции: ${number(q.positions_used||0)} / ${q.position_limit==null?'∞':number(q.position_limit)}</span><span>Запуски сегодня: ${number(q.daily_operations_used||0)} / ${q.daily_operation_limit==null?'∞':number(q.daily_operation_limit)}</span>${q.positions_remaining===0?'<em>Недостаточно позиций — увеличьте лимит</em>':''}</article>`).join('');
    const plans=(data.plans||[]).map(plan=>`<option value="${esc(plan.code)}">${esc(plan.name)} · ${number(plan.price_amount)} ${esc(plan.currency)} / ${number(plan.term_days)} дн.</option>`).join('');
    const addons=(data.addons||[]).map(addon=>`<option value="${esc(addon.code)}">${esc(addon.name)} · ${number(addon.price_amount)} ${esc(addon.currency)}</option>`).join('');
    const marketplaceOptions=Object.keys(ent.marketplaces||{}).map(code=>`<option value="${esc(code)}">${esc(marketplaceLabel(code))}</option>`).join('');
    host.innerHTML=`<div class="subscription-summary ${active?'active':'pending'}"><div><small>Текущий пакет</small><b>${esc(active?current.plan_name||current.plan_code:'Нет активного пакета')}</b><span>${active?`${number(current.price_amount)} ${esc(current.currency)} · до ${esc(String(current.ends_at||'—'))}`:esc(ent.message||'Ожидается подтверждение')}</span></div>${pending?`<strong>Заявка «${esc(pending.plan_name)}» ожидает подтверждения</strong>`:''}</div><div class="subscription-quota-grid">${quotas||'<div class="empty compact">Лимиты появятся после подтверждения пакета.</div>'}</div>${can('manage_company')?`<div class="subscription-actions"><label>Сменить пакет<select id="subscriptionPlanSelect">${plans}</select></label><button type="button" class="secondary" id="requestSubscriptionPlan" ${pending?'disabled':''}>Отправить на подтверждение</button>${active?`<label>Дополнительные позиции<select id="subscriptionAddonSelect">${addons}</select></label><label>Площадка<select id="subscriptionAddonMarketplace">${marketplaceOptions}</select></label><button type="button" class="secondary" id="requestSubscriptionAddon">Запросить</button>`:''}</div>`:''}`;
    $('#requestSubscriptionPlan')?.addEventListener('click',async()=>{try{await api('/api/subscription/request',{method:'POST',body:{plan_code:$('#subscriptionPlanSelect').value}});toast('Заявка на пакет отправлена');loadSettings();}catch(e){toast(e.message,true)}});
    $('#requestSubscriptionAddon')?.addEventListener('click',async()=>{try{await api('/api/subscription/addons/request',{method:'POST',body:{addon_code:$('#subscriptionAddonSelect').value,marketplace_code:$('#subscriptionAddonMarketplace').value,quantity:1}});toast('Заявка на дополнительные позиции отправлена');loadSettings();}catch(e){toast(e.message,true)}});
  }

  function renderTelegramStatus(data){
    state.telegram=data||{};const card=$('#telegramSettings');if(!card)return;
    const link=data?.link||null,available=Boolean(data?.available),username=String(data?.bot_username||'').replace(/^@/,'');
    card.hidden=!available&&!link;
    const title=$('#telegramStatusTitle'),description=$('#telegramStatusText'),dot=$('#telegramStatusDot'),toggle=$('#toggleTelegramNotifications'),disconnect=$('#disconnectTelegram'),open=$('#openTelegramBot');
    if(open){open.hidden=!available;open.href=available?`https://t.me/${encodeURIComponent(username)}`:'#'}
    if(link){
      const enabled=Boolean(link.is_enabled),identity=link.telegram_username?`@${link.telegram_username}`:link.telegram_display_name||'личный чат';
      title.textContent=enabled?'Telegram подключён':'Уведомления приостановлены';description.textContent=`${identity} · привязан ${dateText(link.linked_at)}`;dot.className=enabled?'connected':'paused';
      toggle.hidden=false;toggle.textContent=enabled?'Приостановить':'Возобновить';toggle.dataset.enabled=String(enabled);disconnect.hidden=false;
    }else{
      title.textContent='Telegram не подключён';description.textContent='Откройте бота и выполните /login';dot.className='';toggle.hidden=true;disconnect.hidden=true;
    }
  }
  async function loadTelegramStatus(){try{renderTelegramStatus(await api('/api/telegram/status'))}catch(error){console.error(error)}}

  async function loadSettings(){
    try{
      const d=await api('/api/settings');state.settings=d;const p=d.preferences||{};
      $('#prefLocale').value=p.locale||'ru';state.theme=p.theme||window.ITPUI?.getTheme()||'system';
      window.ITPUI?.setTheme(state.theme,{store:true,emit:false});
      $('#prefCurrency').value=p.display_currency||'KZT';$('#prefUnits').value=p.default_monthly_units??1;
      $('#prefRub').value=p.rub_to_kzt??5.5;$('#prefUsd').value=p.usd_to_kzt??520;$('#prefEur').value=p.eur_to_kzt??565;
      const tenant=d.tenant||{};
      renderSubscriptionSettings(d.subscription);
      loadTelegramStatus();
      if($('#tenantName')){
        $('#tenantName').value=tenant.name||'';$('#tenantRegistrationNumber').value=tenant.registration_number||'';
        $('#tenantContactEmail').value=tenant.contact_email||'';$('#tenantContactPhone').value=tenant.contact_phone||'';
      }
      const ozon=d.config?.ozon||{};
      if($('#cfgOzonClientUrls')){
        $('#cfgOzonClientUrls').value=ozon.client_catalog_urls||'';
        $('#cfgOzonMarketUrls').value=ozon.market_category_urls||'';
        $('#cfgOzonExpectedSeller').value=ozon.expected_seller||'';
        $('#cfgOzonCurrentSeller').value=ozon.current_seller_name||'';
        $('#cfgOzonCurrentSellerUrl').value=ozon.current_seller_url||'';
        $('#cfgOzonCurrentProducts').value=ozon.current_products??0;
        $('#cfgOzonMarketProducts').value=ozon.market_products??0;
        $('#cfgOzonCurrentOffers').value=ozon.current_offers??0;
      }
    }catch(e){toast(e.message,true)}
  }
  async function saveSettings(){
    const preferences={locale:$('#prefLocale').value,theme:window.ITPUI?.getTheme()||'system',display_currency:$('#prefCurrency').value,default_monthly_units:Number($('#prefUnits').value),rub_to_kzt:Number($('#prefRub').value),usd_to_kzt:Number($('#prefUsd').value),eur_to_kzt:Number($('#prefEur').value)};
    const body={preferences};
    if(can('manage_company')&&$('#tenantName'))body.tenant={name:$('#tenantName').value.trim(),registration_number:$('#tenantRegistrationNumber').value.trim(),contact_email:$('#tenantContactEmail').value.trim(),contact_phone:$('#tenantContactPhone').value.trim()};
    if(user.platform_role==='superadmin'&&$('#cfgOzonClientUrls'))body.config={ozon:{client_catalog_urls:$('#cfgOzonClientUrls').value,market_category_urls:$('#cfgOzonMarketUrls').value,expected_seller:$('#cfgOzonExpectedSeller').value.trim()}};
    try{const d=await api('/api/settings',{method:'PUT',body});if(d.tenant){state.settings.tenant=d.tenant;user.tenant_profile_complete=true;applyPermissions()}applyI18n(preferences.locale);toast(t('settings_saved','Настройки сохранены'));loadOverview();loadProducts();}catch(e){toast(e.message,true)}
  }

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
            <p>${esc((state.scheduleActions.find(a=>a.code===x.action)||{}).name||x.action)}${x.seller_name?` · ${esc(x.seller_name)}`:''}</p>
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
            ${can('manage_operations')?`<button class="delete" data-delete-schedule="${x.id}">${esc(t('delete','Удалить'))}</button>`:''}
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
    updateScheduleSeller(item?.tenant_seller_id||null);
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

  function updateScheduleSeller(preferred=null){
    const action=$('#scheduleAction')?.value||'';
    const platform=actionInfo(action).platform;
    const sellers=user.marketplace_sellers?.[platform]||[];
    const select=$('#scheduleSeller'),field=$('#scheduleSellerField');
    if(!select||!field)return;
    select.innerHTML=sellers.map(item=>`<option value="${Number(item.id)}">${esc(item.display_name||item.external_seller_id||`#${item.id}`)}</option>`).join('');
    if(preferred&&[...select.options].some(option=>Number(option.value)===Number(preferred)))select.value=String(preferred);
    field.hidden=sellers.length<=1;
    select.required=sellers.length>1;
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
  async function loadUsers(){
    if(!can('manage_users'))return;
    try{
      const d=await api('/api/users');
      $('#usersGrid').innerHTML=(d.users||[]).map(u=>{
        const marketplaceCodes=Object.keys(u.available_marketplaces||{}).filter(code=>u.available_marketplaces?.[code]);
        const marketplaces=marketplaceCodes.length
          ? `<fieldset class="marketplace-access-field"><legend>Доступ к площадкам компании</legend><div class="marketplace-access-grid">${marketplaceCodes.map(code=>`<label><input type="checkbox" data-user-marketplace="${u.id}" value="${esc(code)}" ${u.marketplace_permissions?.[code]!==false?'checked':''}><span>${esc(marketplaceLabel(code))}</span></label>`).join('')}</div></fieldset>`
          : '<div class="empty compact">У компании пока нет доступных площадок.</div>';
        const permissions=Object.entries(PERMISSION_LABELS).map(([code,label])=>`<label><input type="checkbox" data-user-permission="${u.id}" value="${esc(code)}" ${u.permissions?.[code]?'checked':''}><span>${esc(label)}</span></label>`).join('');
        return `<article class="user-card" data-user-id="${u.id}" data-permissions-ready="1">
          <div class="user-card-head"><span class="user-avatar">${esc((u.display_name||'?')[0].toUpperCase())}</span><div><h4>${esc(u.display_name)}</h4><small>${esc(u.email)}</small></div></div>
          <div class="user-access-grid"><label><span>${esc(t('role','Роль'))}</span><select data-user-role="${u.id}"><option value="admin" ${u.role==='admin'?'selected':''}>Администратор</option><option value="operator" ${u.role==='operator'?'selected':''}>Оператор</option><option value="viewer" ${u.role==='viewer'?'selected':''}>Наблюдатель</option></select></label><div class="user-access-state"><span>${esc(t('access','Доступ'))}</span><label class="access-switch"><input type="checkbox" data-user-active="${u.id}" ${u.is_active?'checked':''} ${Number(u.id)===Number(user.id)?'disabled':''}><i></i><em>${u.is_active?t('active','Активен'):t('disabled','Отключён')}</em></label></div></div>
          <div class="user-marketplaces">${marketplaces}</div>
          <details class="user-permissions"><summary><span>Точечные разрешения</span><small>Развернуть</small></summary><div>${permissions}</div></details>
          <div class="user-actions"><button class="primary" data-save-user="${u.id}">${esc(t('save','Сохранить'))}</button><button class="secondary" data-recovery-user="${u.id}">${esc(t('new_code','Новый код'))}</button>${Number(u.id)!==Number(user.id)?`<button class="danger-soft" data-delete-user="${u.id}" data-user-name="${esc(u.display_name)}">${esc(t('delete','Удалить'))}</button>`:''}</div>
        </article>`;
      }).join('');
      $$('[data-save-user]').forEach(b=>b.onclick=()=>saveUserAccess(Number(b.dataset.saveUser)));
      $$('[data-recovery-user]').forEach(b=>b.onclick=()=>regenerateUserRecovery(Number(b.dataset.recoveryUser)));
      $$('[data-delete-user]').forEach(b=>b.onclick=()=>deleteUser(Number(b.dataset.deleteUser),b.dataset.userName));
      $$('[data-user-active]').forEach(input=>input.onchange=()=>{const em=input.closest('.access-switch').querySelector('em');em.textContent=input.checked?t('active','Активен'):t('disabled','Отключён')});
    }catch(e){toast(e.message,true)}
  }
  async function saveUserAccess(id){
    try{
      const role=$(`[data-user-role="${id}"]`).value;
      const activeNode=$(`[data-user-active="${id}"]`);
      const permissions={};
      Object.keys(PERMISSION_LABELS).forEach(code=>permissions[code]=Boolean($(`[data-user-permission="${id}"][value="${code}"]`)?.checked));
      const marketplaces={};
      $$(`[data-user-marketplace="${id}"]`).forEach(node=>marketplaces[node.value]=Boolean(node.checked));
      await api(`/api/users/${id}`,{method:'PUT',body:{role,is_active:activeNode?activeNode.checked:true,permissions,marketplaces}});
      toast('Данные и права пользователя сохранены');
      await loadUsers();
    }catch(e){toast(e.message,true)}
  }
  async function regenerateUserRecovery(id){try{const d=await api(`/api/users/${id}/recovery`,{method:'POST'});alert(`Новый код восстановления:
${d.recovery_code}`);}catch(e){toast(e.message,true)}}
  async function deleteUser(id,name){if(!confirm(`Удалить пользователя «${name}»? Действие нельзя отменить.`))return;try{await api(`/api/users/${id}`,{method:'DELETE'});toast('Пользователь удалён');loadUsers();}catch(e){toast(e.message,true)}}

  function showModal(id){$('#'+id).hidden=false} function hideModals(){$$('.modal').forEach(m=>m.hidden=true)}
  async function analyzeSelectedProducts(){
    const groups={
      kaspi:[...state.selected].filter(code=>!code.startsWith('ozon:')&&!code.startsWith('ozon_kz:')&&!code.startsWith('halyk:')&&!code.startsWith('forte:')&&!code.startsWith('wb:')),
      ozon:[...state.selected].filter(code=>code.startsWith('ozon:')),
      ozon_kz:[...state.selected].filter(code=>code.startsWith('ozon_kz:')),
      halyk_market:[...state.selected].filter(code=>code.startsWith('halyk:')),
      forte_market:[...state.selected].filter(code=>code.startsWith('forte:')),
      wildberries:[...state.selected].filter(code=>code.startsWith('wb:'))
    };
    const actions={kaspi:'kaspi_price_actualize',ozon:'ozon_price_actualize',ozon_kz:'ozon_kz_price_actualize',halyk_market:'halyk_price_actualize',forte_market:'forte_price_actualize',wildberries:'wb_price_actualize'};
    const active=Object.entries(groups).filter(([,codes])=>codes.length);
    if(!active.length)return toast('Не выбраны товары.',true);
    for(const [platform,codes] of active)await startTask(actions[platform],'selected',codes,null,{navigate:false});
    navigate('operations');
  }

  async function setWatch(){const codes=[...state.selected];if(!codes.length)return toast('Выберите позиции для наблюдения',true);try{await api('/api/products/state',{method:'PUT',body:{codes,watched:true}});toast('Позиции добавлены в наблюдение');state.selected.clear();loadProducts();}catch(e){toast(e.message,true)}}

  function updateHelpButton(){const button=$('#helpButton');if(!button)return;button.dataset.page=state.page;button.title=t('help_open','Открыть помощь по текущему разделу');button.dataset.tooltip=button.title;}
  function fallbackHelp(){return{title:t('help','Помощь'),intro:t(`${state.page}_subtitle`,''),sections:[{title:t('help_quick_actions','Быстрые действия'),items:[t('help_contact','Обратитесь к администратору рабочего пространства.')] }]};}
  function helpFocusable(){const drawer=$('#helpDrawer');return drawer?$$('button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])',drawer).filter(node=>!node.disabled&&!node.hidden&&node.offsetParent!==null):[];}
  function openHelp({preserveFocus=false}={}){
    const content=window.ITPUI?.helpFor(state.page)||window.ITPUI?.helpFor('dashboard')||fallbackHelp();
    const title=$('#helpTitle'),body=$('#helpBody'),drawer=$('#helpDrawer'),backdrop=$('#helpBackdrop'),button=$('#helpButton');
    if(!content||!title||!body||!drawer)return;
    if(!preserveFocus)helpReturnFocus=document.activeElement instanceof HTMLElement?document.activeElement:button;
    title.textContent=content.title||t('help','Помощь');
    body.innerHTML=`<p class="help-intro">${esc(content.intro||'')}</p>${(content.sections||[]).map(section=>`<section><h3>${esc(section.title)}</h3><ul>${(section.items||[]).map(item=>`<li>${esc(item)}</li>`).join('')}</ul></section>`).join('')}${content.tip?`<aside><b>${esc(t('help_tip','Подсказка'))}</b><span>${esc(content.tip)}</span></aside>`:''}`;
    body.scrollTop=0;if(backdrop)backdrop.hidden=false;drawer.classList.add('open');drawer.setAttribute('aria-hidden','false');button?.setAttribute('aria-expanded','true');document.body.classList.add('overlay-open');
    if(!preserveFocus)setTimeout(()=>($('#closeHelp')||drawer).focus(),0);
  }
  function closeHelp({restoreFocus=true}={}){
    const drawer=$('#helpDrawer');if(!drawer)return;const wasOpen=drawer.classList.contains('open'),backdrop=$('#helpBackdrop');
    drawer.classList.remove('open');drawer.setAttribute('aria-hidden','true');if(backdrop)backdrop.hidden=true;$('#helpButton')?.setAttribute('aria-expanded','false');if(!$('#productDrawer')?.classList.contains('open'))document.body.classList.remove('overlay-open');
    if(wasOpen&&restoreFocus&&helpReturnFocus?.isConnected)setTimeout(()=>helpReturnFocus.focus(),0);if(wasOpen)helpReturnFocus=null;
  }
  async function persistUiPreference(values){try{await api('/api/settings',{method:'PUT',body:{preferences:values}});}catch(e){toast(e.message,true)}}
  function handleThemeChange(pref){state.theme=pref;persistUiPreference({theme:pref});}

  function bind(){
    initMultiSelects();
    $$('.nav').forEach(b=>b.onclick=()=>navigate(b.dataset.page));
    $$('[data-page-link]').forEach(b=>b.onclick=()=>navigate(b.dataset.pageLink));
    $('#mobileMenu').onclick=()=>{const nav=$('#sidebar');nav.classList.toggle('open');$('#mobileMenu').setAttribute('aria-expanded',String(nav.classList.contains('open')))};
    $('#profileButton').onclick=e=>{e.stopPropagation();$('#profileMenu').hidden=!$('#profileMenu').hidden};
    if($('#notificationButton'))$('#notificationButton').onclick=async e=>{e.stopPropagation();const drawer=$('#notificationDrawer');drawer.hidden=!drawer.hidden;$('#notificationButton').setAttribute('aria-expanded',String(!drawer.hidden));if(!drawer.hidden)await loadNotifications({announce:false})};
    if($('#closeNotifications'))$('#closeNotifications').onclick=closeNotifications;
    if($('#readAllNotifications'))$('#readAllNotifications').onclick=async()=>{try{await api('/api/notifications/read-all',{method:'POST',body:{}});await loadNotifications({announce:false})}catch(error){toast(error.message,true)}};
    if($('#notificationList'))$('#notificationList').onclick=async event=>{const item=event.target.closest('[data-notification-id]');if(!item)return;try{await api(`/api/notifications/${item.dataset.notificationId}/read`,{method:'POST',body:{}});await loadNotifications({announce:false})}catch(error){toast(error.message,true)}};
    if($('#toggleTelegramNotifications'))$('#toggleTelegramNotifications').onclick=async()=>{const enabled=$('#toggleTelegramNotifications').dataset.enabled==='true';try{await api('/api/telegram/enabled',{method:'POST',body:{enabled:!enabled}});await loadTelegramStatus();toast(enabled?'Telegram-уведомления приостановлены':'Telegram-уведомления включены')}catch(error){toast(error.message,true)}};
    if($('#disconnectTelegram'))$('#disconnectTelegram').onclick=async()=>{if(!confirm('Отвязать Telegram от аккаунта Spyon?'))return;try{await api('/api/telegram/disconnect',{method:'POST',body:{}});await loadTelegramStatus();toast('Telegram отключён')}catch(error){toast(error.message,true)}};
    $('#profileMenu').onclick=e=>e.stopPropagation();$('#openPassword').onclick=()=>showModal('passwordModal');$$('.modal-close').forEach(b=>b.onclick=hideModals);$('#closeDrawer').onclick=closeDrawer;$('#backdrop').onclick=closeDrawer;
    $$('[data-lang]').forEach(b=>b.onclick=()=>{applyI18n(b.dataset.lang);persistUiPreference({locale:b.dataset.lang});if(state.page==='dashboard')loadOverview()});
    if($('#languageSelect'))$('#languageSelect').onchange=e=>{applyI18n(e.target.value);persistUiPreference({locale:e.target.value});if(state.page==='dashboard')loadOverview()};
    if($('#helpButton'))$('#helpButton').onclick=e=>{e.stopPropagation();openHelp()};if($('#closeHelp'))$('#closeHelp').onclick=()=>closeHelp();if($('#helpBackdrop'))$('#helpBackdrop').onclick=()=>closeHelp();
    if($('#heroCollapse'))$('#heroCollapse').onclick=()=>{const hero=$('#dashboardHero'),button=$('#heroCollapse');const collapsed=!hero.classList.contains('collapsed');hero.classList.toggle('collapsed',collapsed);button.setAttribute('aria-expanded',String(!collapsed));button.title=t(collapsed?'expand_dashboard':'collapse_dashboard',collapsed?'Развернуть обзор':'Свернуть обзор');button.dataset.tooltip=button.title};
    window.ITPUI?.onTheme(handleThemeChange);window.ITPUI?.onLocale(lang=>{state.lang=lang;applyI18n(lang,{persist:false});if(state.page==='dashboard')loadOverview()});
    $$('[data-quick-action]').forEach(b=>b.onclick=()=>startTask(b.dataset.quickAction));
    $('#refreshProducts').onclick=loadProducts;
    $('#productSearch').oninput=debounce(()=>{state.products.page=1;updateFilterResetVisibility();loadProducts()},350);
    ['platformFilter','brandFilter','statusFilter','freshnessFilter','productTypeFilter','sizeFilter','seasonFilter','characteristicGroupFilter','pageSize','sortProducts'].forEach(id=>{const node=$('#'+id);if(node)node.onchange=()=>{state.products.page=1;updateFilterResetVisibility();loadProducts()}});
    if($('#platformFilter'))$('#platformFilter').onchange=async()=>{state.products.page=1;await loadCatalogConfiguration();updateFilterResetVisibility();loadProducts()};
    $('#resetFilters').onclick=()=>{$('#productSearch').value='';['platformFilter','brandFilter','statusFilter','freshnessFilter','productTypeFilter','sizeFilter','seasonFilter','characteristicGroupFilter'].forEach(id=>clearMultiSelect('#'+id));$$('[data-attribute-key]').forEach(node=>clearMultiSelect(node));state.products.scope='all';$$('#scopeTabs button').forEach(b=>b.classList.toggle('active',b.dataset.scope==='all'));loadCatalogConfiguration();updateFilterResetVisibility();loadProducts()};
    $$('#scopeTabs button').forEach(b=>b.onclick=()=>{state.products.scope=b.dataset.scope;state.products.page=1;$$('#scopeTabs button').forEach(x=>x.classList.toggle('active',x===b));updateFilterResetVisibility();loadProducts()});
    $('#prevPage').onclick=()=>{if(state.products.page>1){state.products.page--;loadProducts()}};$('#nextPage').onclick=()=>{if(state.products.page<state.products.pages){state.products.page++;loadProducts()}};
    $('#selectPage').onchange=e=>{state.products.items.forEach(p=>e.target.checked?state.selected.add(p.product_code):state.selected.delete(p.product_code));renderProducts(state.products.items)};$('#clearSelection').onclick=()=>{state.selected.clear();renderProducts(state.products.items)};$('#watchSelected').onclick=setWatch;$('#analyzeSelected').onclick=analyzeSelectedProducts;$('#exportSelected').onclick=()=>startTask('export_report','selected',[...state.selected]);$('#selectedReport').onclick=()=>exportVisibleProducts();
    operationLaunchers.forEach(ids=>{const platformNode=$('#'+ids.platform),launchNode=$('#'+ids.launch);if(platformNode)platformNode.onchange=updateOperationActions;if(launchNode)launchNode.onclick=()=>{const scope=$('#'+ids.scope).value,codes=scope==='selected'?[...state.selected]:[],filters=scope==='filtered'?productFiltersPayload():null;startTask($('#'+ids.action).value,scope,codes,filters,{tenantSellerId:Number($('#'+ids.seller)?.value||0)})}});
    if($('#clearOperations'))$('#clearOperations').onclick=async()=>{if(!confirm('Удалить историю завершённых операций и журналы?'))return;try{await api('/api/tasks',{method:'DELETE'});loadTasks();}catch(e){toast(e.message,true)}};$('#refreshLog').onclick=refreshLog;$('#stopTask').onclick=()=>stopTask(state.currentTask);
    $('#generateReport').onclick=generateFilteredReport;if($('#refreshReportPreview'))$('#refreshReportPreview').onclick=loadReports;
    ['reportPlatforms','reportScope','reportBrand','reportFreshness','reportProductType','reportSize','reportSeason','reportCharacteristicGroup'].forEach(id=>{if($('#'+id))$('#'+id).onchange=queueReportLoad});
    if($('#catalogAttributeSearch'))$('#catalogAttributeSearch').oninput=applyCatalogSettingsSearch;
    if($('#catalogAttributeMarketplace'))$('#catalogAttributeMarketplace').onchange=applyCatalogSettingsSearch;
    if($('#enableVisibleCatalogFilters'))$('#enableVisibleCatalogFilters').onclick=()=>{$$('.catalog-filter-option:not([hidden]) [data-catalog-filter-key]').forEach(node=>node.checked=true);queueCatalogFilterSave()};
    if($('#disableVisibleCatalogFilters'))$('#disableVisibleCatalogFilters').onclick=()=>{$$('.catalog-filter-option:not([hidden]) [data-catalog-filter-key]').forEach(node=>node.checked=false);queueCatalogFilterSave()};
    if($('#refreshCatalogAttributes'))$('#refreshCatalogAttributes').onclick=async()=>{try{await api('/api/catalog/attributes/refresh',{method:'POST',timeoutMs:120000});await loadCatalogConfiguration({settings:true});toast('Характеристики обновлены');}catch(e){toast(e.message,true)}};
    $('#saveSettings').onclick=saveSettings;if($('#addSchedule'))$('#addSchedule').onclick=openScheduleModal;if($('#scheduleRecurrence'))$('#scheduleRecurrence').onchange=updateScheduleFields;if($('#scheduleAction'))$('#scheduleAction').onchange=()=>updateScheduleSeller();if($('#scheduleForm'))$('#scheduleForm').onsubmit=createSchedule;
    $('#passwordForm').onsubmit=async e=>{e.preventDefault();const f=Object.fromEntries(new FormData(e.target));try{await api('/api/account/password',{method:'POST',body:f});toast(t('password_changed','Пароль изменён'));hideModals();e.target.reset();}catch(err){toast(err.message,true)}};
    if($('#addUser'))$('#addUser').onclick=()=>showModal('userModal');if($('#userForm'))$('#userForm').onsubmit=async e=>{e.preventDefault();try{const fd=new FormData(e.target),body=Object.fromEntries(fd);const d=await api('/api/users',{method:'POST',body});toast(`Пользователь создан. Код восстановления: ${d.recovery_code}`);hideModals();e.target.reset();loadUsers();}catch(err){toast(err.message,true)}};
    document.addEventListener('click',e=>{closeMultiSelects();const menu=$('#profileMenu'),button=$('#profileButton');if(menu&&!menu.hidden&&!menu.contains(e.target)&&!button.contains(e.target))menu.hidden=true;const nav=$('#sidebar'),mobile=$('#mobileMenu');if(nav?.classList.contains('open')&&!nav.contains(e.target)&&!mobile?.contains(e.target)){nav.classList.remove('open');mobile?.setAttribute('aria-expanded','false')};$$('.modal:not([hidden])').forEach(modal=>{if(e.target===modal)modal.hidden=true});});
    document.addEventListener('keydown',e=>{
      const helpOpen=$('#helpDrawer')?.classList.contains('open');
      if(e.key==='Tab'&&helpOpen){const nodes=helpFocusable();if(nodes.length){const first=nodes[0],last=nodes[nodes.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}}
      if(e.key==='Escape'){closeMultiSelects();closeDrawer();closeHelp();closeNotifications();hideModals();if($('#profileMenu'))$('#profileMenu').hidden=true;$('#sidebar')?.classList.remove('open');$('#mobileMenu')?.setAttribute('aria-expanded','false')}
      if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();navigate('products');$('#productSearch').focus()}
    });
  }
  function debounce(fn,ms){let t;return(...a)=>{clearTimeout(t);t=setTimeout(()=>fn(...a),ms)}}

  function applyMarketplaceAccess(){
    const access=user.marketplaces&&typeof user.marketplaces==='object'?user.marketplaces:{};
    ['kaspi','ozon','ozon_kz','halyk_market','forte_market','wildberries'].forEach(code=>{
      const allowed=Boolean(access[code]);
      const productOption=$(`#platformFilter option[value="${code}"]`);if(productOption){productOption.hidden=!allowed;productOption.disabled=!allowed;if(!allowed)productOption.selected=false;}
      const reportOption=$(`#reportPlatforms option[value="${code}"]`);if(reportOption){reportOption.hidden=!allowed;reportOption.disabled=!allowed;if(!allowed)reportOption.selected=false;}
      $$(`#operationPlatform option[value="${code}"],#opsOperationPlatform option[value="${code}"]`).forEach(option=>option.hidden=!allowed);
    });
    ['operationPlatform','opsOperationPlatform'].forEach(id=>{const node=$('#'+id);if(!node)return;const visible=[...node.options].filter(option=>!option.hidden);if(visible.length&&!visible.some(option=>option.value===node.value))node.value=visible[0].value;});
    refreshMultiSelect('#platformFilter');refreshMultiSelect('#reportPlatforms');
  }



  async function init(){
    bind();applyMarketplaceAccess();applyPermissions();state.lang=window.ITPUI?.getLocale()||localStorage.getItem('itp_lang')||'ru';state.theme=window.ITPUI?.getTheme()||'system';window.ITPUI?.setTheme(state.theme,{store:false,emit:false});applyI18n(state.lang,{persist:false});updateHelpButton();updateOperationActions();
    if(!can('view_dashboard'))navigate(Object.keys(PAGE_PERMISSIONS).find(page=>can(PAGE_PERMISSIONS[page]))||'settings');
    $('#app').hidden=false;setTimeout(()=>$('#boot').classList.add('hide'),80);
    const jobs=[];
    if(can('view_products'))jobs.push(loadOptions(),loadCatalogConfiguration({settings:can('manage_filters')}));
    if(can('view_dashboard'))jobs.push(loadOverview());
    if(can('view_operations'))jobs.push(loadTasks());
    jobs.push(loadNotifications({announce:false}));
    Promise.allSettled(jobs).then(results=>results.filter(item=>item.status==='rejected').forEach(item=>console.error(item.reason)));
    setInterval(()=>{if(can('view_operations')&&(state.page==='operations' || state.tasks.some(task=>task.running)))loadTasks()},3000);if(can('view_dashboard'))setInterval(()=>{if(state.page==='dashboard')loadOverview()},15000);
    setInterval(()=>loadNotifications(),10000);
  }
  init().catch(e=>{console.error(e);$('#boot').innerHTML=`<strong>Ошибка запуска</strong><span>${esc(e.message)}</span>`});
})();
