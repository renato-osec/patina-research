use std::collections::BTreeMap;
use std::collections::HashMap;

pub struct Clearinghouse {
    pub assets: HashMap<u64, AssetState>,
    pub current_time: u64,
    pub block_height: u64,
    pub equity_threshold: u64,
    pub margin_threshold: u64,
    pub fees: u64,
    pub seq: u64,
    pub user_states: UserStateRegistry,
    pub n_assets: u64,
    pub margin_table: Vec<MarginEntry>,
    pub liq_table: Vec<MarginEntry>,
    pub adl_table: Vec<MarginEntry>,
    pub limits: MarginLimits,
    pub vault_states: BTreeMap<UserAddr, VaultState>,
    pub adl_enabled: bool,
    pub triggers: BTreeMap<UserAddr, TriggerState>,
}
#[derive(PartialEq, Eq, PartialOrd, Ord, Clone)]
pub struct UserAddr(pub [u8; 20]);
pub struct AssetState { pub bid: u64, pub ask: u64 }
pub struct MarginEntry { pub a: u64, pub b: u64, pub c: u64 }
pub struct UserStateRegistry {
    pub orderbook: HashMap<u64, AssetState>,
    pub current_time: u64,
    pub block_height: u64,
    pub margin_buffer: u64,
    pub assets: Vec<u64>,
    pub funding: Vec<u64>,
}
pub struct MarginLimits {
    pub a: u64, pub b: u64, pub c: u64, pub d: u64, pub e: u64,
    pub f: u64, pub g: u64, pub h: u64, pub i: u64, pub j: u64,
    pub k: u64, pub l: u64, pub m: u64, pub n: u64, pub o: u64,
    pub p: u64, pub q: u64, pub r: u64, pub s: u64, pub t: u64,
    pub u: u64, pub v: u64, pub w: u64, pub x: u64, pub y: u64,
    pub z: u64,
}
pub struct VaultState { pub a: u64, pub b: u64 }
pub struct TriggerState { pub flag: u8 }

pub struct AssetMeta {
    pub orderbook: HashMap<u64, OrderRow>,
    pub px_index: u64,
    pub mark_price: u64,
    pub px_decimals: u32,
    pub _trail: u32,
    pub trade_history: Vec<u64>,
}
pub struct OrderRow { pub p: u64, pub q: u64 }

pub struct MarketCtx {
    pub orderbook: BTreeMap<u64, OrderRow>,
    pub epoch: u64,
    pub trigger: u64,
    pub last_update: u64,
}

pub struct AdlCandidate {
    pub equity: i128,
    pub shortfall_fn: fn(u64, u64, u32) -> u64,
}

#[derive(PartialEq, Eq, PartialOrd, Ord)]
pub struct AdlResult { pub asset_idx: u64, pub side: u8 }

fn adl_init_user_position_iterators(_ch: &Clearinghouse) -> Vec<UserAddr> { unimplemented!() }
fn compute_user_shortfall_tag(_ch: &Clearinghouse, _user: &UserAddr, _meta: &AssetMeta, _cb: fn(u64, u64, u32) -> u64) -> u8 { unimplemented!() }
fn compute_user_shortfall_value(_ch: &Clearinghouse, _user: &UserAddr, _meta: &AssetMeta, _cb: fn(u64, u64, u32) -> u64) -> u64 { unimplemented!() }
fn next_counterparty(_ch: &Clearinghouse, _user: &UserAddr, _i: usize) -> Option<(UserAddr, u64)> { unimplemented!() }
fn apply_adl_fill(_market: &mut MarketCtx, _meta: &AssetMeta, _user: &UserAddr, _cpty: &UserAddr, _qty: u64) -> u64 { unimplemented!() }
fn adl_side_from_equity(_eq: i128) -> u8 { unimplemented!() }
fn adl_asset_for_user(_ch: &Clearinghouse, _user: &UserAddr) -> u64 { unimplemented!() }

pub fn clearinghouse_adl_orchestrator(ch: &Clearinghouse, adl_fill_ctx: &AssetMeta, r12: &mut MarketCtx, adl_candidates: &AdlCandidate, arg6: bool) -> BTreeMap<AdlResult, Vec<u64>> {
    let clearinghouse = ch;
    let clearinghouse_1 = clearinghouse;

    let user_shortfall_3 = adl_candidates.shortfall_fn;
    let user_equity_qty_2 = adl_candidates.equity;

    let mut total_shortfall_value: u64 = 0;
    let mut deferred_cross_queue: Vec<u64> = Vec::new();
    let mut nonzero_bal_log_vec: Vec<u64> = Vec::new();
    let mut result: BTreeMap<AdlResult, Vec<u64>> = BTreeMap::new();

    let result_2: Vec<UserAddr> = adl_init_user_position_iterators(clearinghouse_1);

    let mut i: usize = 0;
    while i < result_2.len() {
        let counterparty_addr = result_2[i].clone();
        i = i + 1;

        let user_state = clearinghouse.triggers.get(&counterparty_addr);
        if user_state.is_none() { continue; }

        let total_shortfall_tag =
            compute_user_shortfall_tag(clearinghouse, &counterparty_addr, adl_fill_ctx, user_shortfall_3);
        let user_shortfall =
            compute_user_shortfall_value(clearinghouse, &counterparty_addr, adl_fill_ctx, user_shortfall_3);
        if total_shortfall_tag == 0 || user_shortfall == 0 { continue; }

        let mut remaining_shortfall = user_shortfall;
        total_shortfall_value = total_shortfall_value.wrapping_add(user_shortfall);

        let asset_idx = adl_asset_for_user(clearinghouse, &counterparty_addr);
        let s_1 = adl_side_from_equity(user_equity_qty_2);

        let mut j: usize = 0;
        while remaining_shortfall != 0 {
            let counterparty_info = next_counterparty(clearinghouse, &counterparty_addr, j);
            if counterparty_info.is_none() { break; }
            let cpty_addr_bytes = counterparty_info.as_ref().unwrap().0.clone();
            let cpty_abs_position = counterparty_info.as_ref().unwrap().1;
            j = j + 1;

            let fill_delta = if cpty_abs_position < remaining_shortfall { cpty_abs_position } else { remaining_shortfall };
            remaining_shortfall = remaining_shortfall.saturating_sub(fill_delta);

            let adl_value_unit =
                apply_adl_fill(r12, adl_fill_ctx, &counterparty_addr, &cpty_addr_bytes, fill_delta);
            if arg6 {
                deferred_cross_queue.push(adl_value_unit);
            } else {
                nonzero_bal_log_vec.push(adl_value_unit);
            }

            result.entry(AdlResult { asset_idx, side: s_1 }).or_insert_with(Vec::new).push(fill_delta);
        }
    }

    r12.last_update = total_shortfall_value;
    let _ = (nonzero_bal_log_vec, deferred_cross_queue);
    result
}