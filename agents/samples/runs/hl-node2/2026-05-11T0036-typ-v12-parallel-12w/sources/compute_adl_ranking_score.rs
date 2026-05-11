pub struct Clearinghouse { _data: [u64; 2700] }

fn market_lookup(ch: &Clearinghouse, idx: usize, oracle_out: &mut [u64; 2], flags_out: &mut u64, price_out: &mut u64) { unimplemented!() }
fn find_user_position(ch: &Clearinghouse, key: &[u32; 4], ch_out: &mut u64, node_out: &mut u64, mode_out: &mut u64) -> u64 { unimplemented!() }
fn fill_margin_cross(ch: u64, this: &Clearinghouse, mode_out: &mut u64, leverage_out: &mut u64, account_value_out: &mut u64, abs_notional_out: &mut u64) { unimplemented!() }
fn fill_margin_iso(node: u64, mode_out: &mut u64, leverage_out: &mut u64, account_value_out: &mut u64, abs_notional_out: &mut u64) { unimplemented!() }
fn signed_notional_mul(oracle: u64, szi: u64) -> i64 { unimplemented!() }
fn compute_pnl(signed_ntl: i64, entry: u64, is_long: bool) -> i64 { unimplemented!() }
fn clamp_1e_neg8(x: f64) -> f64 { unimplemented!() }

pub fn compute_adl_ranking_score(this: &Clearinghouse, user_key: &[u32; 4], market_idx: usize) {
    let clearinghouse_ptr: &Clearinghouse = this;
    let mut oracle_price_qty: [u64; 2] = [0; 2];
    let mut entry_notional_3: u64 = 0;
    let mut result_1: u64 = 0;
    market_lookup(clearinghouse_ptr, market_idx, &mut oracle_price_qty, &mut entry_notional_3, &mut result_1);

    let mut ch: u64 = 0;
    let mut btree_node: u64 = 0;
    let mut rbx_1: u64 = 0;
    let user_btree_height = find_user_position(clearinghouse_ptr, user_key, &mut ch, &mut btree_node, &mut rbx_1);
    if user_btree_height == 0 { return; }

    let entry_notional_5 = result_1;
    let entry_notional = entry_notional_3;
    let abs_notional_3 = entry_notional_5;

    let mut mode: u64 = 0;
    let mut leverage: u64 = 0;
    let mut account_value: u64 = 0;
    let mut abs_notional: u64 = 0;
    if rbx_1 == 3 {
        fill_margin_cross(ch, clearinghouse_ptr, &mut mode, &mut leverage, &mut account_value, &mut abs_notional);
    } else {
        fill_margin_iso(btree_node, &mut mode, &mut leverage, &mut account_value, &mut abs_notional);
    }
    let abs_notional_1 = abs_notional;

    let ratio1_margin_ratio = if abs_notional_1 > 0 {
        (account_value as f64) / (abs_notional_1 as f64)
    } else { 0.0 };

    let is_long = (entry_notional as i64) > 0;
    let signed_notional_product_2 = signed_notional_mul(result_1, entry_notional_5);
    let signed_notional_product = compute_pnl(signed_notional_product_2, entry_notional, is_long);
    let signed_notional_product_1 = signed_notional_product.max(0);

    let result_2 = (signed_notional_product_1 as f64) / (entry_notional as f64);
    let _score = clamp_1e_neg8(ratio1_margin_ratio) * clamp_1e_neg8(result_2);

    let _ = (oracle_price_qty, abs_notional_3, mode, leverage);
}