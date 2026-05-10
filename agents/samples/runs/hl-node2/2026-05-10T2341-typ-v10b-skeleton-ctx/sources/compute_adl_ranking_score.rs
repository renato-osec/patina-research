pub struct Clearinghouse { _data: [u64; 2700] }

fn num_markets(_c: &Clearinghouse) -> u64 { unimplemented!() }
fn markets_ptr(_c: &Clearinghouse) -> u64 { unimplemented!() }
fn user_btree_root(_c: &Clearinghouse) -> u64 { unimplemented!() }
fn user_btree_height_get(_c: &Clearinghouse) -> u64 { unimplemented!() }
fn position_offset(_node: u64, _market_idx: usize, _height: u64) -> u64 { unimplemented!() }
fn market_field(_markets: u64, _slot: u64, _off: u64) -> u64 { unimplemented!() }
fn pos_field(_node: u64, _off: u64) -> u64 { unimplemented!() }
fn maintenance_margin(_c: &Clearinghouse, _ch: u64, _abs_notional: u64, _entry: u64) -> u64 { unimplemented!() }
fn cross_margin(_c: &Clearinghouse, _ch: u64) -> u64 { unimplemented!() }
fn isolated_margin(_c: &Clearinghouse, _abs_notional: u64, _entry: u64) -> u64 { unimplemented!() }
fn checked_mul(_a: u64, _b: u64) -> u64 { unimplemented!() }
fn ratio_div(_n: u64, _d: u64) -> u64 { unimplemented!() }
fn clamp_eps_mul(_a: u64, _b: u64, _c: u64, _d: u64, _e: u64, _f: u64, _g: u64) -> u64 { unimplemented!() }
fn descend_btree(_root: u64, _user_key: &[u32; 4], _height: u64) -> u64 { unimplemented!() }
fn neg_i64(_x: u64) -> u64 { unimplemented!() }

pub fn compute_adl_ranking_score(this: &Clearinghouse, user_key: &[u32; 4], market_idx: usize) {
    let clearinghouse_ptr = this;
    let rsi = num_markets(clearinghouse_ptr);
    if rsi <= market_idx as u64 { panic!("oob"); }

    let rdx = markets_ptr(clearinghouse_ptr);
    let rsi_2 = market_idx as u64 * 0x60;
    let entry_notional_3 = market_field(rdx, rsi_2, 0x40);
    let oracle_price_qty = market_field(rdx, rsi_2, 0x30);
    let result = market_field(rdx, rsi_2, 0x48);
    let result_1 = result;

    let rdx_1 = user_btree_root(clearinghouse_ptr);
    let user_btree_height = user_btree_height_get(clearinghouse_ptr);
    let ch_1 = descend_btree(rdx_1, user_key, user_btree_height);
    let ch = if ch_1 != 0 { ch_1 } else { 0x60 };
    let btree_node = ch + 0x30;
    let rbp_2 = position_offset(btree_node, market_idx, user_btree_height);

    let rcx_2 = pos_field(btree_node, rbp_2 - 0xa0);
    let rcx_3 = pos_field(btree_node, rbp_2 - 0x98);
    let entry_notional_5 = pos_field(btree_node, rbp_2 - 0x90);
    let rcx_4 = pos_field(btree_node, rbp_2 - 0x48);
    let rcx_5 = pos_field(btree_node, rbp_2 - 0x40);
    let rcx_6 = pos_field(btree_node, rbp_2 - 0x38);
    let entry_notional = pos_field(btree_node, rbp_2 - 0x30);
    let rbx_1 = pos_field(btree_node, rbp_2 - 0x28);
    let rcx_7 = pos_field(btree_node, rbp_2 - 0x10);
    let rdx_3 = pos_field(btree_node, rbp_2 - 8);
    let abs_notional_3 = entry_notional_5;
    let abs_notional_2 = result + 0x10;

    // maintenance margin path
    let mode = maintenance_margin(clearinghouse_ptr, ch, abs_notional_3, entry_notional);
    let _ = mode;

    // cross (rbx_1==3) vs isolated
    let r13_1;
    let r14_1;
    let abs_notional;
    let leverage;
    let account_value;
    if rbx_1 == 3 {
        let mode_1 = cross_margin(clearinghouse_ptr, ch);
        r13_1 = mode_1;
        r14_1 = rcx_2;
        abs_notional = abs_notional_3;
        leverage = rcx_3;
        account_value = rcx_4;
    } else {
        let mode_1 = isolated_margin(clearinghouse_ptr, abs_notional_2 as u64, entry_notional_5);
        r13_1 = mode_1;
        r14_1 = rcx_3;
        abs_notional = abs_notional_2;
        leverage = rcx_5;
        account_value = rcx_6;
    }
    let mode_1 = r13_1;
    let leverage_1 = leverage;
    let account_value_1 = account_value;
    let abs_notional_1 = abs_notional;

    // ratio1 = account_value / abs_notional, weighted by leverage/r14_1
    let ratio1_margin_ratio = if abs_notional_1 > 0 && mode_1 == 2 && r13_1 == 2 {
        ratio_div(account_value_1, abs_notional_1).wrapping_add(leverage_1).wrapping_add(r14_1)
    } else { 0 };

    // signed_notional = oracle * szi
    let signed_notional_product_2 = checked_mul(result_1, entry_notional_5);
    let rbx_2 = entry_notional;
    let var_188_2 = rbx_2;
    let _ = var_188_2;

    // direction (shorts vs longs)
    let is_long = pos_field(btree_node, rbp_2 - 0x60);
    let r12_1;
    let r15_2;
    let signed_notional_product;
    let signed_notional_product_3 = signed_notional_product_2;
    let _ = signed_notional_product_3;
    if is_long == 0 {
        r15_2 = rcx_6;
        r12_1 = rcx_5;
        let entry_notional_4 = neg_i64(entry_notional);
        let entry_notional_1 = entry_notional_4;
        let _ = entry_notional_1;
        signed_notional_product = signed_notional_product_2.wrapping_sub(entry_notional_4);
    } else {
        let mut rax_23 = 2;
        r12_1 = rcx_5;
        if entry_notional != 0 { rax_23 = r12_1; }
        r15_2 = rcx_6;
        let entry_notional_1 = entry_notional.wrapping_add(rax_23);
        let _ = entry_notional_1;
        signed_notional_product = signed_notional_product_2.wrapping_sub(entry_notional);
    }

    let signed_notional_product_1 = if (signed_notional_product as i64) > 0 { signed_notional_product } else { 0 };
    let signed_notional_product_4 = signed_notional_product_2;
    let _ = signed_notional_product_4;
    let result_2 = if (signed_notional_product as i64) > 0 { result } else { 2 };

    // ratio2 = pnl / entry_notional; score = max(r1,eps) * max(r2,eps); fold remaining state vars in
    let oracle = entry_notional_3 ^ oracle_price_qty;
    if entry_notional > 0 {
        let ratio2 = ratio_div(signed_notional_product_1, entry_notional);
        let _final = clamp_eps_mul(ratio1_margin_ratio, ratio2, oracle, r12_1, r15_2, result_2, rcx_7 ^ rdx_3 ^ rcx_2);
        let _ = _final;
    }
}