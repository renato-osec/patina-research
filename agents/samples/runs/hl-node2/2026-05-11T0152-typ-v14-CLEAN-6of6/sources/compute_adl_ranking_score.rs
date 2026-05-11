pub struct Clearinghouse { _data: [u64; 2700] }

fn market_oracle_flags(c: &Clearinghouse, m: usize) -> i64 { unimplemented!() }
fn market_oracle_qty(c: &Clearinghouse, m: usize) -> [u64; 2] { unimplemented!() }
fn market_oracle_price(c: &Clearinghouse, m: usize) -> i64 { unimplemented!() }
fn user_btree_root(c: &Clearinghouse) -> usize { unimplemented!() }
fn user_btree_height_of(c: &Clearinghouse) -> i64 { unimplemented!() }
fn btree_walk(node: usize, key: &[u32; 4], height: i64) -> usize { unimplemented!() }
fn user_default_account(c: &Clearinghouse) -> usize { unimplemented!() }
fn find_position(user: usize, m: usize) -> usize { unimplemented!() }
fn pos_field0(n: usize) -> i64 { unimplemented!() }
fn pos_field1(n: usize) -> i64 { unimplemented!() }
fn pos_szi(n: usize) -> i64 { unimplemented!() }
fn pos_disc(n: usize) -> i64 { unimplemented!() }
fn pos_entry_ntl(n: usize) -> i64 { unimplemented!() }
fn pos_abs_ntl(n: usize) -> i64 { unimplemented!() }
fn pos_is_long(n: usize) -> i64 { unimplemented!() }
fn maintenance_margin(c: &Clearinghouse, u: usize) -> (i64, i64, u64, u64, u64) { unimplemented!() }
fn isolated_margin(o_price: i64, szi: i64, n: usize) -> (i64, i64, u64, u64, u64) { unimplemented!() }

pub fn compute_adl_ranking_score(this: &Clearinghouse, user_key: &[u32; 4], market_idx: usize) {
    let clearinghouse_ptr = this;

    // Pull the market's oracle entry (bounds-checked indexing into Clearinghouse.markets).
    let entry_notional_3 = market_oracle_flags(clearinghouse_ptr, market_idx);
    let oracle_price_qty = market_oracle_qty(clearinghouse_ptr, market_idx);
    let result = market_oracle_price(clearinghouse_ptr, market_idx);
    let result_1 = result;

    // Walk the per-user BTreeMap to locate this user's account; fall back to a sentinel.
    let rdx_1 = user_btree_root(clearinghouse_ptr);
    let user_btree_height = user_btree_height_of(clearinghouse_ptr);
    let ch_1 = if rdx_1 != 0 {
        btree_walk(rdx_1, user_key, user_btree_height)
    } else {
        0
    };
    let ch = if ch_1 != 0 { ch_1 } else { user_default_account(clearinghouse_ptr) };

    // Find the user's position entry for this market; bail if absent.
    let btree_node = find_position(ch, market_idx);
    if btree_node == 0 { return; }

    let rcx_2 = pos_field0(btree_node);
    let rcx_3 = pos_field1(btree_node);
    let entry_notional_5 = pos_szi(btree_node);
    let entry_notional = pos_entry_ntl(btree_node);
    let rbx_1 = pos_disc(btree_node);
    let abs_notional_3 = pos_abs_ntl(btree_node);
    let is_long = pos_is_long(btree_node);

    // Margin: cross (disc==3) uses account-wide equity; isolated uses oracle*szi + extra.
    let (r13_1, r14_1, mode, leverage, account_value) = if rbx_1 == 3 {
        maintenance_margin(clearinghouse_ptr, ch)
    } else {
        isolated_margin(result_1, entry_notional_5, btree_node)
    };
    let abs_notional = abs_notional_3;
    let abs_notional_1 = abs_notional;

    // ratio1 = account_value / abs_notional (margin coverage); 0 when no notional.
    let ratio1_margin_ratio = if abs_notional_1 > 0 {
        let _ = (mode == r13_1 as u64) && (r13_1 != 1 || leverage == r14_1 as u64);
        (account_value as f64) / (abs_notional_1 as f64)
    } else {
        0.0
    };

    // signed_notional = oracle_price * szi; PnL = signed_notional ± entry_notional.
    let signed_notional_product_2 = result_1.checked_mul(entry_notional_5).unwrap();
    let entry_notional_4 = -entry_notional;
    let signed_notional_product = if is_long != 0 {
        signed_notional_product_2 - entry_notional
    } else {
        signed_notional_product_2 - entry_notional_4
    };
    let signed_notional_product_1 = signed_notional_product.max(0);
    let _pnl_ratio = (signed_notional_product_1 as f64) / (entry_notional as f64);

    // ADL score = clamp(margin_ratio, 1e-8) * clamp(pnl_ratio, 1e-8).
    let _score = ratio1_margin_ratio.max(1e-8) * _pnl_ratio.max(1e-8);
    let _ = (rcx_2, rcx_3, oracle_price_qty, entry_notional_3);
}