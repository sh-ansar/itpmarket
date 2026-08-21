(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const csrf = $('meta[name="csrf-token"]')?.content || '';
  const t = (key, fallback) => window.ITPUI?.t(key, fallback) || fallback || key;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const locale = () => ({kk:'kk-KZ', en:'en-GB'}[window.ITPUI?.getLocale?.()] || 'ru-RU');
  const formatNumber = value => new Intl.NumberFormat(locale()).format(Number(value || 0));
  const formatDate = value => value ? new Date(value).toLocaleDateString(locale()) : '—';
  const remainingDays = value => { const time=new Date(value).getTime(); if(!Number.isFinite(time))return '—'; const days=Math.max(0,Math.ceil((time-Date.now())/86400000)); return days===0?'Заканчивается сегодня':`${days} дн.`; };
  let snapshot = null;
  const section=window.ITP_PLATFORM_SECTION||'companies';

  async function api(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers: {'Content-Type':'application/json', 'X-CSRF-Token':csrf, ...(options.headers || {})},
      body: options.body && typeof options.body !== 'string' ? JSON.stringify(options.body) : options.body
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  function toast(message, error = false) {
    const node = document.createElement('div');
    node.className = `toast${error ? ' error' : ''}`;
    node.textContent = message;
    $('#platformToasts')?.append(node);
    setTimeout(() => node.remove(), 4500);
  }

  function statusLabel(value) {
    return ({pending:t('company_pending','На рассмотрении'), approved:t('company_approved','Подтверждена'), rejected:t('company_rejected','Отклонена'), blocked:t('company_blocked','Заблокирована')}[value] || value || '—');
  }

  function chips(values, empty = '—') {
    return values?.length ? values.map(value => `<span class="company-chip">${esc(value)}</span>`).join('') : `<span class="muted">${esc(empty)}</span>`;
  }

  const sourceRuleFields = [
    ['allowed_hosts', 'marketplace_rule_hosts', 'Разрешённые домены'],
    ['seller_path_patterns', 'marketplace_rule_seller_paths', 'Regex пути продавца'],
    ['product_path_patterns', 'marketplace_rule_product_paths', 'Regex пути товара'],
    ['product_query_keys', 'marketplace_rule_product_keys', 'Ключи product ID в query'],
    ['seller_name_patterns', 'marketplace_rule_seller_names', 'Regex имени продавца'],
    ['seller_url_template', 'marketplace_rule_seller_url', 'Шаблон ссылки продавца'],
    ['product_url_template', 'marketplace_rule_product_url', 'Шаблон ссылки товара'],
    ['seller_name_template', 'marketplace_rule_name_template', 'Шаблон имени'],
    ['bare_id_pattern', 'marketplace_rule_bare_id', 'Regex отдельного ID']
  ];

  function sourceRuleCard(code, rule, catalogItem = {}) {
    const fields = sourceRuleFields.map(([field, key, fallback]) => {
      const value = Array.isArray(rule[field]) ? rule[field].join('\n') : (rule[field] || '');
      const rows = Array.isArray(rule[field]) ? Math.min(5, Math.max(2, rule[field].length + 1)) : 2;
      return `<label><span>${esc(t(key, fallback))}</span><textarea data-source-rule-field="${esc(field)}" rows="${rows}" spellcheck="false">${esc(value)}</textarea></label>`;
    }).join('');
    const example = rule.examples?.find(value => String(value).startsWith('http')) || rule.examples?.[0] || '';
    return `<details class="source-rule-card" data-source-rule-code="${esc(code)}"${code === 'kaspi' ? ' open' : ''}>
      <summary><span><small>MARKETPLACE</small><strong>${esc(catalogItem.name || code)}</strong></span><code>${esc(code)}</code></summary>
      <div class="source-rule-body">
        <p>${esc(t('marketplace_rule_one_per_line', 'Массивы заполняются по одному значению в строке. Regex должен содержать named group seller_id, product_id/product_slug или seller_name.'))}</p>
        <div class="source-rule-fields">${fields}</div>
        <div class="source-rule-preview">
          <label><span>${esc(t('marketplace_rule_test_input', 'Проверочная ссылка или ID'))}</span><input data-source-rule-test value="${esc(example)}" placeholder="https://..."></label>
          <button type="button" data-source-rule-preview>${esc(t('marketplace_rule_test', 'Проверить'))}</button>
          <output data-source-rule-result></output>
        </div>
      </div>
    </details>`;
  }

  function renderSourceRules(rules = {}, catalog = []) {
    const host = $('#marketplaceSourceRules');
    if (!host) return;
    const byCode = Object.fromEntries(catalog.map(item => [item.code, item]));
    host.innerHTML = Object.entries(rules).map(([code, rule]) => sourceRuleCard(code, rule, byCode[code])).join('');
  }

  function collectSourceRules() {
    const rules = {};
    document.querySelectorAll('[data-source-rule-code]').forEach(card => {
      const code = card.dataset.sourceRuleCode;
      const base = snapshot?.marketplace_source_rules?.[code] || {};
      rules[code] = {...base};
      card.querySelectorAll('[data-source-rule-field]').forEach(field => {
        const key = field.dataset.sourceRuleField;
        rules[code][key] = Array.isArray(base[key])
          ? field.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean)
          : field.value.trim();
      });
    });
    return rules;
  }

  const marketplaceNames={kaspi:'Kaspi',ozon:'Ozon.ru',ozon_kz:'Ozon.kz',halyk_market:'Halyk Market',forte_market:'Forte Market',wildberries:'Wildberries'};

  function planEditor(plan, featureCatalog, marketplaceCatalog) {
    const features=Object.fromEntries((plan.features||[]).map(item=>[item.feature_code,item]));
    const limits=Object.fromEntries((plan.marketplaces||[]).map(item=>[item.marketplace_code,item]));
    const featureFields=featureCatalog.map(feature=>{const value=features[feature.code]||{};return `<label class="subscription-toggle"><input type="checkbox" data-plan-feature="${esc(feature.code)}" ${value.is_enabled?'checked':''}><span>${esc(feature.name||feature.code)}</span></label>`}).join('');
    const marketplaceFields=marketplaceCatalog.map(code=>{const value=limits[code]||{};return `<div class="subscription-marketplace-limit" data-plan-marketplace="${esc(code)}"><label><input type="checkbox" data-limit-enabled ${value.is_enabled?'checked':''}> ${esc(marketplaceNames[code]||code)}</label><input data-limit-positions type="number" min="0" placeholder="Позиции: ∞" value="${value.position_limit??''}"><input data-limit-daily type="number" min="0" placeholder="Запуски: ∞" value="${value.daily_operation_limit??''}"></div>`}).join('');
    return `<form class="subscription-plan-editor" data-plan-id="${plan.id||''}"><div class="subscription-editor-grid"><label>Код<input name="code" required value="${esc(plan.code||'')}"></label><label>Название<input name="name" required value="${esc(plan.name||'')}"></label><label>Цена<input name="price_amount" type="number" min="0" step="0.01" value="${plan.price_amount??0}"></label><label>Валюта<input name="currency" value="${esc(plan.currency||'KZT')}"></label><label>Срок, дней<input name="term_days" type="number" min="1" value="${plan.term_days??30}"></label><label>Позиции по умолчанию<input name="position_limit" type="number" min="0" placeholder="Безлимит" value="${plan.position_limit??''}"></label><label>Запусков в день<input name="daily_operation_limit" type="number" min="0" placeholder="Безлимит" value="${plan.daily_operation_limit??''}"></label><label>Порядок<input name="display_order" type="number" value="${plan.display_order??100}"></label><label class="wide">Описание<textarea name="description" rows="2">${esc(plan.description||'')}</textarea></label></div><div class="subscription-toggle-grid">${featureFields}</div><div class="subscription-marketplace-grid">${marketplaceFields}</div><div class="subscription-editor-actions"><label><input name="is_public" type="checkbox" ${plan.is_public!==false?'checked':''}> На сайте</label><label><input name="is_active" type="checkbox" ${plan.is_active!==false?'checked':''}> Активен</label><button class="approve" type="submit">${plan.id?'Сохранить пакет':'Создать пакет'}</button></div></form>`;
  }

  function addonEditor(addon={}) {
    return `<form class="subscription-addon-editor" data-addon-id="${addon.id||''}"><label>Код<input name="code" required value="${esc(addon.code||'')}"></label><label>Название<input name="name" required value="${esc(addon.name||'')}"></label><label>Позиций<input name="extra_positions" type="number" min="1" required value="${addon.extra_positions??100}"></label><label>Цена<input name="price_amount" type="number" min="0" step="0.01" value="${addon.price_amount??0}"></label><label>Валюта<input name="currency" value="${esc(addon.currency||'KZT')}"></label><label>Срок, дней<input name="term_days" type="number" min="1" placeholder="До конца пакета" value="${addon.term_days??''}"></label><label class="wide">Описание<input name="description" value="${esc(addon.description||'')}"></label><div class="subscription-editor-actions wide"><label><input name="is_public" type="checkbox" ${addon.is_public!==false?'checked':''}> На сайте</label><label><input name="is_active" type="checkbox" ${addon.is_active!==false?'checked':''}> Активно</label><button class="approve" type="submit">${addon.id?'Сохранить':'Создать'}</button></div></form>`;
  }

  function renderSubscriptions(data={}) {
    const plans=data.plans||[],addons=data.addons||[];
    const featureMap=new Map();plans.forEach(plan=>(plan.features||[]).forEach(feature=>featureMap.set(feature.feature_code,{code:feature.feature_code,name:feature.name||feature.feature_code})));
    const features=[...featureMap.values()];
    const marketplaces=[...new Set(plans.flatMap(plan=>(plan.marketplaces||[]).map(item=>item.marketplace_code)))];
    if(!$('#subscriptionPlans'))return;
    $('#subscriptionPlans').innerHTML=plans.filter(plan=>plan.code!=='legacy').map(plan=>planEditor(plan,features,marketplaces)).join('')+planEditor({},features,marketplaces);
    $('#subscriptionAddons').innerHTML=addons.map(addonEditor).join('')+addonEditor();
    const pending=(data.pending_subscriptions||[]).map(item=>`<article class="subscription-review-row"><div><b>${esc(item.tenant_name)} · ${esc(item.plan_name)}</b><small>${esc(formatDate(item.requested_at))}</small></div><label>Цена<input data-review-price type="number" min="0" value="${item.price_amount}"></label><label>Срок<input data-review-term type="number" min="1" value="${item.term_days}"></label><label>С даты<input data-review-start type="datetime-local"></label><label>По дату<input data-review-end type="datetime-local"></label><button class="approve" data-subscription-review="${item.id}" data-decision="approved">Подтвердить</button><button class="decline" data-subscription-review="${item.id}" data-decision="rejected">Отклонить</button></article>`);
    const pendingAddons=(data.pending_addons||[]).map(item=>`<article class="subscription-review-row"><div><b>${esc(item.tenant_name)} · ${esc(item.addon_name)} · ${esc(marketplaceNames[item.marketplace_code]||item.marketplace_code)}</b><small>+${formatNumber(Number(item.extra_positions||0)*Number(item.quantity||1))} позиций · ${formatNumber(item.price_amount)} ${esc(item.currency)}</small></div><button class="approve" data-addon-review="${item.id}" data-decision="approved">Подтвердить</button><button class="decline" data-addon-review="${item.id}" data-decision="rejected">Отклонить</button></article>`);
    $('#subscriptionRequests').innerHTML=[...pending,...pendingAddons].join('')||'<div class="loading">Нет заявок на подтверждение.</div>';
  }

  function renderPayments(data={}){
    const activeHost=$('#activeSubscriptionTableBody'),paymentHost=$('#paymentTableBody');
    if(activeHost){
      const items=data.active_subscriptions||[];
      activeHost.innerHTML=items.length?items.map(item=>`<tr><td><strong>${esc(item.tenant_name)}</strong></td><td>${esc(item.plan_name||item.plan_code)}</td><td>${formatNumber(item.price_amount)} ${esc(item.currency||'KZT')}</td><td>${esc(formatDate(item.ends_at))}</td><td><span class="billing-remaining ${new Date(item.ends_at).getTime()-Date.now()<5*86400000?'due':''}">${esc(remainingDays(item.ends_at))}</span></td></tr>`).join(''):'<tr><td colspan="5" class="loading">Нет активных периодов.</td></tr>';
    }
    if(paymentHost){
      const items=data.payments||[];
      paymentHost.innerHTML=items.length?items.map(item=>`<tr><td><strong>${esc(item.tenant_name)}</strong></td><td>${esc(item.plan_name||item.plan_code)}</td><td>${formatNumber(item.amount)} ${esc(item.currency||'KZT')}</td><td>${esc(formatDate(item.period_start))}<small>до ${esc(formatDate(item.period_end))} · ${Number(item.months_count||0).toLocaleString()} мес.</small></td><td>${esc(formatDate(item.paid_at))}</td></tr>`).join(''):'<tr><td colspan="5" class="loading">Подтверждённых оплат пока нет.</td></tr>';
    }
  }

  function render(data) {
    snapshot = data;
    const totals = data.totals || {};
    if($('#platformKpis'))$('#platformKpis').innerHTML = [
      [t('platform_total_clients','Компаний'), totals.tenants],
      [t('platform_active_clients','Подтверждено'), totals.active_tenants],
      [t('platform_new_requests','На рассмотрении'), totals.new_requests],
      [t('platform_products','Товаров'), totals.products]
    ].map(([label, value]) => `<article><small>${esc(label)}</small><b>${formatNumber(value)}</b></article>`).join('');

    const companies = data.tenants || [];
    if($('#tenantTableBody'))$('#tenantTableBody').innerHTML = companies.length ? companies.map(company => {
      const owner = company.owner || {};
      const subscription=company.subscription||{};
      return `<tr data-company-row="${Number(company.id)}">
        <td><strong>${esc(company.name)}</strong><small>${esc(company.registration_number || 'БИН не указан')}</small></td>
        <td><span class="tenant-status ${esc(company.status)}">${esc(statusLabel(company.status))}</span></td>
        <td><strong>${esc(owner.display_name || '—')}</strong><small>${esc(owner.email || '')}</small></td>
        <td><div class="company-chips">${chips(company.connected_marketplaces)}</div></td>
        <td><strong>${esc(subscription.plan_name||company.plan_code||'—')}</strong><small>${subscription.price_amount!=null?`${formatNumber(subscription.price_amount)} ${esc(subscription.currency||'KZT')}`:'Не назначен'}</small></td>
        <td><strong>${esc(subscription.ends_at?formatDate(subscription.ends_at):'—')}</strong><small>${subscription.ends_at?remainingDays(subscription.ends_at):'Ожидает подтверждения'}</small></td>
        <td><div class="company-actions"><button class="company-edit-button" data-open-tenant="${Number(company.id)}">Редактировать</button></div></td>
      </tr>`;
    }).join('') : `<tr><td colspan="7" class="loading">${esc(t('platform_no_companies','Компании ещё не созданы'))}</td></tr>`;

    const requests = data.requests || [];
    renderSubscriptions(data.subscriptions||{});
    renderSourceRules(data.marketplace_source_rules, data.integration_catalog);
    renderPayments(data.subscriptions||{});
    window.ITPUI?.translateTree(document.body);
  }

  async function load() {
    try { render(await api(`/api/platform/overview?section=${encodeURIComponent(section)}`)); }
    catch (error) { toast(error.message, true); }
  }

  document.addEventListener('click', async event => {
    const admin = event.target.closest('[data-admin]');
    const status = event.target.closest('[data-company-status]');
    const review = event.target.closest('[data-review]');
    const preview = event.target.closest('[data-source-rule-preview]');
    const subscriptionReview=event.target.closest('[data-subscription-review]');
    const addonReview=event.target.closest('[data-addon-review]');
    if (admin) {
      event.preventDefault();
      $('#tenantAdminForm').elements.tenant_id.value = admin.dataset.admin;
      $('#tenantAdminModal').hidden = false;
      return;
    }
    if (preview) {
      event.preventDefault();
      const card = preview.closest('[data-source-rule-code]');
      const output = card.querySelector('[data-source-rule-result]');
      output.className = 'checking';
      output.textContent = t('marketplace_rule_checking', 'Распознаю…');
      try {
        const data = await api('/api/platform/marketplace-source-rules/preview', {method:'POST', body:{
          marketplace_code:card.dataset.sourceRuleCode,
          source:card.querySelector('[data-source-rule-test]').value.trim(),
          marketplace_source_rules:collectSourceRules()
        }});
        const result = data.result;
        output.className = 'ok';
        output.innerHTML = `<strong>${esc(result.seller_name)}</strong><span>ID: ${esc(result.seller_identifier)}${result.product_id ? ` · productId: ${esc(result.product_id)}` : ''}</span><a href="${esc(result.seller_url)}" target="_blank" rel="noreferrer">${esc(result.seller_url)}</a>`;
      } catch (error) {
        output.className = 'error';
        output.textContent = error.message;
      }
      return;
    }
    if(subscriptionReview){
      const row=subscriptionReview.closest('.subscription-review-row');
      try{await api(`/api/platform/subscriptions/${subscriptionReview.dataset.subscriptionReview}/${subscriptionReview.dataset.decision}`,{method:'POST',body:{price_amount:row.querySelector('[data-review-price]')?.value,term_days:row.querySelector('[data-review-term]')?.value,starts_at:row.querySelector('[data-review-start]')?.value,ends_at:row.querySelector('[data-review-end]')?.value}});toast('Заявка на пакет обработана');await load();}catch(error){toast(error.message,true)}
      return;
    }
    if(addonReview){
      try{await api(`/api/platform/subscription-addons/requests/${addonReview.dataset.addonReview}/${addonReview.dataset.decision}`,{method:'POST',body:{}});toast('Заявка на позиции обработана');await load();}catch(error){toast(error.message,true)}
      return;
    }
    try {
      if (status) {
        await api(`/api/platform/tenants/${status.dataset.tenantId}`, {method:'PUT', body:{status:status.dataset.companyStatus}});
        toast(t('settings_saved','Сохранено'));
        await load();
      } else if (review) {
        await api(`/api/platform/registration-requests/${review.dataset.review}/${review.dataset.decision}`, {method:'POST', body:{}});
        toast(review.dataset.decision === 'approved' ? t('company_approved','Компания подтверждена') : t('company_rejected','Компания отклонена'));
        await load();
      }
    } catch (error) { toast(error.message, true); }
  });

  document.addEventListener('submit',async event=>{
    const planForm=event.target.closest('.subscription-plan-editor');
    const addonForm=event.target.closest('.subscription-addon-editor');
    if(!planForm&&!addonForm)return;
    event.preventDefault();
    try{
      if(planForm){
        const raw=Object.fromEntries(new FormData(planForm));
        const features={};planForm.querySelectorAll('[data-plan-feature]').forEach(input=>features[input.dataset.planFeature]={is_enabled:input.checked});
        const marketplaces={};planForm.querySelectorAll('[data-plan-marketplace]').forEach(row=>marketplaces[row.dataset.planMarketplace]={is_enabled:row.querySelector('[data-limit-enabled]').checked,position_limit:row.querySelector('[data-limit-positions]').value,daily_operation_limit:row.querySelector('[data-limit-daily]').value});
        const body={...raw,is_public:Boolean(raw.is_public),is_active:Boolean(raw.is_active),position_limit:raw.position_limit||null,daily_operation_limit:raw.daily_operation_limit||null,features,marketplaces};
        const id=planForm.dataset.planId;await api(id?`/api/platform/subscription-plans/${id}`:'/api/platform/subscription-plans',{method:id?'PUT':'POST',body});toast('Пакет сохранён');
      }else{
        const raw=Object.fromEntries(new FormData(addonForm));const body={...raw,is_public:Boolean(raw.is_public),is_active:Boolean(raw.is_active),term_days:raw.term_days||null};const id=addonForm.dataset.addonId;await api(id?`/api/platform/subscription-addons/${id}`:'/api/platform/subscription-addons',{method:id?'PUT':'POST',body});toast('Дополнение сохранено');
      }
      await load();
    }catch(error){toast(error.message,true)}
  });

  $('#tenantAdminForm')?.addEventListener('submit', async event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.target));
    const tenantId = values.tenant_id;
    delete values.tenant_id;
    try {
      const data = await api(`/api/platform/tenants/${tenantId}/admin`, {method:'POST', body:values});
      window.alert(`${t('platform_admin_created','Пользователь создан')}\n${t('platform_recovery_code','Код восстановления')}: ${data.recovery_code}`);
      $('#tenantAdminModal').hidden = true;
      event.target.reset();
      await load();
    } catch (error) { toast(error.message, true); }
  });
  document.querySelectorAll('.modal-close').forEach(button => button.addEventListener('click', () => { $('#tenantAdminModal').hidden = true; }));
  $('#saveMarketplaceSourceRules')?.addEventListener('click', async event => {
    event.currentTarget.disabled = true;
    try {
      const data = await api('/api/platform/marketplace-source-rules', {method:'PUT', body:{marketplaces:collectSourceRules()}});
      snapshot.marketplace_source_rules = data.marketplace_source_rules;
      snapshot.integration_catalog = data.integration_catalog;
      renderSourceRules(snapshot.marketplace_source_rules, snapshot.integration_catalog);
      toast(t('marketplace_source_rules_saved', 'Правила ссылок сохранены'));
    } catch (error) { toast(error.message, true); }
    finally { event.currentTarget.disabled = false; }
  });
  $('#refreshPlatform')?.addEventListener('click', load);
  window.ITPUI?.onLocale(() => snapshot && render(snapshot));
  window.PlatformAdmin = {api, toast, load};
  load();
})();
