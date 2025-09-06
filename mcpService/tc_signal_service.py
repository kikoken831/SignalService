#!/usr/bin/env python3
"""
tc_signal_service.py

Trading Central MCP FX signal generator - initial scaffold.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from typing import Any, Dict, Optional


TC_MCP_URL = "https://api.tradingcentral.com/mcp/v1"
TC_MCP_TOKEN = "PASTE_YOUR_TOKEN_HERE"
MCP_PROTOCOL_VERSION = "2025-03-26"


def normalize_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace("-", "").replace("_", "").upper().strip()


def pip_size(symbol: str) -> float:
    symbol = normalize_symbol(symbol)
    if symbol.endswith("JPY"):
        return 0.01
    return 0.0001
