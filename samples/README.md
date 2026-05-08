# patina / python samples

One-time setup:

```bash
cd python_tests
uv venv --python 3.12
uv sync
./build_extensions.sh
cargo build --release --features binja -p patina
```

## Simplest API: analyze a binary from Python

```python
import sys, pathlib
sys.path.insert(0, "python_tests")
from patina_query import analyze

report = analyze("/path/to/binary")
print(report)                          # Report(329 functions, dir=...)
for fn in report.by_label("Vec"):
    print(f"{fn.addr:#x}  {fn.exact}")
```

`analyze()` shells out to the `patina` CLI under the hood, so the
calling Python does not need to link `librustc_driver`.

Set `BINJA_PATH` once (or write it into `/tmp/patina.env`) and
`analyze()` will find it. If the `patina` binary isn't on PATH, set
`PATINA_BIN=/path/to/patina` or run from the patina workspace so the
helper can find `target/release/patina`.

## Example scripts

```bash
uv run --project ../python_tests python analyze_and_query.py <binary>
uv run --project ../python_tests python inspect_function.py    <binary> 0x41cea0
uv run --project ../python_tests python find_vecs.py           <binary>
uv run --project ../python_tests python layout_probe.py
uv run --project ../python_tests python flow_dump.py           <source.rs>
```

The first three need `BINJA_PATH` plus a release-compiled patina
binary. `layout_probe.py` and `flow_dump.py` are pure Rust-source
analysis (no binja).

## Why the subprocess?

`librustc_driver.so` is tagged `DF_STATIC_TLS`, so glibc must fit its
~1.4 KB TLS block into the static-TLS surplus (default 1664 B).
Loading it in-process alongside Binary Ninja's dlopen'd plugins
sometimes works, often hangs, and is not worth debugging per call
site. `analyze()` runs `patina` as a child process with a clean env
(`LD_PRELOAD` and `GLIBC_TUNABLES` stripped) so the two never share a
process. Not a NixOS-specific issue; affects every glibc distro.
