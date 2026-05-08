// Edge case from benchmark_183/2020-toobz/source.rs: a tuple-newtype
// wrapping a heavy stdlib type. `TcpListener` is a repr(transparent)
// chain over an OS file descriptor (Socket → OwnedFd → i32 with niche
// `valid_range`). rustc collapses the whole thing to a single
// BackendRepr::Scalar(Int), so the catalog walker should:
//   - Not strip the wrappers (matches DWARF: Socket/OwnedFd/etc. all
//     stay as `DW_TAG_structure_type` even though they're scalars).
//   - Render the leaf integer as int32_t inside a niche-typed wrapper.
// Expected size: 4 bytes (the fd).
pub struct TSuck {
    pub sucker: std::net::TcpListener,
}
