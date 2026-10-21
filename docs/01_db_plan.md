# План БД - v1.1 (28.09.2026)

Одна схема на заглушку и на будущий полный бот. Заглушка создаёт таблицы с меткой **[MVP]**,
полный бот добавляет **[FULL]** только аддитивными миграциями (новые таблицы/колонки), ядро не переделывается.

## Решения (журнал)

| #   | Вопрос                                 | Решение                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | Влияние на БД                                                                                                                                                                                     |
| --- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Токен                                  | Заглушка и полный бот - один токен, одна БД                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Telegram file_id валидны навсегда (они привязаны к боту); старую БД mainDirectory переносим разовым скриптом                                                                                      |
| 2   | Наполнение курсов                      | Админка в боте, мастер как в mainDirectory                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | admins в MVP; мастер делится на «Курс» (один раз) и «Поток» (на каждое проведение)                                                                                                                |
| 3   | Оплата                                 | Онлайн, провайдер не выбран                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Провайдеро-независимые orders/payments/payment_events/fiscal_receipts [FULL]                                                                                                                      |
| 4   | Клиники                                | Возможна оплата клиникой за нескольких врачей                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | organizations + organization_members + orders/order_items [FULL]; в MVP - поле requested_seats в заявке                                                                                           |
| 5   | Сертификаты                            | Выдаются после курса                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | certificates [FULL], ФИО для сертификата в clients уже в MVP                                                                                                                                      |
| 6   | География                              | Только СПб, разные адреса                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | venues; единый часовой пояс Europe/Moscow                                                                                                                                                         |
| 7   | Каналы                                 | Листовки (QR), сайт, VK, Instagram, TG-канал, рефералы                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | acquisition_sources + client_source_touches в MVP                                                                                                                                                 |
| 8   | ФИО (29.09.2026)                       | Одним полем, как написал человек; на части не разбираем                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | clients.full_name и lecturers.full_name вместо last/first/middle_name (миграция 7b1e2c9a4d10)                                                                                                     |
| 9   | Старая база (30.09.2026)               | Реальных данных в mainDirectory не было - стартуем с чистой БД                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Разовый перенос не нужен; MVP → FULL - обычными миграциями Alembic на той же БД                                                                                                                   |
| 10  | Лекторы (30.09.2026)                   | Курсы авторские: курс читают только его лекторы                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | program_lecturers вместо session_lecturers (миграция 9c4f1a7e2b35)                                                                                                                                |
| 11  | Онлайн (30.09.2026)                    | Онлайн-курсы - живые, в даты; записи не храним                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Хватает sessions.format + online_url                                                                                                                                                              |
| 12  | Объём MVP (30.09.2026)                 | MVP - афиша: расписание + личный кабинет, без заявок                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Таблицы заявок остаются в схеме; в боте выключены (APPLICATIONS_ENABLED=false)                                                                                                                    |
| 13  | Оплата [FULL] (30.09.2026)             | **Заменено решением 18.** Было: нет оплаты за 24–48 ч после заявки - место снимается                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | orders.expires_at + фоновая задача; тарифы «теория/практика», «для двух коллег», когда занимается место - решаем в FULL                                                                           |
| 14  | Курс без дат в афише (02.10.2026)      | **Отменено решением 16.** Было: курс без проведений по умолчанию виден как «даты уточняются», админ может скрыть                                                                                                                                                                                                                                                                                                                                                                                                                                 | programs.show_without_dates - колонка удалена (миграция c7d2e91f4a58)                                                                                                                             |
| 15  | Откуда пришёл клиент (05.10.2026)      | Хвост ссылки первого /start хранится текстом у клиента, даже если источник не заведён в справочнике. Аналитики в боте пока нет - только выгрузка клиентов в CSV                                                                                                                                                                                                                                                                                                                                                                                  | clients.start_param varchar(64) NULL (миграция a1f47c2d9e60); acquisition_sources и touches остаются как есть                                                                                     |
| 16  | Афиша - только проведения (11.10.2026) | Курс - запись библиотеки, сам в афишу не попадает. В афише стоят проведения: с датами или без дат («даты уточняются»). «Скрыть» нет: убрать из афиши - отправить в архив; прошедшее по датам считается архивом само («прошедшие мероприятия»). Удаление навсегда - только из архива; с заявками или подписками клиентов удалять нельзя                                                                                                                                                                                                           | sessions.start_date / end_date NULL (оба сразу, CHECK dates_together), sessions.archived_at NULL вместо is_visible; programs.show_without_dates удалена (миграция c7d2e91f4a58, только структура) |
| 17  | Согласия (16.10.2026)                  | Документы - на сайте заказчика (политика и согласие), заказчик одобрил их для бота. При регистрации - две отдельные галочки и «Продолжить»: 1 - «Я согласен с Политикой…, даю согласие на обработку… и на получение информационных сообщений» (обязательна), 2 - рекламные рассылки (по желанию, отдельное согласие); из кабинета рассылки включаются отдельно. Тексты, которые показывает бот, - в коде с номером версии; новую версию бот заводит в legal_documents сам при запуске. Имя и @username из Telegram - только после согласия на ПД | Схема не меняется: legal_documents.url - адрес на сайте, text_hash - sha256 текста из бота; clients.tg_* до согласия пустые                                                                      |
| 18  | Запись и оплата [FULL] (19-20.10.2026) | «Записаться» сразу занимает место, цена фиксируется при записи. Записаться можно за себя или вдвоём с коллегой (тариф «Для двух коллег», платит записывающий, у коллеги своя запись). Заказ: номер - шесть цифр по порядку с 100001, срок оплаты 72 часа; частичная оплата закрепляет место; оплату отмечает админ, бронь снимает только админ. Отмена оплаченной записи - деньги на аванс клиента (зачисляет админ) | orders (number из последовательности order_number_seq; total_amount NULL - «стоимость уточняется»; payer_type, due_at, отметки напоминаний, файл реквизитов юрлица), payments (каждая поступившая сумма; поля под онлайн-оплату), advances (журнал аванса, баланс - сумма строк), enrollments.order_id вместо order_items (миграция 20568b109f4b) |

