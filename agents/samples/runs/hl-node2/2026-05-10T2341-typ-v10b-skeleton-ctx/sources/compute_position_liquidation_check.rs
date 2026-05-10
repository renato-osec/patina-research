#[derive(Default, Clone)]
pub struct LiqOutcome {
    pub status: u64,
    pub payload: u64,
    pub margin: u64,
}

pub struct OracleEntry {
    pub name: String,
    pub asset_idx: Box<u64>,
    pub has_per_position: u64,
}

pub struct AssetInfo {
    pub clearinghouse_state: u64,
    pub oracle_entries: Vec<OracleEntry>,
}

pub struct Clearinghouse {
    pub spot_status: u64,
    pub spot_payload: u64,
    pub margin: u64,
}

#[derive(Default, Clone)]
pub struct AssetState {
    pub value: u64,
}

pub struct Position {
    pub assets: Vec<AssetState>,
}

fn ch_margin(c: &Clearinghouse) -> u64 { let _ = c; unimplemented!() }
fn ch_spot(c: &Clearinghouse) -> LiqOutcome { let _ = c; unimplemented!() }
fn entry_at(info: &AssetInfo, idx: u64) -> &OracleEntry { let _ = (info, idx); unimplemented!() }
fn entry_count(info: &AssetInfo) -> u64 { let _ = info; unimplemented!() }
fn has_per_pos(e: &OracleEntry) -> u64 { let _ = e; unimplemented!() }
fn idx_from(e: &OracleEntry) -> u64 { let _ = e; unimplemented!() }
fn name_len_of(e: &OracleEntry) -> u64 { let _ = e; unimplemented!() }
fn name_word_of(e: &OracleEntry) -> u32 { let _ = e; unimplemented!() }
fn asset_count(p: &Position) -> u64 { let _ = p; unimplemented!() }
fn asset_at(p: &Position, idx: u64) -> &AssetState { let _ = (p, idx); unimplemented!() }
fn info_state(a: &AssetInfo) -> u64 { let _ = a; unimplemented!() }
fn read_status(o: &LiqOutcome) -> u64 { let _ = o; unimplemented!() }
fn read_payload(o: &LiqOutcome) -> u64 { let _ = o; unimplemented!() }
fn write_outcome(dst: &mut LiqOutcome, status: u64, payload: u64, margin: u64) { let _ = (dst, status, payload, margin); unimplemented!() }
fn default_status_blob() -> LiqOutcome { unimplemented!() }
fn floor_blob(value: u64) -> LiqOutcome { let _ = value; unimplemented!() }

fn fn_0x555556b543a0(out: &mut LiqOutcome, state: &LiqOutcome) { let _ = (out, state); unimplemented!() }
fn fn_0x555556b3f830(out: &mut LiqOutcome, asset: &AssetState) { let _ = (out, asset); unimplemented!() }
fn fn_0x555556b549a0(
    out: &mut LiqOutcome,
    clearinghouse: &Clearinghouse,
    oracle_asset_idx: u64,
    state: &LiqOutcome,
    info: u64,
) { let _ = (out, clearinghouse, oracle_asset_idx, state, info); unimplemented!() }
fn fn_0x555556b65d00(a: &LiqOutcome, b: &LiqOutcome) -> i32 { let _ = (a, b); unimplemented!() }
fn fn_0x5555556c9896(asset_idx: u64, num_assets: u64) -> ! { let _ = (asset_idx, num_assets); unimplemented!() }

fn compute_position_liquidation_check(
    result: &mut LiqOutcome,
    asset_info: &AssetInfo,
    oracle_asset_idx: u64,
    clearinghouse: &Clearinghouse,
    position: &Position,
) {
    let mut liq_margin_buf = LiqOutcome::default();
    let margin_value_1: u64 = 0;
    let mut oracle_asset_state = LiqOutcome::default();

    if oracle_asset_idx == 0 {
        let var_58 = ch_margin(clearinghouse);
        let _ = var_58;
        oracle_asset_state = ch_spot(clearinghouse);
        fn_0x555556b543a0(&mut liq_margin_buf, &oracle_asset_state);
        write_outcome(result, read_status(&liq_margin_buf), read_payload(&liq_margin_buf), margin_value_1);
        return;
    }

    let mut var_88 = default_status_blob();
    let mut var_78: u64 = 0;

    if entry_count(asset_info) <= oracle_asset_idx {
        write_outcome(result, read_status(&var_88), read_payload(&var_88), var_78);
        return;
    }

    let oracle_entries_ptr = asset_info;
    let oracle_stride_offset = oracle_asset_idx;
    let _entry = entry_at(oracle_entries_ptr, oracle_stride_offset);

    if has_per_pos(_entry) == 0 {
        var_88 = default_status_blob();
        var_78 = 0;
        write_outcome(result, read_status(&var_88), read_payload(&var_88), var_78);
        return;
    }

    let num_assets = asset_count(position);
    let asset_idx: u64 = idx_from(_entry);
    if asset_idx >= num_assets {
        fn_0x5555556c9896(asset_idx, num_assets);
    }

    fn_0x555556b3f830(&mut liq_margin_buf, asset_at(position, asset_idx));
    if read_payload(&liq_margin_buf) == 2 {
        var_88 = default_status_blob();
        var_78 = 0;
        write_outcome(result, read_status(&var_88), read_payload(&var_88), var_78);
        return;
    }

    let asset_info_extra = liq_margin_buf.clone();
    oracle_asset_state = asset_info_extra;
    var_88 = default_status_blob();
    var_78 = 0;
    if read_status(&oracle_asset_state) == 2 {
        write_outcome(result, read_status(&var_88), read_payload(&var_88), var_78);
        return;
    }

    fn_0x555556b549a0(
        &mut liq_margin_buf,
        clearinghouse,
        oracle_asset_idx,
        &oracle_asset_state,
        info_state(asset_info),
    );

    let mut hype_liq_status_1 = read_status(&liq_margin_buf);
    let mut margin_value_2: u64 = 0;
    let mut margin_value: u64 = 0;
    if hype_liq_status_1 != 3 {
        margin_value = margin_value_1;
    }
    if margin_value == 0 {
        hype_liq_status_1 = 2;
    }
    if margin_value >= 0x28f5c28f5c28f5c {
        hype_liq_status_1 = 2;
    }
    let rdx_1 = read_payload(&liq_margin_buf);
    if margin_value < 0x28f5c28f5c28f5c {
        margin_value_2 = margin_value;
    }

    if oracle_asset_idx == 1 {
        let asset_name_ptr = name_word_of(_entry);
        let temp0 = name_len_of(_entry);
        if temp0 == 4 && (asset_name_ptr == 0x45505948 || asset_name_ptr == 0x4a51585a) {
            let mut hype_liq_status = LiqOutcome::default();
            write_outcome(&mut hype_liq_status, hype_liq_status_1, rdx_1, margin_value_2);
            let var_40 = rdx_1;
            let _ = var_40;
            let margin_value_3 = margin_value_2;
            let hype_margin_floor: u64 = 0x2386f26fc10000;
            liq_margin_buf = floor_blob(hype_margin_floor);
            let _ = margin_value_3;
            let min_margin_ptr: &LiqOutcome = if fn_0x555556b65d00(&hype_liq_status, &liq_margin_buf) == 1 {
                &liq_margin_buf
            } else {
                &hype_liq_status
            };
            write_outcome(result, read_status(min_margin_ptr), read_payload(min_margin_ptr), margin_value_2);
            return;
        }
    }

    write_outcome(result, hype_liq_status_1, rdx_1, margin_value_2);
}