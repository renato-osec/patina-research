#!/usr/bin/env bash
# Smoke runner for tests under agents/common/tests/. Sets up the same
# LD_PRELOAD/BINJA env that .envrc constructs for interactive shells,
# then drops into each *.py test in turn. Exits non-zero on the first
# failure.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
PY=${COMMON_PY:-$HERE/../../venv/bin/python}

# Build the same LD_PRELOAD trampoline our .envrc.local does — binja's
# core dlopens librustc_driver and libstdc++.so.6 which the host has at
# nonstandard paths under /nix/store.
if [[ -z ${LD_PRELOAD:-} ]]; then
    SYSROOT=$(rustup run nightly-2024-12-07 rustc --print sysroot 2>/dev/null || true)
    LIBRUSTC=$(ls "$SYSROOT"/lib/librustc_driver-*.so 2>/dev/null | head -1 || true)
    LIBSTDCPP=$(ls -1 /nix/store/*-gcc-*-lib/lib/libstdc++.so.6 2>/dev/null \
        | grep -v -- '-aarch64-' | head -1 || true)
    if [[ -n $LIBRUSTC && -n $LIBSTDCPP ]]; then
        export GLIBC_TUNABLES=glibc.rtld.optional_static_tls=2000000
        export LD_PRELOAD="$LIBSTDCPP $LIBRUSTC"
        export LD_LIBRARY_PATH="$(dirname "$LIBSTDCPP")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    fi
fi
export BN_DISABLE_USER_PLUGINS=1

if [[ ! -x $PY ]]; then
    echo "SKIP: no interpreter at $PY"
    exit 0
fi

fail=0
for t in "$HERE"/test_*.py; do
    [[ -f $t ]] || continue
    if ! "$PY" "$t"; then
        fail=1
    fi
done
exit "$fail"
