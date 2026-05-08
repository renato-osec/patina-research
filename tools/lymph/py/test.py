import lymph

source = open("./mock-crate/src/main.rs").read()
g = lymph.analyze(source, depth=1)

for gg in g:
    for e in gg.edges():
        print(e)
    if(gg.fn_name == "main"):
        print(gg.depends_on("b", "a"))
