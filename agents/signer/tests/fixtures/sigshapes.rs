// Function shapes mirroring those in decompetition samples, but with
// #[inline(never)] so they survive as testable units in the binary.
//
// Coverage (signature pattern -> source it mirrors):
//   habidasher::foo, ::bar     -> hash_str / dj_str
//   s2ring::rand               -> rand
//   s2ring::step               -> mut_string + ref_struct
//   2021-baby-rust::step       -> string_step (also kept naturally — recursive)
//   endeavour::enco, ::deco    -> enco_two_args
//   endeavour::trim            -> trim_string
//   parasite::enco, ::deco     -> str_to_string
//   toobz::new_suck            -> box_dyn_arg
#![allow(warnings)]

use std::collections::HashMap;
use std::num::Wrapping;

pub struct Match { pub pos: usize, pub len: usize, pub kind: u32 }
pub struct Big { pub a: u64, pub b: u64, pub c: u64 }
pub trait Tr { fn run(&self) -> u32; }
pub struct TrImpl;
impl Tr for TrImpl { fn run(&self) -> u32 { 7 } }

#[inline(never)]
pub fn hash_str(input: &str) -> u32 {
    let mut h = Wrapping(0u32);
    for c in input.chars() { h = (h << 16) + (h << 6) + Wrapping(c as u32) - h; }
    h.0
}

#[inline(never)]
pub fn dj_str(input: &str) -> u32 {
    let mut h = Wrapping(5381u32);
    for c in input.chars() { h = (h << 5) + h + Wrapping(c as u32); }
    h.0
}

#[inline(never)]
pub fn rand_step(state: u64) -> u64 {
    state.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407)
}

#[inline(never)]
pub fn mut_string(tape: &mut String, m: &Match) {
    tape.replace_range(m.pos..m.pos + m.len, "x");
}

#[inline(never)]
pub fn string_step(input: String) -> String {
    let mut chars: Vec<char> = input.chars().collect();
    match chars.pop() {
        Some(x) => format!("{}{}", x, string_step(chars.into_iter().collect())),
        None    => String::new(),
    }
}

#[inline(never)]
pub fn enco_two_args(key: &Vec<char>, input: &str) -> String {
    let mut out = String::with_capacity(input.len());
    for (i, c) in input.chars().enumerate() {
        let k = key[i % key.len()] as u32;
        out.push(char::from_u32(c as u32 ^ k).unwrap_or(c));
    }
    out
}

#[inline(never)]
pub fn trim_string(s: &mut String) {
    while s.ends_with(' ') { s.pop(); }
}

#[inline(never)]
pub fn str_to_string(input: &str) -> String {
    input.chars().rev().collect::<String>()
}

#[inline(never)]
pub fn box_dyn_arg(arg: &str) -> Box<dyn Tr> {
    if arg == "x" { Box::new(TrImpl) } else { Box::new(TrImpl) }
}

#[inline(never)]
pub fn copy_big(b: Big) -> Big {
    Big { a: b.a.wrapping_add(1), b: b.b.wrapping_add(2), c: b.c.wrapping_add(3) }
}

#[inline(never)]
pub fn add_two(a: u64, b: u64) -> u64 { a.wrapping_add(b) }

#[inline(never)]
pub fn six_args(a: u64, b: u64, c: u64, d: u64, e: u64, f: u64) -> u64 {
    a.wrapping_add(b).wrapping_add(c).wrapping_add(d).wrapping_add(e).wrapping_add(f)
}

#[inline(never)]
pub fn opt_ptr(p: Option<&u64>) -> Option<&u64> {
    // Add a non-trivial body so rustc can't reduce the function to
    // identity / inline-promote it.
    p.filter(|v| **v != 0)
}

#[inline(never)]
pub fn pair_ret(seed: u64) -> (u64, u64) {
    (seed, seed.wrapping_add(1))
}

// black_box prevents the optimizer from constant-folding the call sites
// and propagating literal values into the (otherwise non-inlined) callees,
// and prints/asserts force the returned values to be observed so DCE
// can't eliminate the calls themselves.
fn main() {
    use std::hint::black_box as bb;
    let arg = std::env::args().nth(1).unwrap_or("hi".into());
    let m = Match { pos: bb(0), len: bb(1), kind: bb(0) };
    let mut s = bb(arg.clone());
    let v: Vec<char> = bb(arg.clone()).chars().collect();
    println!("{}", hash_str(bb(arg.as_str())));
    println!("{}", dj_str(bb(arg.as_str())));
    println!("{}", rand_step(bb(7)));
    println!("{}", string_step(bb(arg.clone())));
    println!("{}", enco_two_args(bb(&v), bb(arg.as_str())));
    println!("{}", str_to_string(bb(arg.as_str())));
    println!("{}", box_dyn_arg(bb(arg.as_str())).run());
    println!("{}", copy_big(bb(Big { a: 1, b: 2, c: 3 })).a);
    println!("{}", add_two(bb(1), bb(2)));
    println!("{}", six_args(bb(1), bb(2), bb(3), bb(4), bb(5), bb(6)));
    let n: u64 = 7;
    if let Some(v) = opt_ptr(bb(Some(&n))) { println!("{}", v); }
    let (x, y) = pair_ret(bb(n));
    println!("{} {}", bb(x), bb(y));
    mut_string(bb(&mut s), bb(&m));
    trim_string(bb(&mut s));
    println!("{}", s);
}
