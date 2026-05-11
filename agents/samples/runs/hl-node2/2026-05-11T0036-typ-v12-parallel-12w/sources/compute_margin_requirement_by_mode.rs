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

fn oracle_price_type_validate(im: &mut u128, klo: u64, khi: u64) -> (i64, i64) { unimplemented!() }
fn option_unit_qty_add_a(lhs: &i64, im: &u128) -> (i64, u64) { unimplemented!() }

pub fn compute_margin_requirement_by_mode(position: &Position, oracle: &OraclePx) -> MarginResult {
    // Notional from oracle * coin quantity, then validate the oracle (source,timestamp) tuple against position.kind.
    let position_size: i64 = oracle.price;
    let px_abs_value: i64 = position_size.wrapping_mul(position.coin_idx as i64);
    let mut initial_margin: u128 = (oracle.source as u128) | ((oracle.timestamp as u128) << 64);
    let oracle_px_field_10: u64 = oracle.flags;
    let _ = oracle_px_field_10;
    let (oracle_px_tag, oracle_px_abs_value): (i64, i64) =
        oracle_price_type_validate(&mut initial_margin, position.kind as u64, (position.kind >> 64) as u64);
    let px_validated_value: i64 = oracle_px_abs_value;
    let px_validated_value_1: i64 = px_validated_value;
    let _ = px_validated_value_1;
    let px_tag_1: u64 = 2;
    let px_tag: i64 = if px_abs_value == 0 { 2 } else { oracle_px_tag };
    let lhs: i64 = px_tag;
    let abs_notional: u64 = px_abs_value.unsigned_abs();

    // Mode dispatch on the low byte of position.initial_margin: 0=skip, 1=table, 2=liq, 3=adl.
    let margin_mode: u8 = position.initial_margin as u8;
    let mut margin_result: u64 = margin_mode as u64;
    let mut divisor: u64 = 0;
    let mut divisor_1: u8 = 0;
    let mut max_leverage: u64 = 0;
    let mut adl_divisor: u64 = 0;
    match margin_mode {
        1 => { divisor = position.reserved; }
        2 => {
            max_leverage = position.is_isolated as u64;
            divisor = max_leverage.wrapping_mul(2);
        }
        3 => {
            divisor_1 = (3u8).wrapping_mul(position.is_isolated as u8);
            divisor = divisor_1 as u64;
            adl_divisor = divisor;
        }
        _ => {}
    }
    let _ = (divisor_1, max_leverage, adl_divisor);
    margin_result = if divisor != 0 && abs_notional >= divisor { abs_notional / divisor } else { 0 };

    // Two output layouts: ADL (disc==3) stores notional + a fixed maint/freemargin pair;
    // every other mode folds the position's accumulated unit/qty totals via option_unit_qty_add_a.
    let mut _ret = MarginResult {
        status: 0, abs_notional_lo: 0, abs_notional_hi: 0,
        px_tag: 0, px_value: 0, mode: 0, leverage: 0,
        margin_amount: 0, maintenance_margin: 0,
        free_margin_lo: 0, free_margin_hi: 0,
        equity: 0, upnl: 0, funding: 0, fee: 0, flags: 0,
    };
    if position.initial_margin == 3 {
        let initial_margin_1: u128 = lhs as u128;
        _ret.abs_notional_lo = px_abs_value as u64;
        _ret.equity = initial_margin_1;
        _ret.status = (initial_margin_1 >> 64) as u64;
        _ret.abs_notional_hi = px_tag_1;
        _ret.px_tag = px_validated_value as u64;
        _ret.px_value = margin_result;
        _ret.margin_amount = abs_notional;
        _ret.equity = 3;
    } else {
        let leverage_setting: u64 = position.leverage_setting;
        let pos_margin_val: u64 = leverage_setting;
        let _ = pos_margin_val;
        _ret.free_margin_lo = leverage_setting;
        let initial_margin_1: u128 = position.initial_margin as u128;
        _ret.margin_amount = initial_margin_1 as u64;
        _ret.maintenance_margin = (initial_margin_1 >> 64) as u64;
        let signed_notional: i64 = px_abs_value.wrapping_add(leverage_setting as i64);
        let (oqty_sum_tag, px_tag_out): (i64, u64) = option_unit_qty_add_a(&lhs, &initial_margin);
        let signed_notional_1: u64 = if signed_notional > 0 { signed_notional as u64 } else { 0 };
        let oqty_sum_tag_1: i64 = if signed_notional == 0 { 2 } else { oqty_sum_tag };
        _ret.equity = (oqty_sum_tag_1 as u128) | ((px_tag_out as u128) << 64);
        _ret.status = signed_notional as u64;
        _ret.abs_notional_lo = oqty_sum_tag as u64;
        _ret.abs_notional_hi = px_tag_out;
        _ret.px_tag = signed_notional_1;
        _ret.px_value = px_tag as u64;
        _ret.free_margin_hi = px_tag_1;
        _ret.upnl = px_validated_value as u64;
        _ret.funding = margin_result;
    }
    _ret.mode = px_tag as u64;
    _ret.leverage = px_validated_value as u64;
    _ret
}