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

fn fn_0x555556b5a850(initial_margin: &mut u128, k_lo: u64, k_hi: u64) -> (u64, u64) { unimplemented!() }
fn fn_0x555556c30280(lhs: &u64, initial_margin: &u128) -> (u64, u64) { unimplemented!() }

pub fn compute_margin_requirement_by_mode(position: &Position, oracle: &OraclePx) -> MarginResult {
    let position_size = oracle.price;
    let px_abs_value = position_size.wrapping_mul(position.size);
    let abs_notional = px_abs_value.unsigned_abs();

    let oracle_px_field_10 = oracle.flags;
    let mut initial_margin: u128 = oracle.source as u128 | ((oracle_px_field_10 as u128) << 64);
    let (oracle_px_tag, oracle_px_abs_value) =
        fn_0x555556b5a850(&mut initial_margin, position.kind as u64, (position.kind >> 64) as u64);
    let px_validated_value = oracle_px_abs_value;
    let px_tag = if px_abs_value == 0 { 2u64 } else { oracle_px_tag };
    let lhs = px_tag;

    let margin_mode = position.initial_margin as u32;
    let mut margin_result = margin_mode as u64;
    let mut divisor: u64 = 0;
    let mut px_tag_1: u64 = 2;

    match margin_result {
        1 => {
            let rax_1 = (position.initial_margin != 3) as u32;
            divisor = position.reserved.wrapping_add((rax_1 << 4) as u64);
        }
        2 => {
            let max_leverage = position.is_isolated as u64;
            divisor = max_leverage.wrapping_mul(2);
        }
        3 => {
            let divisor_1 = 3u8.wrapping_mul(position.is_isolated as u8);
            divisor = divisor_1 as u64;
        }
        _ => {}
    }

    if divisor != 0 && abs_notional >= divisor {
        margin_result = abs_notional / divisor;
        px_tag_1 = px_tag;
    }

    if position.initial_margin == 3 {
        return MarginResult {
            status: 0,
            abs_notional_lo: px_abs_value as u64,
            abs_notional_hi: px_tag_1,
            px_tag: px_validated_value,
            px_value: margin_result,
            mode: px_tag,
            leverage: px_validated_value,
            margin_amount: abs_notional,
            maintenance_margin: 0,
            free_margin_lo: 0,
            free_margin_hi: 0,
            equity: 3,
            upnl: 0,
            funding: 0,
            fee: 0,
            flags: 0,
        };
    }

    let leverage_setting = position.leverage_setting;
    let pos_margin_val = leverage_setting;
    let initial_margin_1: u128 = position.initial_margin as u128;
    let signed_notional = (px_abs_value as i64).wrapping_add(leverage_setting as i64);
    let (oqty_sum_tag, px_tag_out) = fn_0x555556c30280(&lhs, &initial_margin);
    let signed_notional_1 = if signed_notional > 0 { signed_notional as u64 } else { 0 };
    let oqty_sum_tag_1 = if signed_notional == 0 { 2 } else { oqty_sum_tag };

    MarginResult {
        status: signed_notional as u64,
        abs_notional_lo: oqty_sum_tag_1,
        abs_notional_hi: px_tag_out,
        px_tag: signed_notional_1,
        px_value: px_tag,
        mode: px_tag,
        leverage: px_validated_value,
        margin_amount: initial_margin_1 as u64,
        maintenance_margin: pos_margin_val,
        free_margin_lo: leverage_setting,
        free_margin_hi: px_tag_1,
        equity: oqty_sum_tag_1 as u128 | ((px_tag_out as u128) << 64),
        upnl: px_validated_value,
        funding: margin_result,
        fee: 0,
        flags: 0,
    }
}