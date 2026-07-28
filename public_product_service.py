from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

PUBLIC_CAPABILITIES = [
    {"code":"sales_automation","title_key":"public_cap_sales_title","text_key":"public_cap_sales_text"},
    {"code":"market_analytics","title_key":"public_cap_market_title","text_key":"public_cap_market_text"},
    {"code":"assortment_control","title_key":"public_cap_assortment_title","text_key":"public_cap_assortment_text"},
    {"code":"price_recommendations","title_key":"public_cap_recommend_title","text_key":"public_cap_recommend_text"},
    {"code":"scheduled_operations","title_key":"public_cap_automation_title","text_key":"public_cap_automation_text"},
    {"code":"multi_channel","title_key":"public_cap_channels_title","text_key":"public_cap_channels_text"},
]

DEFAULT_PUBLIC_SETTINGS = {
    "operator_name":"",
    "operator_registration_number":"",
    "operator_address":"",
    "legal_email":"",
    "support_email":"",
    "jurisdiction":"",
    "privacy_effective_date":"",
    "data_retention_months":12,
    "cross_border_transfer":False,
    "analytics_cookies_enabled":False,
    "offer_enabled":False,
    "offer_title":"",
    "offer_price_text":"",
    "offer_payment_terms":"",
    "public_contact_phone":"",
}

CONSENT_VERSION = "3.4.0-2026-07-28"

