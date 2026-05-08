# patina workspace

Agentic decompilation tooling for release Rust binaries, with a formal skeleton for flow and type checking.

Currently integrated with Binary Ninja, gated by the `binja` feature flag, can be extended to more decompilers

## Members

- `chela` - extracts rustc/llvm metadata and crate list (from panic paths) from ELFs
- `carpace` - locate dep crate sources and build rlibs, for later use in type layout computation
- `nacre` - extracts type memory layouts, given rlibs and a specific toolchain. Can be used to verify compatibility between binary level type and Rust type, or to make a catalog of types
- `roe` - shape type algebra using egraphs and the `egg` crate, matches catalog types with observed layouts (doesn't work very well with agents, should revisit later or abandon)
- `exoskeleton` - binja LLIL + offset traces (feature-gated in roe).
- `patina` - original rust orchestrator, will probably deprecate

This is basically the logic behind the scoping:


|          | *flow*  | *typing*    | *bootstrapping*    |
| -------- | ------- | ----------- |-----------         |
| *Rust*   | lymph   | nacre       |    carpace         |
| *Binary* | anemone | exoskeleton |    chela           |


All of these crates exist to power the tools used by the agents in `./agents`, through the use of `maturin` to build python packages from Rust crates

## Agents

Given a binary, we first do some groundwork with the "bootstrapping" tools, in order to get basic stuff like library signatures and compiler versions to be used by tools later.

After that we have a hierarchy of agents to be ran in a loop, from the simplest to the most complex:

- `marinator` : basically "marinates" a binary's database, renaming functions and vars based on the bootstrap done earlier
- `signer` : recovers function signatures, assigning Rust types to them. this step is quite important for bounding later analysis, which given the assumption of safe code, can avoid recursing too deeply by trusting the function signatures produced by this agent. also makes the code way for readable for later stages. this is validated by matching offsets between what the compiler would produce and what is observed
- `flower` : builds back individual Rust blocks, while keeping the same var and func names and signatures (from the last stage), in a way that is consistent with the data flow in HLIL blocks. uses 
- `mastermind` : builds back full functions given all the previous stages. it can be optionally validated with binary diffing (kind of like https://decompetition.io/ does), but I find this to cause way too much overfitting. it's quite free at the moment, but we do enforce that the proven truths from the previous stages are kept

## Requirements

The `binja` feature realies on a headless binaryninja.so . I am in the process of turning this into a plugin for the standard Binja frontend

## Quick start

Requires a working `claude-code` either with an API key or sub, in order to use the Anthropic SDK

```
./utils/dev_install.sh
cd ./agents/marinator
# example, put whatever binary or .bndb and list of functions needed here
python pipeline.py ../../bench/benchmark_stripped_183/2021-braintrust/binary --workers 8 --depth 1 --addresses 0x004088b0 --verbose --timeout 1600
```
