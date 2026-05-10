# braintrust-stripped 0x4088b0 — auto-preflight demo

Source: `bench/benchmark_stripped_183/2021-braintrust/binary` (stripped).
Single fn: `0x4088b0` → renamed `hashmap_get_expect` (post-marinator).

## What this run demonstrates

The first attempt (not archived) on this binary recovered the prototype
correctly at signer but flower invented a different incompatible struct
because:
- Signer's score was 0.80 (< 0.85 APPLY_SCORE_THRESHOLD)
- Sidecar write was nested inside `_apply_to_bv`, which the threshold
  gate skipped
- Flower had no signer types in the cross-stage sidecar
- Flower's destructor subagent was Task-spawnable but not auto-fired

Two fixes (signer/submit hoist + flower auto-preflight) close that gap.

## Recovered output

```rust
pub struct State {
    pub stack: Vec<i64>,
    pub program: Vec<i64>,
    pub map: HashMap<i64, i64>,
    pub side: bool,
}

pub fn hashmap_get_expect(map_ptr: &State, lookup_key: i64, side_flag: bool) -> i64 {
    if map_ptr.side == side_flag {
        return lookup_key;
    }
    *map_ptr.map.get(&lookup_key).expect("no entry found for key")
}
```

## Cost

| stage     | model  | t (s) | $ | submits |
|-----------|--------|------:|---:|--------:|
| warper    | -      |  ~5   | 0    | -       |
| marinator | sonnet | ~150  | 0.30 | -       |
| signer_v2 | opus   | 244   | 0.71 | 1 (score 0.80 — sidecar still written) |
| flower_v3 | opus   | 209   | 0.32 | 1 (perfect, with preflight) |

Flower preflight cost (haiku destructor walker): rolled into flower's
total. Single submit, 12 tools, 13 iters.

## Comparison vs no-preflight

| | flower_v1 (no preflight) | flower_v3 (auto preflight) |
|---|---|---|
| receiver type | `&MapStruct { list, other, entries, flag }` ❌ | `&State { stack, program, map, side }` ✓ |
| field name | `entries` (invented) | `map` (matches signer) |
| body | `(flag != 0) == side` (awkward bool cast) | `map_ptr.side == side_flag` |
| tools | 15 | 12 |
| $ | 0.60 | 0.32 |

Both validate; only v3 is structurally aligned with signer.

## Files

- `recovered.bndb` (4.2MB) — open in binja; prototype + struct types applied
- `sidecar.json` — cross-stage findings (signer State, flower body)
- `signer/signer.json` — submitted_types + binja_signature + binja_propagation
- `flower/flower.json` — submitted_source (whole-fn opaque, no regions yet)

## Known gaps

- 0 regions emitted. Flower used whole-fn submit, no BB→Rust mapping.
- Forced region-first + non-contiguous BB sets is the next refactor.
