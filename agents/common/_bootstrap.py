# Per-agent path bootstrap. Each agent's entry-point script does:
#
#   import sys; from pathlib import Path
#   sys.path[:0] = [str(Path(__file__).resolve().parent),
#                   str(Path(__file__).resolve().parent.parent / "common")]
#
# That makes `from agent import Agent`, `from tools import binja`, etc. work
# from any agents/<name>/script.py without packaging gymnastics.
#
# This file is only documentation; nothing imports it.
