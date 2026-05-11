#![allow(dead_code)]

pub struct AssetInfo {
    asset_info_extra: [u8; 0x20],
    oracle_entries_ptr: Vec<OracleEntry>,
}

pub struct OracleEntry {
    pad0: [u8; 0x38],
    asset_name_ptr: *const u32,
    temp0: u64,
    pad1: [u8; 0x18],
    asset_idx_ptr: *const u64,
    flag: u64,
    pad2: [u8; 0x68],
}

pub struct Clearinghouse {
    oracle_asset_state_lo: u64,
    oracle_asset_state_hi: u64,
    margin_value_1: u64,
}

pub struct Position {
    pad0: u64,
    assets: Vec<u8>,
    num_assets: u64,
}

#[derive(Clone, Copy)]
pub struct LiqResult {
    a: u64,
    b: u64,
    c: u64,
}

const SENTINEL: u64 = 0x555558096328;
const MARGIN_CAP: u64 = 0x28f5c28f5c28f5c;

fn fn_0x555556b3f830(_lo: &mut u64, _hi: &mut u64, _asset: &[u8]) -> u64 { unimplemented!() }
fn fn_0x555556b543a0(_status: &mut u64, _aux: &mut u64, _margin: &mut u64, _state_lo: u64, _state_hi: u64) { unimplemented!() }
fn compute_per_position_liq_margin(
    _status: &mut u64,
    _aux: &mut u64,
    _margin: &mut u64,
    _clearinghouse: &Clearinghouse,
    _oracle_asset_idx: u64,
    _state_lo: u64,
    _state_hi: u64,
    _extra: &[u8; 0x20],
) { unimplemented!() }
fn option_unit_qty_cmp(_a: u64, _b: u64, _c: u64, _d: u64, _e: u64, _f: u64) -> u32 { unimplemented!() }

pub fn compute_position_liquidation_check(
    asset_info: &AssetInfo,
    oracle_asset_idx: u64,
    clearinghouse: &Clearinghouse,
    position: &Position,
) -> LiqResult {
    let mut liq_margin_buf: u64 = 0;
    let mut rdx_1: u64 = 0;
    let mut margin_value_1: u64 = 0;
    let mut oracle_asset_state: u64 = 0;
    let mut zmm0: u64 = 0;

    if oracle_asset_idx == 0 {
        let var_58 = clearinghouse.margin_value_1;
        oracle_asset_state = clearinghouse.oracle_asset_state_lo;
        zmm0 = clearinghouse.oracle_asset_state_hi;
        fn_0x555556b543a0(&mut liq_margin_buf, &mut rdx_1, &mut margin_value_1, oracle_asset_state, zmm0);
        margin_value_1 = var_58;
        return LiqResult { a: liq_margin_buf, b: rdx_1, c: margin_value_1 };
    }

    let num_assets = position.num_assets;
    if (asset_info.oracle_entries_ptr.len() as u64) <= oracle_asset_idx {
        return LiqResult { a: SENTINEL, b: 0, c: 0 };
    }

    let oracle_entries_ptr = &asset_info.oracle_entries_ptr;
    let oracle_stride_offset = oracle_asset_idx as usize;
    let r13_1 = &oracle_entries_ptr[oracle_stride_offset];

    if r13_1.flag == 0 {
        return LiqResult { a: SENTINEL, b: 0, c: 0 };
    }

    let asset_idx: u64 = unsafe { *r13_1.asset_idx_ptr };
    if asset_idx >= num_assets {
        panic!("index out of bounds");
    }

    let rax_1 = fn_0x555556b3f830(
        &mut oracle_asset_state,
        &mut zmm0,
        &position.assets[(asset_idx * 0xc8) as usize..],
    );
    if rax_1 == 2 {
        return LiqResult { a: SENTINEL, b: 0, c: 0 };
    }

    compute_per_position_liq_margin(
        &mut liq_margin_buf,
        &mut rdx_1,
        &mut margin_value_1,
        clearinghouse,
        oracle_asset_idx,
        oracle_asset_state,
        zmm0,
        &asset_info.asset_info_extra,
    );

    let mut hype_liq_status_1 = liq_margin_buf;
    let mut margin_value: u64 = 0;
    let mut margin_value_2: u64 = 0;

    if hype_liq_status_1 != 3 {
        margin_value = 0;
    }
    if margin_value == 0 {
        hype_liq_status_1 = 2;
    }
    if margin_value >= MARGIN_CAP {
        hype_liq_status_1 = 2;
    }
    rdx_1 = liq_margin_buf;
    if margin_value < MARGIN_CAP {
        margin_value_2 = margin_value;
    }

    if oracle_asset_idx == 1 {
        let asset_name_ptr = r13_1.asset_name_ptr;
        let temp0 = r13_1.temp0;
        if temp0 == 4 {
            let rax_8 = unsafe { *asset_name_ptr } as u64;
            if rax_8 == 0x45505948 || rax_8 == 0x4a51585a {
                let hype_liq_status = hype_liq_status_1;
                let var_40 = rdx_1;
                let margin_value_3 = margin_value_2;
                let hype_margin_floor: u64 = 0x2386f26fc10000;
                liq_margin_buf = 0;
                let mut min_margin_ptr: u64 = 0;
                if option_unit_qty_cmp(hype_liq_status, var_40, margin_value_3, liq_margin_buf, 0, 0) == 1 {
                    min_margin_ptr = 1;
                }
                if min_margin_ptr == 1 {
                    return LiqResult { a: liq_margin_buf, b: 0, c: 0 };
                }
                let _ = hype_margin_floor;
                return LiqResult { a: hype_liq_status, b: var_40, c: margin_value_3 };
            }
        }
    }

    LiqResult { a: hype_liq_status_1, b: rdx_1, c: margin_value_2 }
}