use std::collections::{BTreeMap, BTreeSet, HashMap};

pub struct UserAddress(pub [u8; 20]);

pub struct PositionState {
    pub coin: u64,
    pub size: i128,
    pub entry_px: u128,
    pub leverage: u64,
    pub margin: u64,
    pub unrealized: i128,
    pub funding: i128,
}

pub struct OrderState {
    pub oid: u64,
    pub price: u128,
    pub size: u128,
    pub side: u64,
    pub flags: u64,
    pub trigger: u128,
}

pub struct UserState {
    pub equity: u128,
    pub balance: u128,
    pub margin_used: u128,
    pub positions: BTreeMap<u64, PositionState>,
    pub orders: BTreeMap<u64, OrderState>,
}

pub struct Exchange {
    pub asset_ctxs: HashMap<u64, u128>,
    pub spot_ctxs: HashMap<u64, u128>,
    pub mark_pxs: HashMap<u64, u128>,
    pub oracle_pxs: HashMap<u64, u128>,
    pub asset_meta: HashMap<u64, u128>,
    pub spot_meta: HashMap<u64, u128>,
    pub vault_states: HashMap<UserAddress, u128>,
    pub clearing_state: HashMap<UserAddress, u128>,
    pub spot_balances: HashMap<UserAddress, u128>,
    pub funding_state: HashMap<u64, u128>,
    pub adl_state: HashMap<u64, u128>,
    pub asset_books: HashMap<u64, u128>,
    pub spot_books: HashMap<u64, u128>,
    pub triggers: HashMap<u64, u128>,
    pub schedules: HashMap<u64, u128>,
    pub recent_trades: HashMap<u64, u128>,
    pub fills: HashMap<u64, u128>,
    pub events: HashMap<u64, u128>,
    pub liquidations: HashMap<u64, u128>,
    pub vault_owners: HashMap<UserAddress, u128>,
    pub vault_followers: HashMap<UserAddress, u128>,
    pub user_index: HashMap<UserAddress, u128>,
    pub fee_credits: HashMap<UserAddress, u128>,
    pub referral_state: HashMap<UserAddress, u128>,
    pub staking: HashMap<UserAddress, u128>,
    pub airdrops: HashMap<UserAddress, u128>,
    pub api_keys: HashMap<UserAddress, u128>,
    pub builder_codes: HashMap<UserAddress, u128>,
    pub leverage_caps: HashMap<u64, u128>,
    pub margin_modes: HashMap<u64, u128>,
    pub asset_class: HashMap<u64, u128>,
    pub asset_funding: HashMap<u64, u128>,
    pub asset_premiums: HashMap<u64, u128>,
    pub asset_volume: HashMap<u64, u128>,
    pub asset_open_interest: HashMap<u64, u128>,
    pub asset_indices: HashMap<u64, u128>,
    pub spot_pairs: HashMap<u64, u128>,
    pub spot_volume: HashMap<u64, u128>,
    pub spot_indices: HashMap<u64, u128>,
    pub price_history: HashMap<u64, u128>,
    pub funding_history: HashMap<u64, u128>,
    pub vol_history: HashMap<u64, u128>,
    pub trade_history: HashMap<u64, u128>,
    pub fill_history: HashMap<u64, u128>,
    pub block_history: HashMap<u64, u128>,
    pub clearing_meta: HashMap<u64, u128>,
    pub coin_meta: HashMap<u64, u128>,
    pub iter_state: Option<Box<UserState>>,
    pub epoch: u128,
    pub block_time: u128,
    pub block_height: u128,
    pub default_user: UserState,
    pub recent_funding: BTreeMap<u64, u128>,
    pub recent_premiums: BTreeMap<u64, u128>,
    pub user_summaries: BTreeMap<UserAddress, u128>,
    pub user_perp_summaries: BTreeMap<UserAddress, u128>,
    pub vault_summaries: BTreeMap<UserAddress, u128>,
    pub fill_summaries: BTreeMap<u64, u128>,
    pub trade_summaries: BTreeMap<u64, u128>,
    pub users: BTreeMap<UserAddress, UserState>,
}

struct AdlIterEntry {
    discriminant: u8,
    asset_idx: u64,
    user_address: UserAddress,
    direction_byte: u8,
}

fn adl_init_user_position_iterators(
    _users: &BTreeMap<UserAddress, UserState>,
    _mode: u64,
    _filter: &Option<BTreeSet<UserAddress>>,
) -> Vec<AdlIterEntry> {
    unimplemented!()
}

fn compute_adl_requirement(
    _exchange: &Exchange,
    _pos: &PositionState,
    _asset_idx: u64,
    _user: &UserAddress,
) {
    unimplemented!()
}

pub fn build_adl_candidate_set(this: &Exchange) -> BTreeSet<UserAddress> {
    let exchange = this;
    let filter_users_opt: Option<BTreeSet<UserAddress>> = None;
    let iter_vec = adl_init_user_position_iterators(&exchange.users, 2u64, &filter_users_opt);
    let mut candidate_root: BTreeSet<UserAddress> = BTreeSet::new();
    for entry_cursor in iter_vec {
        let discriminant: u8 = entry_cursor.discriminant;
        let asset_idx: u64 = entry_cursor.asset_idx;
        let direction_byte: u8 = entry_cursor.direction_byte;
        let user_address: UserAddress = entry_cursor.user_address;
        let user_state_ptr: &UserState = exchange
            .users
            .get(&user_address)
            .unwrap_or(&exchange.default_user);
        let _ = direction_byte;
        if (discriminant & 1) == 0 {
            for lhs in user_state_ptr.positions.values() {
                compute_adl_requirement(exchange, lhs, asset_idx, &user_address);
            }
        } else if let Some(lhs_2) = user_state_ptr.positions.get(&asset_idx) {
            compute_adl_requirement(exchange, lhs_2, asset_idx, &user_address);
        }
        candidate_root.insert(UserAddress(user_address.0));
    }
    candidate_root
}