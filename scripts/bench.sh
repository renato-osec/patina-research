#!/usr/bin/env bash
# Run patina --analyze on every test binary registered below and print a
# summary table. Each binary's crate was cloned under
# `rust-samples/abstract_il/programs-test/<name>-src/` and built under
# the matched nightly (see `nacre/rust-toolchain.toml`). Add a new line
# to `BINS` to bring a new program into the test set.

set -eu

WS="$(dirname "$0")/.."
PT="$WS/rust-samples/abstract_il/programs-test"
PATINA="$WS/target/release/patina"

: "${LIBCLANG_PATH:=/nix/store/845ph6k0bsg956nf7f5w9jsbzz1qz3ls-rocm-llvm-clang-unwrapped-6.0.2/lib}"
: "${LD_LIBRARY_PATH:=/nix/store/hh698a2nnpqr47lh52n26wi8fiah3hid-gcc-13.3.0-lib/lib:${BINJA_PATH:-}}"
export LIBCLANG_PATH LD_LIBRARY_PATH

BINS=(
    "ripgrep|$PT/ripgrep-src/target/release/rg|$PT/ripgrep-src/target/release/deps"
    "fd|$PT/fd-src/target/release/fd|$PT/fd-src/target/release/deps"
    "hyperfine|$PT/hyperfine-src/target/release/hyperfine|$PT/hyperfine-src/target/release/deps"
    "tentypes|$PT/target/release/tentypes|$PT/target/release/deps"
    "realish|$PT/target/release/realish|$PT/target/release/deps"
    "nested-generics|$PT/target/release/nested-generics|$PT/target/release/deps"
)

printf "%-18s %8s %8s %8s %8s %8s %8s\n" name crates catalog traced exact labels synth
for row in "${BINS[@]}"; do
    IFS='|' read -r name bin deps <<<"$row"
    if [ ! -x "$bin" ]; then
        printf "%-18s (binary missing)\n" "$name"
        continue
    fi
    out=$(mktemp)
    "$PATINA" "$bin" --target-deps "$deps" --analyze \
        --register rdi --register rsi > "$out" 2>/dev/null || true
    crates=$(grep -oE 'crates detected  : [0-9]+' "$out" | awk '{print $4}')
    cat=$(grep -oE '[0-9]+ dep-type entries' "$out" | head -1 | awk '{print $1}')
    traced=$(grep -oE 'traced [0-9]+' "$out" | head -1 | awk '{print $2}')
    exact=$(grep -oE '[0-9]+ exact-match hits' "$out" | head -1 | awk '{print $1}')
    labels=$(grep -oE 'dep-type exact matches \([0-9]+\)' "$out" | head -1 | grep -oE '[0-9]+')
    synth=$(grep -oE '[0-9]+ synthetic types' "$out" | head -1 | awk '{print $1}')
    printf "%-18s %8s %8s %8s %8s %8s %8s\n" \
        "$name" "${crates:-?}" "${cat:-?}" "${traced:-?}" "${exact:-?}" "${labels:-?}" "${synth:-?}"
    rm -f "$out"
done
