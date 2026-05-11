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

impl Clone for UserAddress { fn clone(&self) -> Self { UserAddress(self.0) } }
impl Copy for UserAddress {}
impl PartialEq for UserAddress { fn eq(&self, o: &Self) -> bool { self.0 == o.0 } }
impl Eq for UserAddress {}
impl PartialOrd for UserAddress { fn partial_cmp(&self, o: &Self) -> Option<std::cmp::Ordering> { Some(self.cmp(o)) } }
impl Ord for UserAddress { fn cmp(&self, o: &Self) -> std::cmp::Ordering { self.0.cmp(&o.0) } }
impl std::hash::Hash for UserAddress { fn hash<H: std::hash::Hasher>(&self, h: &mut H) { self.0.hash(h) } }

pub struct AdlIterEntry {
    pub discriminant: u64,
    pub asset_idx: u64,
    pub user_address: UserAddress,
    pub direction_byte: u32,
}

fn adl_init_user_position_iterators(_default_user: &UserState, _kind: u64, _filter: &mut Option<UserAddress>) -> Vec<AdlIterEntry> { unimplemented!() }
fn compute_adl_requirement(_exchange: &Exchange, _position: &PositionState, _asset_idx: u64, _user: &UserAddress) { unimplemented!() }

pub fn build_adl_candidate_set(this: &Exchange) -> BTreeSet<UserAddress> {
    let exchange = this;
    let mut filter_users_opt: Option<UserAddress> = None;
    let iter_vec = adl_init_user_position_iterators(&exchange.default_user, 2u64, &mut filter_users_opt);
    let mut result: BTreeSet<UserAddress> = BTreeSet::new();
    let entry_count = iter_vec.len();
    let mut entry_cursor: usize = 0;
    while entry_cursor < entry_count {
        let entry_cursor_1 = &iter_vec[entry_cursor];
        let discriminant = entry_cursor_1.discriminant;
        let asset_idx = entry_cursor_1.asset_idx;
        let user_address = entry_cursor_1.user_address;
        let user_state_ptr = exchange.users.get(&user_address).unwrap_or(&exchange.default_user);
        let positions_clone = &user_state_ptr.positions;
        let orders_root = &user_state_ptr.orders;
        result.insert(user_address);
        if discriminant & 1 == 0 {
            let mut j: usize = 0;
            let positions_count = positions_clone.len();
            while j < positions_count {
                if let Some((_, rhs)) = positions_clone.iter().nth(j) {
                    compute_adl_requirement(exchange, rhs, asset_idx, &user_address);
                }
                j += 1;
            }
        } else if let Some(rhs) = positions_clone.get(&asset_idx) {
            compute_adl_requirement(exchange, rhs, asset_idx, &user_address);
        }
        let _ = orders_root;
        entry_cursor += 1;
    }
    result
}