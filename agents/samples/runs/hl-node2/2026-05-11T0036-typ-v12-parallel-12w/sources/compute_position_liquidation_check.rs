#[repr(C)]
pub struct LiqMarginResult { pub _q0: u64, pub _q1: u64, pub _q2: u64 }

#[repr(C)]
pub struct OracleState { pub _s0: u64, pub _s1: u64 }

#[repr(C)]
pub struct OracleEntry {
    pub _pad0: [u8; 0x38],
    pub _name_ptr: *const u32,
    pub _name_len: u64,
    pub _pad1: [u8; 0x10],
    pub _asset_idx_ptr: *const u64,
    pub _has_entry: u64,
    pub _pad2: [u8; 0x68],
}

#[repr(C)]
pub struct AssetInfo {
    pub _pad0: [u8; 0x58],
    pub _liq_config: [u8; 0x20],
    pub _oracle_entries: *const OracleEntry,
    pub _oracle_count: u64,
}

#[repr(C)]
pub struct Clearinghouse {
    pub _state: OracleState,
    pub _margin_value: u64,
}

#[repr(C)]
pub struct Position {
    pub _pad0: [u8; 8],
    pub _assets: *const u8,
    pub _num_assets: u64,
}

const NONE_STATUS: u64 = 2;
const MAX_MARGIN: u64 = 0x28f5c28f5c28f5c;
const HYPE_FLOOR: u64 = 0x2386f26fc10000;
const HYPE_TAG: u32 = 0x45505948;
const ZXQJ_TAG: u32 = 0x4a51585a;

fn q0(x: &LiqMarginResult) -> u64 { x._q0 }
fn q1(x: &LiqMarginResult) -> u64 { x._q1 }
fn q2(x: &LiqMarginResult) -> u64 { x._q2 }
fn mk_result(a: u64, b: u64, c: u64) -> LiqMarginResult { LiqMarginResult { _q0: a, _q1: b, _q2: c } }
fn os_dup(x: &OracleState) -> OracleState { OracleState { _s0: x._s0, _s1: x._s1 } }
fn os_from(a: u64, b: u64) -> OracleState { OracleState { _s0: a, _s1: b } }

fn fn_0x555556b543a0(_state: &OracleState) -> LiqMarginResult { unimplemented!() }
fn fn_0x555556b3f830(_entry: *const u8) -> LiqMarginResult { unimplemented!() }
fn fn_0x555556b549a0(_c: &Clearinghouse, _idx: u64, _state: &OracleState, _cfg: *const u8) -> LiqMarginResult { unimplemented!() }
fn fn_0x555556b65d00(_a: &u64, _b: &u64) -> u64 { unimplemented!() }
fn fn_0x5555556c9896(_a: u64, _b: u64) -> ! { unimplemented!() }

fn compute_position_liquidation_check(
    asset_info: &AssetInfo,
    oracle_asset_idx: u64,
    clearinghouse: &Clearinghouse,
    position: &Position,
) -> LiqMarginResult {
    if oracle_asset_idx == 0 {
        let var_58 = clearinghouse._margin_value;
        let oracle_asset_state = os_dup(&clearinghouse._state);
        let liq_margin_buf = fn_0x555556b543a0(&oracle_asset_state);
        let margin_value_1 = var_58;
        return mk_result(q0(&liq_margin_buf), q1(&liq_margin_buf), margin_value_1);
    }

    if asset_info._oracle_count <= oracle_asset_idx {
        let var_88 = NONE_STATUS;
        let var_78: u64 = 0;
        return mk_result(var_88, 0, var_78);
    }

    let oracle_entries_ptr = asset_info._oracle_entries;
    let oracle_stride_offset = oracle_asset_idx * 0xd0;
    let _ = oracle_stride_offset;
    let _e = unsafe { &*oracle_entries_ptr.add(oracle_asset_idx as usize) };

    if _e._has_entry == 0 {
        let var_88 = NONE_STATUS;
        let var_78: u64 = 0;
        return mk_result(var_88, 0, var_78);
    }

    let num_assets = position._num_assets;
    let asset_idx: u64 = unsafe { *_e._asset_idx_ptr };
    if asset_idx >= num_assets {
        fn_0x5555556c9896(asset_idx, num_assets);
    }

    let liq_margin_buf = fn_0x555556b3f830(unsafe { position._assets.add((asset_idx * 0xc8) as usize) });
    if q0(&liq_margin_buf) == 2 {
        let var_88 = NONE_STATUS;
        let var_78: u64 = 0;
        return mk_result(var_88, 0, var_78);
    }

    let var_98: u64 = q1(&liq_margin_buf);
    let asset_info_extra: u64 = var_98;
    let _ = asset_info_extra;
    let oracle_asset_state = os_from(q1(&liq_margin_buf), q2(&liq_margin_buf));
    if q1(&liq_margin_buf) == 2 {
        let var_88 = NONE_STATUS;
        let var_78: u64 = 0;
        return mk_result(var_88, 0, var_78);
    }

    let liq_margin_buf = fn_0x555556b549a0(
        clearinghouse, oracle_asset_idx, &oracle_asset_state,
        unsafe { (asset_info as *const AssetInfo as *const u8).add(0x58) },
    );
    let mut hype_liq_status_1 = q0(&liq_margin_buf);
    let rdx_1 = q1(&liq_margin_buf);
    let margin_value_1 = q2(&liq_margin_buf);
    let mut margin_value_2: u64 = 0;
    let mut margin_value: u64 = 0;
    if hype_liq_status_1 != 3 {
        margin_value = margin_value_1;
    }
    if margin_value == 0 {
        hype_liq_status_1 = 2;
    }
    if margin_value >= MAX_MARGIN {
        hype_liq_status_1 = 2;
    }
    if margin_value < MAX_MARGIN {
        margin_value_2 = margin_value;
    }

    let asset_name_ptr = _e._name_ptr;
    let temp0 = _e._name_len;
    let _hype = oracle_asset_idx == 1
        || (temp0 == 4 && {
            let _t = unsafe { *asset_name_ptr };
            _t == HYPE_TAG || _t == ZXQJ_TAG
        });

    if !_hype {
        return mk_result(hype_liq_status_1, rdx_1, margin_value_2);
    }

    let hype_liq_status = hype_liq_status_1;
    let var_40 = rdx_1;
    let margin_value_3 = margin_value_2;
    let _ = (var_40, margin_value_3);
    let hype_margin_floor = HYPE_FLOOR;
    let _ = hype_margin_floor;
    let _floor_buf = mk_result(0, 0, 0);
    let _floor_q0: u64 = q0(&_floor_buf);
    let min_margin_ptr: u64 =
        if fn_0x555556b65d00(&hype_liq_status, &_floor_q0) == 1 { _floor_q0 } else {
            return mk_result(hype_liq_status, rdx_1, margin_value_3);
        };
    mk_result(min_margin_ptr, rdx_1, margin_value_3)
}