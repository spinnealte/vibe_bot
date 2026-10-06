#!/usr/bin/env bash
# Резервная копия базы stubBot. Запускается от root: по расписанию (cron, каждый день) и перед каждым обновлением.
# Печатает путь к созданному файлу. Копии старше KEEP_DAYS дней удаляются.
#
# Восстановить на пустую базу:
#   systemctl stop stubbot
#   runuser -u postgres -- dropdb stubbot && runuser -u postgres -- createdb -O stubbot stubbot
#   gunzip -c /opt/stubbot/backups/<файл>.sql.gz | runuser -u postgres -- psql -q stubbot
#   systemctl start stubbot
#
# Копии лежат на том же сервере: от ошибки или неудачного обновления они спасут, от потери сервера — нет.
# В файлах персональные данные клиентов — каталог закрыт для всех, кроме root.

set -euo pipefail
# У cron короткий PATH (/usr/bin:/bin), а runuser лежит в /usr/sbin.
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

DIR=/opt/stubbot/backups
DB_NAME=stubbot
KEEP_DAYS=14

mkdir -p "$DIR"
chmod 700 "$DIR"
umask 077
FILE="$DIR/stubbot-$(date +%Y%m%d-%H%M%S).sql.gz"

# --no-owner: копию можно развернуть под любой ролью. Сначала во временный файл — оборванная копия не останется.
runuser -u postgres -- pg_dump --no-owner "$DB_NAME" | gzip > "$FILE.tmp"
mv "$FILE.tmp" "$FILE"
find "$DIR" -name 'stubbot-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
echo "$FILE"
