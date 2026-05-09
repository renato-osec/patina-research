# hl-node2 chain run @ 2026-05-09T05:50

Target: `--addresses 0x555556bb4d20`, depth=1, workers=8, model=opus.

## Cost / time
- signer: 1652s, $7.04, 5/8 perfect, 137k tokens
- flower: 1108s, $9.00, 6/8 "perfect", 139k tokens
- total: ~46min, $16.04, 16 attempted recoveries (8 fns × signer+flower)

## Per-fn flower decompilation quality

| fn | flower.perfect | grade | what was produced | notes |
|---|---|---|---|---|
| `panic_assertion_failed` | True | **C** | 26-line body with `format_args!("")` + 5× `let _ = X` and a manual fn_0x... call | binding theater pattern; new cheese detector now flags this (`5/16 stmts are let _`) |
| `panic_index_out_of_bounds` | True | **A-** | extern `fn_0x5555556c96f0(args) -> !`, calls with `format_args!("index out of bounds: len {} index {}")` | clean recovery of stdlib panic shim |
| `panic_unwrap_err` | True | **A** | matches the `fmt::Arguments` shim pattern | clean |
| `panic_overflow_sub` | True | **A** | `fn panic_overflow_sub() -> ! { panic!("attempt to subtract with overflow") }` | trivially correct 1-liner |
| `option_unit_qty_add_a` | True | **B+** | plausible body: conditions on tag/unit/qty, returns `(tag, unit)` from one operand based on which is zero / which has tag==2 | structurally reasonable but no ground truth |
| `compute_margin_requirement_by_mode` | False | **F (transport)** | empty source (`submits=0`); SDK CLI subprocess crashed mid-stream | $0.96 wasted, 17 turns |
| `compute_user_margin_and_shortfall` | False | **F (cheese)** | `fn ...(*mut u8, ...) {}` — empty body for a complex computation; transport-crashed | $1.95 wasted; new empty-body detector now rejects this |
| `compute_account_value_for_direction` | True | **F (cheese)** | zeroes out `result` struct + `let _ = (user_state, is_isolated, asset_idx, margin_mode, oracle_data)` | new multi-arg-tuple detector now rejects this |

Net: 4 real recoveries, 1 borderline, 3 zero-value submissions (2 transport-crashed, 1 cheese).

## Cheese caught after this run (commit `9448a61`)

- `compute_user_margin_and_shortfall` empty body → would be rejected (binary has > 4 BBs).
- `compute_account_value_for_direction` `let _ = (a, b, c, d, e)` → would be rejected (multi-arg discard).
- `panic_assertion_failed` 5/16 let_ density → would be rejected.

## Architectural takeaways

1. Whole-fn opus reconstructions on > 30-BB fns crash the SDK CLI ~50% of the time. Per-fn cap is ~$2 burned on those. → motivates region-based reconstruction (commit `25373ed`) where the agent submits 3-8 BBs at a time and gets clean dataflow verdicts.
2. Cheese works because anemone is conservative through opaque calls AND the validator accepts "no-flow ↔ no-flow" as compatible. Region-scoped checks should reduce this since smaller windows have less opaque-call opportunity.
3. Transport crashes on iter ≥ max_turns happen when the agent rambles to wrap up. `default_max_turns=12` for flower (commit `88a986d`) caps this.
