# baby-rust 2021 — 2026-05-09T1758

Source: `/home/renny/doc/work/research/patina/bench/benchmark_183/2021-baby-rust/`

## Chain totals

| stage     | targets | perfect | failed | cost   |
|-----------|---------|---------|--------|--------|
| marinator | 11      | 0       | 0      | $2.12  |
| signer    | 11      | 8       | 0      | $8.11  |
| flower    | 11      | 9       | 1      | $8.56  |

Total: ~$18.79, 11 functions.

## Whitebox vs source.rs

GT functions: 2 (`step`, `main`). Matched on binary location: 1 (`step`).

| metric            | result |
|-------------------|--------|
| signer arity      | 1/1    |
| flower arity      | 1/1    |
| flower body_real  | 8/11   |

`step`: signer sig `(input: String) -> String` matches ground truth; flower body is real (not stub).

## Per-fn flower outcome (11 targets)

| leaf                  | flower_arity | body_real |
|-----------------------|:-:|:-:|
| handle_reserve_error  | 1 | yes |
| do_reserve_and_handle | 5 | yes |
| reserve               | 0 | NO (empty) |
| grow_one              | 1 | yes |
| deallocate            | 3 | yes |
| try_allocate_in       | 2 | yes (1 fail in flower run) |
| drop                  | 0 | NO |
| drop                  | 1 | yes |
| format_inner          | 1 | yes |
| clone_bytes           | 1 | NO |
| step                  | 1 | yes |

## Notes

- 3 stubby submissions (`reserve`, one `drop`, `clone_bytes`) — body is trivially empty or `let _ = ...` — cheese detector would normally bounce, but they slipped past on warning-only paths. Worth raising the body-real bar in `_is_trivial_body`.
- `try_allocate_in` is the one flower failure — signer recovered `Result<Box<[u8]>, TryReserveError>` correctly, flower couldn't make a body match.
- Only `step` is in user-source; the rest are stdlib monomorphizations from `alloc::raw_vec::*`.
