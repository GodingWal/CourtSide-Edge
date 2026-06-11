"""Make the repo root importable for shared/ tests regardless of how pytest
is invoked (mirrors agents/conftest.py)."""

import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
