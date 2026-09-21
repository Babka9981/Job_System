#!/usr/bin/env bash
set -Eeuo pipefail

repo="${1:-/repo}"
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT

run_fault() {
  local fail_at="$1" case_root="$work/case-$1" deployment="$work/case-$1/deployment"
  mkdir -p "$deployment/runtime/data" "$deployment/runtime/private" "$case_root/payload/private" "$case_root/fakebin"
  cp "$repo/deployment/restore.sh" "$deployment/restore.sh"
  printf 'JOB_IMAGE_TAG=test\n' > "$deployment/release.env"
  printf 'old-data\n' > "$deployment/runtime/data/old-data"
  printf 'old-private\n' > "$deployment/runtime/private/old-private"
  printf 'new-database\n' > "$case_root/payload/db.sqlite3"
  printf 'new-private\n' > "$case_root/payload/private/new-private"
  tar -cf "$deployment/runtime/restore.snapshot.tar" -C "$case_root" payload

  cat > "$case_root/fakebin/systemctl" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "is-active" ]]; then printf 'inactive\n'; fi
exit 0
EOF
  cat > "$case_root/fakebin/sudo" <<'EOF'
#!/usr/bin/env bash
exec "$@"
EOF
  cat > "$case_root/fakebin/docker" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  cat > "$case_root/fakebin/curl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  cat > "$case_root/fakebin/chown" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  cat > "$case_root/fakebin/mv" <<'EOF'
#!/usr/bin/env bash
count=0
[[ -f "$MV_STATE" ]] && read -r count < "$MV_STATE"
count=$((count + 1))
printf '%s\n' "$count" > "$MV_STATE"
if [[ "$count" -eq "$FAIL_MV_AT" && ! -f "$MV_STATE.failed" ]]; then
  : > "$MV_STATE.failed"
  exit 42
fi
exec /bin/mv "$@"
EOF
  chmod +x "$case_root/fakebin/"*

  set +e
  (
    cd "$deployment"
    PATH="$case_root/fakebin:/usr/bin:/bin" \
      MV_STATE="$case_root/mv-count" FAIL_MV_AT="$fail_at" \
      bash ./restore.sh runtime/restore.snapshot.tar
  ) >"$case_root/output.log" 2>&1
  local status=$?
  set -e
  [[ "$status" -ne 0 ]]
  [[ "$(cat "$deployment/runtime/data/old-data")" == "old-data" ]]
  [[ "$(cat "$deployment/runtime/private/old-private")" == "old-private" ]]
  [[ ! -e "$deployment/runtime/data/db.sqlite3" ]]
  [[ ! -e "$deployment/runtime/private/new-private" ]]
  grep -q 'restore failed; writers remain disabled' "$case_root/output.log"
}

bash -n "$repo/deployment/restore.sh"
run_fault 2
run_fault 4
printf 'restore_faults_ok syntax=pass fail_second_mv=rollback fail_fourth_mv=rollback\n'
