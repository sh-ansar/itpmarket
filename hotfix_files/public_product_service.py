from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

PUBLIC_CAPABILITIES = [
    {"code": "sales_automation", "title_key": "public_cap_sales_title", "text_key": "public_cap_sales_text"},
    {"code": "market_analytics", "title_key": "public_cap_market_title", "text_key": "public_cap_market_text"},
    {"code": "assortment_control", "title_key": "public_cap_assortment_title", "text_key": "public_cap_assortment_text"},
    {"code": "price_recommendations", "title_key": "public_cap_recommend_title", "text_key": "public_cap_recommend_text"},
    {"code": "scheduled_operations", "title_key": "public_cap_automation_title", "text_key": "public_cap_automation_text"},
    {"code": "multi_channel", "title_key": "public_cap_channels_title", "text_key": "public_cap_channels_text"},
]

DEFAULT_PUBLIC_SETTINGS = {
    "operator_name": "",
    "operator_registration_number": "",
    "operator_address": "",
    "legal_email": "",
    "support_email": "",
    "jurisdiction": "",
    "privacy_effective_date": "",
    "data_retention_months": 12,
    "cross_border_transfer": False,
    "analytics_cookies_enabled": False,
    "offer_enabled": False,
    "offer_title": "",
    "offer_price_text": "",
    "offer_payment_terms": "",
    "public_contact_phone": "",
}

CONSENT_VERSION = "3.4.14-2026-07-30"

