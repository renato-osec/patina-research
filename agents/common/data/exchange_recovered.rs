// Exchange / Clearinghouse types recovered from /home/renny/hl-node.bndb.
//
// Sources of evidence:
//   (T) binary trace via exoskeleton across 12 handler functions
//       (4 with arg-reg base + 8 backtraced from rbx/r14/r12)
//   (J) JSON schema from /home/renny/hl/clearinghouse.json (serde-deserialized
//       Clearinghouse state), field order = serde declaration order
//   (S) panic-string filename leakage: paths under /hl/code_Mainnet/l1/...
//
// Convention: each field is annotated [T:n / J / S] where n is the witness
// count from the binary trace.

use std::collections::BTreeMap;

#[repr(C)]
pub struct Exchange {
    // 2272 bytes that none of our 8 trusted Exchange handlers touch.
    // Almost certainly real Exchange state (mempool / signature tracker /
    // latency sampler per panic-string evidence) but unobserved by us.
    pub _pre_clearinghouse: [u8; 0x8e0],                 // UNOBSERVED

    // Clearinghouse begins at offset 0x8e0. Anchored by 2 strong ptr
    // witnesses at exactly this offset (its first ptr field).
    pub clearinghouse:      Clearinghouse,               // [T:2] @ 0x8e0

    // Post-clearinghouse fields visible in HLIL. Trace witnesses sparse here.
    pub _post_ch_unobserved:     [u8; 0x238],          // 0xaf0..0xd28 (we have no clean obs)

    pub _region_0xd28:           [u8; 0x238],          // [S] panic-msg site references
    pub field_f60_ptr:           *mut NtlVolumeRecord, // [T:1] from end_block (Vec.ptr stride 0x38)
    pub field_f60_len:           usize,                // [T:1]
    pub _gap_f70:                [u8; 0x108],
    pub field_1078:              [u8; 0x18],           // [T:1] block region
    pub field_1090:              u64,                  // [T:1]
    pub field_1098:              u64,                  // [T:1]
    pub _gap_10a0:               [u8; 0x20],
    pub field_10c0:              u32,                  // [T:4] correction: u32 (was u64 in 1 witness)
    pub field_10c8:              u32,                  // [T:2]
    pub _gap_10cc:               [u8; 0x158],
    // Vec at 0x1228 (ptr+len). Pointee witnessed by 2 handlers as a
    // contiguous array of 20-byte entries beginning at +0x110 (so first
    // 0x110 bytes are header — likely hashbrown ctrl region or a header
    // struct), each entry leading with an 8B ptr (stride 0x14). 35 entries
    // observed before the loop bound. Plus a 2B scalar at +0x1ee (probable
    // length / sentinel field).
    pub field_1228_ptr:          *mut Field1228Bucket, // [T:2] PTR
    pub field_1230_len:          usize,                // [T:2] paired len
    pub _gap_1238:               [u8; 0x90],
    pub field_12c8:              [u8; 0xd0],           // [T:1]
    pub field_1398:              u64,                  // [T:2]
    pub field_13a0:              u32,                  // [T:2]
    pub _gap_13a4:               [u8; 0x19],
    pub field_13bd:              u8,                   // [T:1]
    pub field_13be:              [u8; 0x42],           // [T:1]
}

// ─── Clearinghouse ───
//
// JSON (serde-source order):
//   meta, user_states, oracle, total_net_deposit, total_non_bridge_deposit,
//   perform_auto_deleveraging, adl_shortfall_remaining, bridge2_withdraw_fee,
//   daily_exchange_scaled_and_raw_vlms, halted_assets,
//   override_max_signed_distances_from_oracle, max_withdraw_leverage,
//   last_set_global_time, usdc_ntl_scale, isolated_external, isolated_oracle,
//   moh, znfn
//
// Trace anchor: oracle.pxs_ptr is at Exchange offset 0x9c0; Clearinghouse
// begins at Exchange offset 0x8e0; therefore oracle starts at CH offset
// 0xe0. With the JSON declaration order, that places three preceding fields
// (meta, user_states, plus head of oracle's 0xe0 prefix) inside [0..0xe0].

