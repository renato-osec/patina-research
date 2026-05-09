// Lower MLIL-SSA into a [`FlowGraph`]; SSA gives def-use precision per version.

use std::collections::HashMap;

use binaryninja::function::Function;
use binaryninja::medium_level_il::{
    MediumLevelILFunction, MediumLevelILLiftedInstruction,
    MediumLevelILLiftedInstructionKind as Lifted,
};
use binaryninja::variable::{SSAVariable, Variable};

use crate::flow::{EdgeKind, FlowGraph, Slot, SlotId};

pub fn lower_function(func: &Function) -> Option<FlowGraph> {
    let mlil = func.medium_level_il().ok()?;
    let ssa = mlil.ssa_form();
    let n = ssa.instruction_count();
    lower_indices(func, &ssa, 0..n)
}

/// Lower the MLIL-SSA basic block whose first instruction has the given
/// index. Slots/edges only reflect this block's instructions; cross-block
/// SSA inputs surface as orphan slots (no in-edges) by design.
pub fn lower_block_at_index(func: &Function, start_index: usize) -> Option<FlowGraph> {
    let mlil = func.medium_level_il().ok()?;
    let ssa = mlil.ssa_form();
    let blocks = ssa.basic_blocks();
    let blk = blocks.iter().find(|b| b.start_index().0 == start_index)?;
    lower_indices(func, &ssa, blk.start_index().0..blk.end_index().0)
}

/// Like [`lower_block_at_index`], but the block is identified by the
/// binary address of its first instruction (the address Binja's UI shows).
pub fn lower_block_at_addr(func: &Function, addr: u64) -> Option<FlowGraph> {
    let mlil = func.medium_level_il().ok()?;
    let ssa = mlil.ssa_form();
    let blocks = ssa.basic_blocks();
    let blk = blocks.iter().find(|b| {
        ssa.instruction_from_index(b.start_index())
            .map(|i| i.address == addr)
            .unwrap_or(false)
    })?;
    lower_indices(func, &ssa, blk.start_index().0..blk.end_index().0)
}

/// Lower a contiguous range of basic blocks `[block_start, block_end)`
/// into a single FlowGraph. Use this to scope flower's dataflow
/// validation to a region of a function instead of the whole body.
/// Returns None if the range is empty or out of bounds.
pub fn lower_region(
    func: &Function,
    block_start: usize,
    block_end: usize,
) -> Option<FlowGraph> {
    let mlil = func.medium_level_il().ok()?;
    let ssa = mlil.ssa_form();
    let bb_vec = ssa.basic_blocks();
    let n_blocks = bb_vec.iter().count();
    if block_start >= n_blocks || block_start >= block_end {
        return None;
    }
    let end = block_end.min(n_blocks);
    let mut indices: Vec<usize> = Vec::new();
    for (i, b) in bb_vec.iter().enumerate() {
        if i < block_start { continue; }
        if i >= end { break; }
        for j in b.start_index().0..b.end_index().0 {
            indices.push(j);
        }
    }
    lower_indices(func, &ssa, indices)
}

/// Public block-listing helper: `[(idx, start_addr, end_addr,
/// instr_count), ...]` so the agent can navigate without re-walking
/// MLIL-SSA in Python.
pub fn list_blocks(func: &Function) -> Option<Vec<(usize, u64, u64, usize)>> {
    let mlil = func.medium_level_il().ok()?;
    let ssa = mlil.ssa_form();
    let bb_vec = ssa.basic_blocks();
    let n = bb_vec.iter().count();
    let mut out = Vec::with_capacity(n);
    for (i, b) in bb_vec.iter().enumerate() {
        let start_addr = ssa
            .instruction_from_index(b.start_index())
            .map(|x| x.address)
            .unwrap_or(0);
        // end_addr = address of the last instr in the block (close approx)
        let end_addr = if b.end_index().0 > b.start_index().0 {
            ssa.instruction_from_index(
                binaryninja::medium_level_il::MediumLevelInstructionIndex(
                    b.end_index().0 - 1,
                ),
            )
            .map(|x| x.address)
            .unwrap_or(start_addr)
        } else {
            start_addr
        };
        let n = b.end_index().0.saturating_sub(b.start_index().0);
        out.push((i, start_addr, end_addr, n));
    }
    Some(out)
}

/// Common body for the three public entry points: seeds params/return slot,
/// then walks the supplied iterator of MLIL-SSA instruction indices.
fn lower_indices(
    func: &Function,
    ssa: &MediumLevelILFunction,
    indices: impl IntoIterator<Item = usize>,
) -> Option<FlowGraph> {
    let _llil = func.low_level_il().ok()?;
    let fn_name = func.symbol().full_name().to_string_lossy().to_string();
    let mut g = FlowGraph::new(fn_name, func.start());
    let names = build_name_map(func);
    let lc = LowerCtx { names: &names };
    for var in func.parameter_variables().contents.iter() {
        let id = g.intern(Slot::root_only(lc.ssa_name(*var, 0), variable_ty(func, var)));
        g.params.push(id);
    }
    let ret_id = g.intern(Slot::root_only("<return>", "_"));
    g.return_slot = Some(ret_id);

    let lc_ssa = LowerCtxSsa { ssa, names: &names };
    for i in indices {
        let Some(instr) = ssa.instruction_from_index(
            binaryninja::medium_level_il::MediumLevelInstructionIndex(i),
        ) else {
            continue;
        };
        let lifted = instr.lift();
        lower_instr(&lc_ssa, &mut g, &lifted, ret_id);
    }
    Some(g)
}