---

## 0. Соглашения

- PK: `BIGINT GENERATED ALWAYS AS IDENTITY`. telegram_id - отдельное UNIQUE-поле, может быть NULL.
- Время: `TIMESTAMPTZ` (UTC в БД, Europe/Moscow в интерфейсе). Даты без времени - `DATE`.
- У всех таблиц `created_at`, `updated_at`. Исключение: неизменяемые строки - журналы (client_source_touches, client_events)
  и чистые m2m - только `created_at`.
- session_days: на одну дату допускается несколько строк (разное время/площадка).
- client_events.type - VARCHAR без CHECK (новые типы событий без миграции); остальные enum - с CHECK.
- Уникальности «только для живых» - частичными индексами: clients.phone (deleted_at IS NULL),
  client_consents (client_id, type) WHERE revoked_at IS NULL, legal_documents (type) WHERE is_current,
  enrollments (client_id, session_id) WHERE status не отменён.
- Enum: Python Enum + `sqlalchemy.Enum(native_enum=False)` → VARCHAR + CHECK (легко расширять миграцией).
- Деньги: `INTEGER` в копейках + `currency CHAR(3)` = 'RUB'.
- Справочники не удаляются (`is_active`), клиенты - `deleted_at` + анонимизация ПД.
- FK: RESTRICT для бизнес-данных, CASCADE только для чистых m2m.
- Naming convention для constraints в MetaData. Alembic с первой миграции; create_all/DROP SCHEMA на проде запрещены.

---

## 1. Разбор старых models.py (кратко)

- Курс и его проведение смешаны → нет нормального расписания, повтор курса = копия.
- PK клиента = telegram_id → нельзя завести врача, которого записала клиника, или клиента с сайта/звонка.
- «Опыт N лет» устаревает; ФИО одной строкой; д.р. строкой.
- Запись хранит только payment_status: нет статуса заявки, тарифа, сумм, дат, повторной записи после отмены.
- Одно согласие вместо двух (ПД и реклама - разные согласия).
- Нет источников трафика, аналитики, интереса к курсу, флага блокировки бота, площадок, админов в БД, заказов/платежей.

---

## 2. Схема

### A. Клиенты

