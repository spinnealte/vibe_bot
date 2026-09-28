#!/usr/bin/env bash
# Проверка туннеля к Telegram на РФ-сервере.
# Использование: ./check-tunnel.sh [путь к .env бота]   (по умолчанию /opt/stubbot/.env)
# Токен читается из .env и нигде не печатается.

set -u

ENV_FILE="${1:-/opt/stubbot/.env}"
PROXY="127.0.0.1:1080"
FAIL=0

ok()   { echo "  [OK]   $*"; }
bad()  { echo "  [FAIL] $*"; FAIL=1; }
info() { echo "  [..]   $*"; }

echo "1. Служба sing-box"
if systemctl is-active --quiet sing-box; then
    ok "sing-box запущен"
else
    bad "sing-box не запущен. Смотреть: journalctl -u sing-box -n 50"
fi

echo "2. Локальный порт $PROXY"
if ss -ltn | grep -q "127.0.0.1:1080 "; then
    ok "порт слушается"
else
    bad "порт 1080 никто не слушает"
fi

echo "3. Telegram Bot API через прокси (getMe)"
BOT_TOKEN="$(grep -E '^BOT_TOKEN=' "$ENV_FILE" 2>/dev/null | head -n1 | cut -d= -f2- | tr -d '\r" ')"
if [ -z "$BOT_TOKEN" ]; then
    bad "BOT_TOKEN не найден в $ENV_FILE"
else
    RESULT="$(curl -sS -o /tmp/getme.$$ -w '%{http_code} %{time_total}' --max-time 15 \
        --socks5-hostname "$PROXY" "https://api.telegram.org/bot${BOT_TOKEN}/getMe" 2>&1)"
    CODE="${RESULT%% *}"
    TIME="${RESULT##* }"
    if [ "$CODE" = "200" ] && grep -q '"ok":true' /tmp/getme.$$; then
        USERNAME="$(grep -o '"username":"[^"]*"' /tmp/getme.$$ | cut -d'"' -f4)"
        ok "getMe: @${USERNAME}, ответ за ${TIME} с"
    else
        bad "getMe не прошёл (HTTP ${CODE:-нет ответа}). Туннель или заграничный сервер недоступен"
    fi
    rm -f /tmp/getme.$$
fi
unset BOT_TOKEN

echo "4. Обычный трафик сервера идёт напрямую"
DIRECT_COUNTRY="$(curl -sS --max-time 10 https://ipinfo.io/country 2>/dev/null | tr -d '[:space:]')"
PROXY_COUNTRY="$(curl -sS --max-time 10 --socks5-hostname "$PROXY" https://ipinfo.io/country 2>/dev/null | tr -d '[:space:]')"
if [ "$DIRECT_COUNTRY" = "RU" ]; then
    ok "прямой выход: RU"
elif [ -z "$DIRECT_COUNTRY" ]; then
    bad "не удалось определить страну прямого выхода"
else
    bad "прямой выход: $DIRECT_COUNTRY — похоже, весь трафик сервера ушёл в туннель"
fi
# ipinfo.io не Telegram, поэтому через прокси тоже должен идти напрямую (правило final: direct).
info "через прокси для не-Telegram адресов: ${PROXY_COUNTRY:-?} (ожидается RU)"

echo
if [ "$FAIL" -eq 0 ]; then
    echo "Итог: туннель в порядке"
else
    echo "Итог: есть проблемы, см. README.md → «Если туннель упал»"
fi
exit "$FAIL"
