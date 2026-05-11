use std::collections::BTreeMap;

pub struct UnitQty {
    pub unit: u64,
    pub value: u64,
}

pub struct AssetPosition;

pub struct UserState {
    pub account_equity: Option<UnitQty>,
    pub raw_equity: Option<UnitQty>,
    pub positions: BTreeMap<u64, AssetPosition>,
}

pub struct OraclePxRef {
    pub asset: u64,
    pub px_unit: u64,
    pub px_value: u64,
}

pub struct UserMarginShortfall {
    pub total_equity: Option<UnitQty>,
    pub margin_req: Option<UnitQty>,
    pub abs_ntl: Option<UnitQty>,
    pub iso_equity: Option<UnitQty>,
}

fn qty_tag(opt: &Option<UnitQty>) -> u64 { unimplemented!() }
fn qty_unit(opt: &Option<UnitQty>) -> u64 { unimplemented!() }
fn qty_value(opt: &Option<UnitQty>) -> u64 { unimplemented!() }
fn position_at(map: &BTreeMap<u64, AssetPosition>, idx: u64) -> AssetPosition { unimplemented!() }
fn position_key(map: &BTreeMap<u64, AssetPosition>, idx: u64) -> u64 { unimplemented!() }
fn compute_margin_for(pos: &AssetPosition, px_unit: u64, px_value: u64) -> [u64; 12] { unimplemented!() }
fn option_unit_qty_add(tag: u64, unit: u64, rhs_tag: u64, rhs_unit: u64) -> (u64, u64) { unimplemented!() }

pub fn compute_user_margin_and_shortfall(this: &UserState, mkt: &OraclePxRef) -> UserMarginShortfall {
    let user_state: &UserState = this;
    let oracle_data: &OraclePxRef = mkt;
    let _this: &UserState = this;
    let _mkt: &OraclePxRef = mkt;

    let saved_cross_margin_flag_1: u64 = oracle_data.asset;
    let var_22c: u64 = saved_cross_margin_flag_1;
    let margin_mode: u64 = var_22c;
    let saved_cross_margin_flag: u64 = saved_cross_margin_flag_1;
    let mkt_data_field_0x40: u64 = oracle_data.px_value;

    let rbx: u64 = qty_tag(&user_state.account_equity);
    let total_equity_unit_3: u64 = qty_unit(&user_state.account_equity);
    let r8: u64 = qty_value(&user_state.account_equity);
    let acct_equity_value: u64 = r8;
    let positions_count: u64 = user_state.positions.len() as u64;

    let mut total_equity_tag: u64 = rbx;
    let mut total_equity_unit: u64 = total_equity_unit_3;
    let mut total_equity_val: u64 = r8;
    let mut margin_req_tag: u64 = 2;
    let mut margin_req_unit: u64 = 0;
    let mut margin_req_value: u64 = 0;
    let mut abs_ntl_tag: u64 = 2;
    let mut abs_ntl_unit: u64 = 0;
    let mut abs_ntl_val: u64 = 0;

    let mut i: u64 = positions_count;
    while i != 0 {
        let asset_key: u64 = position_key(&user_state.positions, i - 1);
        let pos_copy: AssetPosition = position_at(&user_state.positions, asset_key);
        let oracle: u64 = oracle_data.px_unit;
        let _ret: [u64; 12] = compute_margin_for(&pos_copy, oracle, mkt_data_field_0x40);
        let rcx_14: u64 = _ret[0];
        let rcx_15: u64 = _ret[1];
        let rcx_16: u64 = _ret[2];
        let rax_12: u64 = _ret[3];

        let (total_equity_tag_1, total_equity_unit_2) =
            option_unit_qty_add(total_equity_tag, total_equity_unit, _ret[4], _ret[5]);
        let total_equity_val_2: u64 = total_equity_val.wrapping_add(rcx_14);
        total_equity_tag = total_equity_tag_1;
        total_equity_unit = total_equity_unit_2;
        total_equity_val = total_equity_val_2;

        let (margin_req_tag_1, margin_req_unit_2) =
            option_unit_qty_add(margin_req_tag, margin_req_unit, _ret[6], _ret[7]);
        let margin_req_value_2: u64 = margin_req_value.wrapping_add(rcx_15);
        margin_req_tag = margin_req_tag_1;
        margin_req_unit = margin_req_unit_2;
        margin_req_value = margin_req_value_2;

        let (abs_ntl_tag_1, abs_ntl_unit_3) =
            option_unit_qty_add(abs_ntl_tag, abs_ntl_unit, _ret[8], _ret[9]);
        let abs_ntl_val_1: u64 = abs_ntl_val.wrapping_add(rcx_16);
        abs_ntl_tag = abs_ntl_tag_1;
        abs_ntl_unit = abs_ntl_unit_3;
        abs_ntl_val = abs_ntl_val_1;

        let acct_equity_value_2: u64 = acct_equity_value.wrapping_add(rax_12);
        let _ = (saved_cross_margin_flag, margin_mode, acct_equity_value_2, _this, _mkt);

        i -= 1;
    }

    let _ = positions_count;

    UserMarginShortfall {
        total_equity: Some(UnitQty { unit: total_equity_unit, value: total_equity_val }),
        margin_req: Some(UnitQty { unit: margin_req_unit, value: margin_req_value }),
        abs_ntl: Some(UnitQty { unit: abs_ntl_unit, value: abs_ntl_val }),
        iso_equity: Some(UnitQty { unit: total_equity_unit_3, value: acct_equity_value }),
    }
}