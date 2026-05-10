use std::collections::BTreeMap;

pub struct UnitQty { pub unit: u64, pub value: u64 }
pub struct AssetPosition;
pub struct UserState {
    pub account_equity: Option<UnitQty>,
    pub raw_equity: Option<UnitQty>,
    pub positions: BTreeMap<u64, AssetPosition>,
}
pub struct OraclePxRef { pub asset: u64, pub px_unit: u64, pub px_value: u64 }
pub struct UserMarginShortfall {
    pub total_equity: Option<UnitQty>,
    pub margin_req: Option<UnitQty>,
    pub abs_ntl: Option<UnitQty>,
    pub iso_equity: Option<UnitQty>,
}

pub fn compute_user_margin_and_shortfall(
    user_state: &UserState,
    oracle_data: &OraclePxRef,
) -> UserMarginShortfall {
    let saved_cross_margin_flag_1: u32 = oracle_data.px_unit as u32;
    let mkt_data_field_0x40: u64 = oracle_data.px_value;

    let rbx: u64 = if user_state.account_equity.is_some() { 1 } else { 2 };
    let total_equity_unit_3: u64 =
        match &user_state.account_equity { Some(_q) => _q.unit, None => 0 };
    let acct_equity_value: u64 =
        match &user_state.account_equity { Some(_q) => _q.value, None => 0 };

    let mut total_equity_tag: u64 = rbx;
    let mut total_equity_unit: u64 = total_equity_unit_3;
    let mut total_equity_val: u64 = acct_equity_value;
    let mut margin_req_tag: u64 = 2;
    let mut margin_req_unit: u64 = 0;
    let mut margin_req_value: u64 = 0;
    let mut abs_ntl_tag: u64 = 2;
    let mut abs_ntl_unit: u64 = 0;
    let mut abs_ntl_val: u64 = 0;

    let mut btree_node = user_state.positions.keys();
    while let Some(asset_key) = btree_node.next() {
        let _ = saved_cross_margin_flag_1;
        margin_req_unit = margin_req_unit.wrapping_add(*asset_key);
        margin_req_value = margin_req_value.wrapping_add(mkt_data_field_0x40);
        abs_ntl_unit = abs_ntl_unit.wrapping_add(*asset_key);
        abs_ntl_val = abs_ntl_val.wrapping_add(mkt_data_field_0x40);
        total_equity_val = total_equity_val.wrapping_add(mkt_data_field_0x40);
        total_equity_tag = 1;
        margin_req_tag = 1;
        abs_ntl_tag = 1;
    }

    UserMarginShortfall {
        total_equity: if total_equity_tag == 1 {
            Some(UnitQty { unit: total_equity_unit, value: total_equity_val })
        } else { None },
        margin_req: if margin_req_tag == 1 {
            Some(UnitQty { unit: margin_req_unit, value: margin_req_value })
        } else { None },
        abs_ntl: if abs_ntl_tag == 1 {
            Some(UnitQty { unit: abs_ntl_unit, value: abs_ntl_val })
        } else { None },
        iso_equity: if rbx == 1 {
            Some(UnitQty { unit: total_equity_unit_3, value: acct_equity_value })
        } else { None },
    }
}