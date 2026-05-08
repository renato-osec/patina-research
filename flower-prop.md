# flower

let the blocks flow

this agent takes cleaned up, retyped function blocks and outputs rust code, referencing the same variables with the same name
and same verified data dependencies. this prevents the agent from making up links or logic that isn't there.

to prevent overfitting and improve quality, it is expected that the agent will cut out intermediate variables, and possibly recreate inlined function calls

this is possible thanks to the pair of `anemone` (rust side, recursive) and `lymph` (binary side, bounded, trusts sigs from the previous agent and assumes the worst case)


### example

Ground truth :

```rust

struct State {
  jt: HashMap<usize, usize>,
  tl: Vec<u8>,
  tr: Vec<u8>,
  tc: u8
}

impl State {
  fn jump(&mut self, i: usize, fwd: bool) -> usize {
    if fwd ^ (self.tc != 0) {
      self.jt[&i]
    }
    else {
      i
    }
  }
}
```

Decompilation ( sig matched ) :

```c
004088b0    int64_t sub_4088b0(void* arg1, int64_t arg2, char arg3, uint128_t arg4 @ zmm0, int32_t arg5[0x4] @ zmm1)

004088b3        int64_t rax
004088b3        int64_t var_18 = rax
004088b4        var_18 = arg2
004088bc        rax.b = *(arg1 + 0x60) != 0
004088bc        
004088c1        if (rax.b == arg3)
004089bd            return arg2
004089bd        
004088cc        if (*(arg1 + 0x48) != 0)
004088e5            int64_t rax_2
004088e5            uint64_t r9_1
004088e5            rax_2, r9_1 =
004088e5                core::hash::BuildHasher::hash_one::he6ecdc9711935e32(arg1 + 0x50, &var_18)
004088ed            char (* rcx_1)[0x10] = *(arg1 + 0x30)
004088f1            int64_t rsi_1 = *(arg1 + 0x38)
004088f8            int64_t rdi_2 = rsi_1 & rax_2
004088ff            arg4 = zx.o((rax_2 u>> 0x39).d)
0040890c            arg4 = _mm_shuffle_epi32(
0040890c                _mm_shufflelo_epi16(_mm_unpacklo_epi8(arg4, arg4.q), 0), 0)
00408915            int64_t r8_1 = 0
00408918            int32_t temp0_4[0x4] = _mm_cmpeq_epi32(arg5, arg5)
0040891c            int64_t r11
0040891c            
0040891c            while (true)
0040891c                char zmm2[0x10] = *(rcx_1 + rdi_2)
00408929                uint32_t r10_1 = _mm_movemask_epi8(_mm_cmpeq_epi8(zmm2, arg4))
00408931                r11.b = r10_1 == 0
00408931                
00408935                if (r10_1 != 0)
00408948                    while (true)
00408948                        r9_1 = (zx.q(_tzcnt_u32(r10_1)) + rdi_2) & rsi_1
00408948                        
0040895b                        if (*(&rcx_1[-1] - (r9_1 << 4)) == arg2)
0040898c                            r11 = 0
0040898c                            break
0040898c                        
0040895d                        r9_1 = zx.q(r10_1)
00408964                        r10_1.w = (r9_1 - 1).d.w & r9_1.w
00408968                        r11.b = r10_1.w == 0
00408968                        
0040896c                        if (r10_1.w == 0)
0040896c                            goto label_408972
0040896c                    
0040895b                    break
0040895b                
00408972            label_408972:
00408972                
0040897a                if (_mm_movemask_epi8(_mm_cmpeq_epi8(zmm2, temp0_4)) != 0)
0040897a                    break
0040897a                
0040897f                int64_t rdi_4 = rdi_2 + r8_1 + 0x10
00408983                r8_1 += 0x10
00408987                rdi_2 = rdi_4 & rsi_1
00408987            
00408993            void* rcx_2 = rcx_1 - (r9_1 << 4)
00408993            
0040899b            if (r11.b != 0)
0040899b                rcx_2 = nullptr
0040899b            
0040899f            void* rcx_3 = rcx_2 - 0x10
0040899f            
004089a6            if (r11.b != 0)
004089a6                rcx_3 = nullptr
004089a6            
004089ad            if (rcx_3 != 0)
004089af                return *(rcx_3 + 8)
004089af        
004089d8        core::option::expect_failed::h95d2432053ef5ebb()
004089d8        noreturn
```

