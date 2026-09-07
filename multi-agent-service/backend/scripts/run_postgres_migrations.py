#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from 繁中代理.PostgreSQL遷移 import 升級到最新


if __name__ == "__main__":
    升級到最新()