**clients** [MVP]
| Поле | Тип | Зачем |
|---|---|---|
| id | bigint PK | суррогатный ключ |
| telegram*id | bigint UNIQUE NULL | NULL - клиент заведён админом/клиникой, ещё не зашёл в бот |
| tg_username, tg_first_name, tg_last_name, tg_language_code | varchar NULL | снимок из Telegram - только после согласия на ПД (решение 17), дальше обновляется при визитах |
| full_name | varchar(300) NULL | ФИО одной строкой, регистр как ввёл клиент; обращение, сертификат |
| name_confirmed_at | timestamptz NULL | пользователь подтвердил написание ФИО для сертификата |
| birth_date | date NULL | поздравления |
| phone | varchar(20) NULL, UNIQUE partial | E.164 (+7XXXXXXXXXX) |
| phone_confirmed | bool | true - пришёл кнопкой «Поделиться контактом» |
| email | varchar(254) NULL, индекс lower() | чеки, рассылки |
| city | varchar(128) NULL | статистика |
| workplace_raw | varchar(256) NULL | клиника «как ввёл пользователь» |
| practice_since_year | smallint NULL | опыт = текущий год − значение |
| profile_status | new / partial / completed | воронка регистрации |
| referral_code | varchar(32) UNIQUE | ссылка ref*… |
| referred_by_client_id | FK clients NULL | кто пригласил |
| first_source_id | FK acquisition_sources NULL | первый источник (дублирует первую запись touches для быстрых отчётов) |
| start_param | varchar(64) NULL | хвост ссылки `?start=<хвост>` первого захода, как есть; NULL - пришёл без ссылки (решение 15) |
| is_bot_blocked, bot_blocked_at | bool, timestamptz | ставится при Forbidden при отправке |
| last_seen_at | timestamptz | активность |
| admin_note | text NULL | заметка менеджера |
| deleted_at | timestamptz NULL | удаление/анонимизация |

**specialties** [MVP] - id, title UNIQUE, sort_order, is_active
**positions** [MVP] (бывш. roles) - id, title UNIQUE, sort_order, is_active
**client_specialties**, **client_positions** [MVP] - m2m, PK из двух FK, CASCADE

**organizations** [FULL] - клиника/юрлицо

- id, name (как называют), legal_name NULL, inn NULL UNIQUE, kpp, ogrn, legal_address, actual_address,
  bank_name, bik, account, corr_account, contact_phone, contact_email, note, is_active

**organization_members** [FULL] - врач ↔ клиника (m2m: врач может работать в нескольких)

- organization_id, client_id, role (employee / coordinator), is_active, PK(organization_id, client_id)

**client_invites** [FULL] - «клиника записала врача» → врач по ссылке привязывает свой Telegram

- id, client*id, token UNIQUE (start=inv*<token>), created_by_client_id / admin_id, expires_at, used_at

### B. Согласия

**legal_documents** [MVP] - id, type (privacy_policy / pd_consent / marketing_consent / offer), version, url, text_hash, published_at, is_current
**client_consents** [MVP] - id, client_id, type (personal_data / marketing), document_id, granted_at, revoked_at NULL, source (telegram_bot / site / admin)

### C. Источники и аналитика

**acquisition_sources** [MVP]

- id, code UNIQUE (≤50, [A-Za-z0-9_-]), name, channel (flyer / site / vk / instagram / tg_channel / referral / offline_event / other),
  campaign NULL, placement NULL (например «листовка у ресепшена клиники X», «страница курса на сайте»), is_active
- Deep-link: `t.me/<bot>?start=src_<code>`; реферал - `ref_<code>`; приглашение - `inv_<token>`. Лимит start-параметра 64 символа.
- Листовки: QR на src*flyer*<место/партия>. Сайт: отдельный код на страницу/кнопку. Соцсети: код на канал или на пост.

**client_source_touches** [MVP] - каждый заход по коду, в т.ч. уже зарегистрированных

- id, client_id, source_id NULL, raw_payload varchar(64), is_first bool, created_at

**client_events** [MVP] - журнал действий для воронки

- id, client_id, type, payload JSONB, created_at; индексы (type, created_at), (client_id, created_at)
- типы: start, consent_given, profile_completed, schedule_opened, session_viewed, program_opened, application_created, interest_created, profile_edited, …

### D. Каталог и расписание

**programs** [MVP] - курс как продукт

- id, slug UNIQUE, title, short_description, description_html, program_html, level (basic / advanced),
  default_format (online / offline / hybrid), duration_hours NULL, cover_file_id NULL,
  nmo_points NULL (не используется, решение 02.10.2026), is_published, archived_at, sort_order
- курс - запись библиотеки: в афише его нет, пока у него нет проведения (решение 16)
  **program_specialties** [MVP] - m2m

**sessions** [MVP] - поток (конкретное проведение)

