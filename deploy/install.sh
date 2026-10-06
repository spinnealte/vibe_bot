#!/usr/bin/env bash
# Установка и обновление stubBot на сервере (Ubuntu 24.04, от root). Docker не используется.
#
# Обычно этот скрипт запускает deploy.cmd с компьютера разработчика:   deploy\deploy.cmd root@IP
# Вручную на сервере:   bash install.sh /каталог/с/распакованным/кодом
#
# Первый запуск ставит всё с нуля. Повторный — обновляет код, зависимости и схему базы и перезапускает бота.
# Данные при обновлении не трогаются: .env, база, legal/, logs/, backups/.

set -euo pipefail

RELEASE_DIR="${1:?Укажите каталог с распакованным кодом бота}"
APP_DIR=/opt/stubbot
APP_USER=stubbot
DB_NAME=stubbot
DB_USER=stubbot
SERVICE=stubbot
ENV_FILE="$APP_DIR/.env"
PYTHON="$APP_DIR/.venv/bin/python"

step() { echo; echo "== $*"; }
info() { echo "   $*"; }
fail() { echo; echo "ОШИБКА: $*" >&2; exit 1; }
as_postgres() { runuser -u postgres -- "$@"; }
as_app() { runuser -u "$APP_USER" -- "$@"; }
sql() { as_postgres psql -tAqc "$1" "${2:-postgres}"; }

[ "$(id -u)" -eq 0 ] || fail "скрипт запускается от root"
[ -f "$RELEASE_DIR/requirements.lock" ] || fail "в $RELEASE_DIR нет кода бота (нет requirements.lock)"

# ---------------------------------------------------------------------------------------------------------------
step "1/9 Пакеты системы"
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a  # без вопросов «перезапустить службы?»
PACKAGES=(python3 python3-venv rsync postgresql redis-server ufw curl openssl cron)
missing=()
for package in "${PACKAGES[@]}"; do
    dpkg -s "$package" >/dev/null 2>&1 || missing+=("$package")
done
if [ "${#missing[@]}" -gt 0 ]; then
    info "ставлю: ${missing[*]}"
    apt-get update -q
    apt-get install -y -q "${missing[@]}"
else
    info "всё уже установлено"
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
    || fail "нужен Python 3.12 или новее (он есть в Ubuntu 24.04), а здесь $(python3 -V 2>&1)"
systemctl enable --now postgresql redis-server >/dev/null 2>&1
timedatectl set-timezone Europe/Moscow 2>/dev/null || true  # время в логах и резервные копии — по Москве

# ---------------------------------------------------------------------------------------------------------------
step "2/9 Пользователь и каталоги"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$APP_DIR" --no-create-home --shell /usr/sbin/nologin "$APP_USER"
    info "создан пользователь $APP_USER (без входа в систему) — бот работает от него, не от root"
fi
mkdir -p "$APP_DIR"/{logs,legal,backups}

# ---------------------------------------------------------------------------------------------------------------
step "3/9 Код"
if systemctl is-active --quiet "$SERVICE"; then
    info "останавливаю бота на время обновления"
    systemctl stop "$SERVICE"
fi
if [ -f "$ENV_FILE" ] && [ "$(sql "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")" = "1" ]; then
    info "резервная копия базы перед обновлением: $(bash "$RELEASE_DIR/deploy/backup.sh")"
fi
# Код принадлежит root: бот его только читает и изменить не может. Данные (.env, legal, logs, backups) не трогаем.
rsync -a --delete \
    --exclude '/.env' --exclude '/.venv/' --exclude '/logs/' --exclude '/legal/' --exclude '/backups/' \
    "$RELEASE_DIR"/ "$APP_DIR"/
chown -R root:root "$APP_DIR"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR/logs" "$APP_DIR/legal"
chmod 700 "$APP_DIR/backups"
info "код обновлён в $APP_DIR"

