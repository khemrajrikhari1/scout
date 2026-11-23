#!/usr/bin/env python3
"""Launcher:  python procure.py init | run --phase 1 | status | export | dashboard ..."""
import sys

from gcc_procure.cli import main

if __name__ == "__main__":
    sys.exit(main())
