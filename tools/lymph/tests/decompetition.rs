//! Regression corpus: decompetition 2020/2021 samples (env-gated).

#![feature(rustc_private)]

use std::path::PathBuf;
use std::sync::Mutex;

use lymph::{analyze_file, FlowGraph};

static DRIVER_LOCK: Mutex<()> = Mutex::new(());

fn corpus_root() -> Option<PathBuf> {
    std::env::var_os("LYMPH_DECOMPETITION_ROOT")
        .map(PathBuf::from)
        .or_else(|| {
            let p = PathBuf::from(
                "/home/renny/doc/work/research/decompetition/_sources_hidden",
            );
            p.is_dir().then_some(p)
        })
}

fn sample(year: &str, name: &str) -> PathBuf {
    corpus_root()
        .expect("set LYMPH_DECOMPETITION_ROOT")
        .join(year)
        .join("rust")
        .join(format!("{}_source.rs", name))
}

fn run_one(year: &str, name: &str, min_fns: usize) -> Vec<FlowGraph> {
    let _guard = DRIVER_LOCK.lock().unwrap();
    let path = sample(year, name);
    assert!(path.exists(), "missing sample {:?}", path);
    let graphs = analyze_file(&path).expect("rustc driver ok");
    assert!(
        graphs.len() >= min_fns,
        "{year}/{name}: expected >= {min_fns} fns, got {}",
        graphs.len(),
    );
    for g in &graphs {
        assert!(g.return_slot.is_some(), "{year}/{name}: {} has no return slot", g.fn_name);
        for &p in &g.params {
            let s = g.slot(p);
            assert_ne!(
                s.ty, "_",
                "{year}/{name}: {} param {} has unresolved type",
                g.fn_name,
                s.display(),
            );
        }
    }
    graphs
}

fn skip_if_no_corpus() -> bool {
    if corpus_root().is_none() {
        eprintln!("skipping: no LYMPH_DECOMPETITION_ROOT and default path missing");
        return true;
    }
    false
}

#[test]
fn dc2020_baby_rust() {
    if skip_if_no_corpus() { return }
    run_one("2020", "baby-rust", 1);
}

#[test]
fn dc2020_habidasher() {
    if skip_if_no_corpus() { return }
    let g = run_one("2020", "habidasher", 3);
    assert!(g.iter().any(|g| g.fn_name == "foo"));
    assert!(g.iter().any(|g| g.fn_name == "bar"));
}

#[test]
fn dc2020_s2ring() {
    if skip_if_no_corpus() { return }
    let graphs = run_one("2020", "s2ring", 4);
    let find = graphs.iter().find(|g| g.fn_name == "find").expect("find fn");
    let has_rep_field = find
        .slots()
        .iter()
        .any(|s| s.ty.contains("Replacement"));
    assert!(has_rep_field, "find fn lost Replacement-typed slot:\n{}", find);
}

#[test]
fn dc2020_toobz() {
    if skip_if_no_corpus() { return }
    let graphs = run_one("2020", "toobz", 6);
    let trait_impls: Vec<_> = graphs
        .iter()
        .filter(|g| g.fn_name.contains(" as "))
        .map(|g| g.fn_name.clone())
        .collect();
    assert!(
        trait_impls.len() >= 4,
        "expected at least 4 trait-impl methods, saw {:?}",
        trait_impls
    );
}

#[test]
fn dc2021_baby_rust() {
    if skip_if_no_corpus() { return }
    run_one("2021", "baby-rust", 2);
}

#[test]
fn dc2021_braintrust() {
    if skip_if_no_corpus() { return }
    let graphs = run_one("2021", "braintrust", 7);
    let new = graphs.iter().find(|g| g.fn_name.ends_with("::new")).expect("State::new");
    let has_hashmap = new.slots().iter().any(|s| s.ty.contains("HashMap"));
    assert!(has_hashmap, "State::new lost HashMap type:\n{}", new);
}

#[test]
fn dc2021_endeavour() {
    if skip_if_no_corpus() { return }
    run_one("2021", "endeavour", 4);
}

#[test]
fn dc2021_parasite() {
    if skip_if_no_corpus() { return }
    let graphs = run_one("2021", "parasite", 4);
    let deco = graphs.iter().find(|g| g.fn_name == "deco").expect("deco fn");
    let has_str_usize_map = deco
        .slots()
        .iter()
        .any(|s| s.ty.contains("HashMap") && s.ty.contains("&str") && s.ty.contains("usize"));
    assert!(
        has_str_usize_map,
        "deco lost HashMap<&str, usize> type:\n{}",
        deco
    );
}