Decompilation ( marinated ) :

```c
004088b0    uint64_t map_lookup_or_passthrough(void* map, uint64_t key, char passthrough_flag)

004088b3        uint64_t flag_byte
004088b3        uint64_t key_slot = flag_byte
004088b4        key_slot = key
004088bc        flag_byte.b = *(map + 0x60) != 0
004088bc        
004088c1        if (flag_byte.b == passthrough_flag)
004089bd            return key
004089bd        
004088cc        if (*(map + 0x48) != 0)
004088e5            uint64_t hash
004088e5            uint64_t match_idx
004088e5            hash, match_idx = core::hash::BuildHasher::hash_one::he6ecdc9711935e32(
004088e5                keys: map + 0x50, value_ptr: &key_slot)
004088ed            char (* ctrl_ptr)[0x10] = *(map + 0x30)
004088f1            int64_t bucket_mask = *(map + 0x38)
004088f8            int64_t probe_idx = bucket_mask & hash
004088ff            uint128_t h2_broadcast = zx.o((hash u>> 0x39).d)
0040890c            h2_broadcast = _mm_shuffle_epi32(
0040890c                _mm_shufflelo_epi16(_mm_unpacklo_epi8(h2_broadcast, h2_broadcast.q), 
0040890c                    0), 
0040890c                0)
00408915            int64_t probe_step = 0
00408918            int32_t empty_seed[0x4]
00408918            int32_t empty_byte_broadcast[0x4] =
00408918                _mm_cmpeq_epi32(empty_seed, empty_seed)
0040891c            uint64_t not_found
0040891c            
0040891c            while (true)
0040891c                char ctrl_group[0x10] = *(ctrl_ptr + probe_idx)
00408929                uint32_t match_mask =
00408929                    _mm_movemask_epi8(_mm_cmpeq_epi8(ctrl_group, h2_broadcast))
00408931                not_found.b = match_mask == 0
00408931                
00408935                if (match_mask != 0)
00408948                    while (true)
00408948                        match_idx =
00408948                            (zx.q(_tzcnt_u32(match_mask)) + probe_idx) & bucket_mask
00408948                        
0040895b                        if (*(&ctrl_ptr[-1] - (match_idx << 4)) == key)
0040898c                            not_found = 0
0040898c                            break
0040898c                        
0040895d                        match_idx = zx.q(match_mask)
00408964                        match_mask.w = (match_idx - 1).d.w & match_idx.w
00408968                        not_found.b = match_mask.w == 0
00408968                        
0040896c                        if (match_mask.w == 0)
0040896c                            goto label_408972
0040896c                    
0040895b                    break
0040895b                
00408972            label_408972:
00408972                
0040897a                if (_mm_movemask_epi8(_mm_cmpeq_epi8(ctrl_group, 
0040897a                        empty_byte_broadcast)) != 0)
0040897a                    break
0040897a                
0040897f                int64_t probe_idx_next = probe_idx + probe_step + 0x10
00408983                probe_step += 0x10
00408987                probe_idx = probe_idx_next & bucket_mask
00408987            
00408993            void* bucket_match_addr = ctrl_ptr - (match_idx << 4)
00408993            
0040899b            if (not_found.b != 0)
0040899b                bucket_match_addr = nullptr
0040899b            
0040899f            void* bucket_ptr = bucket_match_addr - 0x10
0040899f            
004089a6            if (not_found.b != 0)
004089a6                bucket_ptr = nullptr
004089a6            
004089ad            if (bucket_ptr != 0)
004089af                return *(bucket_ptr + 8)
004089af        
004089d8        core::option::expect_failed::h95d2432053ef5ebb(
004089d8            msg_ptr: "no entry found for keyassertion failed: s.len() == "
004089d8        "0/home/renny/doc/work/research/patina/benchmark_183/2021-braintrust/source."
004089d8        "rs", 
004089d8            msg_len: 0x16)
004089d8        noreturn
```

Decompilation types from nacre with correct toolchain (see [thinking output](./signer.json) ) :

```c
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
```

Decompilation with types:

