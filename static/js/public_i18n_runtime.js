(() => {
  'use strict';
  const extra = {
    ru: {
      public_cap_sales_title:'Автоматизация онлайн-продаж', public_cap_sales_text:'Регулярные операции и рабочие сценарии без постоянного ручного запуска.',
      public_cap_market_title:'Рыночная аналитика', public_cap_market_text:'Контроль позиции и ценового диапазона на основе подтверждённых сопоставлений.',
      public_cap_assortment_title:'Контроль ассортимента', public_cap_assortment_text:'Единый каталог, актуальность данных и история изменения позиций.',
      public_cap_recommend_title:'Рекомендации по позициям', public_cap_recommend_text:'Выделение товаров, которые требуют проверки или дополнительного внимания.',
      public_cap_automation_title:'Плановые операции', public_cap_automation_text:'Расписание обновлений, контроль выполнения и история запусков.',
      public_cap_channels_title:'Несколько каналов продаж', public_cap_channels_text:'Раздельная логика источников при единой аналитической модели.',
      public_process_1_title:'Подключаем данные', public_process_1_text:'Настраиваем источники и правила рабочего пространства.', public_process_2_title:'Нормализуем', public_process_2_text:'Приводим товары и характеристики к единой модели.', public_process_3_title:'Анализируем', public_process_3_text:'Оцениваем позицию, диапазоны и качество данных.', public_process_4_title:'Рекомендуем', public_process_4_text:'Выделяем позиции, которые требуют внимания.',
      public_results_text:'Система помогает видеть изменения раньше и концентрироваться на позициях, которые действительно требуют решения.', public_result_1:'Контроль актуальности ассортимента', public_result_2:'Единый обзор нескольких каналов продаж', public_result_3:'Планирование регулярных операций', public_result_4:'Рекомендации по проблемным и недоиспользованным позициям', public_decision_1_value:'Цена выше рабочего диапазона', public_decision_2_value:'Данные подтверждены', public_decision_3_value:'Проверить позицию'
    },
    en: {
      public_cap_sales_title:'Online sales automation', public_cap_sales_text:'Recurring operations without constant manual launches.', public_cap_market_title:'Market analytics', public_cap_market_text:'Position and price-band control based on confirmed matching.', public_cap_assortment_title:'Assortment control', public_cap_assortment_text:'Unified catalogue, data freshness and change history.', public_cap_recommend_title:'Position recommendations', public_cap_recommend_text:'Identify products that require attention.', public_cap_automation_title:'Scheduled operations', public_cap_automation_text:'Update schedules, execution control and run history.', public_cap_channels_title:'Multiple sales channels', public_cap_channels_text:'Separate source logic within one analytical model.', public_process_1_title:'Connect data', public_process_1_text:'Configure sources and workspace rules.', public_process_2_title:'Normalize', public_process_2_text:'Convert products and attributes into one model.', public_process_3_title:'Analyze', public_process_3_text:'Assess position, ranges and data quality.', public_process_4_title:'Recommend', public_process_4_text:'Highlight positions requiring attention.', public_results_text:'See changes earlier and focus on positions that require action.', public_result_1:'Assortment freshness control', public_result_2:'Unified view across sales channels', public_result_3:'Scheduled recurring operations', public_result_4:'Recommendations for problematic positions', public_decision_1_value:'Price above working range', public_decision_2_value:'Data confirmed', public_decision_3_value:'Review position'
    },
    kk: {
      public_cap_sales_title:'Онлайн-сатылымды автоматтандыру', public_cap_sales_text:'Қолмен тұрақты іске қоспай орындалатын операциялар.', public_cap_market_title:'Нарықтық аналитика', public_cap_market_text:'Расталған сәйкестіктер бойынша баға диапазонын бақылау.', public_cap_assortment_title:'Ассортиментті бақылау', public_cap_assortment_text:'Бірыңғай каталог және деректердің өзектілігі.', public_cap_recommend_title:'Позициялар бойынша ұсынымдар', public_cap_recommend_text:'Назарды қажет ететін тауарларды анықтау.', public_cap_automation_title:'Жоспарлы операциялар', public_cap_automation_text:'Жаңартулар кестесі және іске қосу тарихы.', public_cap_channels_title:'Бірнеше сату арнасы', public_cap_channels_text:'Бірыңғай модельдегі көздердің бөлек логикасы.', public_process_1_title:'Деректерді қосамыз', public_process_1_text:'Көздер мен жұмыс кеңістігін баптаймыз.', public_process_2_title:'Қалыпқа келтіреміз', public_process_2_text:'Тауарларды бірыңғай модельге келтіреміз.', public_process_3_title:'Талдаймыз', public_process_3_text:'Позиция мен дерек сапасын бағалаймыз.', public_process_4_title:'Ұсынамыз', public_process_4_text:'Назар қажет позицияларды бөлеміз.', public_results_text:'Өзгерістерді ертерек көріп, маңызды позицияларға назар аударыңыз.', public_result_1:'Ассортимент өзектілігін бақылау', public_result_2:'Сату арналарына бірыңғай шолу', public_result_3:'Тұрақты операцияларды жоспарлау', public_result_4:'Проблемалы позициялар бойынша ұсынымдар', public_decision_1_value:'Баға жұмыс диапазонынан жоғары', public_decision_2_value:'Деректер расталды', public_decision_3_value:'Позицияны тексеру'
    }
  };
  Object.assign(extra.ru, {
    public_login:'Войти',
    register_step_plan:'ПАКЕТ', register_plan_title:'Выберите пакет',
    register_plan_hint:'Пробный пакет активируется сразу. Для платного пакета после регистрации сформируется счёт: доступ откроется после подтверждения оплаты.',
    register_intro:'Создайте рабочее пространство и выберите пакет.', register_point_review:'Подтвердите email, чтобы завершить активацию учётной записи',
    register_required_hint:'Укажите реальные данные для счёта и рабочего пространства.', register_marketplaces_hint:'Выберите хотя бы одну площадку. Подключение магазина настраивается после регистрации.',
    register_submit:'Зарегистрироваться', public_cta_button:'Зарегистрироваться',
    register_consents_title:'Согласия и отправка', register_guide_step:'Шаг {current} из {total}',
    register_guide_fill:'заполните текущий блок', register_guide_review:'проверьте согласия и отправьте форму',
    register_guide_next:'Далее', register_guide_submit:'К отправке'
  });
  Object.assign(extra.kk, {
    public_login:'Кіру',
    register_step_plan:'ПАКЕТ', register_plan_title:'Пакетті таңдаңыз',
    register_plan_hint:'Сынақ пакеті бірден іске қосылады. Ақылы пакетке тіркелгеннен кейін шот жасалады: қолжетімділік төлем расталған соң ашылады.',
    register_intro:'Жұмыс кеңістігін жасап, пакетті таңдаңыз.', register_point_review:'Тіркелгіні белсендіруді аяқтау үшін email-ды растаңыз',
    register_required_hint:'Шот пен жұмыс кеңістігі үшін нақты деректерді көрсетіңіз.', register_marketplaces_hint:'Кемінде бір маркетплейсті таңдаңыз. Дүкенді қосу тіркелгеннен кейін бапталады.',
    register_submit:'Тіркелу', public_cta_button:'Тіркелу',
    register_consents_title:'Келісімдер және жіберу', register_guide_step:'{current} / {total} қадам',
    register_guide_fill:'ағымдағы бөлімді толтырыңыз', register_guide_review:'келісімдерді тексеріп, нысанды жіберіңіз',
    register_guide_next:'Келесі', register_guide_submit:'Жіберуге өту'
  });
  Object.assign(extra.en, {
    public_login:'Login',
    register_step_plan:'PLAN', register_plan_title:'Choose a plan',
    register_plan_hint:'The trial starts immediately. For a paid plan, registration creates an invoice and access opens after payment confirmation.',
    register_intro:'Create your workspace and choose a plan.', register_point_review:'Confirm your email to finish activating the account',
    register_required_hint:'Enter accurate details for the invoice and workspace.', register_marketplaces_hint:'Select at least one marketplace. Store connection is configured after registration.',
    register_submit:'Register', public_cta_button:'Register',
    register_consents_title:'Consents and submission', register_guide_step:'Step {current} of {total}',
    register_guide_fill:'complete the current section', register_guide_review:'review consents and submit the form',
    register_guide_next:'Next', register_guide_submit:'Go to submission'
  });
  const locales=window.ITP_PUBLIC_LOCALES=window.ITP_PUBLIC_LOCALES||{};
  Object.entries(extra).forEach(([lang,values])=>Object.assign(locales[lang]=locales[lang]||{},values));
  const valid=lang=>['ru','kk','en'].includes(lang)?lang:'ru';
  const apply=raw=>{
    const lang=valid(raw);localStorage.setItem('itp_lang',lang);document.documentElement.lang=lang;
    document.querySelectorAll('[data-pi18n]').forEach(node=>{const value=locales[lang]?.[node.dataset.pi18n]||locales.ru?.[node.dataset.pi18n];if(value)node.textContent=value;});
    document.querySelectorAll('[data-public-lang]').forEach(node=>node.classList.toggle('active',node.dataset.publicLang===lang));
    const select=document.querySelector('#languageSelect');if(select)select.value=lang;
    document.querySelectorAll('[data-legal-link]').forEach(link=>{const url=new URL(link.href,location.href);url.searchParams.set('lang',lang);link.href=url.toString();});
    document.dispatchEvent(new CustomEvent('itp:public-locale',{detail:{lang}}));
  };
  document.addEventListener('click',event=>{const button=event.target.closest('[data-public-lang]');if(button)apply(button.dataset.publicLang);const theme=event.target.closest('[data-public-theme]');if(theme){const current=document.documentElement.dataset.theme==='dark'?'light':'dark';document.documentElement.dataset.theme=current;document.documentElement.style.colorScheme=current;localStorage.setItem('itp_theme',current);}});
  document.querySelector('#languageSelect')?.addEventListener('change',event=>apply(event.target.value));
  apply(window.ITP_LEGAL_LOCALE||localStorage.getItem('itp_lang')||'ru');
})();
