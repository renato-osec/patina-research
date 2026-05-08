import anemone
import lymph
import binaryninja as bn
import os

os.environ["BN_DISABLE_USER_PLUGINS"] = "1"

bv = bn.load("../agents/marinated.bndb")

fg = anemone.analyze(bv, 0x0040ada0)


print(fg.depends_on("buf_cap", "listener"))

"""
for e in fg.edges():
    print(e)
"""
