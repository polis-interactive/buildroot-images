"""Run with `uv run src/main.py`; no default board or action."""
from pathlib import Path
import sys

# Keep package imports working when invoked directly as a script.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
