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
      <summary><span><strong>${esc(catalogItem.name || code)}</strong></span><code>${esc(code)}</code></summary>
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
    const planCode=String(plan.code||'').trim().toLowerCase();
    const periodMonths=Number(
      plan.billing_period_count
      || Math.max(
        1,
        Math.ceil(Number(plan.term_days||30)/30)
      )
    );
    const periodField=planCode==='trial'
      ?`<label>\u0421\u0440\u043e\u043a Trial, \u0434\u043d\u0435\u0439<input name="term_days" type="number" min="1" value="${Number(plan.term_days||3)}"></label>`
      :`<label>\u0421\u0440\u043e\u043a, \u043c\u0435\u0441\u044f\u0446\u0435\u0432<input name="billing_period_months" type="number" min="1" max="120" required value="${periodMonths}"></label>`;
    return `<form class="subscription-plan-editor" data-plan-id="${plan.id||''}"><div class="subscription-editor-grid"><label>Код<input name="code" required value="${esc(plan.code||'')}"></label><label>Название<input name="name" required value="${esc(plan.name||'')}"></label><label>Цена<input name="price_amount" type="number" min="0" step="0.01" value="${plan.price_amount??0}"></label><label>Валюта<input name="currency" value="${esc(plan.currency||'KZT')}"></label>${periodField}<label>Позиции по умолчанию<input name="position_limit" type="number" min="0" placeholder="Безлимит" value="${plan.position_limit??''}"></label><label>Запусков в день<input name="daily_operation_limit" type="number" min="0" placeholder="Безлимит" value="${plan.daily_operation_limit??''}"></label><label>Порядок<input name="display_order" type="number" value="${plan.display_order??100}"></label><label class="wide">Описание<textarea name="description" rows="2">${esc(plan.description||'')}</textarea></label></div><div class="subscription-toggle-grid">${featureFields}</div><div class="subscription-marketplace-grid">${marketplaceFields}</div><div class="subscription-editor-actions"><label><input name="is_public" type="checkbox" ${plan.is_public!==false?'checked':''}> На сайте</label><label><input name="is_active" type="checkbox" ${plan.is_active!==false?'checked':''}> Активен</label><button class="approve" type="submit">${plan.id?'Сохранить пакет':'Создать пакет'}</button></div></form>`;
  }

  function addonEditor(addon={}) {
    return `<form class="subscription-addon-editor" data-addon-id="${addon.id||''}"><label>Код<input name="code" required value="${esc(addon.code||'')}"></label><label>Название<input name="name" required value="${esc(addon.name||'')}"></label><label>Позиций<input name="extra_positions" type="number" min="1" required value="${addon.extra_positions??100}"></label><label>Цена<input name="price_amount" type="number" min="0" step="0.01" value="${addon.price_amount??0}"></label><label>Валюта<input name="currency" value="${esc(addon.currency||'KZT')}"></label><label>Срок действия<input value="До конца текущего тарифа" disabled></label><label class="wide">Описание<input name="description" value="${esc(addon.description||'')}"></label><div class="subscription-editor-actions wide"><label><input name="is_public" type="checkbox" ${addon.is_public!==false?'checked':''}> На сайте</label><label><input name="is_active" type="checkbox" ${addon.is_active!==false?'checked':''}> Активно</label><button class="approve" type="submit">${addon.id?'Сохранить':'Создать'}</button></div></form>`;
  }

  function renderSubscriptions(data={}) {
    const plans=data.plans||[],addons=data.addons||[];
    const featureMap=new Map();plans.forEach(plan=>(plan.features||[]).forEach(feature=>featureMap.set(feature.feature_code,{code:feature.feature_code,name:feature.name||feature.feature_code})));
    const features=[...featureMap.values()];
    const marketplaces=[...new Set(plans.flatMap(plan=>(plan.marketplaces||[]).map(item=>item.marketplace_code)))];
    if(!$('#subscriptionPlans'))return;
    $('#subscriptionPlans').innerHTML=plans.filter(plan=>plan.code!=='legacy').map(plan=>planEditor(plan,features,marketplaces)).join('')+planEditor({},features,marketplaces);
    $('#subscriptionAddons').innerHTML=addons.map(addonEditor).join('')+addonEditor();
  }

  function billingStatusLabel(value) {
    return ({
      awaiting_payment:
        '\u041e\u0436\u0438\u0434\u0430\u0435\u0442 \u043e\u043f\u043b\u0430\u0442\u044b',
      payment_review:
        '\u041d\u0430 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0435',
      payment_rejected:
        '\u041e\u0442\u043a\u043b\u043e\u043d\u0435\u043d\u043e',
      active:'Активно',
      paid:'Оплачено',
      confirmed:'Подтверждено',
      approved:'Подтверждено',
      issued:'Счёт выставлен',
      cancelled:'Отменено',
      overdue:'Просрочено',
      under_review:'На проверке'
    }[value] || value || '\u2014');
  }

  function renderPayments(data={}){
    const reviewHost=$('#paymentReviewTableBody');
    const activeHost=$('#activeSubscriptionTableBody');
    const paymentHost=$('#paymentTableBody');
    const historyHost=$('#platformBillingHistoryTableBody');

    if(reviewHost){
      const items=data.payment_review_items||[];

      reviewHost.innerHTML=items.length
        ?items.map(item=>{
          const proof=item.proof||null;
          const invoiceId=Number(item.invoice_id||0);
          const status=String(item.subscription_status||'');
          const canReject=(
            status==='payment_review'
            && proof
            && proof.status==='under_review'
          );

          const invoiceDocument=item.invoice_pdf_ready
            ?`<a class="billing-document-link" href="/api/platform/billing/invoices/${invoiceId}/pdf" target="_blank" rel="noopener">\u0421\u0447\u0451\u0442 PDF</a><small>${esc(item.invoice_number||'')}</small>`
            :`<span class="muted">\u041d\u0435\u0442 PDF</span><small>${esc(item.invoice_number||'')}</small>`;

          const proofDocument=proof
            ?`<a class="billing-document-link" href="/api/platform/billing/invoices/${invoiceId}/payment-proof" target="_blank" rel="noopener">${esc(proof.original_filename||'\u041e\u0442\u043a\u0440\u044b\u0442\u044c')}</a><small>${esc(formatDate(proof.uploaded_at))}</small>`
            :`<span class="muted">\u041d\u0435 \u0437\u0430\u0433\u0440\u0443\u0436\u0435\u043d</span>`;

          const reviewNote=(
            proof
            && proof.review_note
            && status==='payment_rejected'
          )
            ?`<small class="billing-review-note">${esc(proof.review_note)}</small>`
            :'';

          return `<tr>
            <td>
              <strong>${esc(item.tenant_name||'\u2014')}</strong>
              <small>${esc(item.registration_number||'')}</small>
            </td>
            <td>
              <strong>${esc(item.plan_name||item.plan_code||'\u2014')}</strong>
              <small>${formatNumber(item.months_count||0)} \u043c\u0435\u0441.</small>
            </td>
            <td>
              <strong>${formatNumber(item.total_amount)} ${esc(item.currency||'KZT')}</strong>
              <small>${formatNumber(item.months_count||0)} \u043c\u0435\u0441.</small>
            </td>
            <td class="billing-document-cell">${invoiceDocument}</td>
            <td class="billing-document-cell">${proofDocument}</td>
            <td>
              <span class="billing-status ${esc(status)}">${esc(billingStatusLabel(status))}</span>
              ${reviewNote}
            </td>
            <td>
              <div class="billing-review-actions">
                <button
                  type="button"
                  class="approve"
                  data-billing-action="confirm"
                  data-invoice-id="${invoiceId}"
                  data-invoice-number="${esc(item.invoice_number||'')}"
                  data-tenant-name="${esc(item.tenant_name||'')}"
                >\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c</button>

                <button
                  type="button"
                  class="decline"
                  data-billing-action="reject"
                  data-invoice-id="${invoiceId}"
                  data-invoice-number="${esc(item.invoice_number||'')}"
                  data-tenant-name="${esc(item.tenant_name||'')}"
                  ${canReject?'':'disabled'}
                >\u041e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c</button>
              </div>
            </td>
          </tr>`;
        }).join('')
        :'<tr><td colspan="7" class="loading">\u041d\u0435\u0442 \u043e\u043f\u043b\u0430\u0442, \u043e\u0436\u0438\u0434\u0430\u044e\u0449\u0438\u0445 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438.</td></tr>';
    }

    if(activeHost){
      const items=data.active_subscriptions||[];
      activeHost.innerHTML=items.length
        ?items.map(item=>`<tr><td><strong>${esc(item.tenant_name)}</strong></td><td>${esc(item.plan_name||item.plan_code)}</td><td>${formatNumber(item.price_amount)} ${esc(item.currency||'KZT')}</td><td>${esc(formatDate(item.ends_at))}</td><td><span class="billing-remaining ${new Date(item.ends_at).getTime()-Date.now()<5*86400000?'due':''}">${esc(remainingDays(item.ends_at))}</span></td></tr>`).join('')
        :'<tr><td colspan="5" class="loading">\u041d\u0435\u0442 \u0430\u043a\u0442\u0438\u0432\u043d\u044b\u0445 \u043f\u0435\u0440\u0438\u043e\u0434\u043e\u0432.</td></tr>';
    }

    if(paymentHost){
      const items=data.payments||[];
      paymentHost.innerHTML=items.length
        ?items.map(item=>`<tr><td><strong>${esc(item.tenant_name)}</strong></td><td>${esc(item.plan_name||item.plan_code)}</td><td>${formatNumber(item.amount)} ${esc(item.currency||'KZT')}</td><td>${esc(formatDate(item.period_start))}<small>\u0434\u043e ${esc(formatDate(item.period_end))} \u00b7 ${Number(item.months_count||0).toLocaleString()} \u043c\u0435\u0441.</small></td><td>${esc(formatDate(item.paid_at))}</td></tr>`).join('')
        :'<tr><td colspan="5" class="loading">\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0451\u043d\u043d\u044b\u0445 \u043e\u043f\u043b\u0430\u0442 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442.</td></tr>';
    }

    if(historyHost){
      const history=data.platform_billing_history||{};
      const items=history.items||[];
      historyHost.innerHTML=items.length ? items.map(item=>{
        const proof=item.proof||null;
        const invoiceId=Number(item.invoice_id||0);
        const documents=[
          item.invoice_pdf_ready ? `<a class="billing-document-link" href="/api/platform/billing/invoices/${invoiceId}/pdf" target="_blank" rel="noopener">Счёт PDF</a>` : '',
          proof ? `<a class="billing-document-link" href="/api/platform/billing/invoices/${invoiceId}/payment-proof" target="_blank" rel="noopener">${esc(proof.original_filename||'Платёжный документ')}</a>` : ''
        ].filter(Boolean).join('<br>')||'<span class="muted">Нет документов</span>';
        const reviewInfo=proof
          ?[
              proof.status
                ?`<strong>${esc(billingStatusLabel(proof.status))}</strong>`
                :'',
              proof.reviewed_at
                ?`<small>${esc(formatDate(proof.reviewed_at))}</small>`
                :'',
              proof.reviewed_by
                ?`<small>${esc(proof.reviewed_by)}</small>`
                :'',
              proof.review_note
                ?`<small class="billing-review-note">${esc(proof.review_note)}</small>`
                :''
            ].filter(Boolean).join('')
          :'<span class="muted">Нет платёжного документа</span>';

        const paidInfo=item.paid_at
          ?`<strong>Оплачено ${esc(formatDate(item.paid_at))}</strong>${reviewInfo}`
          :reviewInfo;

        return `<tr>
          <td>
            <strong>${esc(item.tenant_name||'—')}</strong>
            <small>${esc(item.registration_number||'')}</small>
          </td>
          <td>
            <strong>${esc(item.invoice_number||'—')}</strong>
            <small>Выставлен: ${esc(formatDate(item.issued_at))}</small>
            <small>Оплатить до: ${esc(formatDate(item.due_at))}</small>
          </td>
          <td>
            <strong>${esc(item.plan_name||item.plan_code||'—')}</strong>
            <small>${Number(item.months_count||0).toLocaleString()} мес.</small>
          </td>
          <td>
            <strong>${formatNumber(item.total_amount)} ${esc(item.currency||'KZT')}</strong>
          </td>
          <td>
            ${esc(formatDate(item.period_start))}
            <small>до ${esc(formatDate(item.period_end))}</small>
          </td>
          <td>
            <span class="billing-status ${esc(item.subscription_status||item.invoice_status||'')}">
              ${esc(billingStatusLabel(item.subscription_status||item.invoice_status))}
            </span>
            <small>${esc(billingStatusLabel(item.invoice_status||''))}</small>
          </td>
          <td class="billing-document-cell">${documents}</td>
          <td class="billing-history-review">${paidInfo}</td>
        </tr>`;
      }).join('') : '<tr><td colspan="8" class="loading">История счетов пока пуста.</td></tr>';
    }

    const addonHost=$('#addonPaymentReviewTableBody');
    if(addonHost){
      const items=data.addon_payment_review_items||[];
      addonHost.innerHTML=items.length
        ?items.map(item=>`<tr>
          <td><strong>${esc(item.tenant_name||'—')}</strong></td>
          <td><strong>Дополнительные позиции</strong><small>${esc(item.marketplace_code||'—')}</small></td>
          <td>${esc(item.addon_code||'—')}</td>
          <td>${formatNumber(item.quantity||0)} пак. <small>${formatNumber(Number(item.positions||0)*Number(item.quantity||0))} поз.</small></td>
          <td><strong>${esc(item.invoice_number||'—')}</strong><small>${formatNumber(item.total_price||0)} ${esc(item.currency||'KZT')}</small></td>
          <td><a class="billing-document-link" href="/api/platform/billing/addon-payments/${Number(item.id)}/proof" target="_blank" rel="noopener">${esc(item.original_filename||'Открыть')}</a><small>${esc(formatDate(item.uploaded_at))}</small></td>
          <td><span class="billing-status under_review">На проверке</span></td>
          <td><div class="billing-review-actions"><button type="button" class="approve" data-addon-payment-action="approve" data-proof-id="${Number(item.id)}">Подтвердить оплату</button><button type="button" class="decline" data-addon-payment-action="reject" data-proof-id="${Number(item.id)}">Отклонить оплату</button></div></td>
        </tr>`).join('')
        :'<tr><td colspan="8" class="loading">Нет add-on оплат, ожидающих проверки.</td></tr>';
    }
  }

  function openDrawer(title, markup) {
    const drawer=$('#tenantDetailDrawer');
    const backdrop=$('#tenantDetailBackdrop');
    if(!drawer||!backdrop)return;
    $('#tenantDetailTitle').textContent=title;
    $('#tenantDetailBody').innerHTML=markup;
    drawer.hidden=false;
    backdrop.hidden=false;
  }

  function closeDrawer() {
    const drawer=$('#tenantDetailDrawer');
    const backdrop=$('#tenantDetailBackdrop');
    if(drawer)drawer.hidden=true;
    if(backdrop)backdrop.hidden=true;
  }

  function billingHistoryMarkup(history={}) {
    const tenant=history.tenant||{};
    const subscription=history.subscription||{};
    const items=history.items||[];
    const rows=items.length ? items.map(item=>{
      const proof=item.proof;
      const documents=[
        item.invoice_pdf_ready ? `<a class="billing-document-link" href="/api/platform/billing/invoices/${Number(item.invoice_id)}/pdf" target="_blank" rel="noopener">Счёт PDF</a>` : '',
        proof ? `<a class="billing-document-link" href="/api/platform/billing/invoices/${Number(item.invoice_id)}/payment-proof" target="_blank" rel="noopener">${esc(proof.original_filename||'Платёжный документ')}</a>` : ''
      ].filter(Boolean).join('<br>') || '<span class="muted">Нет документов</span>';
      const review=proof ? [
        `<strong>${esc(proof.status||'—')}</strong>`,
        proof.reviewed_at ? esc(formatDate(proof.reviewed_at)) : '',
        proof.reviewed_by ? esc(proof.reviewed_by) : '',
        proof.review_note ? esc(proof.review_note) : ''
      ].filter(Boolean).join('<br>') : '<span class="muted">Документ не загружен</span>';
      return `<tr><td><strong>${esc(item.invoice_number)}</strong><small>${esc(formatDate(item.issued_at))}</small></td><td>${formatNumber(item.total_amount)} ${esc(item.currency||'KZT')}<small>${esc(item.plan_name||item.plan_code||'—')}</small></td><td>${esc(formatDate(item.period_start))}<small>до ${esc(formatDate(item.period_end))}</small></td><td><span class="billing-status ${esc(item.subscription_status||'')}">${esc(billingStatusLabel(item.subscription_status))}</span><small>${esc(item.invoice_status||'')}</small></td><td class="billing-document-cell">${documents}</td><td>${review}</td></tr>`;
    }).join('') : '<tr><td colspan="6" class="loading">История счетов пока пуста.</td></tr>';
    return `<section class="drawer-section"><p><strong>${esc(tenant.name||'—')}</strong><br><small>БИН: ${esc(tenant.registration_number||'—')}</small><br><small>Пакет: ${esc(subscription.plan_name||subscription.plan_code||'—')} · действует до ${esc(formatDate(subscription.ends_at))}</small></p><div class="company-table-wrap"><table class="company-table payment-table"><thead><tr><th>Счёт</th><th>Сумма</th><th>Период</th><th>Статус</th><th>Документы</th><th>Проверка</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }

  async function openBillingHistory(tenantId) {
    try {
      const data=await api(`/api/platform/tenants/${Number(tenantId)}/billing-history`);
      const history=data.history||{};
      openDrawer(`История · ${history.tenant?.name||'Компания'}`, billingHistoryMarkup(history));
    } catch(error) { toast(error.message,true); }
  }

  function openBillingDecision(button) {
    const modal=$('#billingDecisionModal');
    const form=$('#billingDecisionForm');

    if(!modal||!form)return;

    const action=String(
      button.dataset.billingAction||''
    );

    const invoiceId=Number(
      button.dataset.invoiceId||0
    );

    if(
      !Number.isInteger(invoiceId)
      || invoiceId<=0
      || !['confirm','reject'].includes(action)
    ){
      toast(
        '\u041d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u0430\u044f \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u044f.',
        true
      );
      return;
    }

    form.elements.invoice_id.value=String(invoiceId);
    form.elements.action.value=action;

    const note=form.elements.note;
    note.value='';
    note.required=action==='reject';

    const tenant=String(
      button.dataset.tenantName||''
    );

    const invoice=String(
      button.dataset.invoiceNumber||''
    );

    $('#billingDecisionTitle').textContent=(
      action==='confirm'
        ?'\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c \u043e\u043f\u043b\u0430\u0442\u0443'
        :'\u041e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c \u043e\u043f\u043b\u0430\u0442\u0443'
    );

    $('#billingDecisionDescription').textContent=[
      tenant,
      invoice
    ].filter(Boolean).join(' \u00b7 ');

    $('#billingDecisionNoteLabel').textContent=(
      action==='reject'
        ?'\u041f\u0440\u0438\u0447\u0438\u043d\u0430 \u043e\u0442\u043a\u043b\u043e\u043d\u0435\u043d\u0438\u044f'
        :'\u041a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0439'
    );

    const submit=$('#billingDecisionSubmit');

    submit.textContent=(
      action==='confirm'
        ?'\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c'
        :'\u041e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c'
    );

    submit.classList.toggle(
      'decline',
      action==='reject'
    );

    modal.hidden=false;
    note.focus();
  }

  function closeBillingDecision() {
    const modal=$('#billingDecisionModal');
    const form=$('#billingDecisionForm');

    if(modal)modal.hidden=true;
    if(form)form.reset();
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
    renderLegalDocumentAdmin(data.legal_documents||[]);
    window.ITPUI?.translateTree(document.body);
  }

  function resetLegalDraftForm(){
    const form=$('#legalDocumentDraftForm');

    if(!form){
      return;
    }

    form.reset();

    if(form.elements.version_id){
      form.elements.version_id.value='';
    }

    if(form.elements.requires_acceptance){
      form.elements.requires_acceptance.checked=true;
    }

    const cancel=$('#cancelLegalDraftEdit');

    if(cancel){
      cancel.hidden=true;
    }
  }

  function editLegalDraft(item){
    const form=$('#legalDocumentDraftForm');

    if(
      !form
      ||!item
      ||item.status!=='draft'
    ){
      return;
    }

    form.elements.version_id.value=
      String(item.id||'');

    form.elements.type.value=
      item.type||'offer';

    form.elements.number.value=
      item.number||'';

    form.elements.version.value=
      item.version||'';

    form.elements.title.value=
      item.title||'';

    form.elements.effective_at.value=
      String(
        item.effective_at||''
      ).slice(0,10);

    form.elements.body_text.value=
      item.body_text||'';

    form.elements.acceptance_text.value=
      item.acceptance_text||'';

    form.elements.requires_acceptance.checked=
      item.requires_acceptance!==false;

    const cancel=$('#cancelLegalDraftEdit');

    if(cancel){
      cancel.hidden=false;
    }

    form.scrollIntoView({
      behavior:'smooth',
      block:'start'
    });
  }

  function renderLegalDocumentAdmin(items){
    const host=$('#legalDocumentAdminTable');

    if(!host){
      return;
    }

    host.innerHTML=items.length
      ?items.map(item=>{
        const draft=
          item.status==='draft';
        const slug=String(item.slug||item.type||'').replaceAll('_','-');

        const effective=
          item.effective_at
            ?formatDate(item.effective_at)
            :'После публикации';

        const statusLabel={
          draft:'Черновик',
          published:'Опубликован',
          archived:'Архив'
        }[item.status]||item.status||'—';

        const actions=draft
          ?`<div class="legal-row-actions">
              <button
                type="button"
                class="secondary"
                data-edit-legal="${Number(item.id)}"
              >
                Редактировать
              </button>

              <button
                type="button"
                class="approve"
                data-publish-legal="${Number(item.id)}"
              >
                Опубликовать
              </button>
            </div>`
          :`<div class="legal-row-actions">
              <a
                class="secondary"
                href="/legal/${encodeURIComponent(slug)}/${encodeURIComponent(item.version)}"
                target="_blank"
                rel="noopener"
              >
                Открыть
              </a>
              <a
                class="secondary"
                href="/legal/${encodeURIComponent(slug)}/${encodeURIComponent(item.version)}.pdf"
                target="_blank"
                rel="noopener"
              >
                PDF
              </a>
              <button
                type="button"
                class="secondary"
                data-legal-acceptances="${Number(item.id)}"
              >
                Согласия
              </button>
            </div>`;

        return `<tr>
          <td>
            <strong>${esc(item.title||'—')}</strong>
            <small>${esc(item.type||'')}</small>
          </td>

          <td>
            <strong>${esc(item.number||'—')}</strong>
            <small>Редакция ${esc(item.version||'—')}</small>
          </td>

          <td>
            ${esc(effective)}
          </td>

          <td>
            <span class="billing-status ${esc(item.status||'')}">
              ${esc(statusLabel)}
            </span>

            <small>
              ${
                item.requires_acceptance
                  ?'Требует согласия'
                  :'Без повторного согласия'
              }
            </small>
          </td>

          <td>
            <strong>${formatNumber(item.accepted_count||0)}</strong>
          </td>

          <td>
            ${esc(formatDate(item.published_at))}
          </td>

          <td>
            ${actions}
          </td>
        </tr>`;
      }).join('')
      :'<tr><td colspan="7" class="loading">Версий пока нет.</td></tr>';
  }

  function closeLegalAcceptances(){
    const modal=$('#legalAcceptanceModal');

    if(modal){
      modal.hidden=true;
    }
  }

  function shortHash(value){
    const hash=String(value||'');
    return hash.length>16
      ?`${hash.slice(0,12)}…${hash.slice(-4)}`
      :hash||'—';
  }

  function renderLegalAcceptances(item, rows){
    const modal=$('#legalAcceptanceModal');
    const description=$('#legalAcceptanceModalDescription');
    const host=$('#legalAcceptanceTable');

    if(!modal||!host){
      return;
    }

    if(description){
      description.textContent=`${item.title||'Документ'} · v${item.version||'—'}`;
    }

    host.innerHTML=rows.length
      ?rows.map(row=>`<tr>
          <td><strong>${esc(row.display_name||'—')}</strong><small>${esc(row.email||'')}</small></td>
          <td>${esc(row.tenant_name||'—')}</td>
          <td>${esc(formatDate(row.accepted_at))}</td>
          <td><strong>v${esc(row.document_version||'—')}</strong><small title="${esc(row.document_sha256||'')}">${esc(shortHash(row.document_sha256))}</small></td>
          <td><strong>${esc(row.locale||'—')}</strong><small>${esc(row.source||'—')}</small></td>
          <td>${esc(row.ip_address||'—')}</td>
        </tr>`).join('')
      :'<tr><td colspan="6" class="loading">Согласий для этой версии пока нет.</td></tr>';

    modal.hidden=false;
  }

  async function load() {
    try {
      const data=await api(
        `/api/platform/overview?section=${encodeURIComponent(section)}`
      );

      if(section==='payments'){
        try{
          const review=await api(
            '/api/platform/billing/payments'
          );

          const history=await api('/api/platform/billing/history?page=1&page_size=50');

          data.subscriptions={
            ...(data.subscriptions||{}),
            payment_review_items:
              review.items||[],
            platform_billing_history:history.history||{}
          };
        }catch(error){
          data.subscriptions={
            ...(data.subscriptions||{}),
            payment_review_items:[]
          };

          toast(
            error.message,
            true
          );
        }
        try{
          const addonReview=await api('/api/platform/billing/addon-payments');
          data.subscriptions={...(data.subscriptions||{}),addon_payment_review_items:addonReview.items||[]};
        }catch(error){
          data.subscriptions={...(data.subscriptions||{}),addon_payment_review_items:[]};
          toast(error.message,true);
        }
      }

      if(section==='legal-documents'){
        const legal=await api('/api/platform/legal-documents');
        data.legal_documents=legal.items||[];
      }

      render(data);
    }
    catch(error){
      toast(
        error.message,
        true
      );
    }
  }

  async function refreshTenantDrawer(tenantId) {
    const richOpen = window.PlatformCompanyAdmin?.openTenant;

    if (typeof richOpen !== 'function') {
      throw new Error(
        'Карточка компании не загрузилась. Обновите страницу.'
      );
    }

    await richOpen(Number(tenantId));
  }

  document.addEventListener('click', async event => {
    const billingHistoryButton=event.target.closest('[data-billing-history]');
    const marketplaceReview=event.target.closest('[data-marketplace-review]');
    const replacement=event.target.closest('[data-replace-source]');
    const purgeSeller=event.target.closest('[data-purge-seller]');
    const admin = event.target.closest('[data-admin]');
    const status = event.target.closest('[data-company-status]');
    const review = event.target.closest('[data-review]');
    const preview = event.target.closest('[data-source-rule-preview]');
    const billingAction=event.target.closest('[data-billing-action]');
    const addonPaymentAction=event.target.closest('[data-addon-payment-action]');
    const publishLegal=event.target.closest('[data-publish-legal]');
    const editLegal=event.target.closest('[data-edit-legal]');
    const legalAcceptances=event.target.closest('[data-legal-acceptances]');

    if(legalAcceptances){
      event.preventDefault();

      const item=(snapshot?.legal_documents||[]).find(
        value=>Number(value.id)===Number(legalAcceptances.dataset.legalAcceptances)
      );

      if(!item){
        return;
      }

      try{
        legalAcceptances.disabled=true;
        const data=await api(
          `/api/platform/legal-documents/acceptances?version_id=${Number(item.id)}`
        );
        renderLegalAcceptances(item, data.items||[]);
      }catch(error){
        toast(error.message,true);
      }finally{
        legalAcceptances.disabled=false;
      }

      return;
    }

    if(editLegal){
      event.preventDefault();

      const item=(
        snapshot?.legal_documents||[]
      ).find(
        value=>
          Number(value.id)
          ===Number(editLegal.dataset.editLegal)
      );

      if(item){
        editLegalDraft(item);
      }

      return;
    }

    if(publishLegal){
      event.preventDefault();

      const item=(
        snapshot?.legal_documents||[]
      ).find(
        value=>
          Number(value.id)
          ===Number(publishLegal.dataset.publishLegal)
      );

      const label=item
        ?`${item.title} · v${item.version}`
        :'эту версию';

      if(
        !window.confirm(
          `Опубликовать ${label}? `+
          `После публикации версия станет неизменяемой.`
        )
      ){
        return;
      }

      try{
        publishLegal.disabled=true;

        await api(
          `/api/platform/legal-documents/${Number(publishLegal.dataset.publishLegal)}/publish`,
          {
            method:'POST',
            body:{}
          }
        );

        toast(
          'Новая версия опубликована'
        );

        resetLegalDraftForm();

        await load();

      }catch(error){
        publishLegal.disabled=false;
        toast(
          error.message,
          true
        );
      }

      return;
    }
    if(billingHistoryButton){event.preventDefault();await openBillingHistory(billingHistoryButton.dataset.tenantId);return;}
    if(marketplaceReview){
      event.preventDefault();
      try{await api(`/api/platform/tenants/${marketplaceReview.dataset.tenantId}/marketplaces/${marketplaceReview.dataset.marketplaceCode}/${marketplaceReview.dataset.marketplaceReview}`,{method:'POST',body:{tenant_seller_id:Number(marketplaceReview.dataset.sellerId)}});toast('Источник обработан');await refreshTenantDrawer(marketplaceReview.dataset.tenantId);await load();}catch(error){toast(error.message,true);}return;
    }
    if(replacement){
      event.preventDefault();
      const sourceUrl=String(window.prompt('Новый URL продавца:')||'').trim();
      if(!sourceUrl)return;
      try{await api(`/api/platform/tenants/${replacement.dataset.tenantId}/marketplaces/${replacement.dataset.marketplaceCode}/${replacement.dataset.sellerId}/replace`,{method:'POST',body:{source_url:sourceUrl}});toast('Источник заменён после проверки.');await refreshTenantDrawer(replacement.dataset.tenantId);await load();}catch(error){toast(error.message,true);}return;
    }
    if(purgeSeller){
      event.preventDefault();
      const {tenantId,marketplaceCode,sellerId}=purgeSeller.dataset;
      try{
        const preview=await api(`/api/platform/tenants/${tenantId}/marketplaces/${marketplaceCode}/${sellerId}/purge-preview`,{method:'POST',body:{}});
        const counts=Object.entries(preview.preview?.counts||{}).map(([key,value])=>`${key}: ${value}`).join('\n');
        if(!window.confirm(`Будет удалён источник и его текущие данные. История запусков и аудит сохранятся.\n${counts}\n\nПродолжить?`))return;
        const currentPassword=String(window.prompt('Подтвердите текущим паролем:')||'');
        if(!currentPassword)return;
        await api(`/api/platform/tenants/${tenantId}/marketplaces/${marketplaceCode}/${sellerId}/remove`,{method:'POST',body:{current_password:currentPassword}});
        toast('Источник удалён. История запусков и аудит сохранены.');await refreshTenantDrawer(tenantId);await load();
      }catch(error){toast(error.message,true);}return;
    }
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
    if(billingAction){
      event.preventDefault();
      openBillingDecision(
        billingAction
      );
      return;
    }
    if(addonPaymentAction){
      event.preventDefault();
      const proofId=Number(addonPaymentAction.dataset.proofId||0);
      const action=String(addonPaymentAction.dataset.addonPaymentAction||'');
      if(!Number.isInteger(proofId)||proofId<=0||!['approve','reject'].includes(action))return;
      const reviewNote=action==='reject'?String(window.prompt('Причина отклонения оплаты:')||'').trim():'';
      if(action==='reject'&&!reviewNote){toast('Укажите причину отклонения оплаты.',true);return;}
      try{
        addonPaymentAction.disabled=true;
        await api(`/api/platform/billing/addon-payments/${proofId}/${action}`,{method:'POST',body:action==='reject'?{review_note:reviewNote}:{}});
        toast(action==='approve'?'Оплата подтверждена':'Оплата отклонена');
        await load();
      }catch(error){addonPaymentAction.disabled=false;toast(error.message,true);}
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
    const legalDraftForm=event.target.closest('#legalDocumentDraftForm');
    if(!planForm&&!addonForm&&!legalDraftForm)return;
    event.preventDefault();
    try{
      if(legalDraftForm){
        const raw=
          Object.fromEntries(
            new FormData(
              legalDraftForm
            )
          );

        const versionId=
          Number(
            raw.version_id||0
          );

        const body={
          ...raw,
          requires_acceptance:
            Boolean(
              legalDraftForm
                .elements
                .requires_acceptance
                .checked
            )
        };

        delete body.version_id;

        await api(
          versionId
            ?`/api/platform/legal-documents/drafts/${versionId}`
            :'/api/platform/legal-documents/drafts',
          {
            method:
              versionId
                ?'PUT'
                :'POST',
            body
          }
        );

        resetLegalDraftForm();

        toast(
          versionId
            ?'Черновик обновлён'
            :'Черновик сохранён'
        );
      }else if(planForm){
        const raw=Object.fromEntries(new FormData(planForm));
        const features={};planForm.querySelectorAll('[data-plan-feature]').forEach(input=>features[input.dataset.planFeature]={is_enabled:input.checked});
        const marketplaces={};planForm.querySelectorAll('[data-plan-marketplace]').forEach(row=>marketplaces[row.dataset.planMarketplace]={is_enabled:row.querySelector('[data-limit-enabled]').checked,position_limit:row.querySelector('[data-limit-positions]').value,daily_operation_limit:row.querySelector('[data-limit-daily]').value});
        const body={...raw,is_public:Boolean(raw.is_public),is_active:Boolean(raw.is_active),position_limit:raw.position_limit||null,daily_operation_limit:raw.daily_operation_limit||null,features,marketplaces};
        const id=planForm.dataset.planId;await api(id?`/api/platform/subscription-plans/${id}`:'/api/platform/subscription-plans',{method:id?'PUT':'POST',body});toast('Пакет сохранён');
      }else{
        const raw=Object.fromEntries(new FormData(addonForm));const body={...raw,is_public:Boolean(raw.is_public),is_active:Boolean(raw.is_active)};const id=addonForm.dataset.addonId;await api(id?`/api/platform/subscription-addons/${id}`:'/api/platform/subscription-addons',{method:id?'PUT':'POST',body});toast('Дополнение сохранено');
      }
      await load();
    }catch(error){toast(error.message,true)}
  });

  $('#cancelLegalDraftEdit')?.addEventListener(
    'click',
    resetLegalDraftForm
  );

  document.querySelectorAll('[data-legal-acceptance-close]').forEach(
    button=>button.addEventListener('click', closeLegalAcceptances)
  );

  $('#legalAcceptanceModal')?.addEventListener(
    'click',
    event=>{
      if(event.target===event.currentTarget){
        closeLegalAcceptances();
      }
    }
  );

  $('#billingDecisionForm')?.addEventListener(
    'submit',
    async event=>{
      event.preventDefault();

      const form=event.currentTarget;
      const invoiceId=Number(
        form.elements.invoice_id.value||0
      );
      const action=String(
        form.elements.action.value||''
      );
      const note=String(
        form.elements.note.value||''
      ).trim();

      if(
        !Number.isInteger(invoiceId)
        || invoiceId<=0
        || !['confirm','reject'].includes(action)
      ){
        toast(
          '\u041d\u0435\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043d\u0430\u044f \u043e\u043f\u0435\u0440\u0430\u0446\u0438\u044f.',
          true
        );
        return;
      }

      if(
        action==='reject'
        && !note
      ){
        toast(
          '\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u043f\u0440\u0438\u0447\u0438\u043d\u0443 \u043e\u0442\u043a\u043b\u043e\u043d\u0435\u043d\u0438\u044f.',
          true
        );
        return;
      }

      const submit=$(
        '#billingDecisionSubmit'
      );

      if(submit){
        submit.disabled=true;
      }

      try{
        const body=(
          action==='reject'
            ?{
              review_note:note
            }
            :{
              note
            }
        );

        await api(
          `/api/platform/billing/invoices/${invoiceId}/${action}`,
          {
            method:'POST',
            body
          }
        );

        toast(
          action==='confirm'
            ?'\u041e\u043f\u043b\u0430\u0442\u0430 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0430'
            :'\u041e\u043f\u043b\u0430\u0442\u0430 \u043e\u0442\u043a\u043b\u043e\u043d\u0435\u043d\u0430'
        );

        closeBillingDecision();
        await load();
      }
      catch(error){
        toast(
          error.message,
          true
        );
      }
      finally{
        if(submit){
          submit.disabled=false;
        }
      }
    }
  );

  document
    .querySelectorAll(
      '[data-billing-modal-close]'
    )
    .forEach(
      button=>{
        button.addEventListener(
          'click',
          closeBillingDecision
        );
      }
    );

  $('#billingDecisionModal')?.addEventListener(
    'click',
    event=>{
      if(
        event.target
        ===event.currentTarget
      ){
        closeBillingDecision();
      }
    }
  );

  document.addEventListener(
    'keydown',
    event=>{
      if(
        event.key==='Escape'
        && (!$('#billingDecisionModal')?.hidden||!$('#legalAcceptanceModal')?.hidden)
      ){
        closeBillingDecision();
        closeLegalAcceptances();
      }
    }
  );

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
  $('#closeTenantDetail')?.addEventListener('click', closeDrawer);
  $('#tenantDetailBackdrop')?.addEventListener('click', closeDrawer);
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

  const billingSupplierLabels={
    name:'\u041d\u0430\u0438\u043c\u0435\u043d\u043e\u0432\u0430\u043d\u0438\u0435',
    registration_number:'\u0411\u0418\u041d',
    legal_address:'\u042e\u0440\u0438\u0434\u0438\u0447\u0435\u0441\u043a\u0438\u0439 \u0430\u0434\u0440\u0435\u0441',
    iban:'IBAN',
    bank_name:'\u0411\u0430\u043d\u043a',
    bic:'\u0411\u0418\u041a',
    kbe:'\u041a\u0411\u0435',
    payment_purpose_code:'\u041a\u041d\u041f',
    invoice_prefix:'\u041f\u0440\u0435\u0444\u0438\u043a\u0441 \u0441\u0447\u0451\u0442\u0430',
    service_name:'\u041d\u0430\u0438\u043c\u0435\u043d\u043e\u0432\u0430\u043d\u0438\u0435 \u0443\u0441\u043b\u0443\u0433\u0438',
    agreement_basis:'\u041e\u0441\u043d\u043e\u0432\u0430\u043d\u0438\u0435',
    executor_name:'\u0418\u0441\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c'
  };

  function renderBillingSupplierSettings(supplier={}){
    const form=$('#billingSupplierForm');
    if(!form)return;

    [
      'name',
      'registration_number',
      'legal_address',
      'iban',
      'bank_name',
      'bic',
      'kbe',
      'payment_purpose_code',
      'invoice_prefix',
      'service_name',
      'agreement_basis',
      'executor_name',
      'vat_rate',
      'invoice_due_days'
    ].forEach(name=>{
      const input=form.elements.namedItem(name);
      if(input){
        input.value=supplier[name]??'';
      }
    });

    const vatEnabled=form.elements.namedItem('vat_enabled');
    const vatRate=form.elements.namedItem('vat_rate');

    if(vatEnabled){
      vatEnabled.checked=Boolean(supplier.vat_enabled);
    }

    if(vatRate){
      vatRate.disabled=!Boolean(supplier.vat_enabled);
    }

    const status=$('#billingSupplierStatus');
    const missingHost=$('#billingSupplierMissing');
    const missing=Array.isArray(supplier.missing_fields)
      ?supplier.missing_fields
      :[];

    if(status){
      status.className=
        `billing-supplier-status ${supplier.is_complete?'ready':'incomplete'}`;

      status.textContent=supplier.is_complete
        ?'\u0420\u0435\u043a\u0432\u0438\u0437\u0438\u0442\u044b \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d\u044b'
        :'\u0422\u0440\u0435\u0431\u0443\u0435\u0442\u0441\u044f \u0437\u0430\u043f\u043e\u043b\u043d\u0435\u043d\u0438\u0435';
    }

    if(missingHost){
      missingHost.textContent=missing.length
        ?'\u041d\u0435 \u0437\u0430\u043f\u043e\u043b\u043d\u0435\u043d\u043e: '+
          missing.map(field=>billingSupplierLabels[field]||field).join(', ')
        :'';
    }
  }

  async function loadBillingSupplierSettings(){
    const form=$('#billingSupplierForm');
    if(!form)return;

    const status=$('#billingSupplierStatus');

    try{
      if(status){
        status.className='billing-supplier-status loading-state';
        status.textContent='\u0417\u0430\u0433\u0440\u0443\u0437\u043a\u0430\u2026';
      }

      const response=await api(
        '/api/platform/billing/supplier-settings'
      );

      renderBillingSupplierSettings(
        response.supplier||{}
      );
    }catch(error){
      if(status){
        status.className='billing-supplier-status incomplete';
        status.textContent=error.message;
      }

      toast(
        error.message,
        true
      );
    }
  }

  const billingSupplierForm=$('#billingSupplierForm');

  // Requisites are secondary to review/active/history.  Keep the trusted
  // operator profile available, but place the editable billing settings at
  // the end of the payments screen and collapse them by default.
  const billingSupplierPanel=$('#billingSupplierSettings');
  const paymentsPanel=$('#payments');
  if(billingSupplierPanel&&paymentsPanel){
    const details=document.createElement('details');
    details.className='billing-supplier-disclosure';
    details.innerHTML='<summary>Реквизиты и настройки счёта</summary>';
    paymentsPanel.after(details);
    details.append(billingSupplierPanel);
  }

  billingSupplierForm?.elements
    .namedItem('vat_enabled')
    ?.addEventListener('change',event=>{
      const rate=billingSupplierForm.elements.namedItem('vat_rate');

      if(rate){
        rate.disabled=!event.target.checked;

        if(!event.target.checked){
          rate.value='0';
        }
      }
    });

  function requestBillingSupplierPassword(){
    const modal=$('#billingSupplierPasswordModal');
    const form=$('#billingSupplierPasswordForm');
    if(!modal||!form)return Promise.resolve(null);
    return new Promise(resolve=>{
      const close=()=>{
        modal.hidden=true;
        form.reset();
        form.onsubmit=null;
        modal.querySelectorAll('[data-billing-supplier-password-close]').forEach(node=>node.onclick=null);
        resolve(null);
      };
      modal.hidden=false;
      modal.querySelectorAll('[data-billing-supplier-password-close]').forEach(node=>node.onclick=close);
      form.onsubmit=event=>{
        event.preventDefault();
        const password=String(new FormData(form).get('current_password')||'');
        modal.hidden=true;
        form.reset();
        form.onsubmit=null;
        modal.querySelectorAll('[data-billing-supplier-password-close]').forEach(node=>node.onclick=null);
        resolve(password||null);
      };
      window.setTimeout(()=>form.elements.namedItem('current_password')?.focus(),0);
    });
  }

  billingSupplierForm?.addEventListener('submit',async event=>{
    event.preventDefault();

    const form=event.currentTarget;
    const button=$('#saveBillingSupplier');

    try{
      if(button){
        button.disabled=true;
      }

      const editableFields=[
        'name',
        'registration_number',
        'legal_address',
        'iban',
        'bank_name',
        'bic',
        'kbe',
        'payment_purpose_code',
        'invoice_prefix',
        'invoice_due_days',
        'service_name',
        'agreement_basis',
        'executor_name',
        'vat_rate'
      ];

      const body=Object.fromEntries(
        editableFields.map(name=>[
          name,
          form.elements.namedItem(name)?.value??''
        ])
      );

      body.vat_enabled=Boolean(
        form.elements.namedItem('vat_enabled')?.checked
      );

      body.vat_rate=Number(
        body.vat_rate||0
      );

      body.invoice_due_days=Number(
        body.invoice_due_days||5
      );

      const currentPassword=await requestBillingSupplierPassword();
      if(!currentPassword){
        return;
      }
      body.current_password=currentPassword;

      const response=await api(
        '/api/platform/billing/supplier-settings',
        {
          method:'PUT',
          body
        }
      );

      renderBillingSupplierSettings(
        response.supplier||{}
      );

      toast(
        '\u0420\u0435\u043a\u0432\u0438\u0437\u0438\u0442\u044b \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u044b'
      );
    }catch(error){
      toast(
        error.message,
        true
      );
    }finally{
      if(button){
        button.disabled=false;
      }
    }
  });

  $('#refreshPlatform')?.addEventListener('click',()=>{
    void loadBillingSupplierSettings();
  });

  void loadBillingSupplierSettings();

  load();
})();
