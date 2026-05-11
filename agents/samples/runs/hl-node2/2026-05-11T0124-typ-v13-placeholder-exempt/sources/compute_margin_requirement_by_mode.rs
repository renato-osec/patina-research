pub struct OraclePx {
    pub source: u64,
    pub timestamp: u64,
    pub flags: u64,
    pub price: i64,
}

pub struct Position {
    pub coin_idx: u64,
    pub user_idx: u64,
    pub size: i64,
    pub avg_entry: u64,
    pub leverage: u64,
    pub liquidation_px: u64,
    pub funding_paid: u64,
    pub realized_pnl: u64,
    pub fee_paid: u64,
    pub last_update: u64,
    pub margin_used: u64,
    pub upnl: u64,
    pub maintenance: u64,
    pub initial_margin: u64,
    pub reserved: u64,
    pub kind: u128,
    pub leverage_setting: u64,
    pub raw_size: u64,
    pub is_isolated: bool,
}

pub struct MarginResult {
    pub status: u64,
    pub abs_notional_lo: u64,
    pub abs_notional_hi: u64,
    pub px_tag: u64,
    pub px_value: u64,
    pub mode: u64,
    pub leverage: u64,
    pub margin_amount: u64,
    pub maintenance_margin: u64,
    pub free_margin_lo: u64,
    pub free_margin_hi: u64,
    pub equity: u128,
    pub upnl: u64,
    pub funding: u64,
    pub fee: u64,
    pub flags: u64,
}

fn oracle_price_type_validate(om: &mut u128, k_lo: u64, k_hi: u64) -> (i64, i64) { unimplemented!() }
fn option_unit_qty_add_a(lhs: &i64, init: &u128) -> (i64, u64) { unimplemented!() }

pub fn compute_margin_requirement_by_mode(position: &Position, oracle: &OraclePx) -> MarginResult {
    let position_size: i64 = oracle.price;
    let px_abs_value: i64 = position_size.wrapping_mul(position.size);
    let oracle_px_field_10: u64 = oracle.flags;
    let mut initial_margin: u128 = oracle.source as u128 | ((oracle_px_field_10 as u128) << 64);
    let (oracle_px_tag, oracle_px_abs_value) = oracle_price_type_validate(
        &mut initial_margin,
        position.kind as u64,
        (position.kind >> 64) as u64,
    );
    let mut px_validated_value: i64 = oracle_px_abs_value;
    let px_tag_1: u64 = 2;
    let mut px_tag: i64 = if px_abs_value == 0 { 2 } else { oracle_px_tag };
    let px_validated_value_1: i64 = px_validated_value;
    let _ = px_validated_value_1;
    let lhs: i64 = px_tag;
    let abs_notional: i64 = if px_abs_value < 0 { px_abs_value.wrapping_neg() } else { px_abs_value };

    let margin_mode: u8 = position.initial_margin as u8;
    let mut margin_result: u64 = margin_mode as u64;
    let mut divisor: u64 = 0;
    let max_leverage: u64 = position.is_isolated as u64;
    let divisor_1: u8 = (3u8).wrapping_mul(position.is_isolated as u8);

    match margin_result {
        1 => divisor = position.reserved,
        2 => divisor = max_leverage.wrapping_mul(2),
        3 => divisor = divisor_1 as u64,
        _ => {}
    }
    margin_result = if divisor != 0 && (abs_notional as u64) >= divisor {
        (abs_notional as u64) / divisor
    } else {
        0
    };

    let leverage_setting: u64 = position.leverage_setting;
    let pos_margin_val: u64 = leverage_setting;
    let signed_notional: i64 = px_abs_value.wrapping_add(leverage_setting as i64);
    let (oqty_sum_tag, px_tag_out) = option_unit_qty_add_a(&lhs, &initial_margin);
    let oqty_sum_tag_1: i64 = if signed_notional == 0 { 2 } else { oqty_sum_tag };
    let signed_notional_1: i64 = if signed_notional > 0 { signed_notional } else { 0 };

    let initial_margin_1: u128;
    let mut _ret: MarginResult;

    if position.initial_margin == 3 {
        initial_margin_1 = lhs as u128;
        _ret = MarginResult {
            status: (initial_margin_1 >> 64) as u64,
            abs_notional_lo: px_abs_value as u64,
            abs_notional_hi: px_tag_1,
            px_tag: px_validated_value as u64,
            px_value: margin_result,
            mode: 0,
            leverage: 0,
            margin_amount: abs_notional as u64,
            maintenance_margin: 0,
            free_margin_lo: 0,
            free_margin_hi: 0,
            equity: 3,
            upnl: 0,
            funding: 0,
            fee: 0,
            flags: 0,
        };
    } else {
        initial_margin_1 = position.initial_margin as u128;
        _ret = MarginResult {
            status: signed_notional as u64,
            abs_notional_lo: oqty_sum_tag as u64,
            abs_notional_hi: px_tag_out,
            px_tag: signed_notional_1 as u64,
            px_value: px_tag as u64,
            mode: 0,
            leverage: 0,
            margin_amount: initial_margin_1 as u64,
            maintenance_margin: (initial_margin_1 >> 64) as u64,
            free_margin_lo: pos_margin_val,
            free_margin_hi: px_tag_1,
            equity: (oqty_sum_tag_1 as u128) | ((px_tag_out as u128) << 64),
            upnl: 0,
            funding: margin_result,
            fee: 0,
            flags: 0,
        };
        px_tag = px_validated_value;
        _ret.upnl = px_tag as u64;
        px_validated_value = abs_notional;
    }
    _ret.mode = px_tag as u64;
    _ret.leverage = px_validated_value as u64;
    _ret
}