#!/usr/bin/env python3
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
os.execv(sys.executable, [sys.executable, str(root / "x86qw.pyz"), *sys.argv[1:]])
