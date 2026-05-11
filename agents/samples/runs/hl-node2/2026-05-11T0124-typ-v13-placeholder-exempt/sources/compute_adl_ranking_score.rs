pub struct Clearinghouse { _data: [u64; 2700] }

fn num_markets(_ch: &Clearinghouse) -> u64 { unimplemented!() }
fn market_oracle(_ch: &Clearinghouse, _idx: usize) -> ([u64; 2], i64, i64) { unimplemented!() }
fn user_btree_height_of(_ch: &Clearinghouse) -> u64 { unimplemented!() }
fn find_position(_ch: &Clearinghouse, _key: &[u32; 4], _height: u64, _idx: usize) -> usize { unimplemented!() }
fn pos_field_mode(_node: usize) -> i64 { unimplemented!() }
fn pos_is_long(_node: usize) -> u64 { unimplemented!() }
fn pos_entry_notional(_node: usize) -> i64 { unimplemented!() }
fn pos_szi(_node: usize) -> u64 { unimplemented!() }
fn compute_cross_margin(_ch: &Clearinghouse) -> (i64, u64, u64, u64) { unimplemented!() }
fn compute_isolated_margin(_oracle: &[u64; 2], _price: i64, _flags: i64) -> (i64, u64, u64, u64) { unimplemented!() }
fn panic_oob(_idx: usize, _len: u64) -> ! { unimplemented!() }
fn checked_mul_i64_u64(_a: i64, _b: u64) -> i64 { unimplemented!() }

pub fn compute_adl_ranking_score(this: &Clearinghouse, user_key: &[u32; 4], market_idx: usize) {
    let clearinghouse_ptr: &Clearinghouse = this;
    let _ = this;
    let rsi = num_markets(clearinghouse_ptr);
    if rsi <= market_idx as u64 {
        panic_oob(market_idx, rsi);
    }
    let (oracle_price_qty, result_1, entry_notional_3) = market_oracle(clearinghouse_ptr, market_idx);
    let user_btree_height = user_btree_height_of(clearinghouse_ptr);
    let btree_node = find_position(clearinghouse_ptr, user_key, user_btree_height, market_idx);
    let (abs_notional, account_value, mode, leverage) = match pos_field_mode(btree_node) {
        3 => compute_cross_margin(clearinghouse_ptr),
        _ => compute_isolated_margin(&oracle_price_qty, result_1, entry_notional_3),
    };
    let is_long = pos_is_long(btree_node);
    let entry_notional = pos_entry_notional(btree_node);
    let signed_notional_product = checked_mul_i64_u64(result_1, pos_szi(btree_node));
    let signed_notional_product_1 = if is_long != 0 {
        (signed_notional_product - entry_notional).max(0)
    } else {
        (signed_notional_product + entry_notional).max(0)
    };
    let ratio1_margin_ratio = (account_value as f64 / abs_notional as f64).max(1e-8);
    let zmm2_2 = (signed_notional_product_1 as f64 / entry_notional as f64).max(1e-8);
    let result = ratio1_margin_ratio * zmm2_2;
    let _ = (mode, leverage, result);
}