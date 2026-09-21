#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  printf 'usage: %s BACKUP.tar.age AGE-IDENTITY.txt\n' "$0" >&2
  exit 2
fi
backup="$(realpath "$1")"
identity="$(realpath "$2")"
[[ -f "$backup" && -f "$identity" ]] || { printf 'backup or identity missing\n' >&2; exit 2; }
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
age --decrypt --identity "$identity" --output "$work/snapshot.tar" "$backup"
python manage.py verify_snapshot "$work/snapshot.tar"