LEGAL_CONTENT: dict[str, dict[str, dict[str, Any]]] = {
    "ru": {
        "privacy": {
            "title": "Политика конфиденциальности",
            "lead": "Настоящая политика определяет порядок обработки персональных данных на публичном сайте и в сервисе Spyon.",
            "sections": [
                ("1. Кто обрабатывает данные", [
                    "Оператором публичного сайта, заявок на подключение и учётных записей является лицо, указанное в реквизитах настоящего документа. Если Spyon обрабатывает данные сотрудников, клиентов или контрагентов компании-пользователя по её поручению, роли и обязанности сторон дополнительно определяются договором.",
                    "До публичного запуска оператор обязан заполнить наименование, регистрационный номер, адрес, юридический контакт и применимую юрисдикцию.",
                ]),
                ("2. Какие данные могут обрабатываться", [
                    "Данные заявки: имя и должность контактного лица, компания, регистрационный номер, электронная почта, телефон, выбранные задачи и комментарий.",
                    "Данные учётной записи и доступа: имя, рабочая электронная почта, роль, доступные площадки, сведения об активации, входах и изменениях настроек.",
                    "Технические и защитные данные: IP-адрес, идентификаторы сессии, время и результат запросов, журнал действий, диагностические сообщения, сведения о браузере и устройстве в объёме, необходимом для безопасности и поддержки.",
                    "Коммерческие данные рабочего пространства: каталог, цены, характеристики, коды товаров, результаты сопоставления и отчёты. Оператор не запрашивает специальные категории персональных данных и просит не передавать их через публичные формы.",
                ]),
                ("3. Цели и основания обработки", [
                    "Данные используются для обработки заявки, подготовки предложения и подключения, заключения и исполнения договора, управления доступом, оказания поддержки, обеспечения информационной безопасности, предотвращения злоупотреблений, ведения обязательного учёта и защиты законных интересов сторон.",
                    "Когда применимое законодательство требует согласия, обработка выполняется на основании явно выраженного согласия. Для исполнения договора, выполнения юридической обязанности или обеспечения безопасности могут применяться иные предусмотренные законом основания.",
                ]),
                ("4. Источники и получатели", [
                    "Данные поступают от самого пользователя, уполномоченного представителя компании, администратора рабочего пространства, подключённых компанией источников и из технических журналов сервиса.",
                    "Доступ получают только уполномоченные сотрудники и подрядчики, которым он необходим для эксплуатации, хостинга, резервного копирования, связи, поддержки, безопасности или выполнения требований закона. Такие лица обязаны соблюдать конфиденциальность и использовать данные только в пределах поручения.",
                ]),
                ("5. Срок хранения", [
                    "Если договором или законом не установлен иной срок, данные публичной заявки и связанные служебные записи хранятся до {retention} месяцев после завершения обработки обращения. Данные действующего рабочего пространства хранятся в течение срока обслуживания и разумного периода после его завершения для закрытия расчётов, разрешения споров, безопасности и обязательного учёта.",
                    "Резервные копии удаляются по циклу ротации. Обезличенные статистические данные могут храниться дольше, если они не позволяют идентифицировать человека.",
                ]),
                ("6. Трансграничная передача", [
                    "{cross_border}",
                    "До передачи оператор оценивает применимые ограничения, местонахождение получателя, договорные гарантии и необходимые меры защиты. Настройка трансграничной передачи в панели сама по себе не заменяет такую юридическую оценку.",
                ]),
                ("7. Защита данных", [
                    "Применяются разграничение прав, защищённые сессии, журналирование действий, резервное копирование, обновление компонентов и организационные меры конфиденциальности. Меры выбираются с учётом характера данных и рисков.",
                    "Ни один способ передачи или хранения не исключает риск полностью. При выявлении инцидента оператор действует в порядке и сроки, предусмотренные применимым законодательством и договором.",
                ]),
                ("8. Права пользователя", [
                    "В пределах применимого законодательства пользователь может запросить сведения об обработке, доступ к данным, их исправление, обновление, удаление или ограничение обработки, возразить против отдельных операций, получить переносимую копию и отозвать ранее данное согласие.",
                    "Отзыв согласия не влияет на законность обработки до отзыва и не прекращает обработку, необходимую по договору, закону или для защиты прав. Запрос направляется по адресу: {legal_contact}. Перед исполнением запроса оператор вправе проверить личность и полномочия заявителя.",
                    "Пользователь также вправе обратиться в компетентный орган по защите персональных данных или в суд в соответствии с применимым законодательством.",
                ]),
                ("9. Данные несовершеннолетних", [
                    "Публичная заявка и рабочий сервис предназначены для представителей организаций и совершеннолетних пользователей. Оператор не собирает сознательно данные несовершеннолетних через эти формы.",
                ]),
                ("10. Изменения и контакты", [
                    "Актуальная редакция публикуется на этой странице с новой версией и датой вступления в силу. Существенные изменения для действующих клиентов могут дополнительно сообщаться через рабочее пространство или согласованный канал связи.",
                    "Юридические запросы по данным направляются по адресу: {legal_contact}.",
                ]),
            ],
        },
        "terms": {
            "title": "Условия использования сервиса",
            "lead": "Условия регулируют использование публичного сайта и Spyon и применяются вместе с договором, заказом или иным соглашением с клиентом.",
            "sections": [
                ("1. Назначение сервиса", [
                    "Spyon предназначен для сбора и нормализации разрешённых коммерческих данных, контроля ассортимента, анализа рыночной позиции, выполнения плановых операций и подготовки отчётов.",
                    "Функциональность, лимиты, поддержка и уровень доступности для конкретного клиента определяются договором и настройками рабочего пространства.",
                ]),
                ("2. Полномочия пользователя", [
                    "Пользователь подтверждает, что действует от своего имени либо уполномочен компанией, которой принадлежит рабочее пространство. Администратор компании самостоятельно назначает роли и отвечает за своевременное прекращение доступа уволенных или неуполномоченных сотрудников.",
                    "Учётные данные являются персональными. Их нельзя передавать третьим лицам, публиковать или использовать совместно несколькими людьми. О подозрении на компрометацию необходимо незамедлительно сообщить оператору.",
                ]),
                ("3. Законное использование данных и площадок", [
                    "Пользователь вправе загружать и обрабатывать только те данные, на использование которых у него есть законные основания и необходимые разрешения.",
                    "Запрещены обход ограничений, несанкционированный доступ, вмешательство в работу внешних площадок, чрезмерная нагрузка, распространение вредоносного кода, нарушение прав третьих лиц и использование сервиса для незаконной деятельности.",
                    "Подключение внешней площадки не означает, что её правила разрешают любую автоматизацию. Клиент отвечает за соблюдение условий своего аккаунта, договоров и применимых ограничений соответствующей площадки.",
                ]),
                ("4. Аналитика и решения", [
                    "Отчёты и рекомендации формируются по доступным данным, правилам сопоставления и настройкам на момент расчёта. Неполные, устаревшие или изменённые внешней площадкой данные могут повлиять на результат.",
                    "Материалы сервиса носят информационно-аналитический характер, не являются финансовой, налоговой или юридической консультацией и не гарантируют прибыль, продажи, конкретную цену или иной коммерческий результат. Решение принимает пользователь после собственной проверки.",
                ]),
                ("5. Внешние сервисы", [
                    "Работа отдельных функций зависит от доступности сайтов, API, браузерных профилей, сети и правил третьих лиц. Внешняя площадка может изменить интерфейс или ограничить доступ без уведомления оператора.",
                    "Оператор вправе временно приостановить зависимую функцию для диагностики, безопасности или адаптации, не удаляя данные клиента без предусмотренного основания.",
                ]),
                ("6. Интеллектуальная собственность и конфиденциальность", [
                    "Права на программный код, дизайн, документацию, структуру базы, методы нормализации и внутренние алгоритмы принадлежат правообладателю или используются им на законном основании. Клиент получает ограниченное право использования на срок и в пределах договора.",
                    "Данные клиента сохраняют установленную договором принадлежность. Стороны обязаны не раскрывать конфиденциальную информацию, кроме случаев, прямо предусмотренных договором или законом.",
                ]),
                ("7. Приостановление и прекращение", [
                    "Доступ может быть временно ограничен при угрозе безопасности, нарушении настоящих условий, истечении оплаты, требовании компетентного органа или необходимости срочных технических работ. Когда это разумно и допустимо, клиент уведомляется.",
                    "Порядок выгрузки и удаления данных после прекращения обслуживания определяется договором, политикой хранения и обязательными требованиями.",
                ]),
                ("8. Ответственность", [
                    "Ответственность сторон, допустимые ограничения и порядок возмещения определяются договором и императивными нормами применимого права. Настоящие условия не исключают ответственность, которую нельзя ограничить законом.",
                    "Оператор не отвечает за решения клиента, законность его исходных данных, действия его сотрудников, изменения внешних площадок и сбои вне разумного контроля оператора, если иное прямо не установлено договором или законом.",
                ]),
                ("9. Применимое право и споры", [
                    "К отношениям применяется: {jurisdiction}. До обращения в суд стороны стремятся урегулировать спор письменной претензией и переговорами, если иной обязательный порядок не установлен законом или договором.",
                ]),
                ("10. Изменения условий", [
                    "Новая редакция публикуется с указанием версии. Для действующих платных клиентов изменения, затрагивающие существенные условия, применяются в порядке, установленном договором и законодательством.",
                ]),
            ],
        },
        "cookies": {
            "title": "Политика cookies и локального хранения",
            "lead": "Документ объясняет, какие данные браузера необходимы для работы сайта и сервиса Spyon.",
            "sections": [
                ("1. Обязательные технологии", [
                    "Сессионные cookies поддерживают авторизацию, защиту запросов и целостность пользовательской сессии. Без них вход и защищённые функции могут не работать.",
                    "Локальное хранилище браузера сохраняет выбранные язык и тему интерфейса. Эти значения не используются для межсайтового рекламного отслеживания.",
                ]),
                ("2. Аналитика", [
                    "{analytics}",
                    "Если необязательная аналитика будет включена, оператор должен указать поставщика, состав данных, срок хранения и механизм согласия до начала такой обработки.",
                ]),
                ("3. Управление", [
                    "Пользователь может удалить cookies и локальные данные в настройках браузера. Блокировка обязательных cookies приведёт к выходу из аккаунта или невозможности пользоваться защищёнными разделами.",
                ]),
            ],
        },
        "consent": {
            "title": "Согласие на обработку персональных данных",
            "lead": "Согласие применяется при отправке публичной заявки на подключение компании к Spyon.",
            "sections": [
                ("1. Содержание согласия", [
                    "Отправляя заявку и устанавливая соответствующую отметку, пользователь подтверждает, что ознакомился с Политикой конфиденциальности и добровольно разрешает оператору обрабатывать указанные в форме контактные и организационные данные.",
                    "Цели обработки: рассмотрение обращения, связь с заявителем, уточнение требований, подготовка демонстрации или предложения, организация подключения и защита от злоупотреблений.",
                ]),
                ("2. Допустимые действия", [
                    "Обработка может включать получение, запись, систематизацию, хранение, уточнение, использование, передачу уполномоченным обработчикам, ограничение, обезличивание и удаление в пределах заявленных целей и применимого законодательства.",
                ]),
                ("3. Срок, отзыв и права", [
                    "Согласие действует до достижения целей либо не более {retention} месяцев после завершения работы с заявкой, если более длительный срок не требуется законом, договором или для защиты прав.",
                    "Согласие можно отозвать по адресу {legal_contact}. Пользователь сохраняет права, перечисленные в Политике конфиденциальности. Отзыв не имеет обратной силы и не прекращает обработку на ином законном основании.",
                ]),
            ],
        },
        "offer": {
            "title": "Публичная оферта",
            "lead": "Оферта может публиковаться только после заполнения реквизитов, состава услуг, цены, порядка оплаты и иных обязательных коммерческих условий.",
            "sections": [
                ("Статус документа", [
                    "До активации оферты эта страница не создаёт обязательства заключить договор. Подключение выполняется на основании отдельного договора, заказа или иного согласованного документа.",
                    "Перед публикацией оферта должна быть проверена юристом с учётом страны оператора, модели оплаты, налогов, возвратов, уровня сервиса и порядка прекращения обслуживания.",
                ]),
            ],
        },
    },
    "kk": {
        "privacy": {
            "title": "Құпиялық саясаты",
            "lead": "Осы саясат Spyon ашық сайтында және сервисінде дербес деректерді өңдеу тәртібін белгілейді.",
            "sections": [
                ("1. Деректерді кім өңдейді", ["Сайттың, қосылу өтінімдерінің және есептік жазбалардың операторы осы құжатта көрсетілген тұлға болып табылады. Клиент компанияның деректері оның тапсырмасы бойынша өңделсе, тараптардың рөлдері шартта қосымша айқындалады.", "Жария іске қосылғанға дейін оператор атауын, тіркеу нөмірін, мекенжайын, заңды байланысын және юрисдикциясын толтыруы тиіс."]),
                ("2. Өңделетін деректер", ["Өтінімдегі байланыс және ұйым туралы деректер, пайдаланушының аты, жұмыс email-ы, рөлі мен қолжетімділігі өңделуі мүмкін.", "Қауіпсіздік пен қолдау үшін IP-мекенжай, сессия, уақыт, әрекет журналы, браузер және диагностикалық оқиғалар тіркелуі мүмкін.", "Жұмыс кеңістігінде каталог, баға, сипаттама, тауар коды, сәйкестендіру нәтижесі және есептер өңделеді. Арнайы санаттағы деректерді жария нысандар арқылы бермеу қажет."]),
                ("3. Мақсаттар мен негіздер", ["Деректер өтінімді қарау, шартты дайындау және орындау, қолжетімділікті басқару, қолдау, қауіпсіздік, теріс пайдалануды болдырмау және заңды міндеттерді орындау үшін қолданылады.", "Заң келісімді талап еткенде, өңдеу нақты берілген келісімге негізделеді; шарт, заңды міндет немесе қауіпсіздік үшін басқа заңды негіздер қолданылуы мүмкін."]),
                ("4. Алушылар", ["Деректерге тек сервис жұмысы, хостинг, резервтік көшіру, байланыс, қолдау, қауіпсіздік немесе заң талабы үшін қажет уәкілетті тұлғалар қол жеткізеді. Олар құпиялықты сақтауға міндетті."]),
                ("5. Сақтау мерзімі", ["Егер шартта немесе заңда басқа мерзім белгіленбесе, жария өтінім деректері өтінім аяқталғаннан кейін {retention} айға дейін сақталады. Жұмыс кеңістігінің деректері қызмет көрсету кезеңінде және есеп айырысу, дау мен қауіпсіздік үшін қажетті негізді мерзімде сақталады.", "Резервтік көшірмелер ротация циклі бойынша жойылады."]),
                ("6. Трансшекаралық беру", ["{cross_border}", "Беруге дейін оператор қолданылатын шектеулерді, алушы елді, шарттық кепілдіктерді және қорғау шараларын бағалайды."]),
                ("7. Қорғау", ["Қолжетімділікті бөлу, қорғалған сессиялар, журналдау, резервтік көшіру және ұйымдастырушылық құпиялық шаралары қолданылады. Ешбір сақтау тәсілі тәуекелді толық жоймайды."]),
                ("8. Пайдаланушы құқықтары", ["Қолданылатын заң шегінде пайдаланушы ақпарат, қолжетімділік, түзету, жою, шектеу, қарсылық, тасымалданатын көшірме және келісімді кері қайтару туралы сұрау жібере алады.", "Сұрау {legal_contact} мекенжайына жіберіледі. Оператор өтініш берушінің жеке басы мен өкілеттігін тексере алады."]),
                ("9. Кәмелетке толмағандар", ["Сервис ұйым өкілдеріне және кәмелетке толған пайдаланушыларға арналған. Оператор жария нысандар арқылы балалардың деректерін әдейі жинамайды."]),
                ("10. Өзгерістер", ["Жаңа редакция нұсқасы және күшіне ену күнімен осы бетте жарияланады. Заңды сұраулар: {legal_contact}."]),
            ],
        },
        "terms": {
            "title": "Сервисті пайдалану шарттары",
            "lead": "Шарттар Spyon сайтын және сервисін пайдалануды реттейді және клиентпен жасалған шартпен бірге қолданылады.",
            "sections": [
                ("1. Мақсаты", ["Spyon рұқсат етілген коммерциялық деректерді жинау мен қалыпқа келтіруге, ассортиментті бақылауға, нарықтық позицияны талдауға және есеп жасауға арналған."]),
                ("2. Пайдаланушы өкілеттігі", ["Пайдаланушы өз атынан немесе компанияның уәкілетті өкілі ретінде әрекет ететінін растайды. Есептік жазбаны басқа тұлғаға беруге болмайды."]),
                ("3. Заңды пайдалану", ["Клиент тек заңды негізі бар деректерді өңдейді және сыртқы алаңдардың шарттарын сақтайды. Қорғанысты айналып өтуге, рұқсатсыз қол жеткізуге және заңсыз әрекетке тыйым салынады."]),
                ("4. Аналитика", ["Нәтижелер қолжетімді деректер мен баптауларға негізделеді. Олар қаржылық, салықтық немесе заңдық кеңес емес және пайдаға кепілдік бермейді. Соңғы шешімді пайдаланушы өзі қабылдайды."]),
                ("5. Сыртқы сервистер", ["Функциялар сайттар, API, браузер профильдері, желі және үшінші тұлғалардың ережелеріне тәуелді болуы мүмкін."]),
                ("6. Құқықтар және құпиялық", ["Кодқа, дизайнға, құжаттамаға және ішкі алгоритмдерге құқықтар құқық иесіне тиесілі. Клиент деректерінің мәртебесі шартпен айқындалады."]),
                ("7. Тоқтату", ["Қауіпсіздік қатері, шарт бұзылуы, төлемнің аяқталуы немесе міндетті талап кезінде қолжетімділік уақытша шектелуі мүмкін."]),
                ("8. Жауапкершілік", ["Жауапкершілік шартпен және міндетті құқық нормаларымен анықталады. Оператор клиент шешімдеріне, оның бастапқы деректеріне және бақылаудан тыс сыртқы өзгерістерге жауап бермейді, егер заңда өзгеше көрсетілмесе."]),
                ("9. Құқық және даулар", ["Қолданылатын құқық: {jurisdiction}. Тараптар дауды алдымен жазбаша талап пен келіссөз арқылы шешуге ұмтылады."]),
                ("10. Өзгерістер", ["Жаңа редакция нұсқасымен жарияланады және шарт пен заңда белгіленген тәртіппен қолданылады."]),
            ],
        },
        "cookies": {
            "title": "Cookies және жергілікті сақтау саясаты",
            "lead": "Құжат сайт пен сервистің жұмысына қажетті браузер деректерін түсіндіреді.",
            "sections": [
                ("1. Міндетті технологиялар", ["Сессиялық cookies авторизацияны, сұрауларды қорғауды және сессия тұтастығын қамтамасыз етеді. Жергілікті сақтау интерфейс тілі мен тақырыбын сақтайды."]),
                ("2. Аналитика", ["{analytics}", "Қосымша аналитика қосылса, оператор алдын ала жеткізушіні, деректер құрамын, сақтау мерзімін және келісім механизмін көрсетуі тиіс."]),
                ("3. Басқару", ["Пайдаланушы браузер баптауларында cookies пен жергілікті деректерді өшіре алады. Міндетті cookies-ті бұғаттау қорғалған бөлімдердің жұмысын тоқтатады."]),
            ],
        },
        "consent": {
            "title": "Дербес деректерді өңдеуге келісім",
            "lead": "Келісім компанияны Spyon сервисіне қосу туралы жария өтінім жіберілгенде қолданылады.",
            "sections": [
                ("1. Келісім", ["Өтінімді жіберу арқылы пайдаланушы Құпиялық саясатымен танысқанын растайды және операторға байланыс пен ұйым деректерін өтінімді қарау, кері байланыс, ұсыныс дайындау және қауіпсіздік мақсатында өңдеуге ерікті түрде келіседі."]),
                ("2. Әрекеттер", ["Өңдеу алу, жазу, жүйелеу, сақтау, нақтылау, пайдалану, уәкілетті өңдеушілерге беру, шектеу, иесіздендіру және жоюды қамтуы мүмкін."]),
                ("3. Мерзім және кері қайтару", ["Келісім мақсатқа жеткенге дейін немесе өтінім аяқталғаннан кейін {retention} айға дейін қолданылады, егер басқа мерзім шартпен немесе заңмен талап етілмесе.", "Келісімді {legal_contact} мекенжайы арқылы кері қайтаруға болады. Кері қайтару бұрынғы заңды өңдеуге әсер етпейді."]),
            ],
        },
        "offer": {
            "title": "Жария оферта",
            "lead": "Оферта оператор деректемелері, қызмет құрамы, баға және төлем тәртібі толық толтырылғаннан кейін ғана жарияланады.",
            "sections": [("Құжат мәртебесі", ["Оферта белсендірілгенге дейін бұл бет шарт жасасу міндеттемесін тудырмайды. Қосылу жеке шарт немесе келісілген тапсырыс негізінде жүргізіледі.", "Жариялау алдында құжатты оператор елінің құқығын, салықты, төлемді, қайтаруды және қызмет деңгейін ескеретін заңгер тексеруі тиіс."])],
        },
    },
    "en": {
        "privacy": {
            "title": "Privacy Policy",
            "lead": "This policy explains how personal data is processed on the public website and in the Spyon service.",
            "sections": [
                ("1. Who processes data", ["The operator of the public website, connection requests and user accounts is the entity identified in this document. Where Spyon processes employee, customer or counterparty data on a client company's instructions, the parties' roles are additionally governed by their agreement.", "Before public launch, the operator must complete its name, registration number, address, legal contact and applicable jurisdiction."]),
                ("2. Data categories", ["Request data may include contact name, company, registration number, email, phone, requested functions and comments.", "Account and access data may include name, business email, role, marketplace permissions, activation, sign-in and settings events.", "Security and technical data may include IP address, session identifiers, timestamps, audit logs, diagnostics, browser and device information to the extent necessary for operation and protection.", "Workspace data may include catalogues, prices, attributes, product codes, matching results and reports. Special-category data is not requested and should not be submitted through public forms."]),
                ("3. Purposes and legal bases", ["Data is used to handle requests, prepare and perform agreements, manage access, provide support, secure the service, prevent abuse, comply with legal duties and protect legitimate interests.", "Where consent is required, processing relies on explicit consent. Contract performance, legal obligations and security may provide other lawful bases under applicable law."]),
                ("4. Sources and recipients", ["Data is obtained from users, authorized company representatives, workspace administrators, sources connected by the client and technical service logs.", "Access is limited to authorized personnel and processors supporting hosting, backups, communications, support, security or legal compliance, subject to confidentiality and purpose limitations."]),
                ("5. Retention", ["Unless a contract or law requires otherwise, public request data and related service records are retained for up to {retention} months after the request is closed. Active workspace data is retained during service and for a reasonable period needed for settlement, disputes, security and mandatory records.", "Backups are deleted through the applicable rotation cycle. Properly anonymized statistics may be retained longer."]),
                ("6. International transfers", ["{cross_border}", "Before a transfer, the operator must assess applicable restrictions, recipient location, contractual safeguards and security measures. Enabling a setting does not replace that legal assessment."]),
                ("7. Security", ["Access controls, protected sessions, audit logs, backups, component updates and organizational confidentiality measures are applied according to risk. No transmission or storage method can eliminate all risk."]),
                ("8. Individual rights", ["Subject to applicable law, an individual may request information, access, correction, deletion, restriction, objection, portability and withdrawal of consent.", "Requests should be sent to {legal_contact}. The operator may verify identity and authority. Withdrawal does not affect prior lawful processing or processing based on another lawful ground.", "Individuals may also complain to a competent data-protection authority or court."]),
                ("9. Children", ["The public request and business service are intended for organization representatives and adults. The operator does not knowingly collect children's data through these forms."]),
                ("10. Changes and contact", ["The current version is published on this page with its version and effective date. Legal data requests should be sent to {legal_contact}."]),
            ],
        },
        "terms": {
            "title": "Terms of Use",
            "lead": "These terms govern the public website and Spyon service together with the applicable client agreement, order or other commercial document.",
            "sections": [
                ("1. Service purpose", ["Spyon supports lawful commercial-data collection and normalization, assortment control, market-position analytics, scheduled operations and reporting. Client-specific functionality, limits, support and availability are defined by the agreement and workspace settings."]),
                ("2. User authority", ["A user confirms that they act for themselves or are authorized by the company that owns the workspace. Credentials are personal and must not be shared. Company administrators are responsible for roles and timely revocation of access."]),
                ("3. Lawful data and marketplace use", ["Users may process only data they are entitled to use and must comply with third-party marketplace terms. Circumventing controls, unauthorized access, excessive load, malicious code, infringement and illegal use are prohibited."]),
                ("4. Analytics and decisions", ["Outputs depend on available data, matching rules and settings at calculation time. They are informational, not financial, tax or legal advice, and do not guarantee sales, profit, price or another business result. Users make final decisions after their own review."]),
                ("5. External services", ["Some functions depend on websites, APIs, browser profiles, networks and third-party rules that may change without notice. A dependent function may be temporarily suspended for security, diagnostics or adaptation."]),
                ("6. Intellectual property and confidentiality", ["Software, design, documentation, database structure, normalization methods and internal algorithms belong to the right holder or are lawfully used. Client-data ownership and confidentiality are governed by the agreement."]),
                ("7. Suspension and termination", ["Access may be limited for security threats, breaches, expired payment, binding authority requests or urgent maintenance. Data export and deletion after termination follow the agreement, retention policy and mandatory law."]),
                ("8. Liability", ["Liability and any lawful limitations are governed by the agreement and mandatory law. Nothing excludes liability that cannot legally be limited. The operator is not responsible for client decisions, unlawful input data, client personnel, third-party marketplace changes or events beyond reasonable control unless the agreement or law provides otherwise."]),
                ("9. Governing law and disputes", ["The applicable framework is: {jurisdiction}. The parties should first attempt written notice and good-faith negotiation unless another mandatory process applies."]),
                ("10. Changes", ["A revised version is published with a new version number. Material changes for active paid clients apply in accordance with the agreement and applicable law."]),
            ],
        },
        "cookies": {
            "title": "Cookies and Local Storage Policy",
            "lead": "This document explains browser data required for the website and Spyon service.",
            "sections": [
                ("1. Essential technologies", ["Session cookies maintain authentication, request protection and session integrity. Local storage keeps the interface language and theme. These values are not used for cross-site advertising tracking."]),
                ("2. Analytics", ["{analytics}", "If optional analytics is enabled, the operator must identify the provider, data, retention period and consent mechanism before processing begins."]),
                ("3. Controls", ["Users can remove cookies and local data in browser settings. Blocking essential cookies may prevent sign-in and protected functions."]),
            ],
        },
        "consent": {
            "title": "Consent to Personal Data Processing",
            "lead": "This consent applies when a public company-connection request is submitted to Spyon.",
            "sections": [
                ("1. Consent", ["By submitting the request and selecting the consent checkbox, the user confirms that they have read the Privacy Policy and voluntarily permits processing of the submitted contact and organization data for request handling, communication, requirements clarification, proposal or onboarding preparation and abuse prevention."]),
                ("2. Processing operations", ["Processing may include collection, recording, organization, storage, correction, use, transfer to authorized processors, restriction, anonymization and deletion within the stated purposes and applicable law."]),
                ("3. Duration, withdrawal and rights", ["Consent applies until the purposes are achieved or for up to {retention} months after the request is closed, unless a longer period is required by contract, law or legal claims.", "Consent may be withdrawn through {legal_contact}. Withdrawal is not retroactive and does not stop processing based on another lawful ground."]),
            ],
        },
        "offer": {
            "title": "Public Offer",
            "lead": "A public offer may be published only after operator details, service scope, pricing, payment and other mandatory commercial terms are completed.",
            "sections": [("Document status", ["Until the offer is activated, this page does not create an obligation to contract. Onboarding is performed under a separate agreement, order or other accepted document.", "Before publication, counsel should review the offer for the operator's jurisdiction, taxes, payment, refunds, service levels and termination rules."])],
        },
    },
}