struct LowerCtx<'a> {
    names: &'a HashMap<u64, String>,
}

struct LowerCtxSsa<'a> {
    #[allow(dead_code)]
    ssa: &'a MediumLevelILFunction,
    names: &'a HashMap<u64, String>,
}

impl<'a> LowerCtx<'a> {
    fn ssa_name(&self, var: Variable, version: usize) -> String {
        ssa_name_for(self.names, var, version)
    }
}

impl<'a> LowerCtxSsa<'a> {
    fn ssa_name(&self, var: Variable, version: usize) -> String {
        ssa_name_for(self.names, var, version)
    }
}

fn ssa_name_for(names: &HashMap<u64, String>, var: Variable, version: usize) -> String {
    let key = var.to_identifier();
    let base = names.get(&key).cloned().unwrap_or_else(|| {
        format!("var_{}_{}", var.index, var.storage)
    });
    format!("{}#{}", base, version)
}

fn build_name_map(func: &Function) -> HashMap<u64, String> {
    let mut out = HashMap::new();
    for nv in func.variables().iter() {
        if !nv.name.is_empty() {
            out.insert(nv.variable.to_identifier(), nv.name.clone());
        }
    }
    out
}

fn variable_ty(_func: &Function, _var: &Variable) -> String {
    "_".to_string()
}

fn lower_instr(
    lc: &LowerCtxSsa<'_>,
    g: &mut FlowGraph,
    instr: &MediumLevelILLiftedInstruction,
    ret_id: SlotId,
) {
    match &instr.kind {
        Lifted::SetVarSsa(op) => {
            let dst = intern_ssa(g, lc, op.dest);
            for src in collect_ssa_uses(&op.src) {
                let s = intern_ssa(g, lc, src);
                g.push_edge(s, dst, EdgeKind::Assign);
            }
        }
        Lifted::SetVarSsaField(op) | Lifted::SetVarAliasedField(op) => {
            let dst = intern_ssa(g, lc, op.dest);
            // Field-update preserves the prior value of the other fields.
            let prev = intern_ssa(g, lc, op.prev);
            g.push_edge(prev, dst, EdgeKind::Assign);
            for src in collect_ssa_uses(&op.src) {
                let s = intern_ssa(g, lc, src);
                g.push_edge(s, dst, EdgeKind::Assign);
            }
        }
        Lifted::SetVarAliased(op) => {
            let dst = intern_ssa(g, lc, op.dest);
            let prev = intern_ssa(g, lc, op.prev);
            g.push_edge(prev, dst, EdgeKind::Assign);
            for src in collect_ssa_uses(&op.src) {
                let s = intern_ssa(g, lc, src);
                g.push_edge(s, dst, EdgeKind::Assign);
            }
        }
        Lifted::VarPhi(op) => {
            let dst = intern_ssa(g, lc, op.dest);
            let dst_id = op.dest.variable.to_identifier();
            for src in &op.src {
                // Cross-variable phi inputs (e.g. `fwd#4 = phi(fwd#0,
                // rdx#2)`) are binja's lifter modeling register
                // coalescing across distinct lifetimes - the same
                // storage held different conceptual values on each
                // path. Treating them as real assigns conflates
                // unrelated locals (an arg looks like it depends on
                // another arg via the joined-storage version), which
                // is the dominant `binary_over` false positive in
                // check_compatibility. Drop those inputs; keep the
                // intra-variable phi semantics intact.
                if src.variable.to_identifier() != dst_id {
                    continue;
                }
                let s = intern_ssa(g, lc, *src);
                g.push_edge(s, dst, EdgeKind::Assign);
            }
        }
        Lifted::CallSsa(op) | Lifted::TailcallSsa(op) => {
            let callee = callee_label(&op.dest);
            let mut arg_ids: Vec<SlotId> = Vec::with_capacity(op.params.len());
            for (i, param) in op.params.iter().enumerate() {
                let arg_dst = g.intern(Slot::root_only(
                    format!("{callee}#{i}"),
                    "_",
                ));
                arg_ids.push(arg_dst);
                for src in collect_ssa_uses(param) {
                    let s = intern_ssa(g, lc, src);
                    g.push_edge(s, arg_dst, EdgeKind::CallArg);
                }
            }
            let ret_src = g.intern(Slot::root_only(format!("<ret:{callee}>"), "_"));
            for out in &op.output {
                let dst = intern_ssa(g, lc, *out);
                g.push_edge(ret_src, dst, EdgeKind::CallReturn);
            }
            g.register_call_group(arg_ids, Some(ret_src));
        }
        Lifted::Ret(op) => {
            for src_expr in &op.src {
                for src in collect_ssa_uses(src_expr) {
                    let s = intern_ssa(g, lc, src);
                    g.push_edge(s, ret_id, EdgeKind::Return);
                }
            }
        }
        Lifted::StoreSsa(op) => {
            let dest_vars = collect_ssa_uses(&op.dest);
            let src_vars = collect_ssa_uses(&op.src);
            for d in &dest_vars {
                let d_id = intern_ssa(g, lc, *d);
                for s in &src_vars {
                    let s_id = intern_ssa(g, lc, *s);
                    g.push_edge(s_id, d_id, EdgeKind::Assign);
                }
            }
        }
        _ => {}
    }
}

