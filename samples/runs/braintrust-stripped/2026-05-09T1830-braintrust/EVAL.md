# 2021-braintrust stripped, chain @ 2026-05-09T18:30

First chain run on `bench/benchmark_stripped_183/2021-braintrust/binary`
(594 fns post-WARP) targeting `0x4088b0` (the jump fn, renamed by
agent to `hashmap_index_key`), depth=1 → 4 fns total: jump,
expect_failed, hash_one, main.

## Cost / time
- marinator: 354s, $0.70 (3/3 done)
- signer:    390s, $1.47 (3/4 perfect)
- flower:    839s, $2.81 (3/4 perfect, 1 failed=`main`)
- chain:    1588s, **$4.98**

## White-box scoring (whitebox_eval.py vs source.rs)

| leaf | in_gt | signer arity | flower arity | flower body real | grade |
|---|---|---|---|---|---|
| `expect_failed` (core::option) | F | 1 | 1 | YES | A — clean panic shim recovery |
| `hash_one` (core::hash::BuildHasher) | F | 2 | 0 (no body) | NO | C — signer recovered sig; flower bailed |
| `hashmap_index_key` (=`State::jump` after rename) | F* | 3 | 3 | YES | B — recovered with HashMap.get + early-return |
| `main` | **T** | 0 | 0 | NO | F — flower exhausted/cheesed; main is too big |

\* leaf doesn't match source.rs because the agent renamed jump → hashmap_index_key.
   Eval needs to track ORIGINAL fn name (pre-rename) for proper GT match.
   Currently: 1/4 matched (main). With un-renamed name preservation: 2/4 (main + jump).

## Per-fn detail

### main (FAILED, $1.18)
- 18 turns, 3 submits, exhausted. Final score 1.0 but perfect=False (last submit didn't pass).
- Submitted ~70 LOC of attempted main body with HashMap stuff.
- Real source.rs `main` is ~80 LOC with stdin/stdout I/O, brainfuck interpreter loop. Big fn.
- TODO: make region tools mandatory for fns above N basic-blocks.

### hashmap_index_key (formerly `jump`) — score 1.0, real body
- Sig recovered: 3 args, returns u64. Source.rs has `fn jump(&self, i: usize, fwd: bool) -> usize`.
- Arity matches (`&self` counts as one). Return type `u64` vs `usize` — semantically same on 64-bit.
- Body recovered the early-return + HashMap lookup pattern.
- Verdict: clean recovery.

### expect_failed — score 1.0, real body
- 1 arg, panics. `fn expect_failed(msg_ptr: &str) -> ! { panic!("{}", msg_ptr) }`. Real.

### hash_one — signer perfect, flower no body
- Signer: `(this: &RandomState, x: &u64) -> u64`. Correct.
- Flower bailed early (likely SIMD-heavy SipHash internals).

## Observations

1. **whitebox_eval needs a name-anchor file**: signer/flower rename fns (e.g. jump → hashmap_index_key); ground-truth match requires the ORIGINAL un-renamed name. Add: read the symbolic `bench/benchmark_183/<sample>/binary` symbol table, build addr→leaf map, anchor eval against that instead of the post-rename name.
2. **No region tools used** by the agent on this run either. main was the obvious candidate (large fn), agent went whole-fn anyway, exhausted.
3. **Cost is reasonable** ($4.98 for 4 fns) — most of it on main's failed attempt.
4. **3/4 successful flower recoveries** is the actual quality score. honest pass rate.
