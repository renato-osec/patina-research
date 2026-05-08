A FLOWer of the sea.

Simple crate with `syn` to analyze the flow in llm generated rust, in order to compare it to the ground truth from the decompilation

## methods
- `anemone.analyze(bv, addr)` : returns data flow analysis of function as a `FlowGraph`

Lifts one function's MLIL-SSA into a flow graph, compatible with lymph

- `anemone.check_compatibility(rust_edges, anem, mapping)` : checks for data flow comptability, returning a verdict and some possible diffs `(bool, list[str])`

Should perhaps add a mode for direct name mapping if we manage to align the agent to only emit existing variable names for the rust source code