fn collect_ssa_uses(expr: &MediumLevelILLiftedInstruction) -> Vec<SSAVariable> {
    let mut out = Vec::new();
    walk_ssa_uses(expr, &mut out);
    out
}

fn walk_ssa_uses(expr: &MediumLevelILLiftedInstruction, out: &mut Vec<SSAVariable>) {
    match &expr.kind {
        Lifted::VarSsa(op)
        | Lifted::VarAliased(op) => out.push(op.src),
        Lifted::VarSsaField(op)
        | Lifted::VarAliasedField(op) => out.push(op.src),

        // `&var` / `&var.f`: the SSA layer drops version info on the
        // address-taken variable, so synthesise a use of `var#0` (the
        // entry version). The existing #0 -> #1 -> ... assign chain then
        // makes any later version reach this use via forward closure.
        Lifted::AddressOf(op) => out.push(SSAVariable::new(op.src, 0)),
        Lifted::AddressOfField(op) => out.push(SSAVariable::new(op.src, 0)),

        Lifted::Add(op)   | Lifted::Sub(op)   | Lifted::Mul(op)
        | Lifted::And(op) | Lifted::Or(op)    | Lifted::Xor(op)
        | Lifted::Lsl(op) | Lifted::Lsr(op)   | Lifted::Asr(op)
        | Lifted::Rol(op) | Lifted::Ror(op)
        | Lifted::MuluDp(op) | Lifted::MulsDp(op)
        | Lifted::Divu(op) | Lifted::DivuDp(op)
        | Lifted::Divs(op) | Lifted::DivsDp(op)
        | Lifted::Modu(op) | Lifted::ModuDp(op)
        | Lifted::Mods(op) | Lifted::ModsDp(op)
        | Lifted::CmpE(op) | Lifted::CmpNe(op)
        | Lifted::CmpSlt(op) | Lifted::CmpUlt(op)
        | Lifted::CmpSle(op) | Lifted::CmpUle(op)
        | Lifted::CmpSge(op) | Lifted::CmpUge(op)
        | Lifted::CmpSgt(op) | Lifted::CmpUgt(op)
        | Lifted::TestBit(op)
        | Lifted::AddOverflow(op)
        | Lifted::FcmpE(op) | Lifted::FcmpNe(op)
        | Lifted::FcmpLt(op) | Lifted::FcmpLe(op)
        | Lifted::FcmpGe(op) | Lifted::FcmpGt(op)
        | Lifted::FcmpO(op) | Lifted::FcmpUo(op)
        | Lifted::Fadd(op) | Lifted::Fsub(op)
        | Lifted::Fmul(op) | Lifted::Fdiv(op) => {
            walk_ssa_uses(&op.left, out);
            walk_ssa_uses(&op.right, out);
        }

        Lifted::Adc(op) | Lifted::Sbb(op)
        | Lifted::Rlc(op) | Lifted::Rrc(op) => {
            walk_ssa_uses(&op.left, out);
            walk_ssa_uses(&op.right, out);
            walk_ssa_uses(&op.carry, out);
        }

        Lifted::Neg(op) | Lifted::Not(op)
        | Lifted::Sx(op) | Lifted::Zx(op)
        | Lifted::LowPart(op) | Lifted::BoolToInt(op)
        | Lifted::UnimplMem(op)
        | Lifted::Fsqrt(op) | Lifted::Fneg(op) | Lifted::Fabs(op)
        | Lifted::FloatToInt(op) | Lifted::IntToFloat(op)
        | Lifted::FloatConv(op) | Lifted::RoundToInt(op)
        | Lifted::Floor(op) | Lifted::Ceil(op) | Lifted::Ftrunc(op)
        | Lifted::Load(op) => walk_ssa_uses(&op.src, out),

        Lifted::LoadSsa(op) => walk_ssa_uses(&op.src, out),

        _ => {}
    }
}

fn intern_ssa(g: &mut FlowGraph, lc: &LowerCtxSsa<'_>, var: SSAVariable) -> SlotId {
    g.intern(Slot::root_only(lc.ssa_name(var.variable, var.version), "_"))
}

fn callee_label(dest_expr: &MediumLevelILLiftedInstruction) -> String {
    match &dest_expr.kind {
        Lifted::Const(c) | Lifted::ConstPtr(c) | Lifted::Import(c) => format!("fn_{:#x}", c.constant),
        _ => "<indirect>".to_string(),
    }
}
