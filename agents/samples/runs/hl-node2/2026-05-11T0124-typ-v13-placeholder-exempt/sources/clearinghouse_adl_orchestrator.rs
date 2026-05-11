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
#[derive(PartialEq, Eq, PartialOrd, Ord)]
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

fn adl_init_user_position_iterators(_out: &mut Vec<(u8, UserAddr, u64)>, _ch: &Clearinghouse, _mode: u64, _asset: &AssetMeta) { unimplemented!() }
fn adl_compute_user_shortfall(_ch: &Clearinghouse, _meta: &AssetMeta, _mkt: &mut MarketCtx, _f: fn(u64, u64, u32) -> u64, _u: &UserAddr) -> (u8, u64) { unimplemented!() }
fn adl_pick_counterparties(_ch: &Clearinghouse, _meta: &AssetMeta, _shortfall: u64, _u: &UserAddr) -> Vec<u64> { unimplemented!() }

pub fn clearinghouse_adl_orchestrator(ch: &Clearinghouse, asset_meta: &AssetMeta, market: &mut MarketCtx, candidates: &AdlCandidate, deferred_only: bool) -> BTreeMap<AdlResult, Vec<u64>> {
    let (oracle_data, adl_fill_ctx, adl_candidates, cpty_addr_bytes) = (asset_meta, market, candidates, deferred_only);
    let clearinghouse: &Clearinghouse = ch;
    let entry_user_shortfall: fn(u64, u64, u32) -> u64 = adl_candidates.shortfall_fn;
    let user_equity_qty_2: i128 = adl_candidates.equity;

    let mut result_2: Vec<(u8, UserAddr, u64)> = Vec::new();
    adl_init_user_position_iterators(&mut result_2, clearinghouse, 0, oracle_data);

    let mut total_shortfall_value: u64 = 0;
    let mut deferred_cross_queue: BTreeMap<AdlResult, Vec<u64>> = BTreeMap::new();

    let mut i_54: usize = 0;
    while i_54 < result_2.len() {
        let shortfall_discriminant: u8 = result_2[i_54].0;
        let counterparty_addr: &UserAddr = &result_2[i_54].1;
        let asset_idx: u64 = result_2[i_54].2;

        let temp0_1 = adl_compute_user_shortfall(clearinghouse, oracle_data, adl_fill_ctx, entry_user_shortfall, counterparty_addr);
        let adl_was_triggered: u8 = temp0_1.0;
        let user_shortfall: u64 = temp0_1.1;

        if adl_was_triggered != 0 && !(cpty_addr_bytes && shortfall_discriminant == 0) {
            let remaining_shortfall: u64 = user_shortfall;
            total_shortfall_value = total_shortfall_value.wrapping_add(remaining_shortfall);

            adl_fill_ctx.last_update = clearinghouse.current_time;
            adl_fill_ctx.epoch = clearinghouse.seq;
            adl_fill_ctx.trigger = remaining_shortfall;

            let counterparty_array: Vec<u64> =
                adl_pick_counterparties(clearinghouse, oracle_data, remaining_shortfall, counterparty_addr);

            deferred_cross_queue
                .entry(AdlResult { asset_idx, side: shortfall_discriminant })
                .or_insert_with(Vec::new)
                .extend(counterparty_array);
        }

        i_54 += 1;
    }

    let _ = user_equity_qty_2;
    let _ = total_shortfall_value;
    deferred_cross_queue
}