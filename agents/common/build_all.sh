#!/usr/bin/env bash
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)

export VIRTUAL_ENV="$HERE/venv"
export PATH="$VIRTUAL_ENV/bin:$PATH"
export LIBCLANG_PATH=$(clang -print-file-name=libclang.so | xargs dirname)

SITE=$("$VIRTUAL_ENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')

for c in nacre chela carpace lymph exoskeleton; do
    echo "building $c"
    (cd "$ROOT/tools/$c" && maturin develop --release >/dev/null)
    pkg="$SITE/$c"
    [[ -f "$ROOT/tools/$c/$c.pyi" ]] && cp "$ROOT/tools/$c/$c.pyi" "$pkg/__init__.pyi"
    touch "$pkg/py.typed"
done
echo "done"
