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
