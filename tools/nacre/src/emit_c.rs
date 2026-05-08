//api to get types to be defined in the decompiler

use std::collections::{HashMap, HashSet};

use rustc_abi::{BackendRepr, Primitive};
use rustc_middle::ty::{self, Ty, TyCtxt};
use rustc_target::callconv::PassMode;

use crate::{pointee_of, query_layout, StructLayout};

/// Emit a deepest-first C struct, elide ZSTs 
pub fn emit_c_nested(sls: &[StructLayout]) -> String {
    let sls: Vec<&StructLayout> = sls.iter().filter(|sl| sl.size != 0).collect();
    let by_name: HashMap<&str, &StructLayout> =
        sls.iter().map(|sl| (sl.name.as_str(), *sl)).collect();

    fn depth_of<'a>(
        name: &'a str,
        by_name: &HashMap<&'a str, &'a StructLayout>,
        cache: &mut HashMap<&'a str, usize>,
        stack: &mut HashSet<&'a str>,
    ) -> usize {
        if let Some(&d) = cache.get(name) {
            return d;
        }
        if !stack.insert(name) {
            return 0;
        }
        let mut d = 0;
        if let Some(sl) = by_name.get(name) {
            for f in &sl.flat {
                if let Some(inner) = by_name.get(f.ty_desc.as_str()) {
                    let cd = depth_of(inner.name.as_str(), by_name, cache, stack);
                    d = d.max(cd + 1);
                }
            }
        }
        stack.remove(name);
        cache.insert(name, d);
        d
    }
    let mut cache = HashMap::new();
    let mut order: Vec<&StructLayout> = sls.iter().copied().collect();
    {
        let mut stack = HashSet::new();
        for sl in &order {
            depth_of(&sl.name, &by_name, &mut cache, &mut stack);
        }
    }
    order.sort_by_key(|sl| cache.get(sl.name.as_str()).copied().unwrap_or(0));

    let known: HashSet<&str> = sls.iter().map(|sl| sl.name.as_str()).collect();
    let mut out = String::new();
    for sl in order {
        out.push_str(&emit_one_struct(sl, &known));
        out.push('\n');
    }
    out
}

pub fn emit_one_struct(sl: &StructLayout, known: &HashSet<&str>) -> String {
    let mut lines: Vec<String> = Vec::new();
    let mut cursor = 0u64;
    for f in &sl.flat {
        // ZST field  
        if f.size == 0 {
            continue;
        }
        if f.offset > cursor {
            lines.push(format!("    uint8_t _pad_{cursor}[{}];", f.offset - cursor));
        }
        // Prefer ADT-by-name over scalar/ptr collapse - match rustc's
        // own DWARF emission, which keeps NonNull/Unique/Box/Rc/Arc and
        // repr(transparent) wrappers as DW_TAG_structure_type even
        // though `BackendRepr` collapses them to Scalar(Pointer) at ABI.
        let ctype = if known.contains(f.ty_desc.as_str()) {
            format!("struct {}", path_to_ident(&f.ty_desc))
        } else if f.is_ptr {
            "void*".to_string()
        } else {
            leaf_c_type(&f.ty_desc, f.size, false)
        };
        let safe = sanitize_field(&f.path);
        lines.push(format!("    {ctype} {safe};"));
        cursor = f.offset + f.size;
    }
    if cursor < sl.size {
        lines.push(format!("    uint8_t _pad_{cursor}[{}];", sl.size - cursor));
    }
    format!("struct {} {{\n{}\n}};\n", path_to_ident(&sl.name), lines.join("\n"))
}

/// I hate binja, but it would be nice to keep :: in general
pub fn path_to_ident(s: &str) -> String {
    s.replace("::", "_")
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '_' { c } else { '_' })
        .collect()
}

pub fn sanitize_field(s: &str) -> String {
    if s.chars().next().map_or(true, |c| c.is_ascii_digit()) {
        return format!("_{s}");
    }
    let cleaned = sanitize(s);
    // binja parses prototypes with a C++ frontend, so any C++ reserved
    // word as an arg name fails. Escape with a trailing underscore:
    if is_cpp_keyword(&cleaned) {
        return format!("{cleaned}_");
    }
    cleaned
}

// stupid binja
fn is_cpp_keyword(s: &str) -> bool {
    matches!(s,
        "alignas" | "alignof" | "and" | "and_eq" | "asm" | "auto" |
        "bitand" | "bitor" | "bool" | "break" | "case" | "catch" |
        "char" | "char8_t" | "char16_t" | "char32_t" | "class" |
        "compl" | "concept" | "const" | "consteval" | "constexpr" |
        "constinit" | "const_cast" | "continue" | "co_await" |
        "co_return" | "co_yield" | "decltype" | "default" | "delete" |
        "do" | "double" | "dynamic_cast" | "else" | "enum" | "explicit" |
        "export" | "extern" | "false" | "float" | "for" | "friend" |
        "goto" | "if" | "inline" | "int" | "long" | "mutable" |
        "namespace" | "new" | "noexcept" | "not" | "not_eq" |
        "nullptr" | "operator" | "or" | "or_eq" | "private" |
        "protected" | "public" | "register" | "reinterpret_cast" |
        "requires" | "return" | "short" | "signed" | "sizeof" |
        "static" | "static_assert" | "static_cast" | "struct" |
        "switch" | "template" | "this" | "thread_local" | "throw" |
        "true" | "try" | "typedef" | "typeid" | "typename" | "union" |
        "unsigned" | "using" | "virtual" | "void" | "volatile" |
        "wchar_t" | "while" | "xor" | "xor_eq" | "restrict"
    )
}

