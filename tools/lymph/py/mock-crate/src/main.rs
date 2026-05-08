
fn deep(a: u64) -> u64 {
    return deep(a * 2);
}

fn deeep(a: u64) -> u64 {
    return a * 2;
}

fn main() -> u64 {
    let a = 5u64;
    let b = deep(a);
    return b;
}
