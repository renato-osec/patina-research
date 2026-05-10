# hl-node2 typ targets — v3 (auto preflight, max_turns=12, BB-scaled timeouts)

Source: `/home/renny/hl-node2.bndb` (rustc-1.83 binary).
Target list: `/home/renny/hl/bndb-viewer/report/main.typ` — 6 unique fn addrs.

## Chain totals

| stage     | targets | perfect | failed | cost  | wall  |
|-----------|---------|---------|--------|------:|------:|
| warper    | 594     | -       | -      | 0     |  ~5s  |
| marinator | 6       | 0       | 0      | $2.21 | 12.3m |
| signer    | 6       | 3       | 0      | $8.51 | 26.9m |
| flower    | 6       | 0       | 6      | $3.83 |  8.2m |

Total: $14.55, 47.7m wall, ~315k tokens.

## Per-fn

| addr | name | signer | flower | preflight | flower fail mode |
|---|---|:-:|:-:|:-:|---|
| 0x555556266b10 | build_adl_candidate_set | imperfect | FAILED 0.99 | - | exhausted, cheese warn |
| 0x555556abb140 | compute_adl_ranking_score | imperfect | FAILED 0.97 | - | exhausted, cheese warn |
| 0x555556ac3850 | clearinghouse_adl_orchestrator | imperfect | FAILED 0.88 | - | exhausted (very large fn) |
| 0x555556b61a50 | compute_margin_requirement_by_mode | imperfect | FAILED 1.00 | - | exhausted on cheese warn (extern C) |
| 0x555556bb4fb0 | compute_user_margin_and_shortfall | OK 1.00 | FAILED 0.99 | T=UserState ✓ | exhausted, cheese warn |
| 0x555556beadd0 | compute_position_liquidation_check | OK 1.00 | FAILED 0.00 | T=OracleAsset ✓ | consistency rejected |

Two preflight destructor walks fired automatically (the only 2 fns
where signer applied non-trivial structs to the bv).

## What worked

- Auto destructor preflight: when signer recovered `OracleAsset`
  (with HashMap, Vec, struct slice fields) and `UserState`
  (BTreeMap, UnitQty fields), flower's harness automatically ran
  the destructor walker subagent before the main agent started, no
  Task-tool plumbing required.
- Sidecar mirroring: signer's bv-applied types now live both at
  the input-bndb-side patina.json AND next to the output bndb in
  signer/, so flower (which loaded the signer-output bndb) saw
  them without a manual cp. Confirmed via the preflight firing.
- BB-scaled timeouts: `clearinghouse_adl_orchestrator` (huge fn)
  got 1800s instead of 300s and didn't time-out at signer.

## What broke

- All 6 flower submissions FAILED. `submit_rounds=3` is too tight
  for these large fns; 5 of 6 scored 0.88-0.99 (very close to
  perfect) but exhausted on cheese-detector warnings before
  converging. The `exhausted=True` outcome means the agent's last
  submission gets recorded as "FAILED" even when it's structurally
  correct.
- `compute_position_liquidation_check` flower submission scored
  0.00 — consistency.check rejected outright, not a cheese warning.
  Worth investigating.
- 2-3 SDK CLI exit-1 events fired during the run (caught by the
  `_fallback: True` path in marinator + the cli `transport_error`
  field). The new `turn_budget` hook (max_turns=24, warn at -4 and
  -1) was NOT yet active for this run; it should reduce these in
  the next run.

## Files

- `recovered.bndb` (151 MB) — final flower-stage bv with signer
  prototypes + struct types applied.
- `sidecar.json` — cross-stage sidecar; flower regions empty (0
  region submits across 6 fns; whole-fn path used).
- `signer/signer.json` — submitted_types + binja_signature +
  binja_propagation per fn.
- `flower/flower.json` — submitted_source per fn.

## Next experiment

Bump `submit_rounds` to 5 + retry. Investigate why score=0.99 keeps
failing the cheese warning gate; possibly relax the cheese
detectors to accept submissions at >=0.95 even with warnings.
