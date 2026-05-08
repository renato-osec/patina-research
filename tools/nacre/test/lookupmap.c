struct std_ptr_NonNull_u8_ {
    void* pointer;
};

struct std_hash_RandomState {
    uint64_t k0;
    uint64_t k1;
};

struct alloc_raw_vec_Cap {
    uintptr_t _0;
};

struct hashbrown_raw_RawTableInner {
    struct std_ptr_NonNull_u8_ ctrl;
    uintptr_t bucket_mask;
    uintptr_t growth_left;
    uintptr_t items;
};

struct std_ptr_Unique_u8_ {
    struct std_ptr_NonNull_u8_ pointer;
};

struct hashbrown_raw_RawTable__u64__u64__ {
    struct hashbrown_raw_RawTableInner table;
};

struct alloc_raw_vec_RawVecInner {
    struct alloc_raw_vec_Cap cap;
    struct std_ptr_Unique_u8_ ptr;
};

struct hashbrown_map_HashMap_u64__u64__std_hash_RandomState_ {
    struct hashbrown_raw_RawTable__u64__u64__ table;
    struct std_hash_RandomState hash_builder;
};

struct alloc_raw_vec_RawVec_u64_ {
    struct alloc_raw_vec_RawVecInner inner;
};

struct std_collections_HashMap_u64__u64_ {
    struct hashbrown_map_HashMap_u64__u64__std_hash_RandomState_ base;
};

struct std_vec_Vec_u64_ {
    struct alloc_raw_vec_RawVec_u64_ buf;
    uintptr_t len;
};

struct LookupMap {
    struct std_vec_Vec_u64_ keys;
    struct std_vec_Vec_u64_ values;
    struct std_collections_HashMap_u64__u64_ lookup;
    bool passthrough;
    uint8_t _pad_97[7];
};

