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

fn compute_margin_requirement_by_mode(_pos: &AssetPosition, _oracle: &OraclePxRef) -> [u64; 8] {
    unimplemented!()
}
fn option_unit_qty_add_a(_a: u64, _b: u64, _c: u64, _d: u64) -> (u64, u64) { unimplemented!() }
fn option_unit_qty_add_b(_a: u64, _b: u64, _c: u64, _d: u64) -> (u64, u64) { unimplemented!() }

pub fn compute_user_margin_and_shortfall(this: &UserState, mkt: &OraclePxRef) -> UserMarginShortfall {
    let user_state: &UserState = this;
    let oracle_data: &OraclePxRef = mkt;
    let rcx: u64 = oracle_data.px_unit;
    let rcx_1: u64 = oracle_data.px_value;
    let mkt_data_field_0x40: u64 = oracle_data.asset;
    let _ = (rcx, rcx_1, mkt_data_field_0x40);

    let positions_count: u64 = user_state.positions.len() as u64;

    let mut rbx: u64;
    let mut total_equity_unit_3: u64;
    let r8: u64;
    match &user_state.account_equity {
        Some(_eq) => { rbx = 1; total_equity_unit_3 = _eq.unit; r8 = _eq.value; }
        None => { rbx = 2; total_equity_unit_3 = 0; r8 = 0; }
    }

    let mut total_equity_tag: u64 = rbx;
    let mut total_equity_unit: u64 = total_equity_unit_3;
    let mut total_equity_val: u64 = r8;
    let mut margin_req_tag: u64 = 2;
    let mut margin_req_unit: u64 = 0;
    let mut margin_req_value: u64 = 0;
    let mut abs_ntl_tag: u64 = 2;
    let mut abs_ntl_unit: u64 = 0;
    let mut abs_ntl_val: u64 = 0;
    let mut acct_equity_value: u64 = r8;

    if user_state.account_equity.is_some() && positions_count != 0 {
        let mut btree_cursor = user_state.positions.iter();
        let oracle: &OraclePxRef = oracle_data;
        while let Some((asset_key, pos_copy)) = btree_cursor.next() {
            let _key: u64 = *asset_key;
            let _ret = compute_margin_requirement_by_mode(pos_copy, oracle);

            let (total_equity_tag_1, total_equity_unit_2) =
                option_unit_qty_add_a(total_equity_tag, total_equity_unit, _ret[0], _ret[1]);
            total_equity_tag = total_equity_tag_1;
            total_equity_unit = total_equity_unit_2;
            total_equity_val = total_equity_val.wrapping_add(_ret[1]).wrapping_add(_key);

            let (margin_req_tag_1, margin_req_unit_2) =
                option_unit_qty_add_b(margin_req_tag, margin_req_unit, _ret[2], _ret[3]);
            margin_req_tag = margin_req_tag_1;
            margin_req_unit = margin_req_unit_2;
            margin_req_value = margin_req_value.wrapping_add(_ret[3]);

            let (abs_ntl_tag_1, abs_ntl_unit_3) =
                option_unit_qty_add_b(abs_ntl_tag, abs_ntl_unit, _ret[4], _ret[5]);
            abs_ntl_tag = abs_ntl_tag_1;
            abs_ntl_unit = abs_ntl_unit_3;
            abs_ntl_val = abs_ntl_val.wrapping_add(_ret[5]);

            let (rax_15, total_equity_unit_5) =
                option_unit_qty_add_a(rbx, total_equity_unit_3, _ret[6], _ret[7]);
            rbx = rax_15;
            total_equity_unit_3 = total_equity_unit_5;
            acct_equity_value = acct_equity_value.wrapping_add(_ret[7]);
        }
    }

    UserMarginShortfall {
        total_equity: Some(UnitQty { unit: total_equity_unit, value: total_equity_val }),
        margin_req: Some(UnitQty { unit: margin_req_unit, value: margin_req_value }),
        abs_ntl: Some(UnitQty { unit: abs_ntl_unit, value: abs_ntl_val }),
        iso_equity: Some(UnitQty { unit: total_equity_unit_3, value: acct_equity_value }),
    }
}