(() => {
  'use strict';

  const drawer = document.querySelector('#tenantDetailDrawer');
  const backdrop = document.querySelector('#tenantDetailBackdrop');
  const title = document.querySelector('#tenantDetailTitle');
  const body = document.querySelector('#tenantDetailBody');
  if (!drawer || !body) return;

  const t = (key, fallback) => window.ITPUI?.t(key, fallback) || fallback || key;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const api = (...args) => window.PlatformAdmin.api(...args);
  const toast = (...args) => window.PlatformAdmin.toast(...args);
  const permissionKeys = {
    view_dashboard:'permission_view_dashboard', view_products:'permission_view_products',
    manage_products:'permission_manage_products', view_operations:'permission_view_operations',
    run_operations:'permission_run_operations', manage_operations:'permission_manage_operations',
    view_reports:'permission_view_reports', create_reports:'permission_create_reports',
    view_settings:'permission_view_settings', manage_company:'permission_manage_company',
    manage_marketplaces:'permission_manage_marketplaces', manage_filters:'permission_manage_filters',
    manage_users:'permission_manage_users', view_help:'permission_view_help'
  };
  const permissionFallbacks = {
    view_dashboard:'Обзор', view_products:'Просмотр товаров', manage_products:'Изменение товаров',
    view_operations:'Просмотр операций', run_operations:'Запуск операций', manage_operations:'Управление операциями',
    view_reports:'Просмотр отчётов', create_reports:'Создание отчётов', view_settings:'Просмотр настроек',
    manage_company:'Профиль компании', manage_marketplaces:'Подключение площадок', manage_filters:'Фильтры каталога',
    manage_users:'Пользователи и права', view_help:'Справка'
  };
  let currentTenantId = 0;
  let snapshot = null;

  function marketplaceGrant(item, approved) {
    const state = item.is_connected
      ? t('marketplace_connected', 'Подключено')
      : item.is_allowed
        ? t('marketplace_available', 'Доступно для подключения')
        : t('marketplace_not_granted', 'Не выдано');
    return `<label class="marketplace-grant ${item.is_connected ? 'connected' : ''}">
      <input type="checkbox" data-marketplace-grant="${esc(item.code)}" ${item.is_allowed ? 'checked' : ''} ${approved ? '' : 'disabled'}>
      <span><strong>${esc(item.name)}</strong><small>${esc(state)}</small></span>
    </label>`;
  }

  function connection(item) {
    const state = item.is_connected
      ? t('marketplace_connected', 'Подключено')
      : item.approval_status === 'pending'
        ? 'Ожидает подтверждения'
      : item.approval_status === 'rejected'
        ? 'Отклонено'
      : item.is_allowed
        ? t('marketplace_not_connected', 'Не подключено')
        : t('marketplace_unavailable', 'Недоступно');
    return `<article class="company-connection ${esc(item.connection_status)}">
      <div><strong>${esc(item.name)}</strong><span>${esc(state)}</span></div>
      ${item.seller_identifier ? `<small>${esc(item.seller_name || item.seller_identifier || t('marketplace_seller_detected', 'Продавец определён'))}</small><a href="${esc(item.seller_url)}" target="_blank" rel="noreferrer">${esc(t('marketplace_open_store', 'Открыть магазин'))}</a>` : ''}
      ${item.review_note ? `<small class="connection-review-note">${esc(item.review_note)}</small>` : ''}
      ${item.approval_status === 'pending' ? `<div class="platform-row-actions"><button class="approve" data-marketplace-review="approved" data-marketplace-code="${esc(item.code)}">Подтвердить</button><button class="decline" data-marketplace-review="rejected" data-marketplace-code="${esc(item.code)}">Отклонить</button></div>` : ''}
    </article>`;
  }

  function sellerManagementCard(seller) {
    const id=Number(
      seller.id||
      seller.tenant_seller_id||
      0
    );

    const code=String(
      seller.marketplace_code||
      ''
    );

    const marketplaceName=({
      kaspi:'Kaspi',
      ozon:'Ozon.ru',
      ozon_kz:'Ozon.kz',
      halyk_market:'Halyk Market',
      forte:'Forte Market',
      wildberries:'Wildberries'
    })[code]||code;

    const active=(
      String(seller.status||'')==='active' &&
      String(seller.approval_status||'')==='approved'
    );

    const sourceUrl=String(
      seller.source_url||
      ''
    );

    return `<article class="company-connection ${active?'connected':''}">
      <div>
        <strong>${esc(marketplaceName)}</strong>
        <span>${esc(seller.status||'—')}</span>
      </div>

      <small>
        ${esc(
          seller.display_name||
          seller.external_seller_id||
          '—'
        )}
      </small>

      <small>
        ID: ${esc(seller.external_seller_id||'—')}
        · verification: ${esc(seller.discovery_status||'parsed')}
        · approval: ${esc(seller.approval_status||'—')}
      </small>

      ${sourceUrl
        ?`<a href="${esc(sourceUrl)}"
             target="_blank"
             rel="noreferrer">
             Открыть магазин
           </a>`
        :''
      }

      ${active&&id
        ?`<div class="platform-row-actions">
            <button type="button"
                    class="primary"
                    data-replace-source
                    data-tenant-id="${Number(currentTenantId)}"
                    data-marketplace-code="${esc(code)}"
                    data-seller-id="${id}">
              Заменить источник
            </button>

            <button type="button"
                    class="decline"
                    data-purge-seller
                    data-tenant-id="${Number(currentTenantId)}"
                    data-marketplace-code="${esc(code)}"
                    data-seller-id="${id}">
              Очистить данные
            </button>
          </div>`
        :''
      }
    </article>`;
  }

  function userEditor(user) {
    const permissions = Object.entries(permissionKeys).map(([code, key]) => `<label><input type="checkbox" data-user-permission="${esc(code)}" ${user.permissions?.[code] ? 'checked' : ''}>${esc(t(key, permissionFallbacks[code]))}</label>`).join('');
    return `<article class="platform-user-editor" data-user-id="${Number(user.id)}">
      <div class="platform-user-editor-head"><div><b>${esc(user.display_name)}</b><small>${esc(user.email)}</small></div><span>${esc(user.is_active ? t('active', 'Активен') : t('disabled', 'Отключён'))}</span></div>
      <div class="platform-form-grid">
        <label>${esc(t('name', 'Имя'))}<input data-user-name value="${esc(user.display_name)}" required></label>
        <label>${esc(t('role', 'Роль'))}<select data-user-role><option value="admin" ${user.role === 'admin' ? 'selected' : ''}>${esc(t('role_admin', 'Администратор'))}</option><option value="operator" ${user.role === 'operator' ? 'selected' : ''}>${esc(t('role_operator', 'Оператор'))}</option><option value="viewer" ${user.role === 'viewer' ? 'selected' : ''}>${esc(t('role_viewer', 'Наблюдатель'))}</option></select></label>
      </div>
      <label class="platform-active-check"><input type="checkbox" data-user-active ${user.is_active ? 'checked' : ''}>${esc(t('account_active', 'Учётная запись активна'))}</label>
      <details><summary>${esc(t('permissions', 'Разрешения'))}</summary><div class="platform-check-grid permissions">${permissions}</div></details>
      <div class="platform-row-actions"><button class="primary" data-user-save>${esc(t('save', 'Сохранить'))}</button><button data-user-recovery>${esc(t('new_recovery_code', 'Новый код восстановления'))}</button></div>
    </article>`;
  }

  function missingFieldLabel(value) {
    return ({
      'Название компании':t('company_name', 'Название компании'),
      'Регистрационный номер / БИН':t('company_registration_number', 'Регистрационный номер / БИН'),
      'Email компании':t('company_email', 'Email компании'),
      'Телефон компании':t('company_phone', 'Телефон компании')
    })[value] || value;
  }

  function render(data) {
    snapshot = data;
    const tenant = data.tenant || {};
    const approved = tenant.status === 'approved';
    const missing = (tenant.profile_missing || []).map(missingFieldLabel);
    const entitlement=data.subscription?.entitlement||{};
    const currentPlan=entitlement.subscription||{};
    const quotaRows=Object.entries(entitlement.marketplaces||{}).map(([code,value])=>`<div class="company-connection ${value.enabled?'connected':''}"><div><b>${esc(code)}</b><span>${value.enabled?'Доступна':'Выключена'}</span></div><small>Позиции ${Number(value.positions_used||0)} / ${value.position_limit==null?'∞':Number(value.position_limit)} · запуски ${Number(value.daily_operations_used||0)} / ${value.daily_operation_limit==null?'∞':Number(value.daily_operation_limit)}</small></div>`).join('');
    const packagePlans=(data.subscription?.plans||[]).filter(plan=>plan.code!=='legacy');
    const scheduledPackage=(data.subscription?.requests||[]).find(item=>item.status==='scheduled');
    const packageCode=scheduledPackage?.plan_code||currentPlan.plan_code||packagePlans[0]?.code||'';
    const packageOptions=packagePlans.map(plan=>`<option value="${esc(plan.code)}" ${plan.code===packageCode?'selected':''}>${esc(plan.name)} · ${Number(plan.price_amount||0).toLocaleString()} ${esc(plan.currency||'KZT')}</option>`).join('');
    const packageStart=String(scheduledPackage?.starts_at||currentPlan.ends_at||'').slice(0,16);
    const packageEnd=String(scheduledPackage?.ends_at||'').slice(0,16);
    title.textContent = tenant.name || t('company_profile', 'Компания');
    body.innerHTML = `
      <section class="detail-section platform-company-editor">
        <div class="platform-section-title"><div><h3>${esc(t('platform_company_details', 'Данные компании'))}</h3><p>${esc(t('platform_company_required', 'Реквизиты обязательны для подтверждения.'))}</p></div><span class="tenant-status ${esc(tenant.status)}">${esc(tenant.status_label || tenant.status)}</span></div>
        ${missing.length ? `<div class="platform-warning">${esc(t('platform_missing_fields', 'Не заполнено'))}: ${missing.map(esc).join(', ')}</div>` : ''}
        <form id="platformCompanyForm" class="platform-form-grid">
          <label>${esc(t('company_name', 'Название компании'))}<input name="name" value="${esc(tenant.name)}" required></label>
          <label>${esc(t('company_registration_number', 'Регистрационный номер / БИН'))}<input name="registration_number" value="${esc(tenant.registration_number)}" required></label>
          <label>${esc(t('company_email', 'Email компании'))}<input name="contact_email" type="email" value="${esc(tenant.contact_email)}" required></label>
          <label>${esc(t('company_phone', 'Телефон компании'))}<input name="contact_phone" value="${esc(tenant.contact_phone)}" required></label>
          <label>${esc(t('company_legal_address', 'Legal address'))}<input name="legal_address" value="${esc(tenant.legal_address || '')}"></label>
          <label>${esc(t('company_actual_address', 'Actual address'))}<input name="actual_address" value="${esc(tenant.actual_address || '')}"></label>
          <label>${esc(t('status', 'Статус'))}<select name="status"><option value="pending" ${tenant.status === 'pending' ? 'selected' : ''}>${esc(t('company_pending', 'На рассмотрении'))}</option><option value="approved" ${tenant.status === 'approved' ? 'selected' : ''}>${esc(t('company_approved', 'Подтверждена'))}</option><option value="rejected" ${tenant.status === 'rejected' ? 'selected' : ''}>${esc(t('company_rejected', 'Отклонена'))}</option><option value="blocked" ${tenant.status === 'blocked' ? 'selected' : ''}>${esc(t('company_blocked', 'Заблокирована'))}</option></select></label>
          <button class="primary" type="submit">${esc(t('save', 'Сохранить'))}</button>
        </form>
      </section>
      <section class="detail-section">
        <div class="platform-section-title"><div><h3>Пакет и лимиты</h3><p>${entitlement.active?`${esc(currentPlan.plan_name||currentPlan.plan_code)} · ${Number(currentPlan.price_amount||0).toLocaleString()} ${esc(currentPlan.currency||'KZT')} · до ${esc(currentPlan.ends_at||'—')}`:esc(entitlement.message||'Нет активного пакета')}</p>${scheduledPackage?`<small>Следующий пакет: ${esc(scheduledPackage.plan_name||scheduledPackage.plan_code)} · с ${esc(scheduledPackage.starts_at||'—')} до ${esc(scheduledPackage.ends_at||'—')}</small>`:''}</div></div>
        <form id="platformSubscriptionForm" class="platform-form-grid">
          <label>Пакет<select name="plan_code" required>${packageOptions}</select></label>
          <label>Действует с<input name="starts_at" type="datetime-local" value="${esc(packageStart)}"></label>
          <label>Действует до<input name="ends_at" type="datetime-local" value="${esc(packageEnd)}"></label>
          <label>Цена<input name="price_amount" type="number" min="0" step="0.01"></label>
          <label class="wide">Комментарий<input name="review_note"></label>
          <button class="primary" type="submit">Применить / запланировать</button>
        </form>
        <div class="company-connections">${quotaRows||'<div>Лимиты ещё не назначены.</div>'}</div>
      </section>
      <section class="detail-section">
        <div class="platform-section-title"><div><h3>${esc(t('platform_available_marketplaces', 'Доступные площадки'))}</h3><p>${esc(t('platform_available_marketplaces_help', 'Это разрешения компании. Подключение выполняет её администратор по ссылке на магазин.'))}</p><small>${esc(t('platform_requested_marketplaces', 'Запрошено при регистрации'))}: ${esc((tenant.workspace_profile?.selected_integration_names || []).join(', ') || '—')}</small></div></div>
        ${approved ? '' : `<div class="platform-warning">${esc(t('platform_confirm_company_first', 'Сначала подтвердите компанию.'))}</div>`}
        <div class="marketplace-grant-grid">${(data.marketplace_access || []).map(item => marketplaceGrant(item, approved)).join('')}</div>
        <div class="platform-row-actions"><button class="primary" data-save-marketplace-grants ${approved ? '' : 'disabled'}>${esc(t('platform_save_marketplaces', 'Сохранить площадки'))}</button></div>
      </section>
      <section class="detail-section">
        <div class="platform-section-title">
          <div>
            <h3>${esc(t('platform_connections', 'Подключения'))}</h3>
            <p>${esc(t('platform_connections_help', 'Фактические подключения компании.'))}</p>
          </div>
        </div>

        <div class="company-connections">
          ${(data.marketplace_access || []).map(connection).join('')}
        </div>

        ${(data.sellers||[]).length
          ?`<div class="platform-section-title" style="margin-top:18px">
              <div>
                <h3>Источники продавцов</h3>
                <p>
                  Управление фактически подключёнными магазинами.
                </p>
              </div>
            </div>

            <div class="company-connections">
              ${(data.sellers||[])
                .map(sellerManagementCard)
                .join('')}
            </div>`
          :''
        }

        <div class="platform-row-actions" style="margin-top:16px">
          <button type="button"
                  data-billing-history
                  data-tenant-id="${Number(currentTenantId)}">
            История платежей
          </button>
        </div>
      </section>
      <section class="detail-section"><div class="platform-section-title"><div><h3>${esc(t('platform_users', 'Пользователи'))}</h3><p>${esc(t('platform_users_help', 'Редактируйте роль, активность и функциональные права.'))}</p></div></div><div class="platform-user-list">${(data.users || []).map(userEditor).join('') || '<div>—</div>'}</div></section>`;
    drawer.hidden = false;
    backdrop.hidden = false;
  }

  async function openTenant(tenantId) {
    currentTenantId = Number(tenantId);
    drawer.hidden = false;
    backdrop.hidden = false;
    body.innerHTML = `<div class="loading">${esc(t('platform_loading', 'Загрузка…'))}</div>`;
    try { render(await api(`/api/platform/tenants/${currentTenantId}/detail`)); }
    catch (error) { toast(error.message, true); body.innerHTML = `<div class="loading">${esc(error.message)}</div>`; }
  }

  function close() { drawer.hidden = true; backdrop.hidden = true; snapshot = null; }
  document.querySelector('#closeTenantDetail')?.addEventListener('click', close);
  backdrop.addEventListener('click', close);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-open-tenant]');
    if (!button) return;
    event.preventDefault();
    openTenant(button.dataset.openTenant);
  });

  body.addEventListener('submit', async event => {
    if (event.target.matches('#platformSubscriptionForm')) {
      event.preventDefault();
      try {
        await api(`/api/platform/tenants/${currentTenantId}/subscription`, {
          method:'PUT',
          body:Object.fromEntries(new FormData(event.target))
        });
        toast('Пакет и период сохранены.');
        await window.PlatformAdmin.load();
        await openTenant(currentTenantId);
      } catch (error) {
        toast(error.message, true);
      }
      return;
    }
    if (!event.target.matches('#platformCompanyForm')) return;
    event.preventDefault();
    try {
      await api(`/api/platform/tenants/${currentTenantId}`, {method:'PUT', body:Object.fromEntries(new FormData(event.target))});
      toast(t('company_saved', 'Данные компании сохранены.'));
      await window.PlatformAdmin.load();
      await openTenant(currentTenantId);
    } catch (error) { toast(error.message, true); }
  });

  body.addEventListener('click', async event => {
    const grants = event.target.closest('[data-save-marketplace-grants]');
    const saveUser = event.target.closest('[data-user-save]');
    const recovery = event.target.closest('[data-user-recovery]');
    const marketplaceReview = event.target.closest('[data-marketplace-review]');
    if (!grants && !saveUser && !recovery && !marketplaceReview) return;
    const button = grants || saveUser || recovery || marketplaceReview;
    button.disabled = true;
    try {
      if (marketplaceReview) {
        const note=marketplaceReview.dataset.marketplaceReview==='rejected'
          ? (window.prompt('Причина отклонения (будет видна компании):','Уточните ссылку или продавца.')||'').trim()
          : '';
        await api(`/api/platform/tenants/${currentTenantId}/marketplaces/${encodeURIComponent(marketplaceReview.dataset.marketplaceCode)}/${marketplaceReview.dataset.marketplaceReview}`,{method:'POST',body:{review_note:note}});
        toast(marketplaceReview.dataset.marketplaceReview==='approved'?'Площадка подтверждена.':'Площадка отклонена; компания сможет отправить данные заново.');
      } else if (grants) {
        const marketplaces = {};
        body.querySelectorAll('[data-marketplace-grant]').forEach(input => { marketplaces[input.dataset.marketplaceGrant] = input.checked; });
        await api(`/api/platform/tenants/${currentTenantId}/marketplaces`, {method:'PUT', body:{marketplaces}});
        toast(t('marketplaces_saved', 'Доступные площадки сохранены.'));
      } else {
        const editor = button.closest('[data-user-id]');
        const userId = Number(editor.dataset.userId);
        if (saveUser) {
          const permissions = {};
          editor.querySelectorAll('[data-user-permission]').forEach(input => { permissions[input.dataset.userPermission] = input.checked; });
          await api(`/api/platform/tenants/${currentTenantId}/users/${userId}`, {method:'PUT', body:{
            display_name:editor.querySelector('[data-user-name]').value.trim(),
            role:editor.querySelector('[data-user-role]').value,
            is_active:editor.querySelector('[data-user-active]').checked,
            permissions
          }});
          toast(t('user_saved', 'Пользователь сохранён.'));
        } else {
          const data = await api(`/api/platform/tenants/${currentTenantId}/users/${userId}/recovery`, {method:'POST', body:{}});
          window.alert(`${t('new_recovery_code', 'Новый код восстановления')}: ${data.recovery_code}`);
        }
      }
      await window.PlatformAdmin.load();
      await openTenant(currentTenantId);
    } catch (error) { toast(error.message, true); button.disabled = false; }
  });

  window.ITPUI?.onLocale?.(() => { if (snapshot) render(snapshot); });
})();
