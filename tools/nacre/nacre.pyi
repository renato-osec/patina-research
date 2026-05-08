from typing import TypedDict

class Field(TypedDict):
    path: str
    offset: int
    size: int
    type: str
    is_ptr: bool
    is_scalar: bool

class Layout(TypedDict):
    name: str
    size: int
    align: int
    fields: list[Field]

class Store(TypedDict):
    offset: int
    size: int
    is_ptr: bool
    is_scalar: bool
    writes: int

class AccessNode(TypedDict):
    offset: int
    size: int
    is_ptr: bool
    is_scalar: bool
    children: list["AccessNode"]

def compute(
    source: str,
    target: str | None = None,
    struct_name: str | None = None,
) -> list[Layout]: ...

def probe(
    ty: str,
    prelude: str | None = None,
    target: str | None = None,
    opt_level: int = 3,
) -> list[Store]:
    """Compile a probe function for any Rust type and return surviving stores.

    Examples:
        probe("Vec<u8>")
        probe("State", prelude="struct State { jt: u64, tc: u8 }")
        probe("HashMap<String, u64>", prelude="use std::collections::HashMap;")
    """

def layout(
    ty: str,
    prelude: str | None = None,
    target: str | None = None,
    max_depth: int = 2,
) -> list[AccessNode]:
    """Layout tree for any Rust type expression. Works for primitives,
    generics, references, and user-defined structs (pass struct definitions
    or `use` statements in `prelude`).

    Examples:
        layout("Vec<u8>")
        layout("&Vec<char>")
        layout("State", prelude="struct State { jt: u64, tc: u8 }")
        layout("HashMap<String, u64>", prelude="use std::collections::HashMap;")

    Bare `&` references get `'static` lifetime automatically. Returns the
    same shape as `exoskeleton.trace_function_bv` so they're directly
    comparable via `roe.compatible(layout_result, observation)`."""

class SignatureSlot(TypedDict):
    ty: str
    size: int
    align: int
    pass_mode: str          # "ignore" | "direct" | "pair" | "cast" | "indirect"
    regs: list[str]         # SysV-x64 regs in order; empty if on_stack
    on_stack: bool
    indirect: bool
    access_tree: list[AccessNode]


class SignatureLayout(TypedDict):
    args: list[SignatureSlot]
    ret: SignatureSlot
    sret: bool              # True iff return is passed via hidden out-pointer in rdi


def signature(
    decl: str,
    prelude: str | None = None,
    target: str | None = None,
    max_depth: int = 2,
) -> SignatureLayout:
    """Authoritative SysV-x64 calling-convention layout for a Rust `fn` decl.

    `decl` is the parenthesized parameter list and optional return type:
      `(a: u64, b: &str) -> i32`     # bare
      `fn(a: u64, b: &str) -> i32`   # leading `fn` keyword
      `fn foo(a: u64, b: &str)`      # named fn (name is stripped)

    PassMode comes from rustc's own `fn_abi_of_instance` query, so all
    Rust-ABI subtleties (niche optimizations, tuple returns, sret,
    Result/Option layout) are handled authoritatively. The reg mapping
    on top of PassMode follows the standard SysV-x64 calling convention.

    Examples:
        signature("(a: u64, b: u32) -> i64")
        signature("(s: &str) -> Option<usize>")
        signature("(state: &mut State, req: Req) -> Result<(), E>", prelude="...")
    """


def c_layout(
    ty: str,
    prelude: str | None = None,
    target: str | None = None,
) -> str:
    """Full C struct catalog for any Rust type expression.

    Returns the same C source produced by the `--c` CLI flag: every
    reachable ADT rendered as a `struct <path>` typedef, deepest-first,
    with byte-accurate offsets sourced from rustc's `Layout` query. ZST
    fields (PhantomData, Global, ...) are erased so downstream parsers
    (binja in particular) don't add a phantom alignment byte.

    Examples:
        c_layout("Vec<u8>")
        c_layout("HashMap<u64, u64>", prelude="use std::collections::HashMap;")
        c_layout("State", prelude="struct State { a: u64, b: Vec<u8> }")
    """


class CSignature(TypedDict):
    decl: str       # C function declaration string, e.g. "int64_t f(uint64_t a0)"
    structs: str    # all referenced structs as C, deepest-first


def c_signature(
    decl: str,
    prelude: str | None = None,
    target: str | None = None,
) -> CSignature:
    """Rust fn declaration -> C declaration + reachable-struct catalog.

    Drives off the same `fn_abi_of_instance` query as `signature()`.
    `Indirect` args become `T*`; sret returns get a hidden `T* _ret`
    first parameter and `void` return; pointer-shaped types
    (`&T` / `*const T` / `Box<T>` / `Rc<T>` / `Unique<T>` / ...) collapse
    to typed `T*` whenever the pointee is recoverable.

    Examples:
        c_signature("(a: u64, b: u32) -> i64")
        c_signature("(s: &Vec<u8>) -> Option<usize>")
        c_signature("(state: &mut State, req: Req) -> Result<(), E>", prelude="...")
    """


RUSTC_VERSION: str
def rustc_version() -> str: ...
