#!/usr/bin/env bash
set -eo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
cd "$ROOT"
[ -f "$ROOT/.envrc" ] && . "$ROOT/.envrc"
exec "$VIRTUAL_ENV/bin/python3" "$HERE/run.py"