- id, program_id
- status: draft / announced / registration_open / waitlist / full / registration_closed / in_progress / completed / cancelled
- format, venue_id NULL, online_url NULL, start_date NULL, end_date NULL (оба NULL - проведение без дат,
  «даты уточняются»), registration_deadline NULL, capacity NULL, title_override NULL, cover_file_id NULL,
  archived_at NULL (в афише - пока не в архиве и не прошло; решение 16)
- индекс (status, start_date)

**session_days** [MVP] - id, session_id (CASCADE), date, start_time NULL, end_time NULL, topic NULL, venue_id NULL
**program_lecturers** [MVP] - m2m курс ↔ лектор (курсы авторские, у всех потоков курса одни лекторы)
**lecturers** [MVP] - id, full_name, regalia, bio_html, photo_file_id, is_active, sort_order
**venues** [MVP] - id, name, address, map_url, directions_text, is_active (город по умолчанию СПб)

**price_options** [MVP] - тарифы потока

- id, session_id, kind (full / theory / practice / group / early_bird / other), label,
  amount (копейки), unit (per_person / per_group), min_seats default 1, max_seats NULL,
  valid_from NULL, valid_until NULL, seats_limit NULL, is_active, sort_order
- «Для двух коллег» = kind=group, unit=per_group, min_seats=max_seats=2.

### E. Заявки и записи

**enrollments** [MVP] - место конкретного врача на потоке

- id, client_id, session_id, price_option_id NULL
- order_id NULL [FULL] - заказ, по которому оплачивается место (у пары «для двух коллег» один заказ на двоих); NULL - лист ожидания
- enrolled_by_client_id NULL [FULL] - кто записал: у коллеги из пары - записавший его плательщик; NULL - записался сам.
  Связывает пару и в листе ожидания, где заказа нет (миграция 5d3a9c7e1b20)
- status: application / confirmed / waitlist / cancelled_by_client / cancelled_by_admin / attended / no_show / completed
- payment_status: unpaid / partial / paid / refunded (кэш, в FULL считается из orders)
- requested_seats smallint default 1 - MVP: «записываюсь с коллегами», менеджер обрабатывает вручную
- source (bot / admin / site / phone), client_comment, admin_comment
- created_at, confirmed_at, cancelled_at, cancel_reason
- UNIQUE (client_id, session_id) WHERE status не отменён

**program_interests** [MVP] - «сообщите о наборе»: client_id, program_id, created_at, notified_at, UNIQUE(client_id, program_id).
В FULL это и лист ожидания курса: туда попадают отменившие запись без оплаты (notified_at сбрасывается), приглашает админ.
**enrollment_status_history** [FULL] - enrollment_id, from_status, to_status, changed_by_admin_id, note, changed_at

### F. Заказы и оплаты [FULL]

Единица оплаты - **заказ**, а не запись. Это покрывает и частное лицо, и «двух коллег» (один заказ на две записи),
а позже - клинику за нескольких врачей. Сделано в миграции 20568b109f4b (решение 18):

**orders** - id, number UNIQUE (шесть цифр по порядку с 100001, последовательность order_number_seq),
payer_client_id, session_id, price_option_id NULL, payer_type NULL (individual / organization; NULL - ещё не выбран),
total_amount NULL (копейки, зафиксировано при записи; NULL - «стоимость уточняется»), currency,
status (awaiting_payment / partially_paid / paid / cancelled), due_at (+72 ч), requisites_file_id / _name /
_received_at (реквизиты юрлица файлом), invoice_sent_at, payment_reminded_at (24 ч), overdue_notified_at (срок вышел),
paid_at, cancelled_at, cancel_reason, cancel_choice NULL (transfer / advance / refund - что клиент выбрал для уже
внесённой оплаты, когда сам отменил запись; деньги двигает админ; миграция 5d3a9c7e1b20), admin_comment.
Позже, когда понадобится: organization_id (клиники), скидки и промокоды, email / телефон для чека.

Участники заказа - enrollments.order_id (у каждого своя запись); отдельная order_items не нужна: сумма у заказа целиком.

**payments** - id, order_id, amount (> 0), currency, method (invoice / cash / card_online / sbp / advance),
provider (manual / yookassa; новые - миграцией), state (pending / succeeded / canceled), provider_payment_id UNIQUE NULL,
idempotency_key UNIQUE NULL, paid_at, created_by_telegram_id NULL (админ, отметивший оплату), comment.
Оплачено по заказу = сумма успешных платежей; частичная оплата - несколько строк.

