# hl-node2 typ targets — v4 (max_turns=24, submit_rounds=5, binary_over fix)

Source: `/home/renny/hl-node2.bndb`. Same 6 typ-listed fns.

## Chain totals

| stage     | targets | perfect (json) | wall  | $ |
|-----------|---------|---------------:|------:|---:|
| marinator | 6       | 0/6            | 22.9m | 2.34 |
| signer    | 6       | 4/6            | 29.4m | 4.89 |
| flower    | 6       | 3/6            | 31.0m | 7.17 |

Total: 1h 23.7m, $14.41, 314k tokens.

## Per-fn (flower)

| fn                                     | score | perfect | submits | $ |
|----------------------------------------|------:|:-:|:-:|---:|
| build_adl_candidate_set                | 0.99  | ❌ (warn-trap) | 5 | 1.67 |
| compute_margin_requirement_by_mode     | 1.00  | ✓ | 2 | 0.76 |
| compute_user_margin_and_shortfall      | 1.00  | ❌ (warn-trap) | 2 | 0.00 |
| compute_adl_ranking_score              | 1.00  | ✓ | 3 | 0.98 |
| clearinghouse_adl_orchestrator         | 1.00  | ✓ (558 BBs!) | 3 | 1.54 |
| compute_position_liquidation_check     | 1.00  | ❌ (warn-trap) | 5 | 2.22 |

Three 1.00-score fns marked perfect=False because the warning-bounce
loop exhausted the budget on cheese antipatterns even though the
dataflow was clean. The `flower leniency` patch (warning-bounce
once not max_rounds; APPLY_SCORE_THRESHOLD 0.85 -> 0.5) landed AFTER
this run started; the next run should turn those 3 into successes.

## Notable wins

- **clearinghouse_adl_orchestrator**: 558-block monster, recovered
  perfectly in 3 submits at $1.54. Auto destructor preflight fired
  on `T=Clearinghouse`. v3 didn't even get this far (timed out at
  signer).
- **compute_adl_ranking_score**: was 0.97 imperfect in v3; clean 1.00
  in v4.
- **compute_margin_requirement_by_mode**: was 1.00 imperfect in v3
  (cheese-warning trap); clean here.

## What landed in v4 vs v3

1. `binary_over` no longer triggers `has_warnings` — anemone's
   opaque-call worst-case stays informational instead of bouncing.
2. `max_turns` 12 → 24 + `turn_budget` PostToolUse hook warns the
   agent at -4 and -1 turns left.
3. `submit_rounds` 3 → 5.
4. Marinator BB-scales its per-fn timeout (1.5×/2×/3× for >30, >100,
   >300 BBs).
5. Sidecar mirror: stage saves now copy the patina.json next to the
   output bndb, so direct pipeline.py runs daisy-chain correctly.

## Followups

- The flower leniency patch (post-run) accepts warnings on attempt
  2 instead of running the full submit_rounds budget. Should turn
  the 3 score-1.00-but-FAILED fns into successes.
- Library-symbol lock (also post-run) prevents marinator/signer/
  flower from renaming WARP-recovered names.
- 0 regions submitted across all 6 fns. Forced region-first refactor
  is still the next big change for BB→Rust mapping.

## Flower-redo (leniency patch active)

`flower_redo/` archives a re-run of just the 3 v4-FAILED fns
against v4's signed bndb, with the leniency patch (warning bounce
at attempt 1, accept at attempt 2; APPLY_SCORE_THRESHOLD 0.5).
$5.18, 19m, 1/3 fully perfect:

| fn | score | perfect | submits | note |
|---|---:|:-:|:-:|---|
| build_adl_candidate_set | 1.00 | ✓ | 2 | leniency cleared the warning |
| compute_user_margin_and_shortfall | 1.00 | imperfect | 4 | applied via threshold; final validator dataflow off |
| compute_position_liquidation_check | 1.00 | imperfect | 1 | hit 600s timeout |

So **5/6 fns now have flower bodies in bv** (4 fully perfect + 1
imperfect-but-applied). compute_position_liquidation_check needs
a longer timeout.
