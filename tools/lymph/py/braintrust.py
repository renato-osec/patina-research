import lymph

source = open("./braintrust-mock/src/main.rs").read()
g = lymph.analyze(source, root="main", depth=5)

for gg in g:
    for e in gg.edges():
        print(e)
    if(gg.fn_name == "main"):
        print(gg.depends_on("s", "k"))
