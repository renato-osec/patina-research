"""Ask nacre for the field layout of a handful of Rust structs."""

from __future__ import annotations

import nacre


SOURCES = {
    "Point":     "pub struct Point { pub x: u64, pub y: u64 }",
    "Packed":    "pub struct Packed { pub flag: bool, pub count: u64, pub tag: u8 }",
    "Sliced":    "pub struct Sliced<'a> { pub name: &'a str, pub weight: u32 }",
    "NodeState": (
        "pub struct NodeState {"
        "  pub ptr: *mut u8,"
        "  pub len: usize,"
        "  pub cap: usize,"
        "  pub flag: bool,"
        "}"
    ),
}


def main() -> None:
    for name, src in SOURCES.items():
        layouts = nacre.compute(src)
        sl = next(layouts[i] for i, l in enumerate(layouts) if l["name"].endswith(name))
        print(f"[{sl['name']}  size={sl['size']}  align={sl['align']}]")
        for f in sl["fields"]:
            print(f"   +{f['offset']:<3}  size={f['size']:<2}  {f['path']:<10} : {f['type']}")
        print()


if __name__ == "__main__":
    main()
