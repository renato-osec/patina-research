// CLI: anemone <binary> <0xADDR>
use std::env;
use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    if args.len() < 3 {
        eprintln!("usage: anemone <binary> <0xADDR>");
        return ExitCode::from(2);
    }
    let path = &args[1];
    let addr = u64::from_str_radix(args[2].trim_start_matches("0x"), 16)
        .expect("address must be hex like 0x4012a0");

    let _session = binaryninja::headless::Session::new().expect("binja session");
    let bv = binaryninja::load(path).expect("load");
    let g = anemone::analyze_function_at(&bv, addr)
        .expect("no MLIL or no function at that address");
    print!("{g}");
    ExitCode::SUCCESS
}
