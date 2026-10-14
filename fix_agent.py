#!/usr/bin/env python
"""
Root-level wrapper for running the Autonomous Fix Agent.
Supports execution via:
    python fix_agent.py --max-attempts 4
    python tests_agent/fix_agent.py --max-attempts 4
"""
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tests_agent.fix_agent import main

if __name__ == "__main__":
    main()
