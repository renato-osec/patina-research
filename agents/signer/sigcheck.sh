#!/usr/bin/env bash
# CLI: agents/signer/sigcheck.sh BNDB 0xADDR '<rust-decl>' [--prelude TEXT] [--json]
#
# Quick check from the command line:
#   sigcheck.sh hl-node.bndb 0x4162b0 '(a: u64, b: &str) -> u32'
#   sigcheck.sh hl-node.bndb 0x4162b0 '(b: Big) -> Big' \
#       --prelude 'pub struct Big { pub a: u64, pub b: u64, pub c: u64 }'
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
cd "$ROOT"
[ -f "$ROOT/.envrc" ] && . "$ROOT/.envrc"
exec "$VIRTUAL_ENV/bin/python3" -c "
import argparse, json, sys
sys.path.insert(0, '$HERE')
from sigcheck import check_signature

ap = argparse.ArgumentParser()
ap.add_argument('binary')
ap.add_argument('addr')
ap.add_argument('decl')
ap.add_argument('--prelude', default=None)
ap.add_argument('--json', action='store_true')
a = ap.parse_args()
addr = int(a.addr, 16) if a.addr.lower().startswith('0x') else int(a.addr)
r = check_signature(a.binary, addr, a.decl, prelude=a.prelude)
if a.json:
    print(json.dumps(r.to_dict(), indent=2))
else:
    print(r.summary())
sys.exit(0 if r.perfect else 1)
" "$@"
