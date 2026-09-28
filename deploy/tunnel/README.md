# Туннель к Telegram для сервера в РФ

Бот на российском сервере ходит к Telegram не напрямую, а через заграничный сервер.
Через туннель идёт **только** трафик Telegram, всё остальное (база, обновления системы) — напрямую.

```
бот  →  127.0.0.1:1080 (sing-box на РФ-сервере)  →  заграничный VPS (x-ui, VLESS+Reality, порт 443)  →  api.telegram.org
```

В репозитории нет настоящих ключей и адресов. В `config.json.template` вместо них стоят метки `<...>`.

## Что где

| Файл | Зачем |
|---|---|
| `config.json.template` | Шаблон настроек sing-box для РФ-сервера |
| `check-tunnel.sh` | Проверка: работает ли туннель и видит ли бот Telegram |

## Развёртывание

### 1. Заграничный сервер (x-ui)
Inbound: VLESS, порт 443, tcp, security Reality, flow `xtls-rprx-vision`, dest и SNI `addons.mozilla.org`.
Из настроек inbound и клиента выписать: IP сервера, UUID клиента, public key, short id.

### 2. РФ-сервер: sing-box
1. Установить sing-box по инструкции с официального сайта (sing-box.sagernet.org), пакетом для Ubuntu.
2. Скопировать шаблон и подставить данные из шага 1 вместо меток `<FOREIGN_SERVER_IP>`, `<CLIENT_UUID>`,
   `<REALITY_PUBLIC_KEY>`, `<REALITY_SHORT_ID>`:
   ```bash
   sudo cp config.json.template /etc/sing-box/config.json
   sudo nano /etc/sing-box/config.json
   ```
3. Проверить конфиг и запустить:
   ```bash
   sudo sing-box check -c /etc/sing-box/config.json
   sudo systemctl enable --now sing-box
   ```

### 3. Бот
В `.env` бота:
```
TELEGRAM_PROXY_URL=socks5://127.0.0.1:1080
```
Локально на Windows эту строку не пишем — бот ходит напрямую.

### 4. Проверка
```bash
chmod +x check-tunnel.sh
./check-tunnel.sh /opt/stubbot/.env
```
Все пункты должны быть `[OK]`.

## Если туннель упал

Признаки: бот не отвечает, в логе бота `Нет связи с Telegram API` или `Сеть: TelegramNetworkError`.

1. Запустить `./check-tunnel.sh` — он покажет, на каком шаге проблема.
2. **sing-box не запущен** → `sudo systemctl restart sing-box`, потом `journalctl -u sing-box -n 50`.
   Частая причина — ошибка в конфиге после правки: `sudo sing-box check -c /etc/sing-box/config.json`.
3. **sing-box работает, но getMe не проходит** → проблема на заграничном сервере или по пути к нему:
   - зайти на заграничный сервер, проверить, что x-ui и inbound включены;
   - если сервер недоступен целиком — переключиться на резервный: в `/etc/sing-box/config.json` заменить
     `server`, `uuid`, `public_key`, `short_id` на данные резервного сервера и `sudo systemctl restart sing-box`.
4. **Прямой выход не RU** → в конфиге сломались правила маршрута (например, пропало `"final": "direct"`),
   весь трафик сервера пошёл за границу. Вернуть конфиг из шаблона.
5. После восстановления туннеля бот продолжит работу сам: короткие сбои он переживает повторами запросов.
   Если бот успел завершиться — перезапустить его службу.

## Предложение: автоматический резерв (не внедрено)

Вместо ручной замены сервера в пункте 3 можно описать в sing-box оба заграничных сервера
и группу `urltest`: sing-box сам раз в несколько минут проверяет, какой сервер отвечает,
и переключается на живой. Бот при этом ничего не замечает.
Внедрим после вашего решения.
