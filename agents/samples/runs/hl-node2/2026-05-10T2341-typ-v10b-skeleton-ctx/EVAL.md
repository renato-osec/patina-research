# hl-node2 typ-v10b — skeleton prompt + context dir + signer enforcement

flower-only re-run on v4's signed bndb. New mechanisms active:
- **Skeleton-framed user_prompt**: signer's prelude + types + fn
  signature pre-filled in a ```rust block; agent fills the body
  between braces.
- **--context-dir /home/renny/hl/context**: agent sees the project
  context directory + nudge to grep it for analogues.
- **Signer-sig + struct-id enforcement**: validator rejects any
  submission whose main fn signature or struct definitions deviate
  from signer's recovery. Bounce message embeds the canonical form
  for copy-paste.

## Headline

| | count | $ |
|---|---:|---:|
| OK perfect | 2/6 | 3.12 |
| FAILED (enforcement-bounced 5 submits) | 2/6 | 4.53 |
| SDK-crashed at 0 submits | 2/6 | 2.99 |
| total | - | 10.64 |

## Per-fn

| fn | result | submits | $ | note |
|---|---|--:|---:|---|
| compute_margin_requirement_by_mode | OK | 2 | 0.91 | skeleton fit easily |
| compute_position_liquidation_check | OK | 3 | 2.20 | (no signer for it; agent did own) |
| build_adl_candidate_set | crash | 0 | 1.55 | SDK exit-1 at tool 24 |
| compute_user_margin_and_shortfall | crash | 0 | 1.44 | SDK exit-1 at tool 24 |
| compute_adl_ranking_score | FAILED | 5 | 2.08 | sig: `(this: &Clearinghouse, ...)` vs agent `_this`; 5 rounds, agent kept missing |
| clearinghouse_adl_orchestrator | FAILED | 5 | 2.45 | struct: signer's `Clearinghouse {_data}` vs agent's `Clearinghouse {assets, ...}` |

## Honest assessment vs v6

- v6 (no enforcement): 6/6 perfect, but bodies invented structs
  (Position{assets}, MapStruct{list,other,entries,flag}, etc).
- v10b (full enforcement): 2/6 perfect — the OK ones use signer's
  exact structs. The FAILED ones are catches: enforcement is
  correctly bouncing the agent's invented variants but the agent
  isn't converging within 5 submits despite the canonical copy-
  paste block in the bounce message.
- The 2 SDK crashes are separate from enforcement - agent burns
  24 tools on the same fns it failed in v8/v9 too.

## What's left to tune

1. Agent ignores the "copy this struct verbatim" bounce block. Try
   embedding signer's struct in the skeleton instead of just citing
   it. The skeleton path already does this for the OK ones; the
   failing ones may be hitting struct-redefinition because the
   agent re-types struct fields in the body somewhere.
2. SDK exit-1 at exactly tool 24 across multiple runs is the
   max_turns ceiling; the turn_budget hook's -4 and -1 warnings
   aren't strong enough to stop the agent from continuing.
3. compute_adl_ranking_score has a tuple-return signature that's
   ambiguous - signer recovered `... usize)` with no `-> ...`
   (return inferred as `()`), agent thinks return is tuple. Sig
   mismatch every time. Signer's recovered prototype probably
   needs `-> (u64, i64, i64)` or similar.

## Files

- `recovered.bndb` (151 MB)
- `flower/flower.json` per-fn metrics
- `flower/flower.log` per-fn done lines
- `sources/*.rs` 4 extracted (the 2 crashes have empty source)