LEGAL_CONTENT = {
"ru": {
"privacy": {"title":"Политика конфиденциальности","lead":"Документ описывает обработку данных при использовании публичного сайта и сервиса ITP Market Intelligence.","sections":[
("1. Оператор",["Оператором является лицо, реквизиты которого указаны ниже. До публичного промышленного запуска эти реквизиты должны быть заполнены администратором платформы."]),
("2. Какие данные обрабатываются",["Контактные данные из заявки: имя, компания, электронная почта, телефон, регистрационный номер и комментарий.","Данные учётной записи сотрудников и технические события, необходимые для безопасности и эксплуатации сервиса."]),
("3. Цели обработки",["Обработка заявки и обратная связь по подключению компании.","Предоставление доступа, разграничение прав, эксплуатация, диагностика и резервное копирование сервиса."]),
("4. Согласие",["При отправке публичной формы пользователь подтверждает согласие на обработку указанных данных и ознакомление с настоящей политикой.","Фиксируются версия согласия, время отправки заявки и выбранный язык."]),
("5. Передача и хранение",["Передача третьим лицам допускается только в объёме, необходимом для работы сервиса, исполнения договора, требований закона или инфраструктуры.","Срок хранения определяется целью обработки, договором, обязательными требованиями и настройками оператора."]),
("6. Права пользователя",["Пользователь вправе запросить сведения об обработке, исправление неточных данных и иные действия, предусмотренные применимым законодательством и договором.","Обращения направляются на юридический контактный адрес оператора."])]},
"terms": {"title":"Условия использования сервиса","lead":"Условия регулируют доступ к информационно-аналитическому сервису и не заменяют индивидуальный договор с компанией.","sections":[
("1. Назначение",["Сервис предназначен для автоматизации коммерческих процессов, контроля ассортимента, аналитики рыночной позиции и подготовки информационных рекомендаций.","Сервис не принимает коммерческие решения вместо пользователя и не гарантирует конкретный финансовый результат."]),
("2. Доступ",["Доступ предоставляется уполномоченным сотрудникам компании после создания рабочего пространства.","Пользователь обязан обеспечивать конфиденциальность учётных данных и действовать в пределах выданных полномочий."]),
("3. Аналитика",["Показатели и рекомендации формируются на основании доступных системе данных и настроек на момент расчёта.","Пользователь самостоятельно оценивает применимость результата и принимает коммерческое решение."]),
("4. Доступность",["Отдельные функции могут зависеть от внешних информационных систем, сети, браузерных сессий и иных технических факторов."]),
("5. Права",["Программный код, интерфейс, структура сервиса и внутренние алгоритмы принадлежат правообладателю либо используются на законном основании."]),
("6. Ответственность",["Ответственность сторон определяется заключённым договором и применимым законодательством.","Публичная демонстрационная информация не является гарантией коммерческого эффекта или офертой, если отдельно не опубликован активный договор-оферта."])]},
"cookies": {"title":"Политика cookies и локального хранения","lead":"Сервис использует технические механизмы, необходимые для авторизации, безопасности и пользовательских настроек.","sections":[
("1. Обязательные данные",["Сессионный cookie поддерживает авторизованную сессию и защиту запросов.","Локальное хранилище браузера используется для выбранного языка и темы интерфейса."]),
("2. Аналитические cookies",["По умолчанию сторонние рекламные и аналитические cookies не используются. При их включении политика должна быть обновлена до активации."]),
("3. Управление",["Пользователь может удалить локальные данные через настройки браузера. Удаление сессионных данных приведёт к выходу из системы."])]},
"consent": {"title":"Согласие на обработку персональных данных","lead":"Текст согласия применяется при отправке публичной заявки на подключение компании.","sections":[
("Согласие",["Отправляя заявку, пользователь добровольно предоставляет данные для обработки обращения, обратной связи, подготовки подключения и администрирования рабочего пространства.","Пользователь подтверждает, что ознакомился с Политикой конфиденциальности и вправе предоставить указанные контактные данные."]),
("Срок и отзыв",["Согласие действует до достижения целей обработки либо в течение иного срока, установленного договором или применимым законодательством.","Запрос на отзыв направляется оператору по юридическому контактному адресу."])]},
"offer": {"title":"Публичная оферта","lead":"Оферта публикуется только после заполнения реквизитов оператора, стоимости и порядка оплаты.","sections":[("Статус документа",["До активации оферты подключение компаний осуществляется на основании отдельного договора или согласованного заказа."])]}
},
"kk": {
"privacy": {"title":"Құпиялық саясаты","lead":"Құжат ITP Market Intelligence ашық сайты мен сервисін пайдалану кезінде деректерді өңдеу тәртібін сипаттайды.","sections":[("1. Оператор",["Оператор — төменде деректемелері көрсетілетін тұлға. Ашық өндірістік іске қосуға дейін деректемелер толтырылуы тиіс."]),("2. Деректер",["Қосылу өтініміндегі байланыс деректері және қауіпсіздікке қажетті техникалық оқиғалар өңделуі мүмкін."]),("3. Мақсаттар",["Өтінімді өңдеу, кері байланыс, қолжетімділікті басқару және сервисті пайдалану."]),("4. Келісім",["Өтінімді жіберу кезінде пайдаланушы деректерді өңдеуге келісімін және саясатпен танысқанын растайды."]),("5. Беру және сақтау",["Деректер сервис, шарт, заң талаптары немесе инфрақұрылым үшін қажетті көлемде ғана беріледі."]),("6. Құқықтар",["Пайдаланушы қолданылатын заңнама мен шартқа сәйкес өз деректері бойынша сұрау жібере алады."])]},
"terms": {"title":"Сервисті пайдалану шарттары","lead":"Шарттар ақпараттық-аналитикалық сервиске қол жеткізуді реттейді және жеке шартты алмастырмайды.","sections":[("1. Мақсат",["Сервис коммерциялық процестерді автоматтандыруға, ассортиментті бақылауға және аналитикаға арналған.","Сервис нақты қаржылық нәтижеге кепілдік бермейді."]),("2. Қолжетімділік",["Қолжетімділік уәкілетті қызметкерлерге беріледі."]),("3. Аналитика",["Пайдаланушы аналитикалық нәтижені қолдану туралы шешімді өзі қабылдайды."]),("4. Қолжетімділік",["Жекелеген функциялар сыртқы жүйелер мен техникалық факторларға тәуелді болуы мүмкін."]),("5. Құқықтар",["Бағдарламалық код пен интерфейс құқық иесіне тиесілі немесе заңды негізде пайдаланылады."]),("6. Жауапкершілік",["Жауапкершілік шартпен және қолданылатын заңнамамен айқындалады."])]},
"cookies": {"title":"Cookies және жергілікті сақтау саясаты","lead":"Сервис авторизация, қауіпсіздік және пайдаланушы параметрлері үшін қажетті техникалық механизмдерді қолданады.","sections":[("1. Міндетті деректер",["Сессиялық cookie авторизацияланған сессияны сақтау үшін қолданылады.","Жергілікті сақтау тіл мен тақырыпты сақтайды."]),("2. Аналитикалық cookies",["Әдепкі бойынша бөгде жарнамалық және аналитикалық cookies қолданылмайды."]),("3. Басқару",["Пайдаланушы жергілікті деректерді браузер параметрлері арқылы жоя алады."])]},
"consent": {"title":"Дербес деректерді өңдеуге келісім","lead":"Келісім мәтіні компанияны қосуға арналған ашық өтінім үшін қолданылады.","sections":[("Келісім",["Өтінімді жіберу арқылы пайдаланушы өтінімді өңдеу, кері байланыс және қосылуды дайындау үшін деректерді өңдеуге келіседі."]),("Мерзім және кері қайтару",["Келісім өңдеу мақсаттарына жеткенге дейін немесе шартта не заңнамада белгіленген мерзімге дейін қолданылады."])]},
"offer": {"title":"Жария оферта","lead":"Оферта оператор деректемелері, құны және төлем тәртібі толтырылғаннан кейін ғана жарияланады.","sections":[("Құжат мәртебесі",["Оферта белсендірілгенге дейін қосылу жеке шарт немесе келісілген тапсырыс негізінде жүргізіледі."])]}
},
"en": {
"privacy": {"title":"Privacy Policy","lead":"This document describes data processing when using the public website and ITP Market Intelligence service.","sections":[("1. Operator",["The operator is the person identified below. Legal details must be completed before a public production launch."]),("2. Data",["Connection-request contact data and technical events required for security and operation may be processed."]),("3. Purposes",["Request handling, feedback, access management, service operation and diagnostics."]),("4. Consent",["By submitting the public form, the user confirms consent to processing and acknowledges this policy."]),("5. Transfer and retention",["Data is shared only to the extent necessary for service delivery, contracts, law or infrastructure."]),("6. User rights",["Users may submit requests regarding their data as provided by applicable law and contract."])]},
"terms": {"title":"Terms of Use","lead":"These terms govern access to the information and analytics service and do not replace an individual agreement.","sections":[("1. Purpose",["The service supports commercial-process automation, assortment control and analytics.","The service does not guarantee a specific financial result."]),("2. Access",["Access is provided to authorized employees."]),("3. Analytics",["Users independently determine whether and how to use analytical results."]),("4. Availability",["Some functions may depend on external systems and technical factors."]),("5. Rights",["Software code and interface belong to the rights holder or are used lawfully."]),("6. Liability",["Liability is determined by the applicable agreement and law."])]},
"cookies": {"title":"Cookies and Local Storage Policy","lead":"The service uses technical mechanisms required for authentication, security and preferences.","sections":[("1. Essential data",["A session cookie maintains the authenticated session.","Local storage keeps language and theme preferences."]),("2. Analytics cookies",["Third-party advertising and analytics cookies are disabled by default."]),("3. Control",["Users may remove local data through browser settings."])]},
"consent": {"title":"Consent to Personal Data Processing","lead":"This consent is used when submitting a public company connection request.","sections":[("Consent",["By submitting a request, the user agrees to processing the submitted data for request handling, feedback and onboarding preparation."]),("Term and withdrawal",["Consent remains effective until the processing purposes are achieved or another period is established by contract or applicable law."])]},
"offer": {"title":"Public Offer","lead":"The public offer is published only after operator details, pricing and payment terms have been completed.","sections":[("Document status",["Until activated, onboarding is performed under a separate agreement or approved order."])]}
}
}

