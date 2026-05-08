import nacre

f = open("./test/braintrust.rs", "r")

prelude = f.read()

res2 = nacre.c_signature("(this: &LookupMap, key: u64, passthrough_flag: bool) -> u64", prelude)

print(res2["structs"])
print(res2["decl"])
