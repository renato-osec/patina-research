"""Edge-case regression tests for c_signature, drawn from benchmark_183
ground-truth Rust sources. Run via test/run.sh after the CLI golden
diffs (run.sh sets LD_PRELOAD so nacre's pyo3 module loads cleanly).

Each case asserts the exact C decl string produced by
nacre.c_signature so a future change to the renderer surfaces here.
"""

import sys
import nacre


FAIL = 0


def expect(label: str, got: str, want: str):
    global FAIL
    if got != want:
        FAIL += 1
        sys.stderr.write(f"FAIL {label}\n  got:  {got!r}\n  want: {want!r}\n")


def expect_in(label: str, needle: str, hay: str):
    global FAIL
    if needle not in hay:
        FAIL += 1
        sys.stderr.write(f"FAIL {label}\n  needle: {needle!r}\n  hay:    {hay!r}\n")


# ---------------------------------------------------------- 2020-habidasher
# `fn foo(input: &str) -> u32` — &str is a fat pointer (ScalarPair, ptr+len).
# rustc passes it as PassMode::Pair, but the *layout* is 16B inline at the
# C boundary, so the renderer falls back to the 16B opaque blob (we don't
# spell out fat-pointer struct decomposition yet).
sig = nacre.c_signature("(input: &str) -> u32")
expect("habidasher.foo decl",
       sig["decl"],
       "uint32_t f(uint8_t /* &str */[16] input)")


# --------------------------------------------------------- 2021-braintrust
# `&mut self + bool` — exercises the `this` C++-keyword sanitizer AND
# the Bool/Char early-return in rust_ty_to_c (otherwise `bool` collapses
# to `uint8_t` because it shares Scalar(Int(I8)) with u8).
sig = nacre.c_signature(
    "(this: &mut State, fwd: bool)",
    prelude=(
        "use std::collections::HashMap;\n"
        "pub struct State {\n"
        "    pub jt: HashMap<usize, usize>,\n"
        "    pub tl: Vec<u8>, pub tr: Vec<u8>, pub tc: u8,\n"
        "}\n"
    ),
)
expect("braintrust.step decl (this/bool sanitization)",
       sig["decl"],
       "void f(struct State* this_, bool fwd)")


# --------------------------------------------------------- 2021-endeavour
# Two refs, String return → sret. Char arg (Rust `char`, 4B Unicode
# scalar) was the other special case in rust_ty_to_c — must stay as
# uint32_t (its real bit-width), not collapse via Scalar(Int(I32, false))
# to u32 noise. Catalog must include both pointee structs.
sig = nacre.c_signature("(key: &Vec<char>, input: &str) -> String")
expect("endeavour.enco decl",
       sig["decl"],
       "void f(struct std_string_String* _ret, "
       "struct std_vec_Vec_char_* key, uint8_t /* &str */[16] input)")
expect_in("endeavour catalog: String",
          "struct std_string_String", sig["structs"])
expect_in("endeavour catalog: Vec<char>",
          "struct std_vec_Vec_char_", sig["structs"])


# ------------------------------------------------------------- 2020-s2ring
# `fn find<'a, 'b>(tape: &String, reps: &'b Vec<Replacement>) -> Vec<Match<'a,'b>>`
# Vec<T> return is 24B (>16) → sret; both args are thin refs (8B each).
# Lifetimes baked into the C ident name end up as a run of trailing
# underscores (`Match<'static,'static>` → `std_vec_Vec_Match_________`)
# — that's binja-safe but worth locking in so the sanitizer is stable.
sig = nacre.c_signature(
    "(tape: &'static String, reps: &'static Vec<Replacement<'static>>) "
    "-> Vec<Match<'static, 'static>>",
    prelude=(
        "pub struct Replacement<'a> { pub src: &'a str, pub dst: &'a str }\n"
        "pub struct Match<'a, 'b> {\n"
        "    pub rep: &'a Replacement<'b>,\n"
        "    pub pos: usize,\n"
        "}\n"
    ),
)
assert sig["decl"].startswith("void f(struct std_vec_Vec_Match"), \
    f"s2ring.find sret prefix: {sig['decl']!r}"
expect_in("s2ring.find tape ptr",
          "struct std_string_String* tape", sig["decl"])
expect_in("s2ring.find reps ptr",
          "struct std_vec_Vec_Replacement", sig["decl"])
# Note: Match itself is NOT in the catalog because Vec<T>'s payload is
# type-erased (RawVecInner.ptr is `Unique<u8>`); the only T-mentioning
# struct is `Vec<Match>` whose buf is opaque. Lock in the Vec entry —
# the elem type is purely informational at the binja-binding level.
expect_in("s2ring catalog: Vec<Match>",
          "struct std_vec_Vec_Match", sig["structs"])


# --------------------------------- C++-keyword sanitizer (binja barfs on
# unescaped C++ keywords as param names — `error: 'this' is a keyword`).
# Locks in the full set: this/new/class/virtual all get a trailing `_`.
sig = nacre.c_signature("(this: u8, new: bool, class: u32, virtual_: u8) -> u64")
expect("cpp-keyword sanitization",
       sig["decl"],
       "uint64_t f(uint8_t this_, bool new_, uint32_t class_, uint8_t virtual_)")


# --------------------------- char primitive — must not become uint32_t
# in name but underlying width IS 32 bits.
sig = nacre.c_signature("(c: char) -> bool")
expect("char primitive",
       sig["decl"],
       "bool f(uint32_t c)")


if FAIL:
    print(f"FAIL: python ({FAIL} case(s))")
    sys.exit(1)
print("PASS: python")
