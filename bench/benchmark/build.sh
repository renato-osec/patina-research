#!/usr/bin/env bash
# Recompile decompetition rust samples with current nightly (matches nacre's
# rustc), place binary + source in per-sample subdirs for layout matching.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SRC_ROOT=/home/renny/doc/work/research/decompetition/_sources_hidden
TOOLCHAIN=nightly

for year in 2020 2021; do
    for src in "$SRC_ROOT/$year/rust/"*.rs; do
        name=$(basename "$src" _source.rs)
        out="$HERE/$year-$name"
        mkdir -p "$out"
        cp "$src" "$out/source.rs"
        echo "building $year-$name"
        rustup run "$TOOLCHAIN" rustc \
            -C opt-level=1 -C codegen-units=1 \
            -o "$out/binary" "$out/source.rs"
    done
done
rustup run "$TOOLCHAIN" rustc --version > "$HERE/rustc.version"
echo "done ($(rustup run $TOOLCHAIN rustc --version))"
