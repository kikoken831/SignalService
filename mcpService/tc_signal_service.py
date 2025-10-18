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
import math
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple


TC_MCP_URL = "https://api.tradingcentral.com/mcp/v1"
TC_MCP_TOKEN = "PASTE_YOUR_TOKEN_HERE"
MCP_PROTOCOL_VERSION = "2025-03-26"

TC_TOOL_GET_FOREX_PAIRS = "get_forex_pairs"
TC_TOOL_GET_CURRENCY_VOLATILITY = "get_currency_volatility"

DEFAULT_WATCHLIST = [
    "USDJPY",
    "EURUSD",
    "GBPUSD",
    "AUDUSD",
    "NZDUSD",
    "USDCAD",
    "USDCHF",
]


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


def http_json(
    url: str,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> Tuple[int, Dict[str, str], Any]:
    data = None
    final_headers = headers.copy() if headers else {}

    if body is not None:
        data = json.dumps(body).encode("utf-8")
        final_headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(
        url=url, data=data, method=method, headers=final_headers,
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            response_headers = {k.lower(): v for k, v in response.headers.items()}
            if not raw.strip():
                return response.status, response_headers, None
            return response.status, response_headers, parse_json_loose(raw)

    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        safe_url = mask_token_in_url(url)
        raise RuntimeError(f"HTTP {exc.code} from {safe_url}: {raw}") from exc

    except urllib.error.URLError as exc:
        safe_url = mask_token_in_url(url)
        raise RuntimeError(f"Network error calling {safe_url}: {exc}") from exc


class MCPClient:
    def __init__(self, base_url: str, token: str, timeout: int = 30) -> None:
        self.base_url = base_url.strip()
        self.token = token.strip()
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self._request_id = 0
        self.url = self._build_url_with_token(self.base_url, self.token)

    def _build_url_with_token(self, base_url: str, token: str) -> str:
        parsed = urllib.parse.urlparse(base_url)
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        existing_token = query.get("token", "").strip()
        supplied_token = token.strip()

        if supplied_token and supplied_token != "PASTE_YOUR_TOKEN_HERE":
            query["token"] = supplied_token
        elif existing_token:
            pass
        else:
            raise RuntimeError("Missing Trading Central MCP token. Set TC_MCP_TOKEN.")

        new_query = urllib.parse.urlencode(query)
        return urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
        )

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

def _post_jsonrpc(self, payload: Dict[str, Any], expect_response: bool = True) -> Any:
    status, headers, response = http_json(
        self.url, method="POST", body=payload,
        headers=self._headers(), timeout=self.timeout,
    )
    session_id = headers.get("mcp-session-id")
    if session_id:
        self.session_id = session_id

    if not expect_response:
        return None
    if response is None:
        raise RuntimeError("Empty MCP response")
    if isinstance(response, dict) and response.get("error"):
        raise RuntimeError(f"MCP error: {response['error']}")
    return response

def initialize(self) -> None:
    import logging
    payload = {
        "jsonrpc": "2.0",
        "id": self._next_id(),
        "method": "initialize",
        "params": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "tc-hourly-signal-service", "version": "1.0.0"},
        },
    }
    self._post_jsonrpc(payload, expect_response=True)

    notification = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }
    try:
        self._post_jsonrpc(notification, expect_response=False)
    except Exception as exc:
        logging.debug("MCP initialized notification ignored: %s", exc)

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        response = self._post_jsonrpc(payload, expect_response=True)
        result = response.get("result", response) if isinstance(response, dict) else response
        return unwrap_mcp_tool_result(result)


def unwrap_mcp_tool_result(result: Any) -> Any:
    if isinstance(result, str):
        parsed = parse_json_loose(result)
        if parsed is result:
            return result
        return unwrap_mcp_tool_result(parsed)

    if isinstance(result, dict):
        if result.get("isError"):
            content = result.get("content", [])
            if isinstance(content, list):
                text = "\n".join(
                    item.get("text", "") for item in content if isinstance(item, dict)
                )
                raise RuntimeError(text or f"MCP tool error: {result}")
            raise RuntimeError(f"MCP tool error: {result}")

        if "structuredContent" in result:
            return unwrap_mcp_tool_result(result["structuredContent"])
        if "result" in result:
            return unwrap_mcp_tool_result(result["result"])

        if "content" in result and isinstance(result["content"], list):
            text_parts = []
            for item in result["content"]:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    text_parts.append(item)
            joined = "\n".join(text_parts).strip()
            return unwrap_mcp_tool_result(joined)

    return result


from dataclasses import dataclass

@dataclass
class Quote:
    symbol: str
    bid: float
    ask: float
    source: str

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


class QuoteProvider:
    def get_quote(self, symbol: str) -> Quote:
        provider = os.getenv("QUOTE_PROVIDER", "manual").lower().strip()
        if provider == "manual":
            return self._get_quote_manual(symbol)
        raise RuntimeError(f"Unsupported QUOTE_PROVIDER={provider}")

    def _get_quote_manual(self, symbol: str) -> Quote:
        symbol = normalize_symbol(symbol)
        env_name = f"QUOTE_{symbol}"
        value = os.getenv(env_name)
        if not value:
            raise RuntimeError(
                f"Missing manual quote for {symbol}. "
                f"Set {env_name}, for example: {env_name}=161.50"
            )
        mid = float(value)
        spread_pips = float(os.getenv("MANUAL_SPREAD_PIPS", "1.0"))
        half_spread = (spread_pips * pip_size(symbol)) / 2
        return Quote(
            symbol=symbol,
            bid=mid - half_spread,
            ask=mid + half_spread,
            source="manual",
        )

def _get_quote_mt5(self, symbol: str) -> Quote:
    symbol = normalize_symbol(symbol)
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package not installed. Run: pip install MetaTrader5") from exc

    mt5_symbol = os.getenv(f"MT5_SYMBOL_{symbol}", symbol)

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        if not mt5.symbol_select(mt5_symbol, True):
            raise RuntimeError(f"MT5 could not select symbol {mt5_symbol}: {mt5.last_error()}")
        tick = mt5.symbol_info_tick(mt5_symbol)
        if tick is None:
            raise RuntimeError(f"No MT5 tick for {mt5_symbol}: {mt5.last_error()}")
        return Quote(
            symbol=symbol,
            bid=float(tick.bid),
            ask=float(tick.ask),
            source=f"mt5:{mt5_symbol}",
        )
    finally:
        mt5.shutdown()
