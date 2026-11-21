"""Kept for old habits: `python -m forgeone.store status|backfill|init` == `forgeone db ...`."""
import sys

from ..cli import main

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    main(["db", {"init": "migrate"}.get(cmd, cmd)])
