# hl-node2 chain run @ 2026-05-09T17:30 (post-cheese-fix)

Same target/flags as 2026-05-09T0550. First run with cheese detectors
(`9448a61`), max-turns=12 (`88a986d`), region tools (`25373ed`),
stderr capture (`50f185f`), and force-iterate-OFF default in place.

## Cost / time
- marinator:  2149s,  $3.39, 105k tok, 8/8 done
- signer:     2027s,  $7.92, 158k tok, **7/8 perfect, 0 failed**
- flower:     1298s,  $7.91, 144k tok, **3/8 perfect, 3 failed**
- chain:      5505s, $19.22 total, 728+406k tokens

## Marinator actionable yield (per-fn rename/retype/prototype counts)

| fn | renamed_vars | funcs | proto | comments | retypes | cost |
|---|---|---|---|---|---|---|
| panic_assertion_failed |  6 | 0 | 1 | 0 | 0 | $0.21 |
| panic_index_out_of_bounds | 11 | 0 | 1 | 0 | 0 | $0.19 |
| panic_unwrap_err | 13 | 1 | 1 | 0 | 0 | $0.43 |
| panic_overflow_sub |  4 | 0 | 0 | 0 | 0 | $0.15 |
| option_unit_qty_add_a | 14 | 0 | 0 | 0 | 0 | $0.49 |
| compute_margin_requirement_by_mode |  9 | 0 | 0 | 0 | 0 | $0.65 |
| compute_user_margin_and_shortfall | 15 | 0 | 0 | 0 | 0 | $0.63 |
| compute_account_value_for_direction | 16 | 0 | 0 | 1 | 0 | $0.66 |

Total marinator yield: 88 vars renamed, 1 fn renamed, 4 prototypes set, 1 comment, 0 retypes/types-declared. **Retypes=0 across all 8 fns is the biggest gap** — marinator should be retyping locals to richer types (Option<UnitQty>, etc.) once signer's struct defs are in scope.

## Signer (vs prior 0550 run)

| fn | prev score | curr score | delta |
|---|---|---|---|
| panic_assertion_failed | 1.00 | 1.00 | = |
| panic_index_out_of_bounds | 1.00 | 1.00 | = |
| core::result::unwrap_failed (was panic_unwrap_err) | 1.00 | 1.00 | = |
| panic_overflow_sub | 1.00 | 1.00 | = |
| option_unit_qty_add_a | 1.00 | 1.00 | = |
| compute_margin_requirement_by_mode | 0.80 | **1.00** | 🟢 |
| compute_user_margin_and_shortfall | 0.80 | **1.00** | 🟢 |
| compute_account_value_for_direction | 0.71 | **0.71** | = |

Signer recovered **rich domain types** this run that were placeholder structs last time:
- `MarginRequirementResult / MarginSpec / Position / OraclePx` (fully named fields)
- `AccountState` with `Option<UnitQty>` and `BTreeMap<u64, AssetPosition>`
- All cleared on **submits=1** (force-iterate-OFF working).

## Flower

| fn | curr | submits | exhausted | $ | grade |
|---|---|---|---|---|---|
| panic_assertion_failed | FAILED | 3 | True | 0.71 | cheese rejected (had `#![feature(fmt_internals)]`) |
| panic_index_out_of_bounds | OK | 1 | False | 0.41 | clean |
| core::result::unwrap_failed | OK | 1 | False | 0.39 | clean |
| panic_overflow_sub | OK | 2 | False | 1.00 | clean (real body) |
| option_unit_qty_add_a | imperfect | 0 | False | 1.18 | SDK transport crash |
| compute_margin_requirement_by_mode | imperfect | 1 | False | 1.14 | SDK transport crash mid-stream |
| compute_user_margin_and_shortfall | FAILED | 3 | True | 2.02 | exhausted |
| compute_account_value_for_direction | FAILED | 3 | True | 1.06 | exhausted (cheese-rejected: 5KB submission with `#![allow(dead_code)] #![allow(non_snake_case)]`) |

3/8 clean recoveries (down from 6/8 in prior run, but **prior was inflated by 3 cheese passes**). Honest fail rate now visible.

## Wins

1. **No false-perfects on cheese**: `panic_assertion_failed` (binding theater), `compute_account_value_for_direction` (allow-spam) both REJECTED. Prior run accepted both as perfect.
2. **Signer cost halved per fn**: $7.92 / 8 = $0.99 vs $7.04 / 5 perfect = $1.41 effective. Force-iterate-OFF.
3. **Big-fn signer recovery**: `compute_margin_requirement_by_mode` now perfect with real `Position { asset_id, side, szi, mid, margin: MarginSpec, ... }` instead of fabricated layout.

## Open issues

1. **2 SDK transport crashes** (option_unit_qty_add_a, compute_margin_requirement_by_mode) on flower side. stderr capture wired but didn't fire (callback registered but Node CLI emitted no stderr before exit). Need a different probe — maybe poll the SDK process state.
2. **Region tools not used**: agent had access to `region_blocks` / `check_region` / `submit_region` but ran whole-fn submissions on the big computeXxx. System-prompt nudge towards regions wasn't strong enough; agent fell back to old workflow. Need stronger hint or auto-detection: "if fn has > 20 BBs, USE regions".
3. **Marinator retypes=0 across 8 fns**: signer's struct defs aren't reaching marinator's retype path. Likely an ordering issue (marinator runs before signer in chain).
4. **Honest failures cost too much**: 5 fns with `final_score=0.0` ate $5.41 of the $7.91 flower spend. Per-fn timeout 1800s and max_turns=12 cap individual cost but agent still burns when target is unrealistic.