#[repr(C)]
pub struct Clearinghouse {
    pub meta:                                    ClearingMeta,                    // [T+J]
    pub user_states:                             BTreeMap<Address, UserState>,    // [T+J] 24B (root_ptr + height + len)
    pub oracle:                                  Oracle,                          // [T+J] anchored: pxs at CH+0xe0
    pub total_net_deposit:                       Unit,                            // [J] (i64,u64) = 16B
    pub total_non_bridge_deposit:                Unit,                            // [J] 16B
    pub perform_auto_deleveraging:               bool,                            // [J]
    pub adl_shortfall_remaining:                 Unit,                            // [J] 16B
    pub bridge2_withdraw_fee:                    Unit,                            // [J] 16B
    pub daily_exchange_scaled_and_raw_vlms:      Vec<(NaiveDate, ScaledRawVlm)>,  // [J] 24B
    pub halted_assets:                           Vec<u32>,                        // [J]
    pub override_max_signed_distances_from_oracle: Vec<Override>,                 // [J]
    pub max_withdraw_leverage:                   f64,                             // [J] number
    pub last_set_global_time:                    SystemTime,                      // [J] 16B
    pub usdc_ntl_scale:                          Unit,                            // [J] 16B
    pub isolated_external:                       Vec<IsolatedExternal>,           // [J]
    pub isolated_oracle:                         Vec<IsolatedOracle>,             // [J]
    pub moh:                                     Moh,                             // [J] enum { Main { asset_to_recent_oi: Vec<...> }, ... }
    pub znfn:                                    (u64, Option<u64>),              // [J] (u64, null|u64)
}

#[repr(C)]
pub struct Oracle {
    pub pxs:                Vec<OraclePx>,    // [T:6] anchor at CH+0xe0 = Exchange+0x9c0
                                              //       (0x9c0 ptr × 2 + 0x9c8 sca × 2 + 0x9d0 cap)
    pub external_perp_pxs:  Vec<OraclePx>,    // [J]  same shape
    pub err_dur_guard:      ErrDurGuard,      // [J]
}

// OraclePx: 96 bytes per element (stride 0x60 confirmed by 23 iteration
// witnesses inside end_block's pxs copy loop). 92/96 bytes covered.
// All fields are scalar — no internal pointers in OraclePx.
#[repr(C)]
pub struct OraclePx {
    pub px:                       Unit,         // [T:23 / J] @0x00 = (i64, u64), 16B
    pub last_update_time:         SystemTime,   // [T:23 / J] @0x10  16B
    pub last_update_block:        u64,          // [T:23]     @0x20  8B
    pub last_update_seq:          u32,          // [T:23]     @0x28  4B
    _pad_2c:                      [u8; 4],      // unobserved tail of 0x28..0x30
    pub daily_px:                 Unit,         // [T:23 / J] @0x30  16B
    pub daily_update_time:        SystemTime,   // [T:23]     @0x40  16B (mirrors last_update_time)
    pub daily_update_block:       u64,          // [T:23]     @0x50  8B
    pub daily_update_seq:         u32,          // [T:23]     @0x58  4B
    _pad_5c:                      [u8; 4],      // unobserved 0x5c..0x60 stride padding
}

// ─── Helper types ───

pub type Address = [u8; 20];
pub struct Unit(pub i64, pub u64);                          // (signed, scale)
pub struct UserState { /* deeply nested; see clearinghouse.json -> user_states */ }
pub struct ClearingMeta {                                   // [J]
    pub universe:                       Vec<Asset>,
    pub collateral_token:               u32,
    pub collateral_is_aligned_quote:    bool,
    pub collateral_token_name:          String,
    pub margin_table_id_to_margin_table: BTreeMap<u32, MarginTable>,
    pub pdi:                            Pdi,
}
pub struct Asset {                                          // [J]
    pub sz_decimals:                u8,
    pub name:                       String,
    pub margin_table_id:            u32,
    pub margin_mode:                MarginMode,
    pub growth_mode:                GrowthMode,
    pub last_growth_mode_change_time: Option<SystemTime>,
}
pub enum MarginMode    { Normal, Tiered, Disabled }
pub enum GrowthMode    { Disabled, Enabled }
pub struct MarginTable;
pub struct Pdi;
pub struct ScaledRawVlm                                     { pub s: u64, pub r: u64 } // [J]
pub struct NaiveDate;                                       // [J] e.g. "2025-12-20"
pub struct Override;                                        // [J]
pub struct IsolatedExternal;                                // [J]
pub struct IsolatedOracle;                                  // [J]
pub enum   Moh { Main { asset_to_recent_oi: Vec<(u32, u32)> } } // [J]
pub struct ErrDurGuard;                                     // [J]
pub struct NtlVolumeRecord;                                 // 0x38 stride per binary
pub struct SystemTime;                                      // (Duration; 16B)

// Pointee of Exchange.field_1228_ptr. Header 0x110 bytes (unobserved),
// then array of 20-byte entries each leading with an 8-byte pointer.
#[repr(C)]
pub struct Field1228Bucket {
    pub _header:    [u8; 0x110],   // unobserved
    pub entries:    [Field1228Entry; 35],   // [T:2] stride 0x14, 35+ items
    // pub length_or_sentinel: u16 @ +0x1ee  (witnessed but offset is mid-array; flagged)
}

#[repr(C)]
pub struct Field1228Entry {
    pub ptr:        *mut u8,        // [T:2] 8B ptr
    pub _tail:      [u8; 0xc],      // [T:0] unobserved 12B
}                                   // total stride 0x14 = 20B
