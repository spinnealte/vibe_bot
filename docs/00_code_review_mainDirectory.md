# Код-ревью mainDirectory (полный бот) — 28.09.2026

Статус: зафиксировано, в коде ничего не менялось. Используется как бэклог для полнофункционального бота.

## Стек
- Python 3.13, aiogram 3.21 (Router, FSM, magic-filter, middlewares, polling)
- Redis (RedisStorage для FSM), запускается в WSL
- PostgreSQL + SQLAlchemy 2.0 async + asyncpg
- pydantic-settings + .env, logging + RotatingFileHandler
- email-validator, python-docx, pytz; тесты: pytest, pytest-asyncio, Faker, docker-compose (Postgres 15)
- requirements.txt неполный: нет asyncpg, email-validator, python-docx, pytz, faker, pytest-asyncio; лишние numpy, Booktype (тянет Django)

## Оценка: ~5/10
Плюсы: роутеры по фичам, тексты отдельно, SQLAlchemy 2.0, association object для оплаты, FSM в Redis.
Минусы: god-class DB_Utility (1400 строк), бизнес-логика в хендлерах, копипаста (9 редакторов профиля ≈1500 строк),
кэш каталога в FSM, пользовательская фича (program_view) лежит в admin_panel, опечатки в именах, print(), мёртвый код.

## 🔴 Критично
1. Нет проверки админа на большинстве админ-хендлеров (курсы, рассылка, экспорт CSV с ПД, статус оплаты). Callback data подделывается. → фильтр на весь admin_panel, program_view вынести.
2. Согласие на ПД пишется в БД, но текст соглашения пользователю не показывается (next_step_start закомментирован).
3. Нет миграций (Alembic); reset_database = DROP SCHEMA.
4. ПД и секреты в логах: print(settings) с токеном и паролем БД, телефоны в bot.log, DEBUG на проде, временные CSV не удаляются.
5. Реферальная ссылка захардкожена на @testovstudy_bot → брать из bot.me().

## 🟠 Баги
- sing_up_on_course: результат не проверяется → всегда «успешно»; не проверяется статус курса (paused/waiting_list).
- Нет html.escape для пользовательского ввода → профиль с «<» больше не открывается (can't parse entities в логах); short_text режет HTML посередине тега.
- Caption > 1024 и MESSAGE_TOO_LONG (22 случая в логе).
- message.text is None для фото/голоса в FSM-шагах → AttributeError.
- questions.saving_data: нормализованный телефон не сохраняется; existing_profile=None → падение; last_inline_markup = id сообщения пользователя; опыт без границ.
- Пустой date[] у курса (update_dates молча отбрасывает невалидные) → каталог падает у всех; fallback render_catalog не фильтрует paused.
- get_signed_courses_dict: status=None → исключение проглочено → «вы не записаны»; сортировка дат как строк.
- Поиск по ФИО при нескольких результатах сломан (хендлер из course_month_selection перехватывает ввод номера); MAX_INLINE_CLIENTS=1.
- Рассылка: повторный callback.answer с reply_markup после рассылки; admin text берётся из message.text (форматирование теряется, «<» ломает отправку); нет обработки RetryAfter/Forbidden; цикл внутри хендлера.
- handle_client_confirm: message.answer(show_alert=True).
- Добавление курса без лекторов → KeyError в самом конце мастера.
- Ручные id (max+1) для специальностей/лекторов → рассинхрон SERIAL.
- email-validator check_deliverability=True → синхронный DNS в event loop.
- Опыт: 0 = «пропустил» и «0 лет»; NULL выпадает из фильтров; max=0 обнуляет min.
- NetworkErrorMiddleware глотает ошибки; ClearStateOnStart дублирует /start.

## 🟡 Мёртвые кнопки
Проверить подтверждения, Просмотреть записи, Статистика, Экспорт/Импорт, Из листа ожидания, Завершить набор, Просмотреть каталог, «По курсу» (callback "some").

## План улучшений (по приоритету)
1. Безопасность и ПД: admin-фильтр на роутер, показ соглашения, чистка логов, bot.me() для ссылки.
2. Alembic, initial-миграция.
3. Единый escape для пользовательского текста; description хранить как html_text; фото и длинный текст раздельно.
4. dp.errors глобальный обработчик + фильтр «только текст» для FSM.
5. Точечные баги выше.
6. Рефакторинг: репозитории вместо DB_Utility, сессия через middleware, generic-редактор профиля, фильтр IsAdmin, CallbackData-фабрики.
7. Рассылка — фоновая задача с прогрессом в БД, RetryAfter/Forbidden, пометка заблокировавших.
8. Инфраструктура: чистый requirements/lock, Dockerfile + compose, детерминированные тесты, ruff.
9. Модель: date_of_birth → Date, опыт nullable, tz-aware даты, флаги is_blocked/profile_completed.