class PublicProductService:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def settings(self) -> dict[str, Any]:
        value = dict(DEFAULT_PUBLIC_SETTINGS)
        conn = self._connect()
        try:
            try:
                row = conn.execute("SELECT value_json FROM platform_settings WHERE setting_key='public_product'").fetchone()
            except sqlite3.OperationalError:
                # Older or source-only installations may not have the platform settings table yet.
                row = None
            if row:
                try:
                    stored = json.loads(row["value_json"] or "{}")
                    if isinstance(stored, dict):
                        value.update(stored)
                except json.JSONDecodeError:
                    pass
        finally:
            conn.close()
        return value

    def update_settings(self, payload: dict[str, Any], actor_user_id: int) -> dict[str, Any]:
        current = self.settings()
        allowed = set(DEFAULT_PUBLIC_SETTINGS)
        for key in allowed:
            if key not in payload:
                continue
            if key in {"cross_border_transfer", "analytics_cookies_enabled", "offer_enabled"}:
                current[key] = bool(payload.get(key))
            elif key == "data_retention_months":
                try:
                    current[key] = max(1, min(int(payload.get(key) or 12), 120))
                except (TypeError, ValueError):
                    current[key] = 12
            else:
                current[key] = str(payload.get(key) or "").strip()
        if current.get("offer_enabled"):
            required = [
                current.get("operator_name"),
                current.get("operator_registration_number"),
                current.get("legal_email"),
                current.get("offer_price_text"),
                current.get("offer_payment_terms"),
            ]
            if not all(required):
                raise ValueError(
                    "Для публикации оферты заполните оператора, регистрационный номер, юридический email, стоимость и порядок оплаты."
                )
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO platform_settings(setting_key,value_json,updated_by,updated_at)
                   VALUES('public_product',?,?,?)
                   ON CONFLICT(setting_key) DO UPDATE SET
                     value_json=excluded.value_json,
                     updated_by=excluded.updated_by,
                     updated_at=excluded.updated_at""",
                (json.dumps(current, ensure_ascii=False), int(actor_user_id), stamp),
            )
            conn.commit()
        finally:
            conn.close()
        return current

    @staticmethod
    def _localized_context(settings: dict[str, Any], lang: str) -> dict[str, str]:
        retention = str(max(1, int(settings.get("data_retention_months") or 12)))
        email = str(settings.get("legal_email") or "").strip()
        jurisdiction = str(settings.get("jurisdiction") or "").strip()
        if lang == "kk":
            legal_contact = email or "оператор баптауларында көрсетілетін заңды email"
            jurisdiction_text = jurisdiction or "оператор мен клиент шартында көрсетілетін құқық және соттылық"
            cross_border = (
                "Оператор баптауларында трансшекаралық беру қарастырылған. Ол тек қолданылатын заң талаптары, қажетті кепілдіктер және жеткілікті қорғау орындалған кезде жүргізіледі."
                if settings.get("cross_border_transfer")
                else "Оператор баптауларында тұрақты трансшекаралық беру жоспарланбаған. Мұндай қажеттілік туындаса, саясат пен құқықтық негіз алдын ала жаңартылуы тиіс."
            )
            analytics = (
                "Оператор баптауларында қосымша аналитикалық технологиялар қосылған. Оларды жария пайдалануға дейін жеткізуші мен келісім механизмі нақты көрсетілуі тиіс."
                if settings.get("analytics_cookies_enabled")
                else "Үшінші тараптың жарнамалық және қосымша аналитикалық cookies файлдары әдепкі бойынша өшірілген."
            )
        elif lang == "en":
            legal_contact = email or "the legal email to be configured by the operator"
            jurisdiction_text = jurisdiction or "the governing law and forum stated in the operator-client agreement"
            cross_border = (
                "The operator settings allow international transfers. A transfer may occur only where applicable legal requirements, safeguards and adequate protection are satisfied."
                if settings.get("cross_border_transfer")
                else "The operator settings do not currently contemplate routine international transfers. If such a need arises, the policy and legal basis must be updated in advance."
            )
            analytics = (
                "Optional analytics technologies are enabled in operator settings. The provider and consent mechanism must be identified before public use."
                if settings.get("analytics_cookies_enabled")
                else "Third-party advertising and optional analytics cookies are disabled by default."
            )
        else:
            legal_contact = email or "юридический email, который должен быть указан оператором"
            jurisdiction_text = jurisdiction or "право и подсудность, установленные договором между оператором и клиентом"
            cross_border = (
                "В настройках оператора предусмотрена трансграничная передача. Она допускается только при соблюдении применимых требований закона, наличии необходимого основания, гарантий и достаточного уровня защиты."
                if settings.get("cross_border_transfer")
                else "В настройках оператора регулярная трансграничная передача не предусмотрена. Если такая необходимость возникнет, оператор должен заранее определить основание, получателей, страну и обновить эту политику."
            )
            analytics = (
                "В настройках оператора включены необязательные аналитические технологии. До публичного применения необходимо указать поставщика и обеспечить требуемый механизм согласия."
                if settings.get("analytics_cookies_enabled")
                else "Сторонние рекламные и необязательные аналитические cookies по умолчанию отключены."
            )
        return {
            "retention": retention,
            "legal_contact": legal_contact,
            "jurisdiction": jurisdiction_text,
            "cross_border": cross_border,
            "analytics": analytics,
        }

    def legal_document(self, document: str, locale: str) -> dict[str, Any]:
        lang = locale if locale in {"ru", "kk", "en"} else "ru"
        docs = LEGAL_CONTENT[lang]
        if document not in docs:
            raise KeyError(document)
        settings = self.settings()
        context = self._localized_context(settings, lang)
        source = docs[document]
        sections = [
            (
                str(heading).format_map(context),
                [str(paragraph).format_map(context) for paragraph in paragraphs],
            )
            for heading, paragraphs in source["sections"]
        ]
        required_fields = {
            "operator_name": settings.get("operator_name"),
            "operator_registration_number": settings.get("operator_registration_number"),
            "operator_address": settings.get("operator_address"),
            "legal_email": settings.get("legal_email"),
            "jurisdiction": settings.get("jurisdiction"),
        }
        missing_fields = [key for key, value in required_fields.items() if not str(value or "").strip()]
        effective_date = str(settings.get("privacy_effective_date") or "2026-07-30").strip()
        return {
            "title": source["title"],
            "lead": source["lead"],
            "sections": sections,
            "code": document,
            "locale": lang,
            "settings": settings,
            "consent_version": CONSENT_VERSION,
            "effective_date": effective_date,
            "missing_fields": missing_fields,
            "publication_ready": not missing_fields,
        }
