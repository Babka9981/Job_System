#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  printf 'usage: %s VERIFIED-SNAPSHOT.tar\n' "$0" >&2
  exit 2
fi

cd "$(dirname "${BASH_SOURCE[0]}")"
set -a
# shellcheck source=/dev/null
. ./release.env
set +a
: "${JOB_IMAGE_TAG:?release.env must contain JOB_IMAGE_TAG}"

runtime="$PWD/runtime"
archive="$(realpath "$1")"
case "$archive" in
  "$runtime"/*) ;;
  *) printf 'snapshot must be inside %s\n' "$runtime" >&2; exit 2 ;;
esac
[[ -f "$archive" ]] || { printf 'snapshot not found\n' >&2; exit 2; }

writers=(
  job-system-monitor.timer
  job-system-cleanup.timer
  job-system-backup.timer
  job-system-monitor.service
  job-system-cleanup.service
  job-system-backup.service
)

assert_writers_inactive() {
  local unit state failed=0
  for unit in "${writers[@]}"; do
    state="$(systemctl is-active "$unit" 2>/dev/null || true)"
    if [[ "$state" != "inactive" ]]; then
      printf 'restore blocked: %s is %s\n' "$unit" "${state:-unknown}" >&2
      failed=1
    fi
  done
  [[ "$failed" -eq 0 ]]
}

sudo systemctl disable --now \
  job-system-monitor.timer job-system-cleanup.timer job-system-backup.timer
sudo systemctl stop \
  job-system-monitor.service job-system-cleanup.service job-system-backup.service
assert_writers_inactive

docker compose stop web
docker compose run --rm -T -v "$archive:/restore.tar:ro" \
  web python manage.py verify_snapshot /restore.tar

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
stage_dir="$runtime/stage-$stamp"
rollback_dir="$runtime/rollback-$stamp"
failed_dir="$runtime/failed-$stamp"
data_dir="$runtime/data"
private_dir="$runtime/private"
swap_started=0
data_backed_up=0
private_backed_up=0
data_installed=0
private_installed=0

rollback_swap() {
  local status=$?
  set +e
  if [[ "$swap_started" -eq 1 ]]; then
    docker compose stop web
    mkdir -m 0700 "$failed_dir"
    [[ "$data_installed" -eq 1 ]] && mv -- "$data_dir" "$failed_dir/data"
    [[ "$private_installed" -eq 1 ]] && mv -- "$private_dir" "$failed_dir/private"
    [[ "$data_backed_up" -eq 1 ]] && mv -- "$rollback_dir/data" "$data_dir"
    [[ "$private_backed_up" -eq 1 ]] && mv -- "$rollback_dir/private" "$private_dir"
  fi
  printf 'restore failed; writers remain disabled, inspect %s and %s\n' "$rollback_dir" "$failed_dir" >&2
  exit "$status"
}
trap rollback_swap ERR

[[ -d "$data_dir" && -d "$private_dir" ]]
mkdir -m 0700 "$stage_dir" "$stage_dir/data" "$stage_dir/private" "$rollback_dir"
tar -xf "$archive" -C "$stage_dir"
[[ -f "$stage_dir/payload/db.sqlite3" && -d "$stage_dir/payload/private" ]]
install -m 0600 "$stage_dir/payload/db.sqlite3" "$stage_dir/data/db.sqlite3"
cp -a "$stage_dir/payload/private/." "$stage_dir/private/"
chown -R 10001:10001 "$stage_dir/data" "$stage_dir/private"

# A timer could be started manually after the first check. Recheck immediately
# before the first mount mutation; any non-inactive writer aborts with no swap.
assert_writers_inactive

# SWAP STARTS HERE
swap_started=1
mv -- "$data_dir" "$rollback_dir/data"
data_backed_up=1
mv -- "$private_dir" "$rollback_dir/private"
private_backed_up=1
mv -- "$stage_dir/data" "$data_dir"
data_installed=1
mv -- "$stage_dir/private" "$private_dir"
private_installed=1

docker compose run --rm -T web python manage.py migrate
docker compose up -d web
curl -fsS -H 'X-Forwarded-Proto: https' http://127.0.0.1:18111/healthz/
swap_started=0
trap - ERR

sudo systemctl enable --now \
  job-system-monitor.timer job-system-cleanup.timer job-system-backup.timer
printf 'restore_ok rollback=%s\n' "$rollback_dir"
