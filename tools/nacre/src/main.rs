#![feature(rustc_private)]

extern crate rustc_driver;
extern crate rustc_interface;

use std::{env, process};

use nacre::{compute_layouts, compute_layouts_nested, display_layout, emit_c, to_json};

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        eprintln!(
            "usage: nacre <file.rs> [--json|--c] [--struct Name | --type Name] [--target TARGET]"
        );
        process::exit(1);
    }

    let source = std::fs::read_to_string(&args[1]).unwrap_or_else(|e| {
        eprintln!("error: {e}");
        process::exit(1);
    });

    // accept --type as alias for --struct (matches user muscle memory)
    let filter = ["--struct", "--type"]
        .iter()
        .find_map(|flag| {
            args.iter()
                .position(|a| a == flag)
                .and_then(|i| args.get(i + 1))
        })
        .map(|s| s.as_str());

    let target = args
        .iter()
        .position(|a| a == "--target")
        .and_then(|i| args.get(i + 1))
        .map(|s| s.as_str());

    let json = args.iter().any(|a| a == "--json");
    let c_out = args.iter().any(|a| a == "--c");

    if c_out {
        let root = filter.unwrap_or_else(|| {
            eprintln!("--c requires --type / --struct <Name>");
            process::exit(1);
        });
        let layouts = compute_layouts_nested(&source, target, root).unwrap_or_else(|e| {
            eprintln!("error: {e}");
            process::exit(1);
        });
        print!("{}", emit_c::emit_c_nested(&layouts));
        return;
    }

    let layouts = compute_layouts(&source, target, filter).unwrap_or_else(|e| {
        eprintln!("error: {e}");
        process::exit(1);
    });

    if json {
        print!("{}", to_json(&layouts));
    } else {
        for sl in &layouts {
            print!("{}", display_layout(sl));
            println!();
        }
    }
}
