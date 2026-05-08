"""Sanity-check buf_cap-vs-listener flow at 0x40ada0.

Lists every variable, every edge from listener (or rsi-arg) and every
predecessor of buf_cap (or its analogue), so a human can verify whether
the dependency the user expected is absent on purpose or because of a
naming mismatch.
"""
import os
os.environ["BN_DISABLE_USER_PLUGINS"] = "1"

import anemone
import binaryninja as bn

bv = bn.load("../agents/marinated.bndb")
fg = anemone.analyze(bv, 0x0040ada0)

print(f"fn  : {fg.fn_name}  @  {hex(fg.fn_addr)}")
print(f"args: {fg.params}")
print(f"ret : {fg.return_slot}")
print()

vars_ = fg.variables()
print(f"== variables ({len(vars_)}) ==")
for v in vars_:
    print(f"  {v}")
print()

# Match user-named slots
def find(name_substr: str) -> list[str]:
    return [v for v in vars_ if name_substr.lower() in v.lower()]

listener_cands = find("listener") or find("rsi")
buf_cap_cands  = find("buf_cap") or find("cap") or find("capacity")
print(f"listener candidates: {listener_cands}")
print(f"buf_cap candidates : {buf_cap_cands}")
print()

print(f"== answer reproduced ==")
print(f"  depends_on('buf_cap','listener') = {fg.depends_on('buf_cap','listener')}")
print()

if listener_cands:
    L = listener_cands[0]
    print(f"== forward reach from {L!r} ==")
    sinks = fg.transitive_sinks(L)
    print(f"  {len(sinks)} reachable slots; first 30:")
    for s in sinks[:30]:
        print(f"    -> {s}")
    print()
    print(f"  direct successors of {L!r}:")
    for dst, kind in fg.successors(L):
        print(f"    [{kind}] -> {dst}")
    print()

if buf_cap_cands:
    B = buf_cap_cands[0]
    print(f"== backward reach to {B!r} ==")
    srcs = fg.transitive_sources(B)
    print(f"  {len(srcs)} sources; first 30:")
    for s in srcs[:30]:
        print(f"    <- {s}")
    print()
    print(f"  direct predecessors of {B!r}:")
    for src, kind in fg.predecessors(B):
        print(f"    [{kind}] <- {src}")
    print()

# any pair (listener_cand, buf_cap_cand) that DOES have flow
print("== any depends_on pair that's True ==")
hit = False
for L in listener_cands:
    for B in buf_cap_cands:
        ok = fg.depends_on(B, L)
        print(f"  depends_on({B!r}, {L!r}) = {ok}")
        if ok:
            hit = True
print(f"any-true: {hit}")
