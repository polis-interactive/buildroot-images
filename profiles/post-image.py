#!/usr/bin/env python3
"""Buildroot entry point for the profile-owned image assembler."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.profiles.image import assemble

if __name__ == '__main__':
    assemble()
