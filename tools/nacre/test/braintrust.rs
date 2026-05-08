use std::collections::HashMap;
pub struct LookupMap {
    keys: Vec<u64>,
    values: Vec<u64>,
    lookup: HashMap<u64, u64>,
    passthrough: bool,
}
