#!/usr/bin/env bash
set -euo pipefail

umask 077
: "${BACKUP_AGE_RECIPIENT:?BACKUP_AGE_RECIPIENT is required}"

backup_dir="${BACKUP_DIR:-/backups}"
case "$backup_dir" in
  /backups|/backups/*) ;;
  *) printf 'unsafe BACKUP_DIR: %s\n' "$backup_dir" >&2; exit 2 ;;
esac
mkdir -p "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
plain="/tmp/job-system-$stamp.tar"
encrypted="$backup_dir/job-system-$stamp.tar.age"
trap 'rm -f -- "$plain" "$plain.tmp" "$encrypted.tmp"' EXIT

python manage.py cleanup_expired
python manage.py create_snapshot "$plain"
age --encrypt --recipient "$BACKUP_AGE_RECIPIENT" --output "$encrypted.tmp" "$plain"
test -s "$encrypted.tmp"
mv "$encrypted.tmp" "$encrypted"
chmod 600 "$encrypted"
rm -f -- "$plain"

latest="$(find "$backup_dir" -maxdepth 1 -type f -name 'job-system-*.tar.age' -printf '%T@ %p\n' | sort -nr | head -n1 | cut -d' ' -f2-)"
while IFS= read -r -d '' candidate; do
  [[ "$candidate" == "$latest" ]] && continue
  rm -f -- "$candidate"
done < <(find "$backup_dir" -maxdepth 1 -type f -name 'job-system-*.tar.age' -mmin +43200 -print0)

printf 'backup_ok %s\n' "$encrypted"
