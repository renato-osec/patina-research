// Custom benchmark, written 2026-04-28. Algorithms intentionally chosen to be
// uncommon (not standard textbook patterns). Used to test the recovery harness
// without LLM training-data contamination.

use std::collections::{BTreeMap, BTreeSet, HashMap};

const ROT_PRIME: u64 = 0xc1d4_c2b3_8e5d_a017;

// 1. xor-rotate combiner. Uniquely keyed by ROT_PRIME constant.
#[inline(never)]
fn rolling_digest(input: &[u8]) -> u64 {
    let mut acc: u64 = 0;
    for (i, b) in input.iter().enumerate() {
        let shift = (i & 0x3f) as u32;
        acc ^= (*b as u64).wrapping_mul(ROT_PRIME).rotate_left(shift);
    }
    acc
}

// 2. ANS-like run-coalesce that emits (byte, run_length) until a sentinel.
#[inline(never)]
fn coalesce_runs(stream: &[u8]) -> Vec<(u8, u16)> {
    let mut out: Vec<(u8, u16)> = Vec::new();
    let mut iter = stream.iter().copied().peekable();
    while let Some(b) = iter.next() {
        if b == 0xFE {
            break;
        }
        let mut n: u16 = 1;
        while let Some(&p) = iter.peek() {
            if p != b || n == u16::MAX {
                break;
            }
            iter.next();
            n += 1;
        }
        out.push((b, n));
    }
    out
}

// 3. Case-fold-then-sort string-pair canonicalizer.
#[inline(never)]
fn canonical_pair(a: &str, b: &str) -> (String, String) {
    let aa: String = a.chars().map(|c| c.to_ascii_lowercase()).collect();
    let bb: String = b.chars().map(|c| c.to_ascii_lowercase()).collect();
    if aa <= bb {
        (aa, bb)
    } else {
        (bb, aa)
    }
}

// 4. Merge-dedup into a BTreeSet (selection of BTree vs Hash matters at layout).
#[inline(never)]
fn merge_dedup(left: Vec<i64>, right: Vec<i64>) -> BTreeSet<i64> {
    let mut out: BTreeSet<i64> = BTreeSet::new();
    for x in left {
        out.insert(x);
    }
    for x in right {
        if x.abs() < 1_000_000_000 {
            out.insert(x);
        }
    }
    out
}

// 5. Streaming top-K via a sorted Vec (no BinaryHeap; layout differs).
#[inline(never)]
fn top_k(values: &[i32], k: usize) -> Vec<i32> {
    let mut top: Vec<i32> = Vec::with_capacity(k + 1);
    for &v in values {
        let pos = top.binary_search(&-v).unwrap_or_else(|e| e);
        top.insert(pos, -v);
        if top.len() > k {
            top.pop();
        }
    }
    top.into_iter().map(|x| -x).collect()
}

// 6. Nested map lookup returning Option<&str>.
#[inline(never)]
fn nested_lookup(
    table: &HashMap<String, BTreeMap<u32, String>>,
    key: &str,
    sub: u32,
) -> Option<String> {
    let inner = table.get(key)?;
    inner.get(&sub).cloned()
}

// 7. Parse "k=v" lines into a Vec of owned pairs.
#[inline(never)]
fn parse_kv_lines(text: &str) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let mut split = trimmed.splitn(2, '=');
        if let (Some(k), Some(v)) = (split.next(), split.next()) {
            out.push((k.trim().to_string(), v.trim().to_string()));
        }
    }
    out
}

// 8. Fold a slice of u32 into a histogram, normalized to f64 in 0..1.
#[inline(never)]
fn histogram_norm(samples: &[u32], buckets: usize) -> Vec<f64> {
    let mut counts: Vec<u64> = vec![0; buckets];
    let max = samples.iter().copied().max().unwrap_or(0).max(1);
    for &s in samples {
        let idx = ((s as u64) * (buckets as u64) / (max as u64 + 1)) as usize;
        counts[idx.min(buckets - 1)] += 1;
    }
    let total = counts.iter().sum::<u64>().max(1) as f64;
    counts.iter().map(|c| (*c as f64) / total).collect()
}

#[inline(never)]
fn main() {
    let s = b"the quick brown fox jumps over the lazy dog";
    println!("{}", rolling_digest(s));
    println!("{:?}", coalesce_runs(b"aaabbcdd\xFEignored"));
    let (a, b) = canonical_pair("Hello", "world");
    println!("{} {}", a, b);
    let m = merge_dedup(vec![1, 2, 2, 3], vec![3, 4, 5]);
    println!("{:?}", m);
    let t = top_k(&[5, 2, 9, 1, 7, 3, 8], 3);
    println!("{:?}", t);
    let mut h: HashMap<String, BTreeMap<u32, String>> = HashMap::new();
    h.insert("k".into(), [(1u32, String::from("v"))].iter().cloned().collect());
    println!("{:?}", nested_lookup(&h, "k", 1));
    println!("{:?}", parse_kv_lines("a=1\n# c\nb=2"));
    println!("{:?}", histogram_norm(&[1, 2, 3, 4, 5, 100], 5));
}
