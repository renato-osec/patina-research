#![allow(dead_code)]

pub struct AssetInfo;
pub struct Clearinghouse;
pub struct Position;
pub struct LiqResult { pub margin_value: u128, pub margin_value_1: u64 }

fn clearinghouse_extra(c: &Clearinghouse) -> u64 { unimplemented!() }
fn clearinghouse_head(c: &Clearinghouse) -> u128 { unimplemented!() }
fn asset_num_oracle(a: &AssetInfo) -> u64 { unimplemented!() }
fn position_num_assets(p: &Position) -> u64 { unimplemented!() }
fn position_assets_base(p: &Position) -> u64 { unimplemented!() }
fn oracle_kind(a: &AssetInfo, idx: u64) -> u64 { unimplemented!() }
fn oracle_inner_idx(a: &AssetInfo, idx: u64) -> u64 { unimplemented!() }
fn oracle_name_word(a: &AssetInfo, idx: u64) -> u32 { unimplemented!() }
fn oracle_name_len(a: &AssetInfo, idx: u64) -> u64 { unimplemented!() }
fn asset_extras_handle(a: &AssetInfo) -> u64 { unimplemented!() }
fn sub_555556b3f830(lo: &mut u128, status: &mut u64, ptr: u64) { unimplemented!() }
fn sub_555556b543a0(lo: &mut u128, status: &mut u64, state: &u128) { unimplemented!() }
fn compute_per_position_liq_margin(
    lo: &mut u128,
    status: &mut u64,
    c: &Clearinghouse,
    idx: u64,
    state: &mut u128,
    extras: u64,
) { unimplemented!() }
fn option_unit_qty_cmp(a: &u64, b: &u64) -> i64 { unimplemented!() }

pub fn compute_position_liquidation_check(
    result: &mut LiqResult,
    asset_info: &AssetInfo,
    oracle_asset_idx: u64,
    clearinghouse: &Clearinghouse,
    position: &Position,
) {
    let mut liq_margin_buf: u128 = 0;
    let mut margin_value_1: u64 = 0;
    let mut oracle_asset_state: u128 = 0;

    if oracle_asset_idx == 0 {
        let var_58 = clearinghouse_extra(clearinghouse);
        oracle_asset_state = clearinghouse_head(clearinghouse);
        sub_555556b543a0(&mut liq_margin_buf, &mut margin_value_1, &oracle_asset_state);
        margin_value_1 = var_58;
        result.margin_value_1 = margin_value_1;
        result.margin_value = liq_margin_buf;
        return;
    }

    let num_assets = position_num_assets(position);
    if asset_num_oracle(asset_info) <= oracle_asset_idx {
        result.margin_value = 0u128;
        result.margin_value_1 = 0u64;
        return;
    }

    let oracle_entries_ptr = asset_info;
    let oracle_stride_offset = oracle_asset_idx;

    if oracle_kind(oracle_entries_ptr, oracle_stride_offset) == 0 {
        result.margin_value = 0u128;
        result.margin_value_1 = 0u64;
        return;
    }

    let asset_idx = oracle_inner_idx(oracle_entries_ptr, oracle_stride_offset);
    if asset_idx >= num_assets {
        panic!("index out of bounds: {} >= {}", asset_idx, num_assets);
    }

    let rdi_1 = asset_idx
        .wrapping_mul(0xc8)
        .wrapping_add(position_assets_base(position));
    sub_555556b3f830(&mut liq_margin_buf, &mut margin_value_1, rdi_1);
    if margin_value_1 == 2 {
        result.margin_value = 0u128;
        result.margin_value_1 = 0u64;
        return;
    }

    compute_per_position_liq_margin(
        &mut liq_margin_buf,
        &mut margin_value_1,
        clearinghouse,
        oracle_asset_idx,
        &mut oracle_asset_state,
        asset_extras_handle(asset_info),
    );

    let mut hype_liq_status_1: u64 = margin_value_1;
    let rdx_1: u64 = liq_margin_buf as u64;
    let mut margin_value: u64 = 0;
    let mut margin_value_2: u64 = 0;

    if hype_liq_status_1 != 3 {
        margin_value = margin_value_1;
    }
    if margin_value == 0 {
        hype_liq_status_1 = 2;
    }
    if margin_value >= 0x28f5c28f5c28f5c {
        hype_liq_status_1 = 2;
    } else {
        margin_value_2 = margin_value;
    }

    let _floor_asset = if oracle_asset_idx == 1 {
        true
    } else {
        let asset_name_ptr = oracle_name_word(oracle_entries_ptr, oracle_stride_offset);
        let temp0 = oracle_name_len(oracle_entries_ptr, oracle_stride_offset);
        temp0 == 4 && (asset_name_ptr == 0x45505948 || asset_name_ptr == 0x4a51585a)
    };

    if !_floor_asset {
        result.margin_value_1 = hype_liq_status_1;
        result.margin_value = rdx_1 as u128;
        return;
    }

    let hype_margin_floor: u64 = 0x2386f26fc10000;
    liq_margin_buf = 0;
    let hype_liq_status = hype_liq_status_1;
    let var_40 = rdx_1;
    let margin_value_3 = margin_value_2;
    let _ = hype_margin_floor;
    let _ = hype_liq_status;
    let _ = var_40;
    let _ = margin_value_3;

    let _cmp = option_unit_qty_cmp(&hype_liq_status_1, &margin_value_1);
    let min_margin_ptr: u128 = if _cmp == 1 {
        liq_margin_buf
    } else {
        hype_liq_status_1 as u128
    };
    result.margin_value = min_margin_ptr;
    result.margin_value_1 = margin_value_2;
}