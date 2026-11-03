# Переменные для деплоя полной версии (без ЮKassa)

```
BOT_TOKEN=            # секрет, тот же рабочий токен
OWNER_TELEGRAM_ID=
DB_HOST=
DB_PORT=5432
DB_NAME=
DB_USER=
DB_PASSWORD=          # секрет
REDIS_URL=            # секрет: redis://:<пароль>@<адрес>:6379/0
APPLICATIONS_ENABLED=true
ADMIN_IDS=            # менеджеры через запятую
MANAGER_URL=          # https://t.me/<менеджер>
```

Не задавать: `ONLINE_PAYMENTS`, `YOOKASSA_*`, `TELEGRAM_PROXY_URL`, `LOG_DIR`.

Владелец и все из `ADMIN_IDS` должны хотя бы раз нажать /start в боте - иначе уведомления о записях им не придут.

## Веб-приложение (Mini App), FullWebTelegramBot

```
WEBAPP_ENABLED=true   # сервер приложения вместе с ботом (python -m stubbot)
WEBAPP_HOST=127.0.0.1 # 0.0.0.0 - открыть с телефона в той же Wi-Fi
WEBAPP_PORT=8080
WEBAPP_URL=           # https-адрес приложения (туннель или домен); пусто - кнопки «Приложение» в боте нет
WEBAPP_ONLY=true      # всё - в приложении: после /start одно приветствие с кнопкой, нижнего меню и разделов бота
                      # нет; false - кнопочный бот, как раньше. Нужен WEBAPP_URL
WEBAPP_DEV_AUTH=      # true - вход «dev <id>» в обычном браузере, только с локальной базой; на сервере не задавать
DB_SCHEMA=            # своя схема в базе для разработки (webdev); на сервере не задавать
```
