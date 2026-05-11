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
#[derive(Clone, PartialEq, Eq, PartialOrd, Ord)]
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

#[derive(Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct AdlResult { pub asset_idx: u64, pub side: u8 }

fn compute_user_shortfall(eq: i128, mark: u64, dec: u32) -> u64 { unimplemented!() }
fn select_counterparties(market: &MarketCtx, asset_idx: u64) -> Vec<UserAddr> { unimplemented!() }
fn enqueue_deferred(queue: &mut Vec<u64>, addr: &UserAddr, qty: u64) { unimplemented!() }
fn vault_position(ch: &Clearinghouse, addr: &UserAddr) -> u64 { unimplemented!() }
fn make_result(asset_idx: u64) -> AdlResult { AdlResult { asset_idx, side: 0 } }

pub fn clearinghouse_adl_orchestrator(ch: &Clearinghouse, asset_meta: &AssetMeta, market: &mut MarketCtx, candidates: &AdlCandidate, deferred_only: bool) -> BTreeMap<AdlResult, Vec<u64>> {
    let mut result: BTreeMap<AdlResult, Vec<u64>> = BTreeMap::new();
    let clearinghouse = ch;
    if !clearinghouse.adl_enabled {
        let _ = (asset_meta, market, candidates, deferred_only);
        return result;
    }

    let user_equity_unit = candidates.equity;
    let user_shortfall = (candidates.shortfall_fn)(
        asset_meta.mark_price,
        asset_meta.px_index,
        asset_meta.px_decimals,
    );
    let mut remaining_shortfall = user_shortfall;
    let mut deferred_cross_queue: Vec<u64> = Vec::new();

    let mut asset_idx: u64 = 0;
    while asset_idx < clearinghouse.n_assets {
        let oracle_asset_data = match clearinghouse.assets.get(&asset_idx) {
            Some(a) => a,
            None => {
                asset_idx += 1;
                continue;
            }
        };

        let entry_user_shortfall = compute_user_shortfall(
            user_equity_unit,
            oracle_asset_data.bid,
            asset_meta.px_decimals,
        );
        let total_shortfall_value = entry_user_shortfall.saturating_add(market.trigger);
        let shortfall_discriminant = total_shortfall_value;
        let adl_value_unit = oracle_asset_data.ask;
        let adl_was_triggered = shortfall_discriminant > clearinghouse.equity_threshold;

        if adl_was_triggered {
            let counterparty_array = select_counterparties(market, asset_idx);
            let counterparty_count = counterparty_array.len();
            let cpty_positions_root = &counterparty_array;
            let mut cpty_array_offset: usize = 0;
            while cpty_array_offset < counterparty_count {
                let counterparty_addr = &cpty_positions_root[cpty_array_offset];
                let cpty_addr_bytes = &counterparty_addr.0;
                let cpty_abs_position = vault_position(clearinghouse, counterparty_addr);
                let counterparty_info = cpty_abs_position;
                let fill_delta = remaining_shortfall.min(counterparty_info);
                let fill_list_idx = cpty_array_offset as u64;

                if deferred_only {
                    enqueue_deferred(&mut deferred_cross_queue, counterparty_addr, fill_delta);
                } else {
                    let adl_fill_ctx = make_result(asset_idx);
                    result
                        .entry(adl_fill_ctx)
                        .or_insert_with(Vec::new)
                        .push(fill_delta);
                    let _ = (cpty_addr_bytes, fill_list_idx);
                }
                remaining_shortfall = remaining_shortfall.saturating_sub(fill_delta);
                cpty_array_offset += 1;
                if remaining_shortfall == 0 {
                    break;
                }
            }
        }
        let _ = adl_value_unit;
        asset_idx += 1;
    }

    let _ = &deferred_cross_queue;
    result
}