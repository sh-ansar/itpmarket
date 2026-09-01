(() => {
  'use strict';
  const host = document.querySelector('#tenantMarketplaceCards');
  if (!host) return;
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const t = (key, fallback) => window.ITPUI?.t(key, fallback) || fallback || key;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const checked = new Map();
  const canManage = Boolean(window.ITP_USER?.permissions?.manage_marketplaces);

  async function request(url, options = {}) {
    const response = await fetch(url, {
      ...options,
      headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...(options.headers || {})},
      body:options.body && typeof options.body !== 'string' ? JSON.stringify(options.body) : options.body
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  function notify(message, error = false) {
    const stack = document.querySelector('#toasts');
    if (!stack) return window.alert(message);
    const node = document.createElement('div');
    node.className = `toast${error ? ' error' : ''}`;
    node.textContent = message;
    stack.append(node);
    setTimeout(() => node.remove(), 4500);
  }

  function card(item) {
    const result = checked.get(item.code);
    const approval=item.approval_status||'draft';
    const activeSellers=(item.sellers||[]).filter(seller=>seller.status==='active'&&seller.approval_status==='approved');
    const sourceActions=canManage&&activeSellers.length ? `<div class="marketplace-source-list">${activeSellers.map(seller=>`<div class="marketplace-discovery-note"><strong>${esc(seller.display_name||seller.external_seller_id)}</strong><br><a href="${esc(seller.source_url)}" target="_blank" rel="noreferrer">${esc(seller.source_url)}</a><div class="settings-inline-actions"><button type="button" class="secondary" data-marketplace-replace data-seller-id="${Number(seller.id)}">Заменить источник</button><button type="button" class="decline" data-marketplace-remove data-seller-id="${Number(seller.id)}">Удалить источник</button></div></div>`).join('')}</div>` : '';
    const statusText=item.is_connected?t('marketplace_connected','Подключено'):approval==='pending'?'Ожидает подтверждения':approval==='rejected'?'Отклонено — можно отправить заново':t('marketplace_not_connected','Не подключено');
    return `<article class="settings-card tenant-marketplace-card ${item.is_connected ? 'connected' : ''}" data-marketplace-code="${esc(item.code)}">
      <div class="marketplace-card-head"><div><h3>${esc(item.name)}</h3></div><span class="marketplace-approval ${esc(approval)}">${esc(statusText)}</span></div>
      <p>${esc(item.description || '')}</p>
      ${item.is_connected || activeSellers.length ? `<div class="marketplace-discovery-note"><strong>${esc(item.seller_name || item.seller_identifier || activeSellers[0]?.display_name || activeSellers[0]?.external_seller_id)}</strong><br><a href="${esc(item.seller_url || activeSellers[0]?.source_url)}" target="_blank" rel="noreferrer">${esc(item.seller_url || activeSellers[0]?.source_url)}</a></div>${sourceActions}` : canManage ? `
        ${approval==='pending'?`<div class="marketplace-discovery-note"><strong>${esc(item.seller_name||item.seller_identifier)}</strong><br>Заявка отправлена супер-администратору. После подтверждения площадка появится в каталоге и операциях.</div>`:''}
        ${approval==='rejected'&&item.review_note?`<div class="marketplace-limitation">Причина: ${esc(item.review_note)}</div>`:''}
        <label>${esc(t('marketplace_source_input','Ссылка, ID продавца или slug'))}<input data-marketplace-source type="text" required value="${esc(result?.source_input || result?.seller_url || '')}" placeholder="${esc(item.connection_fields?.[0]?.placeholder || 'Ссылка или ID продавца')}"></label>
        ${item.source_examples?.length ? `<small class="marketplace-source-examples">${esc(t('marketplace_source_examples','Примеры'))}: ${esc(item.source_examples.join(' · '))}</small>` : ''}
        <div class="settings-inline-actions"><button type="button" class="secondary" data-marketplace-check>${esc(t('marketplace_check_source','Распознать'))}</button></div>
        ${result ? `<div class="marketplace-discovery-note"><strong>${esc(result.marketplace_name)}</strong><br>${esc(result.source_scope === 'product' ? t('marketplace_product_found','Найдена карточка товара') : t('marketplace_seller_found','Найден продавец'))}: ${esc(result.seller_name)}<br><small>ID: ${esc(result.seller_identifier)}${result.product_id ? ` · productId: ${esc(result.product_id)}` : ''}</small><br><a href="${esc(result.seller_url)}" target="_blank" rel="noreferrer">${esc(result.seller_url)}</a><div class="settings-inline-actions"><button type="button" class="primary" data-marketplace-connect>${esc(t('marketplace_confirm_connect','Подтвердить подключение'))}</button></div></div>` : ''}
      ` : `<div class="marketplace-limitation">${esc(t('marketplace_not_connected','Не подключено'))}</div>`}
    </article>`;
  }

  async function load() {
    try {
      const data = await request('/api/tenant?include_unavailable=1');
      const marketplaces = data.marketplace_access || [];
      host.innerHTML = marketplaces.length ? marketplaces.map(card).join('') : `<div class="empty">${esc(t('marketplace_no_access','Супер-администратор ещё не выдал компании доступные площадки.'))}</div>`;
      window.ITPUI?.translateTree(host);
    } catch (error) { host.innerHTML = `<div class="empty">${esc(error.message)}</div>`; }
  }

  host.addEventListener('click', async event => {
    const button = event.target.closest('button');
    const cardNode = event.target.closest('[data-marketplace-code]');
    if (!button || !cardNode) return;
    const code = cardNode.dataset.marketplaceCode;
    const source = cardNode.querySelector('[data-marketplace-source]')?.value.trim() || checked.get(code)?.source_input || checked.get(code)?.seller_url || '';
    button.disabled = true;
    try {
      if (button.matches('[data-marketplace-check]')) {
        const data = await request('/api/tenant/marketplaces/check', {method:'POST', body:{marketplace_code:code, source}});
        checked.set(code, data.result);
        notify(t('marketplace_url_verified','Ссылка проверена. Подтвердите подключение.'));
      } else if (button.matches('[data-marketplace-connect]')) {
        await request('/api/tenant/marketplaces/connect', {method:'POST', body:{marketplace_code:code, source}});
        checked.delete(code);
        notify('Заявка на подключение отправлена супер-администратору.');
      } else if (button.matches('[data-marketplace-replace]')) {
        const replacement=String(window.prompt('Новый URL продавца:')||'').trim();
        if (!replacement) return;
        await request(`/api/tenant/marketplaces/${encodeURIComponent(code)}/${Number(button.dataset.sellerId)}/replace`, {method:'POST', body:{source:replacement}});
        notify('Источник заменён после проверки.');
      } else if (button.matches('[data-marketplace-remove]')) {
        if (!window.confirm('Удалить источник и его текущие собранные данные? История запусков и аудит сохранятся.')) return;
        const currentPassword=String(window.prompt('Подтвердите текущим паролем:')||'');
        if (!currentPassword) return;
        await request(`/api/tenant/marketplaces/${encodeURIComponent(code)}/${Number(button.dataset.sellerId)}/remove`, {method:'POST', body:{current_password:currentPassword}});
        notify('Источник удалён. История запусков и аудит сохранены.');
      }
      await load();
    } catch (error) { notify(error.message, true); button.disabled = false; }
  });

  document.addEventListener('click', event => { if (event.target.closest('[data-page="settings"]')) setTimeout(load, 0); });
  window.ITPUI?.onLocale?.(load);
  load();
})();
