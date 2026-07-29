window.ITP_HELP_CONTENT = {
  "ru": {
    "dashboard": {
      "title": "Обзор",
      "intro": "Сводная картина по каталогу, качеству данных, ценовым рискам и последним операциям.",
      "sections": [
        {
          "title": "Что здесь видно",
          "items": [
            "Общее количество клиентских позиций по подключённым каналам.",
            "Доля товаров с готовыми ценами и рыночными данными.",
            "Риски и ценовой потенциал только по подтверждённым сопоставлениям."
          ]
        },
        {
          "title": "Как действовать",
          "items": [
            "Откройте «Товары» для проверки отдельных позиций.",
            "Используйте «Операции» для обновления каталога и цен.",
            "Переходите в «Отчёты» для сводной аналитики."
          ]
        }
      ],
      "tip": "Показатели пересчитываются после завершения операций и обновления страницы."
    },
    "products": {
      "title": "Товары",
      "intro": "Основной каталог клиента. Рыночные карточки других продавцов не выводятся отдельными строками.",
      "sections": [
        {
          "title": "Фильтры и поиск",
          "items": [
            "Поиск работает по названию, коду, бренду, размеру и продавцу.",
            "Площадка, бренд, статус и актуальность сужают выдачу.",
            "Выбранные позиции можно анализировать или включать в наблюдение."
          ]
        },
        {
          "title": "Рыночная позиция",
          "items": [
            "Kaspi сравнивается внутри одной товарной карточки.",
            "Ozon использует подтверждённые совпадения и сопоставимые позиции бренда и размера.",
            "При отсутствии других продавцов позиция не рассчитывается."
          ]
        }
      ],
      "tip": "Откройте строку стрелкой справа, чтобы увидеть характеристики, историю цены и найденные предложения."
    },
    "operations": {
      "title": "Операции",
      "intro": "Здесь выполняются серверные сборщики и отображается их прогресс.",
      "sections": [
        {
          "title": "Перед запуском",
          "items": [
            "Для Kaspi должен быть доступен рабочий браузерный профиль.",
            "??? Ozon ??????????? ???????? ???????????? ????????: Chrome ????????? ???, ???????? ?????????? ????? ??? ????? ??????.",
            "Не запускайте одновременно две операции, использующие один профиль."
          ]
        },
        {
          "title": "Статусы",
          "items": [
            "«Выполняется» — процесс активен.",
            "«Окончено» — операция завершилась успешно.",
            "«Ошибка» — откройте журнал и повторите проблемные позиции."
          ]
        }
      ],
      "tip": "Окно браузера можно свернуть, но нельзя закрывать до завершения операции."
    },
    "reports": {
      "title": "Отчёты",
      "intro": "Сводная аналитика по качеству данных, рискам, ценовым диапазонам и возможностям.",
      "sections": [
        {
          "title": "Интерпретация",
          "items": [
            "Риски строятся только по подтверждённым рыночным данным.",
            "Потенциал является оценкой возможного изменения выручки, а не гарантированной прибылью.",
            "Покрытие показывает долю обработанных позиций."
          ]
        },
        {
          "title": "Экспорт",
          "items": [
            "HTML подходит для просмотра руководителем.",
            "CSV используется для дальнейшего анализа.",
            "JSON предназначен для интеграционных сценариев."
          ]
        }
      ],
      "tip": "Перед формированием отчёта обновите цены и рыночные предложения."
    },
    "schedules": {
      "title": "Расписание",
      "intro": "Плановые задания запускают операции на серверном компьютере без ручного участия.",
      "sections": [
        {
          "title": "Варианты",
          "items": [
            "Ежедневно в указанное время.",
            "По выбранным дням недели.",
            "Через фиксированный интервал."
          ]
        },
        {
          "title": "Требования",
          "items": [
            "Сервер должен быть включён и пользовательская сессия Windows активна.",
            "?????????? ??????? ?????? ???? ??????; ??? Ozon ??????? ???????? ?????????? ????? ??? ????? ??????.",
            "Операции одного ресурса выполняются последовательно."
          ]
        }
      ],
      "tip": "Первое расписание лучше создать на безопасное время и проверить журнал запуска."
    },
    "users": {
      "title": "Сотрудники",
      "intro": "Управление ролями, активностью учётных записей и доступом к каналам продаж.",
      "sections": [
        {
          "title": "Роли",
          "items": [
            "Администратор управляет сотрудниками и системными параметрами.",
            "Оператор запускает рабочие операции.",
            "Наблюдатель просматривает данные без изменений."
          ]
        },
        {
          "title": "Доступ",
          "items": [
            "Активность учётной записи управляется переключателем.",
            "Доступ к Kaspi и Ozon назначается независимо.",
            "Удаление сотрудника необратимо."
          ]
        }
      ],
      "tip": "Для временного восстановления доступа создайте новый код восстановления."
    },
    "settings": {
      "title": "Настройки",
      "intro": "Персональные параметры интерфейса и системные настройки подключённых сборщиков.",
      "sections": [
        {
          "title": "Персональные параметры",
          "items": [
            "Язык и тема сохраняются для текущего сотрудника.",
            "Курсы валют используются только для отображения и расчётов.",
            "Системная тема следует настройке Windows."
          ]
        },
        {
          "title": "Параметры администратора",
          "items": [
            "Источники каталога клиента и рыночные категории хранятся раздельно.",
            "После изменения ссылок сохраните настройки и выполните обнаружение товаров.",
            "Работающие профили браузеров не удаляются."
          ]
        }
      ],
      "tip": "Изменения темы применяются сразу; остальные параметры сохраняются кнопкой «Сохранить»."
    }
  },
  "kk": {
    "dashboard": {
      "title": "Шолу",
      "intro": "Каталог, дерек сапасы, баға тәуекелдері және соңғы операциялар бойынша жиынтық көрініс.",
      "sections": [
        {
          "title": "Не көрсетіледі",
          "items": [
            "Қосылған арналардағы клиент тауарларының жалпы саны.",
            "Бағалары мен нарық деректері дайын тауарлар үлесі.",
            "Тек расталған сәйкестіктер бойынша тәуекелдер мен баға әлеуеті."
          ]
        },
        {
          "title": "Қалай әрекет ету керек",
          "items": [
            "Жеке позицияларды тексеру үшін «Тауарлар» бөлімін ашыңыз.",
            "Каталог пен бағаларды жаңарту үшін «Операциялар» бөлімін пайдаланыңыз.",
            "Жиынтық талдау үшін «Есептер» бөліміне өтіңіз."
          ]
        }
      ],
      "tip": "Көрсеткіштер операция аяқталып, бет жаңартылғаннан кейін қайта есептеледі."
    },
    "products": {
      "title": "Тауарлар",
      "intro": "Клиенттің негізгі каталогы. Басқа сатушылардың нарық карточкалары жеке жолдар ретінде көрсетілмейді.",
      "sections": [
        {
          "title": "Сүзгілер және іздеу",
          "items": [
            "Іздеу атау, код, бренд, өлшем және сатушы бойынша жұмыс істейді.",
            "Арна, бренд, мәртебе және өзектілік нәтижені тарылтады.",
            "Таңдалған позицияларды талдауға немесе бақылауға қосуға болады."
          ]
        },
        {
          "title": "Нарық позициясы",
          "items": [
            "Kaspi бір тауар карточкасының ішінде салыстырылады.",
            "Ozon расталған сәйкестіктерді және бренд пен өлшем бойынша салыстырмалы позицияларды қолданады.",
            "Басқа сатушылар болмаса, позиция есептелмейді."
          ]
        }
      ],
      "tip": "Сипаттамалар, баға тарихы және ұсыныстарды көру үшін оң жақтағы көрсеткіні басыңыз."
    },
    "operations": {
      "title": "Операциялар",
      "intro": "Серверлік жинаушылардың орындалуы мен прогресі осы жерде көрсетіледі.",
      "sections": [
        {
          "title": "Іске қосар алдында",
          "items": [
            "Kaspi үшін жұмыс браузер профилі қолжетімді болуы керек.",
            "Ozon ???? ?????????? ????????? ???????? ??????????: Chrome ??? ???????, ???????? ???? ?????? ???? ??????? ????????.",
            "Бір профильді пайдаланатын екі операцияны қатар іске қоспаңыз."
          ]
        },
        {
          "title": "Мәртебелер",
          "items": [
            "«Орындалуда» — процесс белсенді.",
            "«Аяқталды» — операция сәтті аяқталды.",
            "«Қате» — журналды ашып, проблемалы позицияларды қайталаңыз."
          ]
        }
      ],
      "tip": "Браузер терезесін кішірейтуге болады, бірақ операция аяқталғанша жаппаңыз."
    },
    "reports": {
      "title": "Есептер",
      "intro": "Дерек сапасы, тәуекелдер, баға диапазондары және мүмкіндіктер бойынша жиынтық талдау.",
      "sections": [
        {
          "title": "Түсіндіру",
          "items": [
            "Тәуекелдер тек расталған нарық деректерімен есептеледі.",
            "Әлеует — табыстың ықтимал өзгеру бағасы, кепілденген пайда емес.",
            "Қамту өңделген позициялар үлесін көрсетеді."
          ]
        },
        {
          "title": "Экспорт",
          "items": [
            "HTML басшыға қарауға ыңғайлы.",
            "CSV кейінгі талдау үшін қолданылады.",
            "JSON интеграциялық сценарийлерге арналған."
          ]
        }
      ],
      "tip": "Есеп жасамас бұрын бағалар мен нарық ұсыныстарын жаңартыңыз."
    },
    "schedules": {
      "title": "Кесте",
      "intro": "Жоспарлы тапсырмалар серверде операцияларды қолмен араласпай іске қосады.",
      "sections": [
        {
          "title": "Нұсқалар",
          "items": [
            "Күн сайын көрсетілген уақытта.",
            "Таңдалған апта күндері.",
            "Белгіленген аралықпен."
          ]
        },
        {
          "title": "Талаптар",
          "items": [
            "Сервер қосулы, Windows пайдаланушы сессиясы белсенді болуы керек.",
            "??????? ??????????? ????? ????? ?????; Ozon ???? ???????? ???? ?????? ???? ??????? ????? ??? ????????.",
            "Бір ресурс операциялары кезекпен орындалады."
          ]
        }
      ],
      "tip": "Алғашқы кестені қауіпсіз уақытқа қойып, іске қосу журналын тексеріңіз."
    },
    "users": {
      "title": "Қызметкерлер",
      "intro": "Рөлдерді, есептік жазба белсенділігін және сату арналарына қолжетімділікті басқару.",
      "sections": [
        {
          "title": "Рөлдер",
          "items": [
            "Әкімші қызметкерлер мен жүйелік параметрлерді басқарады.",
            "Оператор жұмыс операцияларын іске қосады.",
            "Бақылаушы деректерді өзгертпей қарайды."
          ]
        },
        {
          "title": "Қолжетімділік",
          "items": [
            "Есептік жазба белсенділігі ауыстырғышпен басқарылады.",
            "Kaspi және Ozon қолжетімділігі бөлек тағайындалады.",
            "Қызметкерді жою қайтарылмайды."
          ]
        }
      ],
      "tip": "Уақытша қолжетімділікті қалпына келтіру үшін жаңа қалпына келтіру кодын жасаңыз."
    },
    "settings": {
      "title": "Баптаулар",
      "intro": "Интерфейстің жеке параметрлері және қосылған жинаушылардың жүйелік баптаулары.",
      "sections": [
        {
          "title": "Жеке параметрлер",
          "items": [
            "Тіл мен тақырып ағымдағы қызметкер үшін сақталады.",
            "Валюта бағамдары тек көрсету және есептеу үшін қолданылады.",
            "Жүйелік тақырып Windows баптауына сәйкес келеді."
          ]
        },
        {
          "title": "Әкімші параметрлері",
          "items": [
            "Клиент каталогының көздері мен нарық санаттары бөлек сақталады.",
            "Сілтемелерді өзгерткен соң баптауларды сақтап, тауарларды анықтауды іске қосыңыз.",
            "Жұмыс браузер профильдері жойылмайды."
          ]
        }
      ],
      "tip": "Тақырып бірден қолданылады; басқа параметрлер «Сақтау» батырмасымен бекітіледі."
    }
  },
  "en": {
    "dashboard": {
      "title": "Overview",
      "intro": "A consolidated view of the catalogue, data quality, price risks and recent operations.",
      "sections": [
        {
          "title": "What you see",
          "items": [
            "Total client products across connected sales channels.",
            "Share of products with ready prices and market data.",
            "Risks and price potential based only on confirmed matches."
          ]
        },
        {
          "title": "What to do next",
          "items": [
            "Open Products to review individual positions.",
            "Use Operations to refresh catalogues and prices.",
            "Open Reports for consolidated analytics."
          ]
        }
      ],
      "tip": "Metrics are recalculated after operations finish and the page is refreshed."
    },
    "products": {
      "title": "Products",
      "intro": "The client catalogue. Market listings from other sellers are not shown as separate client rows.",
      "sections": [
        {
          "title": "Filters and search",
          "items": [
            "Search by title, code, brand, size or seller.",
            "Channel, brand, status and freshness narrow the results.",
            "Selected products can be analyzed or added to watch."
          ]
        },
        {
          "title": "Market position",
          "items": [
            "Kaspi is compared within the same product card.",
            "Ozon uses confirmed matches and comparable brand-size positions.",
            "Position is not calculated when no other sellers are found."
          ]
        }
      ],
      "tip": "Use the arrow on the right to open specifications, price history and found offers."
    },
    "operations": {
      "title": "Operations",
      "intro": "Server-side collectors and their progress are managed here.",
      "sections": [
        {
          "title": "Before starting",
          "items": [
            "A working browser profile must be available for Kaspi.",
            "For Ozon, use ?Prepare browser?: Chrome opens automatically, then choose a Russian city or pickup point.",
            "Do not run two operations that use the same profile at once."
          ]
        },
        {
          "title": "Statuses",
          "items": [
            "Running — the process is active.",
            "Completed — the operation finished successfully.",
            "Failed — open the log and retry the affected products."
          ]
        }
      ],
      "tip": "The browser may be minimized, but do not close it until the operation finishes."
    },
    "reports": {
      "title": "Reports",
      "intro": "Consolidated analytics for data quality, risks, price bands and opportunities.",
      "sections": [
        {
          "title": "Interpretation",
          "items": [
            "Risks use confirmed market data only.",
            "Potential estimates possible revenue movement; it is not guaranteed profit.",
            "Coverage shows the share of processed products."
          ]
        },
        {
          "title": "Export",
          "items": [
            "HTML is suitable for management review.",
            "CSV supports further analysis.",
            "JSON is intended for integration scenarios."
          ]
        }
      ],
      "tip": "Refresh prices and market offers before generating a report."
    },
    "schedules": {
      "title": "Schedules",
      "intro": "Scheduled jobs run operations on the server without manual action.",
      "sections": [
        {
          "title": "Options",
          "items": [
            "Daily at a specified time.",
            "On selected weekdays.",
            "At a fixed interval."
          ]
        },
        {
          "title": "Requirements",
          "items": [
            "The server must be on and the Windows user session active.",
            "Browser profiles must be ready; for Ozon, choose a Russian city or pickup point first.",
            "Operations using the same resource run sequentially."
          ]
        }
      ],
      "tip": "Create the first schedule at a safe time and verify its run log."
    },
    "users": {
      "title": "Employees",
      "intro": "Manage roles, account status and access to sales channels.",
      "sections": [
        {
          "title": "Roles",
          "items": [
            "Administrators manage employees and system settings.",
            "Operators run working operations.",
            "Viewers can inspect data without modifying it."
          ]
        },
        {
          "title": "Access",
          "items": [
            "Account status is controlled with a switch.",
            "Kaspi and Ozon access is assigned independently.",
            "Deleting an employee cannot be undone."
          ]
        }
      ],
      "tip": "Generate a new recovery code for temporary access recovery."
    },
    "settings": {
      "title": "Settings",
      "intro": "Personal interface preferences and system settings for connected collectors.",
      "sections": [
        {
          "title": "Personal preferences",
          "items": [
            "Language and theme are stored for the current employee.",
            "Exchange rates are used only for display and calculations.",
            "System theme follows the Windows appearance setting."
          ]
        },
        {
          "title": "Administrator settings",
          "items": [
            "Client catalogue sources and market categories are stored separately.",
            "After changing URLs, save settings and run product discovery.",
            "Working browser profiles are preserved."
          ]
        }
      ],
      "tip": "Theme changes immediately; save other settings with the Save button."
    }
  }
};