# ---------------------------------------------------------------------------------------------------------------
step "4/9 Зависимости Python"
[ -x "$PYTHON" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --disable-pip-version-check -r "$APP_DIR/requirements.lock"
"$PYTHON" -m compileall -q "$APP_DIR/stubbot" "$APP_DIR/scripts" "$APP_DIR/migrations" >/dev/null
info "готово"

# ---------------------------------------------------------------------------------------------------------------
step "5/9 База данных и настройки"
if [ ! -f "$ENV_FILE" ]; then
    DB_PASSWORD="$(openssl rand -hex 24)"
    if [ "$(sql "SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER'")" = "1" ]; then
        sql "ALTER ROLE $DB_USER LOGIN PASSWORD '$DB_PASSWORD'"
    else
        sql "CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASSWORD'"
    fi
    [ "$(sql "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")" = "1" ] \
        || as_postgres createdb -O "$DB_USER" "$DB_NAME"
    info "создана база $DB_NAME, пароль к ней сгенерирован и записан только в $ENV_FILE"

    echo
    echo "   Нужны два значения. Любое можно пропустить (Enter) и вписать позже в $ENV_FILE."
    BOT_TOKEN_INPUT=""
    OWNER_INPUT=""
    read -r -s -p "   Токен бота из BotFather (ввод не отображается): " BOT_TOKEN_INPUT || true
    echo
    read -r -p "   Telegram ID владельца бота (число): " OWNER_INPUT || true
    if [ -n "$BOT_TOKEN_INPUT" ] && ! [[ "$BOT_TOKEN_INPUT" =~ ^[0-9]{6,12}:[A-Za-z0-9_-]{30,}$ ]]; then
        info "это не похоже на токен бота — оставляю пустым, впишите его позже"
        BOT_TOKEN_INPUT=""
    fi
    if ! [[ "$OWNER_INPUT" =~ ^[0-9]{5,15}$ ]]; then
        info "Telegram ID не задан — без него в боте не будет кнопки «Админка»; впишите OWNER_TELEGRAM_ID позже"
        OWNER_INPUT=0
    fi

    umask 077
    cat > "$ENV_FILE" <<EOF
# Настройки бота на сервере. Создано установщиком $(date '+%d.%m.%Y %H:%M'). Здесь секреты — никому не показывать.
BOT_TOKEN=$BOT_TOKEN_INPUT
# Туннель до Telegram, если с сервера он недоступен: socks5://127.0.0.1:1080 (см. deploy/tunnel/README.md)
TELEGRAM_PROXY_URL=

DB_HOST=localhost
DB_PORT=5432
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASSWORD
DB_NAME=$DB_NAME

REDIS_URL=redis://127.0.0.1:6379/0

# Владелец бота (всегда админ) и другие админы через запятую
OWNER_TELEGRAM_ID=$OWNER_INPUT
ADMIN_IDS=

LOG_LEVEL=INFO
LOG_DIR=logs
LEGAL_DOCS_DIR=legal
TIMEZONE=Europe/Moscow

FLOOD_LIMIT=20
FLOOD_WINDOW=5

APPLICATIONS_ENABLED=false
# Чат менеджера: https://t.me/<username>. Пусто — кнопки «Менеджер» нет.
MANAGER_URL=
EOF
    umask 022
    unset BOT_TOKEN_INPUT DB_PASSWORD
else
    info "настройки уже есть ($ENV_FILE) — не меняю"
fi
# Читать настройки может только бот, менять — только root.
chown root:"$APP_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"

# ---------------------------------------------------------------------------------------------------------------
step "6/9 Схема базы"
cd "$APP_DIR"
as_app "$PYTHON" -m alembic upgrade head
if [ "$(sql "SELECT count(*) FROM specialties" "$DB_NAME")" = "0" ]; then
    # Только на пустой базе: повторный запуск вернул бы специальности, которые админ переименовал или убрал.
    as_app "$PYTHON" -m scripts.seed
fi

# ---------------------------------------------------------------------------------------------------------------
step "7/9 Служба"
install -m 644 "$APP_DIR/deploy/stubbot.service" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1
info "бот запускается сам после перезагрузки сервера и перезапускается при падении"

# ---------------------------------------------------------------------------------------------------------------
step "8/9 Защита и резервные копии"
SSH_PORT="$(echo "${SSH_CONNECTION:-}" | awk '{print $4}')"
ufw allow 22/tcp >/dev/null
[ -z "$SSH_PORT" ] || ufw allow "$SSH_PORT"/tcp >/dev/null
ufw --force enable >/dev/null
info "файрвол включён: снаружи открыт только SSH (боту входящие порты не нужны)"
if ss -ltnH | awk '{print $4}' | grep -E ':(5432|6379)$' | grep -vqE '^(127\.0\.0\.1|\[::1\]):'; then
    info "ВНИМАНИЕ: PostgreSQL или Redis слушают не только localhost — файрвол их закрывает, но настройку стоит поправить"
fi
install -m 755 "$APP_DIR/deploy/backup.sh" /usr/local/bin/stubbot-backup
cat > /etc/cron.d/stubbot-backup <<'EOF'
# Ежедневная резервная копия базы stubBot в 03:30 по Москве (хранятся 14 дней в /opt/stubbot/backups)
30 3 * * * root /usr/local/bin/stubbot-backup >/dev/null 2>&1
EOF
info "резервная копия базы — каждый день в 03:30, вручную: stubbot-backup"

# ---------------------------------------------------------------------------------------------------------------
step "9/9 Запуск"
TELEGRAM_CODE="$(curl -sS -m 10 -o /dev/null -w '%{http_code}' https://api.telegram.org 2>/dev/null || true)"
if [ -z "$TELEGRAM_CODE" ] || [ "$TELEGRAM_CODE" = "000" ]; then
    info "ВНИМАНИЕ: Telegram с этого сервера напрямую недоступен — нужен туннель (deploy/tunnel/README.md)"
else
    info "Telegram с сервера доступен напрямую — туннель не нужен"
fi

if grep -qE '^BOT_TOKEN=.+' "$ENV_FILE"; then
    systemctl restart "$SERVICE"
    sleep 6
    if systemctl is-active --quiet "$SERVICE"; then
        info "бот запущен"
        journalctl -u "$SERVICE" -n 4 --no-pager -o cat | sed 's/^/     /'
    else
        info "бот НЕ запустился. Последние строки лога:"
        journalctl -u "$SERVICE" -n 25 --no-pager -o cat | sed 's/^/     /'
        exit 1
    fi
else
    info "токен бота не задан — бот пока не запущен."
    info "впишите BOT_TOKEN:   nano $ENV_FILE"
    info "и запустите:         systemctl start $SERVICE"
fi

cat <<EOF

Готово. Полезное на сервере:
   состояние бота          systemctl status $SERVICE
   лог в реальном времени  journalctl -u $SERVICE -f
   действия админа         less $APP_DIR/logs/admin.log
   настройки               nano $ENV_FILE   (после правки: systemctl restart $SERVICE)
   резервная копия сейчас  stubbot-backup
EOF
