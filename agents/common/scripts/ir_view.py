#!/usr/bin/env python3
"""Render the patina sidecar as a per-fn IR view: each function's
recovered signer signature/types alongside flower's whole-fn body and
any per-region snippets, ordered by binary BB index.

Usage:
    ir_view.py <sidecar.patina.json> [--md|--html]
        --md (default): markdown output to stdout
        --html: standalone HTML

Reads the sidecar layout produced by signer + flower:
  {
    "<addr>": {
      "signer": {"name", "rust_signature", "rust_types"},
      "flower": {"name", "source",
                 "regions": [{"block_start","block_end","source","score","note"}, ...]}
    }
  }

The point: a single document where each fn's binary location maps
1-to-1 to its recovered Rust, with per-region snippets ordered by
block-start. This is the "intermediate representation up to Rust"
view - what the agent recovered, indexed by where it lives in the
binary.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path


def _md(sidecar: dict) -> str:
    out: list[str] = []
    out.append("# Patina IR view\n")
    addrs = sorted(sidecar.keys(), key=lambda a: int(a, 16))
    out.append(f"_{len(addrs)} fn(s) recovered_\n")
    for addr in addrs:
        entry = sidecar[addr]
        signer = entry.get("signer") or {}
        flower = entry.get("flower") or {}
        name = (flower.get("name") or signer.get("name") or "").strip() or addr
        out.append(f"\n---\n\n## `{name}` @ {addr}\n")
        if sig := signer.get("rust_signature"):
            out.append("### signer signature\n```rust\n" + sig.strip() + "\n```\n")
        if types := signer.get("rust_types"):
            out.append("### signer types\n```rust\n" + types.strip() + "\n```\n")
        if src := flower.get("source"):
            out.append("### flower whole-fn\n```rust\n" + src.strip() + "\n```\n")
        regions = flower.get("regions") or []
        if regions:
            out.append(f"### flower regions ({len(regions)})\n")
            # Order by block_start; merge any overlapping by listing all.
            for r in sorted(regions, key=lambda r: int(r.get("block_start", 0))):
                bs = r.get("block_start"); be = r.get("block_end")
                sc = r.get("score", 0.0); nt = (r.get("note") or "").strip()
                head = f"BB[{bs}..{be}) score={sc:.2f}"
                if nt: head += f"  — _{nt}_"
                out.append(f"\n#### {head}\n```rust\n"
                           + (r.get("source") or "").strip() + "\n```\n")
        if not signer and not flower:
            out.append("_(no signer/flower entries)_\n")
    return "\n".join(out)


def _html(sidecar: dict) -> str:
    md = _md(sidecar)
    # Cheap markdown→html: just wrap fenced code + headers; pandoc is
    # better but we want zero deps.
    import html
    lines: list[str] = []
    in_code = False
    for raw in md.splitlines():
        line = raw
        if line.startswith("```"):
            if in_code:
                lines.append("</pre>")
                in_code = False
            else:
                lang = line[3:].strip()
                lines.append(f'<pre data-lang="{lang}">')
                in_code = True
            continue
        if in_code:
            lines.append(html.escape(line))
            continue
        if line.startswith("# "):
            lines.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            lines.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("### "):
            lines.append(f"<h3>{html.escape(line[4:])}</h3>")
        elif line.startswith("#### "):
            lines.append(f"<h4>{html.escape(line[5:])}</h4>")
        elif line.startswith("---"):
            lines.append("<hr/>")
        elif line.strip().startswith("_") and line.strip().endswith("_"):
            inner = line.strip().strip("_")
            lines.append(f"<p><em>{html.escape(inner)}</em></p>")
        elif line.strip():
            lines.append(f"<p>{html.escape(line)}</p>")
    body = "\n".join(lines)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>patina IR view</title>"
        "<style>"
        "body{font-family:system-ui,sans-serif;max-width:1100px;margin:1em auto;padding:0 1em}"
        "pre{background:#1e1e1e;color:#dcdcdc;padding:0.8em;border-radius:4px;"
        "overflow:auto;font-size:13px;line-height:1.4}"
        "h2{margin-top:1.2em;border-bottom:1px solid #ccc;padding-bottom:.2em}"
        "h3{margin-top:1em;color:#555}"
        "h4{margin-top:.6em;color:#777;font-weight:normal}"
        "hr{border:none;border-top:2px solid #ddd;margin:2em 0}"
        "</style></head><body>" + body + "</body></html>"
    )


def main(argv: list[str]) -> int:
    fmt = "md"
    paths: list[str] = []
    for a in argv[1:]:
        if a == "--html":
            fmt = "html"
        elif a == "--md":
            fmt = "md"
        else:
            paths.append(a)
    if len(paths) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    p = Path(paths[0])
    if not p.is_file():
        print(f"sidecar not found: {p}", file=sys.stderr)
        return 2
    sidecar = json.loads(p.read_text(encoding="utf-8"))
    print(_html(sidecar) if fmt == "html" else _md(sidecar))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