pub fn sanitize(s: &str) -> String {
    s.chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '_' { c } else { '_' })
        .collect()
}

/// Map a Rust leaf `ty_desc` (no surrounding catalog context) to a C, last resort
pub fn leaf_c_type(ty_desc: &str, size: u64, is_ptr: bool) -> String {
    if is_ptr {
        return "void*".to_string();
    }
    let stripped = inner_primitive(ty_desc);
    if let Some(c) = primitive_to_c(stripped) {
        return c.to_string();
    }
    format!("uint8_t /* {ty_desc} */[{size}]")
}

fn inner_primitive(s: &str) -> &str {
    let s = s.trim();
    if let Some(rest) = s.strip_prefix('(') {
        if let Some(end) = rest.find(')') {
            return rest[..end].trim();
        }
    }
    if let Some(rest) = s.strip_prefix('<') {
        if let Some(end) = rest.find(" as ") {
            return rest[..end].trim();
        }
    }
    if let Some(idx) = s.rfind("::") {
        return &s[idx + 2..];
    }
    s
}

fn primitive_to_c(s: &str) -> Option<&'static str> {
    Some(match s {
        "u8" => "uint8_t",
        "u16" => "uint16_t",
        "u32" => "uint32_t",
        "u64" => "uint64_t",
        "u128" => "__uint128_t",
        "usize" => "uintptr_t",
        "i8" => "int8_t",
        "i16" => "int16_t",
        "i32" => "int32_t",
        "i64" => "int64_t",
        "i128" => "__int128_t",
        "isize" => "intptr_t",
        "f32" => "float",
        "f64" => "double",
        "bool" => "bool",
        "char" => "uint32_t",
        "NonZeroU8" => "uint8_t",
        "NonZeroU16" => "uint16_t",
        "NonZeroU32" => "uint32_t",
        "NonZeroU64" => "uint64_t",
        "NonZeroI8" => "int8_t",
        "NonZeroI16" => "int16_t",
        "NonZeroI32" => "int32_t",
        "NonZeroI64" => "int64_t",
        _ => return None,
    })
}

/// C type rendering for a `Ty` using `BackendRepr`:
pub fn rust_ty_to_c<'tcx>(
    tcx: TyCtxt<'tcx>,
    ty: Ty<'tcx>,
    seen: &mut Vec<String>,
) -> String {
    let layout = match query_layout(tcx, ty) {
        Ok(l) => l,
        Err(_) => return format!("/* layout error: {ty} */ void*"),
    };

    match ty.kind() {
        ty::Bool => return "bool".to_string(),
        ty::Char => return "uint32_t".to_string(),
        _ => {}
    }

    if let BackendRepr::Scalar(s) = layout.backend_repr {
        match s.primitive() {
            Primitive::Pointer(_) => {
                if let Some(pointee) = pointee_of(tcx, ty) {
                    return format!("{}*", rust_ty_to_c(tcx, pointee, seen));
                }
                return "void*".to_string();
            }
            Primitive::Int(int, signed) => {
                use rustc_abi::Integer::*;
                let bits = match int { I8 => 8, I16 => 16, I32 => 32, I64 => 64, I128 => 128 };
                if bits == 128 {
                    return if signed { "__int128_t" } else { "__uint128_t" }.to_string();
                }
                return format!(
                    "{}int{bits}_t",
                    if signed { "" } else { "u" }
                );
            }
            Primitive::Float(f) => {
                use rustc_abi::Float::*;
                return match f {
                    F16 => "_Float16",
                    F32 => "float",
                    F64 => "double",
                    F128 => "_Float128",
                }
                .to_string();
            }
        }
    }

    if let ty::Adt(adt, args) = ty.kind() {
        let path = tcx.def_path_str_with_args(adt.did(), args);
        seen.push(path.clone());
        return format!("struct {}", path_to_ident(&path));
    }

    format!(
        "uint8_t /* {ty} */[{}]",
        layout.size.bytes()
    )
}

/// Build a C function declaration string from a fully resolved FnAbi to reassign the sig
pub fn emit_c_fn_decl<'tcx>(
    tcx: TyCtxt<'tcx>,
    abi: &rustc_target::callconv::FnAbi<'tcx, Ty<'tcx>>,
    fn_name: &str,
    arg_names: &[String],
    seen: &mut Vec<String>,
) -> String {
    let mut params: Vec<String> = Vec::new();
    let sret = matches!(abi.ret.mode, PassMode::Indirect { .. });
    let ret_c = if sret {
        let t = rust_ty_to_c(tcx, abi.ret.layout.ty, seen);
        params.push(format!("{t}* _ret"));
        "void".to_string()
    } else if matches!(abi.ret.mode, PassMode::Ignore) {
        "void".to_string()
    } else {
        rust_ty_to_c(tcx, abi.ret.layout.ty, seen)
    };

    for (i, arg) in abi.args.iter().enumerate() {
        let ty_c = match arg.mode {
            PassMode::Indirect { .. } => {
                format!("{}*", rust_ty_to_c(tcx, arg.layout.ty, seen))
            }
            _ => rust_ty_to_c(tcx, arg.layout.ty, seen),
        };
        let name = arg_names
            .get(i)
            .filter(|n| !n.is_empty())
            .cloned()
            .unwrap_or_else(|| format!("a{i}"));
        params.push(format!("{ty_c} {}", sanitize_field(&name)));
    }

    format!("{ret_c} {fn_name}({})", params.join(", "))
}
