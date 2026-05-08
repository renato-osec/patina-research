
import rustlayout

src = """
struct Two {
    a : bool
}

struct One {
    a : bool,
    b : u32,
    c : u64,
    d : Two
}
"""

structs = rustlayout.layout(src)

print(rustlayout.probe_stores(src, "One"))

for s in structs:
    print(s)
    print(s.size - sum(sz for _, sz in s.padding()))

"""
transform = next(s for s in structs if s.name == "Transform")
scale_offset = next(f.offset for f in transform.fields if f.path == "scale")
print(f"Transform.scale is at byte offset {scale_offset}")
"""
