// Edge cases pulled from benchmark_183/2020-s2ring/source.rs:
//   - lifetime params on a struct (`'a`)
//   - multi-lifetime + nested ref-to-other-lifetimed-struct
//   - both fields are `&str` (fat pointer ScalarPair: ptr + len)
// Ground truth from rustc 1.83 DWARF: `Replacement<'a>` is 32B
// (two `&str`s, each 16B), `Match<'a, 'b>` is 16B (one `&Replacement` ptr
// + one usize).
pub struct Replacement<'a> {
    pub src: &'a str,
    pub dst: &'a str,
}

pub struct Match<'a, 'b> {
    pub rep: &'a Replacement<'b>,
    pub pos: usize,
}