**advances** - журнал аванса клиента: id, client_id, order_id NULL, amount (копейки, + зачисление / - списание, не 0),
currency, reason (cancelled_order / applied_to_order / refund / correction), created_by_telegram_id (меняет только
админ), comment, created_at. Баланс - сумма строк; строки не правятся, ошибку исправляет новая строка.

**payment_events** - сырые вебхуки: id, provider, event_type, provider_payment_id, payload JSONB, received_at, processed_at, error
**refunds** - id, payment_id, amount, reason, provider_refund_id UNIQUE NULL, status, created_at
**fiscal_receipts** - чеки 54-ФЗ: id, payment_id / refund_id, type (income / income_return), provider_receipt_id, status, fiscal_data JSONB
**invoices** - счёт юрлицу: id, order_id, number UNIQUE, issued_at, due_date, file_id NULL (PDF), status (issued / paid / cancelled)

### G. Коммуникации [FULL]

**campaigns** - id, name, created_by_admin_id, segment JSONB, text_html, attachment_file_id, attachment_type, buttons JSONB,
status (draft / scheduled / sending / done / cancelled), scheduled_at, started_at, finished_at, счётчики
**campaign_recipients** - campaign_id, client_id, status (pending / sent / failed / blocked), telegram_message_id, error, sent_at, confirmed_at, clicked_at; UNIQUE(campaign_id, client_id)
**scheduled_notifications** - client_id, enrollment_id NULL, type (reminder_day_before / payment_due / …), scheduled_at, sent_at, status

### H. После курса [FULL]

**certificates** - id, enrollment_id UNIQUE, number UNIQUE, full_name_snapshot (ФИО на момент выдачи), hours, nmo_points NULL,
issued_at, pdf_file_id NULL, verification_code UNIQUE (проверка подлинности по ссылке/QR), revoked_at NULL
**feedback** - id, enrollment_id UNIQUE, rating smallint, text, created_at
**promo_codes / promo_redemptions**, **referral_rewards** - при появлении скидок

### I. Администрирование

**admins** [MVP] - id, telegram_id UNIQUE, name, role (owner / manager / viewer), is_active. В .env только owner для первичного входа.
**audit_log** [FULL] - admin_id, action, entity_type, entity_id, diff JSONB, created_at
**bot_texts** [FULL] - key, text_html, updated_at

---

## 3. Состав MVP (21 таблица)

clients, specialties, positions, client_specialties, client_positions,
legal_documents, client_consents,
acquisition_sources, client_source_touches, client_events,
programs, program_specialties, program_lecturers, sessions, session_days, lecturers, venues, price_options,
enrollments, program_interests,
admins

FULL (~21 таблица, аддитивно): organizations, organization_members, client_invites, enrollment_status_history,
orders, payments, advances (сделаны, решение 18; order_items не нужна), payment_events, refunds, fiscal_receipts, invoices,
campaigns, campaign_recipients, scheduled_notifications, certificates, feedback,
promo_codes, promo_redemptions, referral_rewards, audit_log, bot_texts.

## 4. Индексы (минимум)

- clients: telegram_id UNIQUE, phone UNIQUE partial, lower(email), referral_code UNIQUE, first_source_id, created_at
- sessions: (status, start_date), program_id
- enrollments: client_id, session_id, status, partial UNIQUE(client_id, session_id)
- client_events: (type, created_at), (client_id, created_at)
- client_source_touches: (source_id, created_at)
- client_consents: (client_id, type)
- FULL: payments.provider_payment_id UNIQUE, orders(status), order_items.enrollment_id UNIQUE

## 5. Мастер админки (MVP) под новую схему

1. Справочники: лекторы, площадки, специальности, должности.
2. «Новый курс» (programs) - один раз: название, описание, программа (текст/.docx), уровень, формат, направления, лекторы, обложка.
3. «Новый поток» (sessions) - на каждое проведение: курс → даты/дни и время → площадка / ссылка онлайн → места → тарифы → статус → превью → публикация.
   Кнопка «Скопировать прошлый поток» - меняются только даты.

## 6. Перенос старой БД (mainDirectory)

Не нужен: реальных данных в старом боте не было (решение 9). Полная версия продолжит эту же БД миграциями Alembic.
