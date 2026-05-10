#![allow(dead_code)]

#[derive(Default)]
pub struct LiqOutput {
    pub status: u64,
    pub qty: u64,
    pub margin_value: u64,
}

pub struct OracleEntry {
    pub name_tag: u32,
    pub name_len: u64,
    pub asset_id: u64,
    pub flag: u64,
}

pub struct AssetInfoExtra { pub _opaque: [u8; 64] }
pub struct AssetInfo {
    pub extra: AssetInfoExtra,
    pub oracle_entries: Vec<OracleEntry>,
}

pub struct AssetData { pub _opaque: [u8; 0xc8] }
pub struct Position { pub assets: Vec<AssetData> }
pub struct Clearinghouse { pub state: u128 }

fn sub_555556b543a0(_out: &mut u128, _state: &u128) { unimplemented!() }
fn sub_555556b3f830(_out: &mut u64, _item: &AssetData) { unimplemented!() }
fn compute_per_position_liq_margin(
    _out: &mut u128, _ch: &mut Clearinghouse, _idx: u64,
    _state: &mut u128, _info: &mut AssetInfoExtra,
) { unimplemented!() }
fn option_unit_qty_cmp(_a: &u64, _b: &u64) -> i32 { unimplemented!() }
fn panic_index_out_of_bounds(_i: u64, _n: u64) -> ! { unimplemented!() }

const DEFAULT_LO: u64 = 0x0123_4567;
const DEFAULT_HI: u64 = 0x89ab_cdef;
const MARGIN_CAP: u64 = 0x28f5c28f5c28f5c;

pub fn compute_position_liquidation_check(
    result: &mut LiqOutput,
    asset_info: &mut AssetInfo,
    oracle_asset_idx: u64,
    clearinghouse: &mut Clearinghouse,
    position: &Position,
) {
    let mut liq_margin_buf: u128 = 0;
    let mut oracle_asset_state: u128 = 0;
    let margin_value_1: u64 = 0;

    if oracle_asset_idx == 0 {
        oracle_asset_state = clearinghouse.state;
        sub_555556b543a0(&mut liq_margin_buf, &oracle_asset_state);
        result.margin_value = margin_value_1;
        result.status = liq_margin_buf as u64;
        result.qty = (liq_margin_buf >> 64) as u64;
        return;
    }

    if (asset_info.oracle_entries.len() as u64) <= oracle_asset_idx {
        result.margin_value = 0;
        result.status = DEFAULT_LO;
        result.qty = DEFAULT_HI;
        return;
    }

    let oracle_stride_offset = oracle_asset_idx;
    let asset_name_ptr;
    let temp0;
    let asset_idx;
    {
        let oracle_entries_ptr = &asset_info.oracle_entries;
        let _entry = &oracle_entries_ptr[oracle_stride_offset as usize];
        if _entry.flag == 0 {
            result.margin_value = 0;
            result.status = DEFAULT_LO;
            result.qty = DEFAULT_HI;
            return;
        }
        asset_name_ptr = _entry.name_tag;
        temp0 = _entry.name_len;
        asset_idx = _entry.asset_id;
    }

    let num_assets = position.assets.len() as u64;
    if asset_idx >= num_assets {
        panic_index_out_of_bounds(asset_idx, num_assets);
    }

    let mut _hi_buf: u64 = 0;
    sub_555556b3f830(&mut _hi_buf, &position.assets[asset_idx as usize]);
    liq_margin_buf = (_hi_buf as u128) << 64;
    if _hi_buf == 2 {
        result.margin_value = 0;
        result.status = DEFAULT_LO;
        result.qty = DEFAULT_HI;
        return;
    }

    let asset_info_extra = &mut asset_info.extra;
    compute_per_position_liq_margin(
        &mut liq_margin_buf, clearinghouse, oracle_asset_idx,
        &mut oracle_asset_state, asset_info_extra,
    );
    let mut hype_liq_status_1 = (liq_margin_buf >> 64) as u64;
    let rdx_1 = liq_margin_buf as u64;
    let mut margin_value: u64 = 0;
    if hype_liq_status_1 != 3 { margin_value = margin_value_1; }
    if margin_value == 0 { hype_liq_status_1 = 2; }
    if margin_value >= MARGIN_CAP { hype_liq_status_1 = 2; }
    let mut margin_value_2: u64 = 0;
    if margin_value < MARGIN_CAP { margin_value_2 = margin_value; }

    if oracle_asset_idx != 1
        && (asset_name_ptr == 0x45505948 || asset_name_ptr == 0x4a51585a)
        && temp0 == 4
    {
        let hype_liq_status = hype_liq_status_1;
        let var_40 = rdx_1;
        let margin_value_3 = margin_value_2;
        let hype_margin_floor: u64 = 0x2386f26fc10000;
        liq_margin_buf = 0;
        let _zero_lo: u64 = liq_margin_buf as u64;
        let mut min_margin_ptr: &u64 = &hype_liq_status;
        if option_unit_qty_cmp(&hype_liq_status, &_zero_lo) == 1 {
            min_margin_ptr = &_zero_lo;
        }
        if std::ptr::eq(min_margin_ptr, &hype_liq_status) {
            result.status = hype_liq_status;
            result.qty = var_40;
            margin_value_2 = margin_value_3;
        } else {
            result.status = liq_margin_buf as u64;
            result.qty = (liq_margin_buf >> 64) as u64;
            margin_value_2 = hype_margin_floor;
        }
        result.margin_value = margin_value_2;
        return;
    }
    result.status = hype_liq_status_1;
    result.qty = rdx_1;
    result.margin_value = margin_value_2;
}