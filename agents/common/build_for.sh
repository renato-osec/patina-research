#!/usr/bin/env bash
# Build the patina stack against a specific rustc toolchain and install into
# a per-toolchain venv.
#
# Usage:
#   build_for.sh <toolchain>          # e.g. nightly-2021-08-01, 1.56.0
#   build_for.sh --from <binary>      # read toolchain via chela
#   build_for.sh                      # current rust-toolchain.toml default
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)

TOOLCHAIN=""
if [[ "${1:-}" == "--from" ]]; then
    BINARY="${2:?need binary path}"
    # Parse via chela from the default venv (any venv with chela installed works).
    TOOLCHAIN=$(
        "$HERE/venv/bin/python" -c "
import chela, sys
r = chela.parse('$BINARY')
tc = r.get('rustc_toolchain') or r.get('rustc_version')
if not tc: sys.exit('chela could not read rustc metadata')
print(tc)
"
    )
    echo "chela: binary needs toolchain '$TOOLCHAIN'"
elif [[ -n "${1:-}" ]]; then
    TOOLCHAIN="$1"
fi

if [[ -z "$TOOLCHAIN" ]]; then
    TOOLCHAIN="$(rustc --version | awk '{print $2}')"
    echo "no toolchain specified; using current '$TOOLCHAIN'"
fi

rustup toolchain list | grep -q "^$TOOLCHAIN" || {
    echo "installing toolchain $TOOLCHAIN"
    rustup toolchain install --component rust-src --component rustc-dev --component llvm-tools "$TOOLCHAIN"
}

VENV="$HERE/venv-$TOOLCHAIN"
[[ -d "$VENV" ]] || python -m venv "$VENV"
"$VENV/bin/pip" install -q maturin >/dev/null

export VIRTUAL_ENV="$VENV"
export PATH="$VENV/bin:$PATH"
export RUSTUP_TOOLCHAIN="$TOOLCHAIN"
export LIBCLANG_PATH=$(clang -print-file-name=libclang.so | xargs dirname)

SITE=$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')

for c in nacre chela carpace lymph exoskeleton; do
    echo "building $c [$TOOLCHAIN]"
    (cd "$ROOT/tools/$c" && maturin develop --release >/dev/null)
    pkg="$SITE/$c"
    [[ -f "$ROOT/tools/$c/$c.pyi" ]] && cp "$ROOT/tools/$c/$c.pyi" "$pkg/__init__.pyi"
    touch "$pkg/py.typed"
done

echo
echo "done. venv: $VENV"
echo "activate: source $VENV/bin/activate"
