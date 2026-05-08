#!/usr/bin/env bash
# Recompile decompetition rust samples with rustc 1.83.0 stable, mirror into
# benchmark_stripped_183/ with stripped binaries.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
SRC_ROOT=/home/renny/doc/work/research/decompetition/_sources_hidden
TOOLCHAIN=1.83.0
STRIPPED=$(cd "$HERE/.." && pwd)/benchmark_stripped_183

for year in 2020 2021; do
    for src in "$SRC_ROOT/$year/rust/"*.rs; do
        name=$(basename "$src" _source.rs)
        out="$HERE/$year-$name"
        sout="$STRIPPED/$year-$name"
        mkdir -p "$out" "$sout"
        cp "$src" "$out/source.rs"
        cp "$src" "$sout/source.rs"
        echo "building $year-$name"
        rustup run "$TOOLCHAIN" rustc \
            -C opt-level=1 -C codegen-units=1 \
            -o "$out/binary" "$out/source.rs"
        cp "$out/binary" "$sout/binary"
        strip "$sout/binary"
    done
done
rustup run "$TOOLCHAIN" rustc --version > "$HERE/rustc.version"
cp "$HERE/rustc.version" "$STRIPPED/rustc.version"
echo "done ($(rustup run $TOOLCHAIN rustc --version))"
