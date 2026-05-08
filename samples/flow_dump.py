"""Print the MIR-level dataflow graph of a Rust source file."""

from __future__ import annotations

import sys
from textwrap import dedent

import lymph


FALLBACK = dedent("""\
    use std::collections::HashMap;

    pub struct State {
        pub jt: HashMap<usize, usize>,
        pub tl: Vec<u8>,
        pub tc: u8,
    }

    impl State {
        pub fn push(&mut self, b: u8) {
            self.tl.push(self.tc);
            self.tc = b;
        }
    }
""")


def main() -> None:
    if len(sys.argv) > 1:
        src = open(sys.argv[1]).read()
        print(f"# source: {sys.argv[1]}\n")
    else:
        src = FALLBACK
        print("# no file arg; using built-in State sample\n")

    print(lymph.dump(src))


if __name__ == "__main__":
    main()