class PublicProductService:
    def __init__(self, db_path: Path): self.db_path=Path(db_path)
    def _connect(self):
        conn=sqlite3.connect(self.db_path,timeout=30); conn.row_factory=sqlite3.Row; conn.execute("PRAGMA busy_timeout=30000"); return conn
    def settings(self)->dict[str,Any]:
        value=dict(DEFAULT_PUBLIC_SETTINGS); conn=self._connect()
        try:
            row=conn.execute("SELECT value_json FROM platform_settings WHERE setting_key='public_product'").fetchone()
            if row:
                try:
                    stored=json.loads(row['value_json'] or '{}')
                    if isinstance(stored,dict): value.update(stored)
                except json.JSONDecodeError: pass
        finally: conn.close()
        return value
    def update_settings(self,payload:dict[str,Any],actor_user_id:int)->dict[str,Any]:
        current=self.settings(); allowed=set(DEFAULT_PUBLIC_SETTINGS)
        for key in allowed:
            if key not in payload: continue
            if key in {'cross_border_transfer','analytics_cookies_enabled','offer_enabled'}: current[key]=bool(payload.get(key))
            elif key=='data_retention_months':
                try: current[key]=max(1,min(int(payload.get(key) or 12),120))
                except (TypeError,ValueError): current[key]=12
            else: current[key]=str(payload.get(key) or '').strip()
        if current.get('offer_enabled'):
            required=[current.get('operator_name'),current.get('operator_registration_number'),current.get('legal_email'),current.get('offer_price_text'),current.get('offer_payment_terms')]
            if not all(required): raise ValueError('Для публикации оферты заполните оператора, регистрационный номер, юридический email, стоимость и порядок оплаты.')
        stamp=datetime.now().astimezone().isoformat(timespec='seconds'); conn=self._connect()
        try:
            conn.execute("""INSERT INTO platform_settings(setting_key,value_json,updated_by,updated_at) VALUES('public_product',?,?,?) ON CONFLICT(setting_key) DO UPDATE SET value_json=excluded.value_json,updated_by=excluded.updated_by,updated_at=excluded.updated_at""",(json.dumps(current,ensure_ascii=False),int(actor_user_id),stamp)); conn.commit()
        finally: conn.close()
        return current
    def legal_document(self,document:str,locale:str)->dict[str,Any]:
        lang=locale if locale in {'ru','kk','en'} else 'ru'; docs=LEGAL_CONTENT[lang]
        if document not in docs: raise KeyError(document)
        value=dict(docs[document]); value['code']=document; value['locale']=lang; value['settings']=self.settings(); value['consent_version']=CONSENT_VERSION; return value
