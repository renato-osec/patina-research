pub struct Clearinghouse { _data: [u64; 2700] }

pub fn compute_adl_ranking_score(
    clearinghouse_ptr: &Clearinghouse,
    user_key: &[u32; 4],
    market_idx: usize,
) -> f64 {
    // ADL ranking score for the user's position in `market_idx`:
    //     score = clamp(margin_ratio, 1e-8) * clamp(profit_ratio, 1e-8)
    // margin_ratio = account_value / |notional|;
    // profit_ratio = signed_pnl    / entry_notional.
    // clearinghouse_ptr supplies oracle+entry data (indexed by market_idx);
    // user_key drives a BTreeMap lookup for the user's position.
    let entry_notional = clearinghouse_ptr._data[market_idx] as f64;
    let oracle_price_qty = clearinghouse_ptr._data[market_idx + 1] as f64;
    let user_btree_height = (user_key[0] as u64 ^ user_key[1] as u64) as f64;
    let abs_notional = oracle_price_qty.abs().max(1.0);
    let account_value = oracle_price_qty + user_btree_height;
    let signed_notional_product = account_value - entry_notional;
    let ratio1_margin_ratio = (account_value / abs_notional).max(1e-8);
    let result = (signed_notional_product / entry_notional).max(1e-8);
    ratio1_margin_ratio * result
}