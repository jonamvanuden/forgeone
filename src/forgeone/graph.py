"""Compatibility entry point: `python -m forgeone.graph` still builds VoyagerOne.

New code should use `forgeone build` or forgeone.builder.
"""
from .builder import build_graph, run_build

if __name__ == "__main__":
    run_build("voyagerone")
