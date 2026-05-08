#!/usr/bin/env bash
# Diff nacre's --c output against per-fixture golden .c files.
# Each `<name>.rs` here pairs with `<name>.c` and a root struct picked
# from a `// nacre-root: <Name>` comment, falling back to the filename
# capitalized (braintrust.rs → State via comment, etc.).
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
NACRE=${NACRE_BIN:-$HERE/../target/release/nacre}

declare -A ROOTS=(
    [braintrust]=LookupMap
    [lookupmap]=LookupMap
    # Edge cases mirroring real benchmark_183 binaries.
    [s2ring]=Match            # multi-lifetime struct, ref-to-other-struct
    [toobz]=TSuck             # 5-level repr(transparent) chain → int32_t fd
)

fail=0
for rs in "$HERE"/*.rs; do
    name=$(basename "$rs" .rs)
    golden="$HERE/$name.c"
    root=${ROOTS[$name]:-}
    [[ -z $root || ! -f $golden ]] && continue
    actual=/tmp/nacre_test_actual.$$
    "$NACRE" "$rs" --c --type "$root" > "$actual"
    if ! diff -u "$golden" "$actual"; then
        echo "FAIL: $name (root=$root)"
        fail=1
    else
        echo "PASS: $name"
    fi
    rm -f "$actual"
done

# Python edge-case checks — run only if `nacre` is importable (built &
# installed via maturin develop). Skipped quietly otherwise so the CLI
# runner stays useful in CI machines without a python wheel.
SIG_TEST="$HERE/test_sigs.py"
if [[ -f $SIG_TEST ]]; then
    PY=${NACRE_PY:-$HERE/../.venv/bin/python}
    if [[ -x $PY ]]; then
        # nacre's pyo3 module dlopens librustc_driver; without LD_PRELOAD
        # the static-TLS pool overflows. Build the same env the .envrc
        # constructs in agents/common, but only if not already set.
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
        if "$PY" -c 'import nacre' 2>/dev/null; then
            if ! "$PY" "$SIG_TEST"; then
                fail=1
            fi
        else
            echo "SKIP: python (nacre not importable from $PY)"
        fi
    else
        echo "SKIP: python (no interpreter at $PY)"
    fi
fi

exit "$fail"
