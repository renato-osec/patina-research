# typ-v12 — workers=12 + risk-engines context + multi-arg-let allowed

flower-only re-run on v4 signed bndb. Changes vs v11:
- workers=12 (was 2)
- --context-dir /home/renny/doc/work/research/risk-engines
- multi-arg `let _ = (a, b, c)` accepted as legitimate idiom
- prelude-verbatim check still active

## Headline

| | result | $ |
|---|---|---:|
| OK perfect | 2/6 | 3.26 |
| FAILED (5 submits exhausted) | 3/6 | 6.41 |
| imperfect (SDK-crash mid-rebound) | 1/6 | 2.39 |
| **total** | **22.5m, $12.06** | |

## Per-fn

| fn | result | score | sub | $ | note |
|---|---|--:|--:|---:|---|
| build_adl_candidate_set | FAILED | 0.99 | 5 | 2.25 | prelude not verbatim |
| compute_margin_requirement_by_mode | OK | 1.00 | 2 | 1.25 | small + clean |
| compute_user_margin_and_shortfall | imperfect | 0.99 | 4 | 2.39 | SDK crashed mid-rebound |
| compute_adl_ranking_score | FAILED | 0.99 | 5 | 1.92 | prelude not verbatim |
| clearinghouse_adl_orchestrator | FAILED | 1.00 | 5 | 2.24 | agent re-typed Clearinghouse {assets:HashMap} ignoring signer's {_data} |
| compute_position_liquidation_check | OK | 1.00 | 4 | 2.01 | (no signer to enforce against) |

## Pattern

Same 4 fns fail across v10b/v11/v12: build_adl, compute_user_margin,
compute_adl_ranking_score, clearinghouse_adl. The agent keeps
inventing/reformatting signer's structs despite:
- Skeleton with prelude+sig+body-placeholder in user_prompt
- Prelude-verbatim check rejecting non-matching submissions
- Bounce messages with copy-paste-ready ```rust blocks
- 5 submit rounds + canonical-paste guidance

## What's still wrong

Agents seem to interpret "complete the body" as "redesign the API."
Even with verbatim signer types in the prompt and clear bounce
messages, opus emits `Clearinghouse {assets: HashMap<u64, _>}` when
signer recovered `Clearinghouse {_data: [u64; 2700]}`.

Possible next step: **stop asking the agent for the full source.**
Instead, the `submit_reconstruction` tool takes just the body string,
and the harness wraps it in the signer skeleton automatically. Agent
has no opportunity to redefine the struct.