```c

004088b0    uint64_t map_lookup_or_passthrough(struct LookupMap* map, uint64_t key, bool passthrough_flag)

004088b3        uint64_t flag_byte
004088b3        uint64_t key_slot = flag_byte
004088b4        key_slot = key
004088bc        flag_byte.b = map->passthrough != 0
004088bc        
004088c1        if (flag_byte.b == passthrough_flag)
004089bd            return key
004089bd        
004088cc        if (map->lookup.base.table.table.items != 0)
004088e5            uint64_t hash
004088e5            uint64_t match_idx
004088e5            hash, match_idx = core::hash::BuildHasher::hash_one::he6ecdc9711935e32(
004088e5                keys: &map->lookup.base.hash_builder, value_ptr: &key_slot)
004088ed            char (* ctrl_ptr)[0x10] = map->lookup.base.table.table.ctrl.pointer
004088f1            uint64_t bucket_mask = map->lookup.base.table.table.bucket_mask
004088f8            uint64_t probe_idx = bucket_mask & hash
004088ff            uint128_t h2_broadcast = zx.o((hash u>> 0x39).d)
0040890c            h2_broadcast = _mm_shuffle_epi32(
0040890c                _mm_shufflelo_epi16(_mm_unpacklo_epi8(h2_broadcast, h2_broadcast.q), 0), 0)
00408915            int64_t probe_step = 0
00408918            int32_t empty_seed[0x4]
00408918            int32_t empty_byte_broadcast[0x4] = _mm_cmpeq_epi32(empty_seed, empty_seed)
0040891c            uint64_t not_found
0040891c            
0040891c            while (true)
0040891c                char ctrl_group[0x10] = *(ctrl_ptr + probe_idx)
00408929                uint32_t match_mask =
00408929                    _mm_movemask_epi8(_mm_cmpeq_epi8(ctrl_group, h2_broadcast))
00408931                not_found.b = match_mask == 0
00408931                
00408935                if (match_mask != 0)
00408948                    while (true)
00408948                        match_idx = (zx.q(_tzcnt_u32(match_mask)) + probe_idx) & bucket_mask
00408948                        
0040895b                        if (*(&ctrl_ptr[-1] - (match_idx << 4)) == key)
0040898c                            not_found = 0
0040898c                            break
0040898c                        
0040895d                        match_idx = zx.q(match_mask)
00408964                        match_mask.w = (match_idx - 1).d.w & match_idx.w
00408968                        not_found.b = match_mask.w == 0
00408968                        
0040896c                        if (match_mask.w == 0)
0040896c                            goto label_408972
0040896c                    
0040895b                    break
0040895b                
00408972            label_408972:
00408972                
0040897a                if (_mm_movemask_epi8(_mm_cmpeq_epi8(ctrl_group, empty_byte_broadcast))
0040897a                        != 0)
0040897a                    break
0040897a                
0040897f                int64_t probe_idx_next = probe_idx + probe_step + 0x10
00408983                probe_step += 0x10
00408987                probe_idx = probe_idx_next & bucket_mask
00408987            
00408993            void* bucket_match_addr = ctrl_ptr - (match_idx << 4)
00408993            
0040899b            if (not_found.b != 0)
0040899b                bucket_match_addr = nullptr
0040899b            
0040899f            void* bucket_ptr = bucket_match_addr - 0x10
0040899f            
004089a6            if (not_found.b != 0)
004089a6                bucket_ptr = nullptr
004089a6            
004089ad            if (bucket_ptr != 0)
004089af                return *(bucket_ptr + 8)
004089af        
004089d8        core::option::expect_failed::h95d2432053ef5ebb(
004089d8            msg_ptr: "no entry found for keyassertion failed: s.len() == "
004089d8        "0/home/renny/doc/work/research/patina/benchmark_183/2021-braintrust/source.rs", 
004089d8            msg_len: 0x16)
004089d8        noreturn
```

Decompilation by flower ( lifted per block ) :

```rust
use std::collections::HashMap;

struct State {
  map: HashMap<usize, usize>,
  v1: Vec<u8>,
  v2: Vec<u8>,
  flag: bool
}

impl State {
    fn jump(&self, i: usize, fwd: bool) -> usize {
        if self.flag == fwd {
            return i;
        }
        *self.map.get(&i).expect("no entry found for key")
    }
}
```

