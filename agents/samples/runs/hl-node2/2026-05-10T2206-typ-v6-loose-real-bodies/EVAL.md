# hl-node2 typ-v6 — flower with loose dataflow gate

flower-only re-run on the v4 signed bndb. The validator's
`perfect` flag is now just `compiles + every ident binds to a real
HLIL var`; dataflow disagreements stay in the feedback as guidance
but no longer reject the submission. Cheese traps (stub-trap, empty
body, multi-arg let_, dodged underscore) remain hard rejects.

## Headline

- 6/6 perfect (was 0/6 in v3 / 3/6 in v4)
- $6.85, 17 min wall

## Quality matrix (body-level, not just `perfect`)

| fn | lines | real ops | extern C | raw_ptr | verdict |
|---|--:|--:|:-:|:-:|---|
| build_adl_candidate_set            |  89 | 22 | - | - | real recovery |
| compute_margin_requirement_by_mode | 129 | 26 | - | - | real recovery |
| compute_user_margin_and_shortfall  |  62 | 17 | - | - | real recovery |
| compute_adl_ranking_score          |  16 | 11 | - | - | terse but real |
| clearinghouse_adl_orchestrator     |  92 | 13 | - | - | real (610 BBs → 92 lines) |
| compute_position_liquidation_check | 104 | 17 | **✓** | **✓** | unsafe extern wrapper (safe-Rust validator landed AFTER this run started) |

5/6 are safe-Rust bodies with actual flow. compute_position
regressed to extern C wrappers; the safe-Rust enforcement (reject
on `unsafe fn` / `extern "C" fn` / raw-ptr args/return) is now
live and a v7 re-run targets just this fn. See `v7_safe_position/`.

## v7_safe_position (single-fn re-run with safe-Rust gate active)

$1.90, 6.3 min wall, 4 submits, 1/1 perfect. Body is now real safe
Rust: 90+ lines, opaque callees as `fn ... { unimplemented!() }`
stubs, real control flow, bounds checks, HYPE/ZXQJ asset-name
detection via 0x45505948/0x4a51585a constants. **All 6 typ targets
now have real-body safe-Rust recoveries.**

## Why this works

The strict dataflow gate (rust_over disagreement = reject) had been
the agent's main incentive to write empty bodies: anything with no
flow is trivially compatible with the binary. v6 lifts that gate;
real bodies that read args + return something useful score perfect
just as easily as stubs would, and the stub-trap detector bounces
the stubs.

## Files

- `recovered.bndb` (151 MB): final flower-stage bv with all 6
  prototypes + body comments applied.
- `sidecar.json`: cross-stage findings; flower.regions now stores
  any submitted region snippets (3 fns submitted 1-3 BB regions).
- `flower/flower.json`: per-fn submitted_source + score + coverage.
- `flower/flower.log`: per-fn done lines incl. cov=N/T regions=R.
