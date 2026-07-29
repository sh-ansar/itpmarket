window.ITP_HELP_CONTENT = {
  ru: {
    dashboard: {
      title: "Обзор",
      intro: "Главная страница показывает состояние каталога, покрытия данными и ценовых рисков по всем подключенным площадкам.",
      sections: [
        {title: "На что смотреть", items: ["Всего товаров показывает общий размер каталога.", "Данные обработаны показывает, где уже есть актуальные рыночные предложения.", "Ценовые риски и потенциал помогают быстро понять, какие позиции требуют внимания."]},
        {title: "Быстрые действия", items: ["Откройте каталог для проверки конкретных товаров.", "Перейдите в отчеты, чтобы посмотреть аналитику и выгрузки."]}
      ],
      tip: "Сравнение выполняется только по подтвержденным совпадениям, без смешивания похожих товаров."
    },
    products: {
      title: "Товары",
      intro: "Каталог объединяет позиции Kaspi, Ozon и Halyk Market, но площадки и правила сравнения остаются разделенными.",
      sections: [
        {title: "Фильтры", items: ["Используйте вкладки Все, Риски, Потенциал, Требуют проверки и Наблюдение.", "Фильтруйте по площадке, бренду, статусу и актуальности данных.", "Сортировка помогает быстро найти свежие изменения или самые дорогие позиции."]},
        {title: "Карточка товара", items: ["Откройте строку товара, чтобы увидеть цену, диапазон рынка, продавцов, историю и характеристики.", "Кнопка открытия карточки ведет на страницу товара или поиск по площадке, если прямой URL недоступен."]}
      ],
      tip: "Для Halyk Market сравнение идет внутри той же карточки товара."
    },
    operations: {
      title: "Операции",
      intro: "Раздел управляет сбором данных, обновлением цен, отчетами и служебными задачами.",
      sections: [
        {title: "Запуск", items: ["Выберите площадку, операцию и область запуска.", "Для выбранных товаров отметьте позиции в каталоге и запустите анализ.", "Во время выполнения показывается прогресс и примерное оставшееся время."]},
        {title: "Журнал", items: ["Журнал показывает понятные этапы операции и результат.", "Если операция завершилась ошибкой, откройте журнал и проверьте последнюю строку."]}
      ],
      tip: "Ozon требует открытый отладочный браузер, а Kaspi и Halyk запускаются напрямую."
    },
    reports: {
      title: "Отчеты",
      intro: "Отчеты собирают состояние рынка, риски, потенциал и готовые файлы для выгрузки.",
      sections: [
        {title: "Аналитика", items: ["KPI показывают покрытие данных по площадкам.", "Графики и таблицы помогают найти позиции выше рынка и товары с потенциалом.", "Клик по строке аналитики открывает карточку товара."]},
        {title: "Файлы", items: ["Сформированные отчеты можно скачать из списка внизу страницы.", "CSV подходит для таблиц, JSON для интеграций, HTML для просмотра."]}
      ],
      tip: "Пустые блоки обычно означают, что по товарам еще не запускался сбор рынка."
    },
    schedules: {
      title: "Расписание",
      intro: "Расписание автоматизирует повторные операции сбора и обновления данных.",
      sections: [
        {title: "Настройка", items: ["Создайте задачу, выберите операцию и периодичность.", "Время запуска берется по времени сервера.", "Отключайте расписание, если временно не нужно запускать сбор."]},
        {title: "Контроль", items: ["Следите за последним результатом и ближайшим запуском.", "История показывает, какие плановые операции уже выполнялись."]}
      ],
      tip: "Перед демонстрацией лучше запускать тяжелые операции вручную, чтобы контролировать результат."
    },
    users: {
      title: "Сотрудники",
      intro: "Здесь администратор управляет учетками, ролями и доступом к площадкам.",
      sections: [
        {title: "Роли", items: ["Администратор управляет настройками и пользователями.", "Оператор запускает операции и работает с каталогом.", "Наблюдатель смотрит данные без критичных изменений."]},
        {title: "Доступ", items: ["Площадки можно включать отдельно для каждого сотрудника.", "Временный пароль нужно передать пользователю безопасным способом."]}
      ],
      tip: "Системная админ-панель доступна только владельцу платформы."
    },
    settings: {
      title: "Настройки",
      intro: "Настройки хранят данные компании, интерфейса, валют и параметров сборщиков.",
      sections: [
        {title: "Компания", items: ["Клиент может заполнить название, БИН, email и телефон компании.", "Эти данные используются в рабочем пространстве и публичных документах."]},
        {title: "Сборщики", items: ["Kaspi, Ozon и Halyk Market настраиваются отдельно.", "Меняйте параметры аккуратно и проверяйте результат через операции."]}
      ],
      tip: "После изменения настроек запустите нужную операцию, чтобы обновить данные."
    }
  },
  kk: {
    dashboard: {
      title: "Шолу",
      intro: "Басты бет каталог күйін, деректер қамтылуын және барлық қосылған алаңдардағы баға тәуекелдерін көрсетеді.",
      sections: [
        {title: "Нені қарау керек", items: ["Тауарлар саны каталогтың жалпы көлемін көрсетеді.", "Деректер өңделді көрсеткіші қай жерде өзекті нарық ұсыныстары бар екенін көрсетеді.", "Баға тәуекелі мен әлеует назар қажет позицияларды тез табуға көмектеседі."]},
        {title: "Жылдам әрекеттер", items: ["Нақты тауарды тексеру үшін каталогты ашыңыз.", "Аналитика мен файлдарды көру үшін есептерге өтіңіз."]}
      ],
      tip: "Салыстыру тек расталған сәйкестіктер бойынша орындалады."
    },
    products: {
      title: "Тауарлар",
      intro: "Каталог Kaspi, Ozon және Halyk Market позицияларын біріктіреді, бірақ алаңдар мен салыстыру ережелері бөлек сақталады.",
      sections: [
        {title: "Сүзгілер", items: ["Барлығы, Тәуекел, Әлеует, Тексеру қажет және Бақылау қойындыларын пайдаланыңыз.", "Алаң, бренд, мәртебе және дерек өзектілігі бойынша сүзуге болады.", "Сұрыптау соңғы өзгерістерді немесе қымбат позицияларды табуға көмектеседі."]},
        {title: "Тауар карточкасы", items: ["Жолды ашып, бағаны, нарық диапазонын, сатушыларды, тарихты және сипаттамаларды көріңіз.", "Тікелей URL болмаса, ашу батырмасы алаңдағы іздеуге апарады."]}
      ],
      tip: "Halyk Market үшін салыстыру сол бір тауар карточкасының ішінде жүреді."
    },
    operations: {
      title: "Операциялар",
      intro: "Бұл бөлім дерек жинауды, баға жаңартуды, есептерді және қызметтік тапсырмаларды басқарады.",
      sections: [
        {title: "Іске қосу", items: ["Алаңды, операцияны және іске қосу аймағын таңдаңыз.", "Таңдалған тауарлар үшін каталогта позицияларды белгілеңіз.", "Орындалу кезінде прогресс және шамамен қалған уақыт көрсетіледі."]},
        {title: "Журнал", items: ["Журнал операция кезеңдерін және нәтижесін түсінікті түрде көрсетеді.", "Қате болса, журналды ашып, соңғы жолды тексеріңіз."]}
      ],
      tip: "Ozon үшін ашық debug браузер керек, Kaspi және Halyk тікелей іске қосылады."
    },
    reports: {
      title: "Есептер",
      intro: "Есептер нарық күйін, тәуекелдерді, әлеуетті және дайын файлдарды жинайды.",
      sections: [
        {title: "Аналитика", items: ["KPI алаңдар бойынша дерек қамтылуын көрсетеді.", "Кестелер нарықтан жоғары позицияларды және әлеуеті бар тауарларды табуға көмектеседі.", "Аналитика жолын бассаңыз, тауар карточкасы ашылады."]},
        {title: "Файлдар", items: ["Дайын есептерді төмендегі тізімнен жүктеуге болады.", "CSV кестелерге, JSON интеграцияға, HTML қарауға ыңғайлы."]}
      ],
      tip: "Бос блоктар әдетте нарық жинау әлі іске қосылмағанын білдіреді."
    },
    schedules: {
      title: "Кесте",
      intro: "Кесте дерек жинау мен жаңартуды автоматты қайталауға көмектеседі.",
      sections: [
        {title: "Баптау", items: ["Тапсырма жасап, операцияны және қайталау тәртібін таңдаңыз.", "Іске қосу уақыты сервер уақытымен алынады.", "Қажет болмаса, кестені уақытша өшіріңіз."]},
        {title: "Бақылау", items: ["Соңғы нәтиже мен келесі іске қосуды бақылаңыз.", "Тарих орындалған жоспарлы операцияларды көрсетеді."]}
      ],
      tip: "Көрсетілім алдында ауыр операцияларды қолмен іске қосқан дұрыс."
    },
    users: {
      title: "Қызметкерлер",
      intro: "Мұнда әкімші аккаунттарды, рөлдерді және алаңдарға қолжетімділікті басқарады.",
      sections: [
        {title: "Рөлдер", items: ["Әкімші баптаулар мен пайдаланушыларды басқарады.", "Оператор операцияларды іске қосып, каталогпен жұмыс істейді.", "Бақылаушы маңызды өзгеріссіз деректерді көреді."]},
        {title: "Қолжетімділік", items: ["Әр қызметкерге алаңдарды бөлек қосуға болады.", "Уақытша құпиясөзді қауіпсіз жолмен беріңіз."]}
      ],
      tip: "Жүйелік админ панель тек платформа иесіне қолжетімді."
    },
    settings: {
      title: "Баптаулар",
      intro: "Баптаулар компания деректерін, интерфейсті, валюталарды және жинаушылар параметрлерін сақтайды.",
      sections: [
        {title: "Компания", items: ["Клиент компания атауын, БСН, email және телефонды толтыра алады.", "Бұл деректер жұмыс кеңістігі мен жария құжаттарда қолданылады."]},
        {title: "Жинаушылар", items: ["Kaspi, Ozon және Halyk Market бөлек бапталады.", "Параметрлерді мұқият өзгертіп, нәтижені операциялар арқылы тексеріңіз."]}
      ],
      tip: "Баптаудан кейін деректерді жаңарту үшін керек операцияны іске қосыңыз."
    }
  },
  en: {
    dashboard: {
      title: "Overview",
      intro: "The overview shows catalogue health, data coverage and price risks across connected marketplaces.",
      sections: [
        {title: "What to watch", items: ["Total products shows the catalogue size.", "Data processed shows where current market offers are available.", "Price risks and potential highlight positions that need attention."]},
        {title: "Quick actions", items: ["Open the catalogue to inspect specific products.", "Open reports to review analytics and generated files."]}
      ],
      tip: "Comparisons use confirmed matches only."
    },
    products: {
      title: "Products",
      intro: "The catalogue combines Kaspi, Ozon and Halyk Market positions while keeping marketplace rules separate.",
      sections: [
        {title: "Filters", items: ["Use All, Risks, Potential, Needs review and Watched tabs.", "Filter by marketplace, brand, status and freshness.", "Sorting helps find recent changes or high-value positions."]},
        {title: "Product card", items: ["Open a row to see price, market range, sellers, history and specifications.", "If a direct URL is unavailable, the open button uses marketplace search."]}
      ],
      tip: "Halyk Market comparison is limited to the same marketplace product card."
    },
    operations: {
      title: "Operations",
      intro: "Operations control data collection, price refreshes, reports and service tasks.",
      sections: [
        {title: "Starting", items: ["Choose the marketplace, operation and scope.", "For selected products, mark catalogue rows before starting analysis.", "Progress and estimated remaining time are shown while the task runs."]},
        {title: "Log", items: ["The log shows readable task stages and the result.", "If a task fails, open the log and check the last line."]}
      ],
      tip: "Ozon requires an open debug browser. Kaspi and Halyk run directly."
    },
    reports: {
      title: "Reports",
      intro: "Reports collect market state, risks, potential and generated export files.",
      sections: [
        {title: "Analytics", items: ["KPIs show data coverage by marketplace.", "Charts and tables help find overpriced positions and opportunities.", "Click an analytics row to open the product card."]},
        {title: "Files", items: ["Generated reports can be downloaded from the lower list.", "CSV is for spreadsheets, JSON for integrations and HTML for review."]}
      ],
      tip: "Empty blocks usually mean market collection has not been run yet."
    },
    schedules: {
      title: "Schedules",
      intro: "Schedules automate repeated collection and refresh operations.",
      sections: [
        {title: "Setup", items: ["Create a task, choose the operation and recurrence.", "Run time uses the server time zone.", "Disable schedules when automatic collection is temporarily not needed."]},
        {title: "Control", items: ["Track the last result and next run.", "History shows completed scheduled operations."]}
      ],
      tip: "Before a demo, it is better to run heavy operations manually."
    },
    users: {
      title: "Employees",
      intro: "Administrators manage accounts, roles and marketplace access here.",
      sections: [
        {title: "Roles", items: ["Administrator manages settings and users.", "Operator runs operations and works with the catalogue.", "Viewer sees data without critical changes."]},
        {title: "Access", items: ["Marketplaces can be enabled per employee.", "Share temporary passwords through a safe channel."]}
      ],
      tip: "The system admin panel is available only to the platform owner."
    },
    settings: {
      title: "Settings",
      intro: "Settings store company details, interface preferences, currencies and collector parameters.",
      sections: [
        {title: "Company", items: ["The client can fill company name, registration number, email and phone.", "These details are used in the workspace and public documents."]},
        {title: "Collectors", items: ["Kaspi, Ozon and Halyk Market are configured separately.", "Change parameters carefully and verify the result through operations."]}
      ],
      tip: "After changing settings, run the required operation to refresh data."
    }
  }
};
