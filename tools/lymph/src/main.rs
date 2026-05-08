#![feature(rustc_private)]

//! `lymph <source.rs> [--root NAME --depth N]` -
//! print one [`lymph::FlowGraph`] per function. With `--root` + `--depth`,
//! BFS-walk from `NAME`'s body through statically-resolvable callees up
//! to depth `N` instead of doing a flat per-fn sweep.

use std::path::PathBuf;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let Some(path) = args.get(1) else {
        eprintln!("usage: lymph <source.rs> [--root NAME --depth N]");
        std::process::exit(2);
    };
    let root = args
        .iter()
        .position(|a| a == "--root")
        .and_then(|i| args.get(i + 1))
        .map(|s| s.as_str());
    let depth = args
        .iter()
        .position(|a| a == "--depth")
        .and_then(|i| args.get(i + 1))
        .and_then(|s| s.parse::<u32>().ok());
    let graphs = match lymph::analyze_file_with(&PathBuf::from(path), root, depth) {
        Ok(g) => g,
        Err(e) => {
            eprintln!("lymph: {e}");
            std::process::exit(1);
        }
    };
    for g in &graphs {
        println!("{}", g);
    }
}
