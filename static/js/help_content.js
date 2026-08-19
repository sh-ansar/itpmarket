window.ITP_HELP_CONTENT = {
  ru: {
    dashboard: {
      title: 'Обзор',
      intro: 'Сводная картина по каталогу, качеству данных, ценовым рискам и последним операциям.',
      sections: [
        {title: 'Что здесь видно', items: ['Количество позиций по доступным площадкам.', 'Покрытие ценовыми и рыночными данными.', 'Риски и потенциал только по подтверждённым данным.']},
        {title: 'Что делать дальше', items: ['Откройте «Товары» для проверки отдельных карточек.', 'Запустите обновление в «Операциях».', 'Используйте «Отчёты» для сводной аналитики.']}
      ],
      tip: 'Показатели обновляются после завершения операций и повторной загрузки данных.'
    },
    products: {
      title: 'Товары, остатки и матчинг',
      intro: 'Каталог показывает листинги площадок, а остаток и закупочная цена относятся к одному физическому товару.',
      sections: [
        {title: 'Как читать цену', items: ['«Единственная минимальная» означает, что цена строго ниже всех остальных.', 'При равной минимальной цене статус показывает, что позиция делит минимум с другими продавцами.', 'Средний ориентир в карточке — медиана других продавцов, а не среднее арифметическое.']},
        {title: 'Остаток и рекомендации', items: ['Количество и закупочная цена вводятся в карточке товара при наличии права.', 'Вложенная сумма считается как остаток × закупочная цена.', 'Рекомендованная цена использует целевую наценку и не учитывает комиссии, логистику, налоги и возвраты.']},
        {title: 'Матчинг площадок', items: ['Система предлагает совпадения по артикулу и строгим характеристикам; похожие модели требуют проверки.', 'Объединение выполняется только после подтверждения сотрудником с отдельным правом.', 'Связанные листинги используют один остаток и не удваивают сумму склада.']}
      ],
      tip: 'Не объединяйте карточки только по похожему названию: сначала проверьте модель, размер, индексы и артикул.'
    },
    operations: {
      title: 'Операции',
      intro: 'Серверные сборщики обновляют каталоги, собственные цены и предложения других продавцов.',
      sections: [
        {title: 'Перед запуском', items: ['Выберите площадку, продавца и область обработки.', 'Для браузерных сборщиков заранее подготовьте профиль и географию.', 'Конфликтующие операции одного продавца выполняются последовательно.']},
        {title: 'Статусы', items: ['«Выполняется» — процесс активен.', '«Окончено» — операция завершилась успешно.', 'При ошибке откройте журнал и повторите только проблемные позиции.']}
      ],
      tip: 'Браузерный профиль нельзя закрывать до завершения связанной операции.'
    },
    reports: {
      title: 'Отчёты',
      intro: 'Сводная аналитика по качеству данных, рискам, диапазонам цен и возможностям.',
      sections: [
        {title: 'Интерпретация', items: ['Риски строятся по доступным подтверждённым данным.', 'Ценовой потенциал — сценарная оценка, а не гарантированная прибыль.', 'Проверяйте дату обновления перед решением.']},
        {title: 'Форматы', items: ['HTML удобен руководителю.', 'CSV подходит для дальнейшего анализа.', 'JSON предназначен для интеграций.']}
      ],
      tip: 'Перед отчётом обновите собственные цены и предложения рынка.'
    },
    schedules: {
      title: 'Расписание',
      intro: 'Плановые задания запускают операции на сервере без ручного старта.',
      sections: [
        {title: 'Варианты запуска', items: ['Однократно в указанную дату.', 'Ежедневно или по дням недели.', 'Через фиксированный интервал.']},
        {title: 'Требования', items: ['Сервер и нужная пользовательская сессия должны быть активны.', 'Браузерные профили и география площадки должны быть подготовлены.', 'Сначала проверьте новое расписание на безопасном времени.']}
      ],
      tip: 'Результат каждого планового запуска доступен в журнале операций.'
    },
    users: {
      title: 'Сотрудники и права',
      intro: 'Роль задаёт стартовый набор прав, а администратор может настроить сотрудника точнее.',
      sections: [
        {title: 'Складские права', items: ['Просмотр остатков и закупочных цен выдаётся отдельно.', 'Изменение количества и закупочной цены требует права управления остатками.', 'Подтверждение матчинга — отдельное право и по умолчанию доступно администратору.']},
        {title: 'Безопасность', items: ['Отключение учётной записи запрещает вход.', 'Доступные площадки сначала выдаются всей компании.', 'Выдавайте только права, необходимые для работы.']}
      ],
      tip: 'Оператор может вести остатки, но не подтверждает объединение каталогов без отдельного разрешения.'
    },
    settings: {
      title: 'Настройки',
      intro: 'Здесь находятся параметры компании, интерфейса, подключений и динамических фильтров.',
      sections: [
        {title: 'Интерфейс', items: ['Язык и тема сохраняются для текущего сотрудника.', 'Курсы валют влияют на отображение пересчитанных сумм.', 'Системная тема следует настройке устройства.']},
        {title: 'Подключения', items: ['Проверяйте продавца перед подтверждением подключения.', 'Источники компании и рыночные данные хранятся раздельно.', 'После изменения источника выполните синхронизацию каталога.']},
        {title: 'Telegram', items: ['Откройте указанного бота и выполните /login в личном чате.', 'Логин — email Spyon; сообщения с логином и паролем бот удаляет сразу.', 'Приостановить или полностью отвязать Telegram можно в этой карточке либо командами бота.']}
      ],
      tip: 'Изменения темы видны сразу; параметры компании сохраняются отдельной кнопкой.'
    }
  },
  kk: {
    dashboard: {title: 'Шолу', intro: 'Каталог, деректер сапасы, баға тәуекелдері және соңғы операциялар туралы жиынтық.', sections: [{title: 'Не көрсетіледі', items: ['Қолжетімді алаңдардағы позициялар саны.', 'Баға және нарық деректерімен қамту.', 'Тек расталған деректер бойынша тәуекелдер мен мүмкіндік.']}, {title: 'Келесі қадам', items: ['Жеке карточкаларды «Тауарлар» бөлімінде тексеріңіз.', 'Деректерді «Операциялар» бөлімінде жаңартыңыз.', 'Жиынтықты «Есептер» бөлімінен алыңыз.']}], tip: 'Көрсеткіштер операция аяқталғаннан кейін жаңартылады.'},
    products: {title: 'Тауарлар, қалдықтар және сәйкестендіру', intro: 'Алаң карточкалары бір физикалық тауарға байланысады; қалдық пен сатып алу бағасы бір рет сақталады.', sections: [{title: 'Баға мәртебесі', items: ['Бірегей минимум — барлық басқа бағадан қатаң төмен.', 'Бағалар тең болса, жүйе ортақ минимумды көрсетеді.', 'Карточкадағы орта мән — басқа сатушылардың медианасы.']}, {title: 'Қалдық және есеп', items: ['Саны мен сатып алу бағасы бөлек құқықпен енгізіледі.', 'Салынған сома = қалдық × сатып алу бағасы.', 'Ұсыныс комиссиялар, логистика, салықтар және қайтарымдарды есептемейді.']}, {title: 'Алаңдарды сәйкестендіру', items: ['Алдымен артикул мен қатаң сипаттамалар салыстырылады.', 'Біріктіруді құқығы бар қызметкер растайды.', 'Байланысқан карточкалар қойма сомасын қайталамайды.']}], tip: 'Тек ұқсас атауға қарап біріктірмеңіз; модель мен өлшемді тексеріңіз.'},
    operations: {title: 'Операциялар', intro: 'Жинағыштар каталогтар мен бағаларды жаңартады.', sections: [{title: 'Іске қосу', items: ['Алаңды, сатушыны және өңдеу аймағын таңдаңыз.', 'Браузер профилі мен географияны алдын ала дайындаңыз.', 'Бір сатушының қайшылықты операциялары кезекпен орындалады.']}, {title: 'Мәртебелер', items: ['«Орындалуда» — процесс белсенді.', '«Аяқталды» — сәтті аяқталды.', 'Қате болса журналды ашыңыз.']}], tip: 'Операция аяқталғанша браузер профилін жаппаңыз.'},
    reports: {title: 'Есептер', intro: 'Деректер сапасы, тәуекелдер және баға диапазондары бойынша талдау.', sections: [{title: 'Түсіндіру', items: ['Тәуекелдер расталған деректерден құрылады.', 'Баға мүмкіндігі кепілденген пайда емес.', 'Шешім алдында жаңарту күнін тексеріңіз.']}, {title: 'Пішімдер', items: ['HTML — қарау үшін.', 'CSV — талдау үшін.', 'JSON — интеграциялар үшін.']}], tip: 'Есеп алдында бағаларды жаңартыңыз.'},
    schedules: {title: 'Кесте', intro: 'Жоспарлы тапсырмалар операцияларды автоматты түрде іске қосады.', sections: [{title: 'Нұсқалар', items: ['Бір рет.', 'Күн сайын немесе аптаның күндері бойынша.', 'Белгіленген аралықпен.']}, {title: 'Талаптар', items: ['Сервер мен сессия белсенді болуы керек.', 'Браузер профильдері дайын болуы керек.', 'Алғашқы іске қосуды журналдан тексеріңіз.']}], tip: 'Әр іске қосудың нәтижесі операциялар журналында бар.'},
    users: {title: 'Қызметкерлер және құқықтар', intro: 'Рөл бастапқы құқықтарды береді, әкімші оларды нақтылай алады.', sections: [{title: 'Қойма құқықтары', items: ['Қалдық пен сатып алу бағасын көру — бөлек құқық.', 'Оларды өзгерту — бөлек басқару құқығы.', 'Сәйкестендіруді растау әдепкіде әкімшіге беріледі.']}, {title: 'Қауіпсіздік', items: ['Тек жұмысқа қажет құқықтарды беріңіз.', 'Алаңдарға рұқсат алдымен компанияға беріледі.', 'Есептік жазбаны өшіру кіруді тоқтатады.']}], tip: 'Операторға сәйкестендіру құқығын қажет болғанда ғана беріңіз.'},
    settings: {title: 'Баптаулар', intro: 'Компания, интерфейс, қосылымдар және сүзгілер параметрлері.', sections: [{title: 'Интерфейс', items: ['Тіл мен тақырып қызметкер үшін сақталады.', 'Валюта бағамдары есептік көрсетілімге әсер етеді.', 'Жүйелік тақырып құрылғы баптауына сәйкес.']}, {title: 'Қосылымдар', items: ['Қосуды растау алдында сатушыны тексеріңіз.', 'Компания және нарық деректері бөлек сақталады.', 'Өзгерістен кейін каталогты синхрондаңыз.']}, {title: 'Telegram', items: ['Жеке чатта көрсетілген ботты ашып, /login пәрменін орындаңыз.', 'Логин — Spyon email; бот логин мен құпиясөз хабарларын дереу жояды.', 'Хабарламаларды осы карточкада немесе бот пәрмендерімен тоқтатуға және ажыратуға болады.']}], tip: 'Компания параметрлерін сақтау батырмасымен бекітіңіз.'}
  },
  en: {
    dashboard: {title: 'Overview', intro: 'A summary of catalog coverage, data quality, price risks, and recent operations.', sections: [{title: 'What you see', items: ['Listing count across accessible marketplaces.', 'Price and market-data coverage.', 'Risks and opportunities based on confirmed data.']}, {title: 'Next actions', items: ['Inspect individual cards in Products.', 'Refresh data in Operations.', 'Use Reports for consolidated analysis.']}], tip: 'Metrics update after an operation finishes and data is reloaded.'},
    products: {title: 'Products, inventory, and matching', intro: 'Marketplace listings can point to one physical inventory item, where quantity and purchase cost are stored once.', sections: [{title: 'Price status', items: ['Unique lowest means strictly below every other seller.', 'Equal prices are shown as tied for the minimum.', 'The central reference is the median of other sellers.']}, {title: 'Inventory and guidance', items: ['Quantity and purchase price require inventory permissions.', 'Stock investment equals quantity × purchase price.', 'The recommendation excludes fees, logistics, tax, and returns.']}, {title: 'Cross-market matching', items: ['Article numbers and strict characteristics are checked first.', 'A permitted employee must confirm every merge.', 'Linked listings do not duplicate stock quantity or value.']}], tip: 'Do not merge on a similar title alone; verify model, size, indices, and article number.'},
    operations: {title: 'Operations', intro: 'Server collectors refresh catalogs, own prices, and competing offers.', sections: [{title: 'Before starting', items: ['Choose the marketplace, seller, and scope.', 'Prepare browser profiles and marketplace geography.', 'Conflicting jobs for the same seller run sequentially.']}, {title: 'Statuses', items: ['Running means the process is active.', 'Completed means it finished successfully.', 'Open the log when a task fails.']}], tip: 'Do not close a browser profile while its operation is running.'},
    reports: {title: 'Reports', intro: 'Consolidated analysis of data quality, price risks, ranges, and opportunities.', sections: [{title: 'Interpretation', items: ['Risks use available confirmed data.', 'Price opportunity is a scenario, not guaranteed profit.', 'Check freshness before making a decision.']}, {title: 'Formats', items: ['HTML is suited to reading.', 'CSV is suited to analysis.', 'JSON is suited to integrations.']}], tip: 'Refresh own prices and market offers before generating a report.'},
    schedules: {title: 'Schedules', intro: 'Scheduled jobs start server operations without a manual launch.', sections: [{title: 'Options', items: ['One-time run.', 'Daily or selected weekdays.', 'Fixed interval.']}, {title: 'Requirements', items: ['The server and required user session must be active.', 'Browser profiles and geography must be ready.', 'Review the first run in the operation log.']}], tip: 'Every scheduled run is recorded in Operations.'},
    users: {title: 'Users and permissions', intro: 'A role provides defaults; an administrator can refine each employee’s access.', sections: [{title: 'Inventory permissions', items: ['Viewing stock and purchase prices is separate.', 'Editing inventory requires a management permission.', 'Matching confirmation is separate and defaults to administrators.']}, {title: 'Security', items: ['Grant only the permissions required for the job.', 'Marketplace access is granted to the company first.', 'Disabling an account prevents sign-in.']}], tip: 'Only grant matching permission to employees who verify product identity.'},
    settings: {title: 'Settings', intro: 'Company, interface, connection, and dynamic-filter settings.', sections: [{title: 'Interface', items: ['Language and theme are stored per employee.', 'Exchange rates affect converted display values.', 'System theme follows the device setting.']}, {title: 'Connections', items: ['Verify the seller before confirming a connection.', 'Company and market sources stay separate.', 'Synchronize the catalog after changing a source.']}, {title: 'Telegram', items: ['Open the listed bot in a private chat and run /login.', 'Your Spyon email is the login; the bot deletes login and password messages immediately.', 'Pause or disconnect Telegram from this card or with bot commands.']}], tip: 'Use the save action for company and connection changes.'}
  }
};
