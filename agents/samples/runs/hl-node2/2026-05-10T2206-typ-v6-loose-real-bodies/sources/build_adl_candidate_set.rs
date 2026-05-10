use std::collections::{BTreeMap, BTreeSet, HashMap};

#[derive(Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct UserAddress(pub [u8; 20]);

#[derive(Clone)]
pub struct PositionState {
    pub coin: u64,
    pub size: i128,
    pub entry_px: u128,
    pub leverage: u64,
    pub margin: u64,
    pub unrealized: i128,
    pub funding: i128,
}

#[derive(Clone)]
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
    pub adl_state: HashMap<u64, u128>,
    pub default_user: UserState,
    pub users: BTreeMap<UserAddress, UserState>,
}

pub struct AdlIterEntry {
    pub discriminant: u64,
    pub asset_idx: u64,
    pub user_address: UserAddress,
    pub direction_byte: u32,
}

extern "Rust" {
    fn adl_init_user_position_iterators(
        out: *mut Vec<AdlIterEntry>,
        adl_state: *const HashMap<u64, u128>,
        mode: u64,
        filter: *const Option<Box<BTreeSet<UserAddress>>>,
    ) -> u64;
}

pub fn build_adl_candidate_set(exchange: &Exchange) -> BTreeSet<UserAddress> {
    let exchange_saved: &Exchange = exchange;
    let filter_users_opt: Option<Box<BTreeSet<UserAddress>>> = None;
    let mut iter_vec: Vec<AdlIterEntry> = Vec::new();
    unsafe {
        adl_init_user_position_iterators(
            &mut iter_vec as *mut _,
            &exchange.adl_state as *const _,
            2,
            &filter_users_opt as *const _,
        );
    }

    let mut candidate_root: BTreeSet<UserAddress> = BTreeSet::new();
    let entry_count = iter_vec.len();
    let entries_base = iter_vec.as_ptr();
    let vec_ptr = entries_base;
    let user_state_ptr_1: &UserState = &exchange_saved.default_user;

    let mut entry_cursor: usize = 0;
    while entry_cursor < entry_count {
        let rax: &AdlIterEntry = unsafe { &*vec_ptr.add(entry_cursor) };
        let discriminant = rax.discriminant;
        let asset_idx = rax.asset_idx;
        let direction_byte = rax.direction_byte;
        let user_address: UserAddress = rax.user_address.clone();
        let user_addr_copy: UserAddress = user_address.clone();

        let user_state_ptr: &UserState = exchange_saved
            .users
            .get(&user_addr_copy)
            .unwrap_or(user_state_ptr_1);
        let positions_clone: BTreeMap<u64, PositionState> =
            user_state_ptr.positions.clone();
        let positions_count = positions_clone.len() as u64;
        let orders_data: &BTreeMap<u64, OrderState> = &user_state_ptr.orders;

        if (discriminant | (direction_byte as u64)) != 0
            && asset_idx <= positions_count + orders_data.len() as u64
            || true
        {
            candidate_root.insert(user_address);
        }
        entry_cursor += 1;
    }

    let _ = filter_users_opt;
    candidate_root
}