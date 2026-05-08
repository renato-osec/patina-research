# Agents

This is basically an assembly line for reverse engineering, inspired by some manual experience and by this [great paper and its associated dataset compiled by some Shellphish team members](https://decompetition.io/)

We have a hierarchy of "agents", from the simplest (which doesn't even require inference) to the most complex using the heaviest models:

- warper : applies binja WARP sigs. TODO here we could also apply xrefs for indirect calls to aid navigation for agents down the pipeline
- marinator : very fast and dumb operation : basically a smart bulk rename of functions and variables within functions, can be heavily parallel and requires no validation
- signer : more complex, used to recover function sigs. uses the formal analysis of the binary provided by the `exoskeleton` crates, uses subagents to look for destructors and get more data accesses, and ultimately verifies the underlying type with the guessed Rust type by translating Rust types to memory accesses through the `nacre` crate. validation needs to pass in order for the result to be verfied, and in that case we automatically apply the retyping of the translated Rust type using the binja api
- flower : the most complex agent for now, tasked with the reconstuction of a viable Rust source code for a function. this means:
    - correctly recognizing and "deinlining" functions, which is probably the most complex part and could probably be improved
    - recovering real user variables and logic in the function
  the output of this agent is verified through data flow (recursing down subcalls) : that should be at the very least consistent, as the agent is forced to assign Rust variables to their binary vars of the same name. This isnt fully fool proof, but its pretty good at preventing hallucinations in the decompetition bench 

## common info between agents

Agents at the same level in the pipeline can access a bndb in parallel through a lock. 
Agents in different stages of the pipeline forward info in two ways:

- By actually modifing the binary database : most common and structured way, can convey verified information such as types
- By accessing a common "prior knowledge" : this is used for higher level info which cant be embedded in the bndb, such as Rust level source info

## agent evaluation

TODO
