(() => {
  'use strict';
  const host = document.querySelector('#tenantMarketplaceCards');
  if (!host) return;
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const t = (key, fallback) => window.ITPUI?.t(key, fallback) || fallback || key;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const checked = new Map();
  const canManage = Boolean(window.ITP_USER?.permissions?.manage_marketplaces);
  const modal = document.querySelector('#marketplaceSourceModal');
  const modalTitle = document.querySelector('#marketplaceSourceModalTitle');
  const modalBody = document.querySelector('#marketplaceSourceModalBody');
  let marketplaces = [];
  let sourceAction = null;

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

  function render(items) {
    marketplaces = Array.isArray(items) ? items : [];
    host.innerHTML = marketplaces.length ? marketplaces.map(card).join('') : `<div class="empty">${esc(t('marketplace_no_access','Поддерживаемые площадки пока не настроены в системном реестре.'))}</div>`;
    window.ITPUI?.translateTree(host);
    window.dispatchEvent(
      new CustomEvent(
        'spyon:marketplaces-changed'
      )
    );

  }

  function closeSourceModal() {
    sourceAction = null;
    if (modal) modal.hidden = true;
  }

  function openSourceModal(kind, marketplace, seller) {
    if (!modal || !modalTitle || !modalBody) return;
    sourceAction = {kind, marketplace, seller, verified:null};
    modalTitle.textContent = kind === 'replace' ? 'Заменить источник' : 'Удалить источник';
    if (kind === 'replace') {
      modalBody.innerHTML = `<form id="marketplaceReplaceForm" class="marketplace-modal-form"><p>Введите новый URL или ID продавца и проверьте его перед заменой.</p><label>Новый URL или ID<input name="source" required autocomplete="url"></label><div id="marketplaceReplacementPreview" class="marketplace-discovery-note" hidden></div><div class="modal-actions"><button type="button" class="secondary" data-marketplace-modal-close>Отмена</button><button type="submit" class="secondary" data-modal-verify>Проверить</button><button type="button" class="primary" data-modal-replace disabled>Заменить источник</button></div></form>`;
    } else {
      modalBody.innerHTML = `<form id="marketplaceRemoveForm" class="marketplace-modal-form"><p class="marketplace-danger-copy">Источник и связанные текущие собранные данные будут удалены или отвязаны по действующим правилам. История операций и аудит сохранятся.</p><dl class="marketplace-source-summary"><dt>Площадка</dt><dd>${esc(marketplace.name)}</dd><dt>Продавец</dt><dd>${esc(seller.display_name||seller.external_seller_id||'—')}</dd><dt>Источник</dt><dd>${esc(seller.source_url||'—')}</dd></dl><label>Текущий пароль<input name="current_password" type="password" required autocomplete="current-password"></label><div class="modal-actions"><button type="button" class="secondary" data-marketplace-modal-close>Отмена</button><button type="submit" class="danger">Удалить источник</button></div></form>`;
    }
    modal.hidden = false;
    modal.querySelector('input')?.focus();
  }

  function card(item) {
    const result = checked.get(item.code);
    const approval=item.approval_status||'draft';
    const activeSellers=(item.sellers||[]).filter(seller=>seller.status==='active'&&seller.approval_status==='approved');
    const sellerBlocks=activeSellers.length ? `<div class="marketplace-source-list">${activeSellers.map(seller=>`<div class="marketplace-discovery-note"><strong>${esc(seller.display_name||seller.external_seller_id)}</strong><br><a href="${esc(seller.source_url)}" target="_blank" rel="noreferrer">${esc(seller.source_url)}</a>${canManage?`<div class="marketplace-source-actions"><button type="button" class="marketplace-source-action marketplace-source-action--replace" data-marketplace-replace data-seller-id="${Number(seller.id)}">Заменить источник</button><button type="button" class="marketplace-source-action marketplace-source-action--remove" data-marketplace-remove data-seller-id="${Number(seller.id)}">Удалить источник</button></div>`:''}</div>`).join('')}</div>` : '';
    const statusText=item.is_connected?t('marketplace_connected','Подключено'):approval==='pending'?'Источник требует повторной проверки':approval==='rejected'?'Отклонено — можно отправить заново':t('marketplace_not_connected','Не подключено');
    return `<article class="settings-card tenant-marketplace-card ${item.is_connected ? 'connected' : ''}" data-settings-section="marketplaces" data-marketplace-code="${esc(item.code)}">
      <div class="marketplace-card-head"><div><h3>${esc(item.name)}</h3></div><span class="marketplace-approval ${esc(approval)}">${esc(statusText)}</span></div>
      <p>${esc(item.description || '')}</p>
      ${item.is_connected || activeSellers.length ? sellerBlocks : canManage ? `
        ${approval==='pending'?`<div class="marketplace-discovery-note"><strong>${esc(item.seller_name||item.seller_identifier)}</strong><br>Источник требует повторной проверки. Выполните распознавание и подтвердите подключение ещё раз.</div>`:''}
        ${approval==='rejected'&&item.review_note?`<div class="marketplace-limitation">Причина: ${esc(item.review_note)}</div>`:''}
        <label>${esc(t('marketplace_source_input','Ссылка, ID продавца или slug'))}<input data-marketplace-source type="text" required value="${esc(result?.source_input || result?.seller_url || '')}" placeholder="${esc(item.connection_fields?.[0]?.placeholder || 'Ссылка или ID продавца')}"></label>
        ${item.source_examples?.length ? `<small class="marketplace-source-examples">${esc(t('marketplace_source_examples','Примеры'))}: ${esc(item.source_examples.join(' · '))}</small>` : ''}
        <div class="settings-inline-actions"><button type="button" class="secondary" data-marketplace-check>${esc(t('marketplace_check_source','Распознать'))}</button></div>
        <div class="marketplace-discovery-note" data-marketplace-check-status hidden></div>
        ${result ? `<div class="marketplace-discovery-note"><strong>${esc(result.verified && (item.code==='ozon'||item.code==='ozon_kz') ? 'Магазин подтверждён' : result.marketplace_name)}</strong><br>${esc(result.source_scope === 'product' ? t('marketplace_product_found','Найдена карточка товара') : t('marketplace_seller_found','Найден продавец'))}: ${esc(result.seller_name)}<br><small>ID: ${esc(result.seller_identifier)}${result.product_id ? ` · productId: ${esc(result.product_id)}` : ''}</small><br><a href="${esc(result.seller_url)}" target="_blank" rel="noreferrer">${esc(result.seller_url)}</a><div class="settings-inline-actions"><button type="button" class="primary" data-marketplace-connect>${esc(t('marketplace_confirm_connect','Подтвердить подключение'))}</button></div></div>` : ''}
      ` : `<div class="marketplace-limitation">${esc(t('marketplace_not_connected','Не подключено'))}</div>`}
    </article>`;
  }

  async function load() {
    try {
      const data = await request(
        `/api/tenant?include_unavailable=1&_=${Date.now()}`,
        {cache:'no-store'}
      );

      const access = Array.isArray(data.marketplace_access)
        ? data.marketplace_access
        : [];

      const registry = Array.isArray(data.integration_catalog)
        ? data.integration_catalog
        : [];

      if (!registry.length) {
        render(access);
        return;
      }

      const liveByCode = new Map(
        access.map(item => [String(item.code || ''), item])
      );

      render(
        registry.map(definition => ({
          ...definition,
          ...(liveByCode.get(String(definition.code || '')) || {})
        }))
      );
    } catch (error) {
      host.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    }
  }
  host.addEventListener('click', async event => {
    const button = event.target.closest('button');
    const cardNode = event.target.closest('[data-marketplace-code]');
    if (!button || !cardNode) return;
    const code = cardNode.dataset.marketplaceCode;
    const marketplace = marketplaces.find(item => item.code === code);
    const source = cardNode.querySelector('[data-marketplace-source]')?.value.trim() || checked.get(code)?.source_input || checked.get(code)?.seller_url || '';
    const isCheck = button.matches('[data-marketplace-check]');
    const isOzon = code === 'ozon' || code === 'ozon_kz';
    const checkStatus = cardNode.querySelector('[data-marketplace-check-status]');
    const idleButtonText = button.textContent;
    button.disabled = true;
    if (isCheck && isOzon) {
      button.textContent = 'Проверяем магазин…';
      if (checkStatus) {
        checkStatus.textContent = 'Проверяем магазин Ozon. Это может занять несколько секунд.';
        checkStatus.hidden = false;
      }
    }
    try {
      if (isCheck) {
        const data = await request('/api/tenant/marketplaces/check', {
          method:'POST',
          body:{
            marketplace_code:code,
            source
          }
        });

        const verified = data.result || {};
        checked.set(code, verified);

        if (isOzon) {
          if (verified.catalogue_empty === true) {
            checked.delete(code);

            if (checkStatus) {
              checkStatus.textContent =
                'Магазин найден, но товары не обнаружены. Подключение не выполнено.';
              checkStatus.hidden = false;
            }

            button.disabled = false;
            button.textContent = idleButtonText;

            notify(
              'Магазин Ozon найден, но товары не обнаружены.',
              true
            );

            return;
          }

          if (!verified.verification_proof) {
            throw new Error(
              'Ozon verification proof was not returned.'
            );
          }

          if (checkStatus) {
            checkStatus.textContent =
              'Магазин подтверждён. Подключаем…';
            checkStatus.hidden = false;
          }

          await request(
            '/api/tenant/marketplaces/connect',
            {
              method:'POST',
              body:{
                marketplace_code:code,
                source,
                verification_proof:verified.verification_proof
              }
            }
          );

          checked.delete(code);

          await load();

          notify(
            'Магазин Ozon подтверждён и подключён.'
          );

          return;
        }

        notify(
          t(
            'marketplace_url_verified',
            'Ссылка проверена. Подтвердите подключение.'
          )
        );
      } else if (button.matches('[data-marketplace-connect]')) {
        const data = await request('/api/tenant/marketplaces/connect', {method:'POST', body:{marketplace_code:code, source, verification_proof:checked.get(code)?.verification_proof || ''}});
        checked.delete(code);
        await load();
        notify(t('marketplace_connected','Магазин подключён.'));
      } else if (button.matches('[data-marketplace-replace]')) {
        const seller=(marketplace?.sellers||[]).find(item=>Number(item.id)===Number(button.dataset.sellerId));
        if (seller) openSourceModal('replace', marketplace, seller);
      } else if (button.matches('[data-marketplace-remove]')) {
        const seller=(marketplace?.sellers||[]).find(item=>Number(item.id)===Number(button.dataset.sellerId));
        if (seller) openSourceModal('remove', marketplace, seller);
      }
      if (!button.matches('[data-marketplace-replace],[data-marketplace-remove],[data-marketplace-connect]')) await load();
    } catch (error) {
      notify(error.message, true);
      button.disabled = false;
      if (isCheck && isOzon) {
        button.textContent = idleButtonText;
        if (checkStatus) checkStatus.hidden = true;
      }
    }
  });

  modal?.addEventListener('click', event => {
    if (event.target === modal || event.target.closest('[data-marketplace-modal-close]')) closeSourceModal();
  });
  modal?.addEventListener('submit', async event => {
    event.preventDefault();
    if (!sourceAction) return;
    const form = event.target;
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      if (sourceAction.kind === 'replace') {
        const source = String(new FormData(form).get('source') || '').trim();
        const data = await request('/api/tenant/marketplaces/check', {method:'POST', body:{marketplace_code:sourceAction.marketplace.code, source}});
        sourceAction.verified = {source, result:data.result};
        const found = data.result || {};
        const preview = modalBody.querySelector('#marketplaceReplacementPreview');
        preview.innerHTML = `<strong>${esc(found.marketplace_name || sourceAction.marketplace.name)}</strong><br>${esc(found.seller_name || found.seller_identifier || '')}<br><a href="${esc(found.seller_url || '')}" target="_blank" rel="noreferrer">${esc(found.seller_url || '')}</a>`;
        preview.hidden = false;
        modalBody.querySelector('[data-modal-replace]').disabled = false;
      } else {
        const currentPassword = String(new FormData(form).get('current_password') || '');
        const data = await request(`/api/tenant/marketplaces/${encodeURIComponent(sourceAction.marketplace.code)}/${Number(sourceAction.seller.id)}/remove`, {method:'POST', body:{current_password:currentPassword}});
        await load(); closeSourceModal(); notify('Источник удалён. История операций и аудит сохранены.');
      }
    } catch (error) { notify(error.message, true); }
    finally { if (submit.isConnected) submit.disabled = false; }
  });
  modalBody?.addEventListener('click', async event => {
    const button = event.target.closest('[data-modal-replace]');
    if (!button || !sourceAction?.verified) return;
    button.disabled = true;
    try {
      const data = await request(`/api/tenant/marketplaces/${encodeURIComponent(sourceAction.marketplace.code)}/${Number(sourceAction.seller.id)}/replace`, {method:'POST', body:{source:sourceAction.verified.source}});
      await load(); closeSourceModal(); notify('Источник заменён после проверки.');
    } catch (error) { notify(error.message, true); button.disabled = false; }
  });

  document.addEventListener('click', event => { if (event.target.closest('[data-page="settings"]')) setTimeout(load, 0); });
  window.ITPUI?.onLocale?.(load);
  load();
})();
