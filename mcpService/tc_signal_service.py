#!/usr/bin/env python3
"""
tc_signal_service.py

Trading Central MCP FX signal generator.

Transport:
  Streamable HTTP
  JSON-RPC 2.0 over HTTP POST
  MCP Protocol: 2025-03-26
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple


TC_MCP_URL = "https://api.tradingcentral.com/mcp/v1"
TC_MCP_TOKEN = "PASTE_YOUR_TOKEN_HERE"
MCP_PROTOCOL_VERSION = "2025-03-26"


def normalize_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace("-", "").replace("_", "").upper().strip()


def slash_symbol(symbol: str) -> str:
    symbol = normalize_symbol(symbol)
    return f"{symbol[:3]}/{symbol[3:]}"


def pip_size(symbol: str) -> float:
    symbol = normalize_symbol(symbol)
    if symbol.endswith("JPY"):
        return 0.01
    return 0.0001


def price_precision(symbol: str) -> int:
    symbol = normalize_symbol(symbol)
    if symbol.endswith("JPY"):
        return 2
    return 4


def parse_json_loose(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except Exception:
        return value


def mask_token_in_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    if "token" in query:
        query["token"] = "***MASKED***"
    new_query = urllib.parse.urlencode(query)
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
    )
