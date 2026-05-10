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

pub fn clearinghouse_adl_orchestrator(
    ch: &Clearinghouse,
    _asset_meta: &AssetMeta,
    _market: &mut MarketCtx,
    _candidates: &AdlCandidate,
    _deferred_only: bool,
) -> BTreeMap<AdlResult, Vec<u64>> {
    let mut result: BTreeMap<AdlResult, Vec<u64>> = BTreeMap::new();
    let adl_candidates: &Vec<MarginEntry> = &ch.adl_table;
    let mut total_shortfall_value: u64 = 0;
    let mut adl_was_triggered: bool = false;
    let mut asset_idx: u64 = 0;
    let counterparty_count: u64 = adl_candidates.len() as u64;
    while asset_idx < counterparty_count {
        let entry_user_shortfall: u64 = adl_candidates[asset_idx as usize].a;
        let user_shortfall: u64 = entry_user_shortfall.saturating_sub(ch.equity_threshold);
        if user_shortfall > ch.margin_threshold {
            adl_was_triggered = true;
            total_shortfall_value = total_shortfall_value.saturating_add(user_shortfall);
            let fill_delta: u64 = adl_candidates[asset_idx as usize].b;
            result
                .entry(AdlResult { asset_idx, side: 0 })
                .or_insert_with(Vec::new)
                .push(fill_delta);
        }
        asset_idx += 1;
    }
    let _ = adl_was_triggered;
    let _ = total_shortfall_value;
    result
}