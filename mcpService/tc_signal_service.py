#!/usr/bin/env python3
"""
tc_signal_service.py

Single-file hourly Trading Central MCP FX signal generator.

Auth model:
  Trading Central MCP endpoint expects token in URL query parameter:

    https://<host>/mcp/v1?token=<bearer-token>

  This script DOES NOT send:
    Authorization: Bearer ...

Transport:
  Streamable HTTP
  JSON-RPC 2.0 over HTTP POST
  MCP Protocol: 2025-03-26

What it does:
1. Initializes MCP session
2. Calls get_forex_pairs
3. Calls get_currency_volatility for major FX pairs
4. Optionally calls get_event_volatility
5. Gets live quote from manual / MT5 / HTTP quote source
6. Scores opportunities
7. Outputs strict JSON signal
8. Runs once or loops hourly

Run once:
  python tc_signal_service.py --once

Run hourly:
  python tc_signal_service.py

Manual quote testing, PowerShell:
  $env:QUOTE_PROVIDER="manual"
  $env:QUOTE_USDJPY="161.50"
  python .\\tc_signal_service.py --once

MT5 mode:
  $env:QUOTE_PROVIDER="mt5"
  python .\\tc_signal_service.py --once

HTTP quote mode:
  $env:QUOTE_PROVIDER="http"
  $env:QUOTE_HTTP_URL_TEMPLATE="https://your-broker/api/quote?symbol={symbol}"
  python .\\tc_signal_service.py --once
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


# =============================================================================
# USER CONFIG
# =============================================================================

TC_MCP_URL = "https://api.tradingcentral.com/mcp/v1"

# Set environment variable TC_MCP_TOKEN with your Trading Central token.
# Do NOT include the word "Bearer".
TC_MCP_TOKEN = "PASTE_YOUR_TOKEN_HERE"

MCP_PROTOCOL_VERSION = "2025-03-26"

TC_TOOL_GET_FOREX_PAIRS = "get_forex_pairs"
TC_TOOL_GET_CURRENCY_VOLATILITY = "get_currency_volatility"
TC_TOOL_GET_EVENT_VOLATILITY = "get_event_volatility"

DEFAULT_WATCHLIST = [
    "USDJPY",
    "EURUSD",
    "GBPUSD",
    "AUDUSD",
    "NZDUSD",
    "USDCAD",
    "USDCHF",
]

DEFAULT_EVENT_TYPE = "US:S&P Global Composite PMI Final"


# =============================================================================
# GENERAL HELPERS
# =============================================================================

def now_sgt() -> datetime:
    singapore_tz = timezone(timedelta(hours=8), name="SGT")
    return datetime.now(singapore_tz).replace(microsecond=0)


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


def mean(values: List[float]) -> float:
    clean = [v for v in values if math.isfinite(v)]
    if not clean:
        return 0.0
    return sum(clean) / len(clean)


def same_sign(a: float, b: float) -> bool:
    if a == 0 or b == 0:
        return False
    return (a > 0 and b > 0) or (a < 0 and b < 0)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


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
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )


def parse_sse_json(raw: str) -> Any:
    last_payload = None

    for block in raw.split("\n\n"):
        data_lines = []
        for line in block.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())

        if data_lines:
            joined = "\n".join(data_lines).strip()
            if joined and joined != "[DONE]":
                last_payload = parse_json_loose(joined)

    return last_payload


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
        url=url,
        data=data,
        method=method,
        headers=final_headers,
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            response_headers = {k.lower(): v for k, v in response.headers.items()}

            if not raw.strip():
                return response.status, response_headers, None

            content_type = response_headers.get("content-type", "")

            if "text/event-stream" in content_type:
                return response.status, response_headers, parse_sse_json(raw)

            return response.status, response_headers, parse_json_loose(raw)

    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        safe_url = mask_token_in_url(url)
        raise RuntimeError(f"HTTP {exc.code} from {safe_url}: {raw}") from exc

    except urllib.error.URLError as exc:
        safe_url = mask_token_in_url(url)
        raise RuntimeError(f"Network error calling {safe_url}: {exc}") from exc


# =============================================================================
# MCP CLIENT
# =============================================================================

class MCPClient:
    """
    Minimal MCP Streamable HTTP JSON-RPC client.

    Trading Central expects the token as a URL query parameter,
    not as an Authorization header.
    """

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
            raise RuntimeError(
                "Missing Trading Central MCP token. "
                "Set environment variable TC_MCP_TOKEN."
            )

        new_query = urllib.parse.urlencode(query)

        return urllib.parse.urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query,
                parsed.fragment,
            )
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
            self.url,
            method="POST",
            body=payload,
            headers=self._headers(),
            timeout=self.timeout,
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
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "tc-hourly-signal-service",
                    "version": "1.0.0",
                },
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
            "params": {
                "name": name,
                "arguments": arguments,
            },
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
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict)
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


# =============================================================================
# QUOTE PROVIDERS
# =============================================================================

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
    """
    Provides executable price reference.

    QUOTE_PROVIDER options:
      manual = use environment variable QUOTE_USDJPY, QUOTE_EURUSD, etc.
      mt5    = use local MetaTrader 5 terminal
      http   = use external broker quote endpoint
    """

    def get_quote(self, symbol: str) -> Quote:
        provider = os.getenv("QUOTE_PROVIDER", "manual").lower().strip()

        if provider == "manual":
            return self._get_quote_manual(symbol)
        if provider == "mt5":
            return self._get_quote_mt5(symbol)
        if provider == "http":
            return self._get_quote_http(symbol)

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

    def _get_quote_http(self, symbol: str) -> Quote:
        symbol = normalize_symbol(symbol)
        template = os.getenv("QUOTE_HTTP_URL_TEMPLATE")

        if not template:
            raise RuntimeError("QUOTE_HTTP_URL_TEMPLATE is required when QUOTE_PROVIDER=http")

        url = template.format(
            symbol=symbol,
            slash_symbol=slash_symbol(symbol),
            base=symbol[:3],
            quote=symbol[3:],
        )

        headers = {}
        token = os.getenv("QUOTE_HTTP_BEARER_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        _, _, payload = http_json(url, method="GET", headers=headers)

        bid = deep_find_number(payload, ["bid", "Bid", "BID"])
        ask = deep_find_number(payload, ["ask", "Ask", "ASK"])

        if bid is not None and ask is not None:
            return Quote(symbol=symbol, bid=bid, ask=ask, source="http")

        price = deep_find_number(payload, ["price", "rate", "mid", "last"])

        if price is None:
            raise RuntimeError(f"Could not find bid/ask or price in quote response: {payload}")

        spread_pips = float(os.getenv("HTTP_FALLBACK_SPREAD_PIPS", "1.0"))
        half_spread = (spread_pips * pip_size(symbol)) / 2

        return Quote(
            symbol=symbol,
            bid=price - half_spread,
            ask=price + half_spread,
            source="http-mid",
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


def deep_find_number(payload: Any, keys: List[str]) -> Optional[float]:
    if isinstance(payload, dict):
        for key in keys:
            if key in payload:
                try:
                    return float(payload[key])
                except Exception:
                    pass

        for value in payload.values():
            found = deep_find_number(value, keys)
            if found is not None:
                return found

    if isinstance(payload, list):
        for item in payload:
            found = deep_find_number(item, keys)
            if found is not None:
                return found

    return None


# =============================================================================
# SIGNAL ENGINE
# =============================================================================

@dataclass
class ForexPair:
    symbol_slash: str
    symbol: str
    instrument_id: str
    match_id: str
    precision: int


@dataclass
class CandidateScore:
    pair: ForexPair
    direction: str
    confidence: float
    score: float
    hourly_move_pips: float
    hourly_true_range_pips: float
    weekday_move_pips: float
    event_move_pips: Optional[float]
    event_true_range_pips: Optional[float]
    rationale: str


class SignalEngine:
    def __init__(self, mcp: MCPClient, quote_provider: QuoteProvider, lang: str = "en") -> None:
        self.mcp = mcp
        self.quote_provider = quote_provider
        self.lang = lang

    def discover_pairs(self, region_code: str = "US") -> Dict[str, ForexPair]:
        raw = self.mcp.call_tool(
            TC_TOOL_GET_FOREX_PAIRS,
            {
                "region_code": region_code,
                "lang": self.lang,
            },
        )

        logging.debug("RAW get_forex_pairs response: %s", json.dumps(raw, indent=2, default=str))

        if isinstance(raw, dict):
            pairs_raw = raw.get("forexPairs", [])
        elif isinstance(raw, list):
            pairs_raw = raw
        else:
            raise RuntimeError(f"Unexpected get_forex_pairs response: {raw}")

        logging.debug("Parsed forex pair count: %s", len(pairs_raw))

        pairs: Dict[str, ForexPair] = {}

        for item in pairs_raw:
            if not isinstance(item, dict):
                continue

            symbol_slash = item.get("symbol")
            if not symbol_slash:
                continue

            symbol = normalize_symbol(symbol_slash)

            instrument_id = item.get("instrumentId") or item.get("matchId")
            match_id = item.get("matchId") or f"{symbol_slash}:FOREX"

            if not instrument_id:
                continue

            pairs[symbol] = ForexPair(
                symbol_slash=symbol_slash,
                symbol=symbol,
                instrument_id=instrument_id,
                match_id=match_id,
                precision=int(item.get("precision") or price_precision(symbol)),
            )

        logging.debug("Parsed forex symbols: %s", sorted(pairs.keys()))

        return pairs

    def generate_signal(self, watchlist: List[str]) -> Dict[str, Any]:
        region_code = os.getenv("TC_REGION_CODE", "US")
        pairs = self.discover_pairs(region_code=region_code)

        selected_pairs: List[ForexPair] = []

        for symbol in watchlist:
            normalized = normalize_symbol(symbol)
            if normalized in pairs:
                selected_pairs.append(pairs[normalized])
            else:
                logging.warning("Watchlist pair not found from TC MCP: %s", symbol)

        if not selected_pairs:
            raise RuntimeError("No watchlist pairs found from Trading Central MCP")

        candidates: List[CandidateScore] = []

        for pair in selected_pairs:
            try:
                candidate = self.score_pair(pair)
                candidates.append(candidate)

                logging.info(
                    "Candidate %s %s confidence=%.2f score=%.4f",
                    pair.symbol,
                    candidate.direction,
                    candidate.confidence,
                    candidate.score,
                )
            except Exception as exc:
                logging.warning("Skipping %s due to error: %s", pair.symbol, exc)

        if not candidates:
            raise RuntimeError("No valid candidates generated")

        sorted_candidates = sorted(candidates, key=lambda c: c.score, reverse=True)

        selected_candidate = None
        selected_quote = None

        for candidate in sorted_candidates:
            try:
                selected_quote = self.quote_provider.get_quote(candidate.pair.symbol)
                selected_candidate = candidate
                break
            except Exception as exc:
                logging.warning(
                    "Could not quote %s, trying next candidate: %s",
                    candidate.pair.symbol,
                    exc,
                )

        if selected_candidate is None or selected_quote is None:
            raise RuntimeError(
                "No candidate had an available quote. "
                "If using manual mode, set QUOTE_USDJPY / QUOTE_EURUSD / etc."
            )

        best = selected_candidate
        quote = selected_quote

        entry = quote.ask if best.direction == "BUY" else quote.bid

        stop_loss, take_profit = self.build_risk_levels(
            symbol=best.pair.symbol,
            direction=best.direction,
            entry=entry,
            true_range_pips=best.hourly_true_range_pips,
        )

        generated_at = now_sgt()
        valid_until = generated_at + timedelta(hours=1)

        signal = {
            "signal_id": f"tc-{best.pair.symbol.lower()}-{generated_at:%Y%m%d-%H%M}",
            "generated_at": generated_at.isoformat(),
            "valid_until": valid_until.isoformat(),
            "symbol": best.pair.symbol,
            "direction": best.direction,
            "confidence": round(best.confidence, 2),
            "entry_type": "market",
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "source": "Trading Central MCP - Forex Volatility",
            "rationale": best.rationale,
        }

        return signal

    def score_pair(self, pair: ForexPair) -> CandidateScore:
        volatility = self.mcp.call_tool(
            TC_TOOL_GET_CURRENCY_VOLATILITY,
            {
                "pair_id": pair.instrument_id,
                "lang": self.lang,
            },
        )

        if not isinstance(volatility, dict):
            raise RuntimeError(f"Unexpected volatility response: {volatility}")

        hourly = volatility.get("hourlyVolatility", [])
        weekday = volatility.get("weekdayVolatility", [])

        if not hourly:
            raise RuntimeError("No hourlyVolatility returned")

        utc_now = datetime.now(timezone.utc)
        hour_idx = min(utc_now.hour, len(hourly) - 1)

        hour_now = hourly[hour_idx]
        hour_next = hourly[(hour_idx + 1) % len(hourly)] if len(hourly) > 1 else hour_now

        hourly_move = mean([
            float(hour_now.get("priceMovement", 0.0)),
            float(hour_next.get("priceMovement", 0.0)),
        ])

        hourly_true_range = mean([
            abs(float(hour_now.get("trueRange", 0.0))),
            abs(float(hour_next.get("trueRange", 0.0))),
        ])

        weekday_move = 0.0
        if weekday:
            weekday_idx = min(utc_now.weekday(), len(weekday) - 1)
            weekday_move = float(weekday[weekday_idx].get("priceMovement", 0.0))

        event_move = None
        event_true_range = None

        use_event = os.getenv("TC_USE_EVENT_VOLATILITY", "true").lower() in {
            "1", "true", "yes",
        }

        event_type = os.getenv("TC_EVENT_TYPE", DEFAULT_EVENT_TYPE).strip()

        if use_event and event_type:
            try:
                event_currency_id = (
                    pair.match_id
                    if ":FOREX" in pair.match_id
                    else f"{pair.symbol_slash}:FOREX"
                )

                event_data = self.mcp.call_tool(
                    TC_TOOL_GET_EVENT_VOLATILITY,
                    {
                        "event_type": event_type,
                        "currency_id": event_currency_id,
                        "lang": self.lang,
                    },
                )

                if isinstance(event_data, dict):
                    event_type_vol = event_data.get("eventTypeVolatility", {})
                    one_hour = event_type_vol.get("onehour", {})

                    event_move = float(one_hour.get("priceMovement", 0.0))
                    event_true_range = abs(float(one_hour.get("trueRange", 0.0)))

            except Exception as exc:
                logging.debug("Event volatility unavailable for %s: %s", pair.symbol, exc)

        combined_bias = hourly_move + (0.25 * weekday_move)

        if event_move is not None:
            combined_bias += 0.35 * event_move

        direction = "BUY" if combined_bias >= 0 else "SELL"

        directional_strength = abs(combined_bias) / max(hourly_true_range, 1.0)
        volatility_strength = min(hourly_true_range / 30.0, 1.0)

        alignment_bonus = 0.0

        if event_move is not None and same_sign(combined_bias, event_move):
            alignment_bonus += 0.08
        if same_sign(combined_bias, weekday_move):
            alignment_bonus += 0.05

        confidence = 0.52
        confidence += min(directional_strength * 0.25, 0.20)
        confidence += volatility_strength * 0.08
        confidence += alignment_bonus
        confidence = clamp(confidence, 0.50, 0.85)

        score = directional_strength + (volatility_strength * 0.25) + alignment_bonus

        rationale_parts = [
            f"{pair.symbol} selected from Trading Central volatility scan",
            f"{direction.lower()} bias based on hourly movement of {hourly_move:.2f} pips",
            f"expected hourly true range around {hourly_true_range:.2f} pips",
        ]

        if event_move is not None:
            rationale_parts.append(f"event-volatility bias of {event_move:.2f} pips")

        rationale = "; ".join(rationale_parts)

        return CandidateScore(
            pair=pair,
            direction=direction,
            confidence=confidence,
            score=score,
            hourly_move_pips=hourly_move,
            hourly_true_range_pips=hourly_true_range,
            weekday_move_pips=weekday_move,
            event_move_pips=event_move,
            event_true_range_pips=event_true_range,
            rationale=rationale,
        )

    def build_risk_levels(
        self,
        symbol: str,
        direction: str,
        entry: float,
        true_range_pips: float,
    ) -> Tuple[float, float]:
        symbol = normalize_symbol(symbol)
        pip = pip_size(symbol)
        precision = price_precision(symbol)

        min_stop_pips = float(os.getenv("MIN_STOP_PIPS", "15"))
        max_stop_pips = float(os.getenv("MAX_STOP_PIPS", "100"))
        rr = float(os.getenv("RISK_REWARD_RATIO", "1.8"))

        stop_pips = clamp(true_range_pips * 1.2, min_stop_pips, max_stop_pips)
        take_profit_pips = stop_pips * rr

        if direction == "BUY":
            stop_loss = entry - (stop_pips * pip)
            take_profit = entry + (take_profit_pips * pip)
        else:
            stop_loss = entry + (stop_pips * pip)
            take_profit = entry - (take_profit_pips * pip)

        return round(stop_loss, precision), round(take_profit, precision)


# =============================================================================
# SERVICE LOOP
# =============================================================================

def get_mcp_token() -> str:
    env_token = os.getenv("TC_MCP_TOKEN", "").strip()

    if env_token:
        return env_token

    token = TC_MCP_TOKEN.strip()

    if not token or token == "PASTE_YOUR_TOKEN_HERE":
        raise RuntimeError(
            "Missing Trading Central MCP token. "
            "Set environment variable TC_MCP_TOKEN."
        )

    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    return token


def run_once(output_path: Path, watchlist: List[str]) -> Dict[str, Any]:
    mcp_url = os.getenv("TC_MCP_URL", TC_MCP_URL).strip()
    token = get_mcp_token()

    mcp = MCPClient(
        base_url=mcp_url,
        token=token,
        timeout=int(os.getenv("TC_MCP_TIMEOUT_SECONDS", "30")),
    )

    mcp.initialize()

    engine = SignalEngine(
        mcp=mcp,
        quote_provider=QuoteProvider(),
        lang=os.getenv("TC_LANG", "en"),
    )

    signal = engine.generate_signal(watchlist=watchlist)

    atomic_write_json(output_path, signal)

    print(json.dumps(signal, indent=2))

    return signal


def sleep_until_next_run(interval_seconds: int, align_to_hour: bool) -> None:
    if not align_to_hour:
        time.sleep(interval_seconds)
        return

    current = now_sgt()
    next_hour = (current + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    sleep_seconds = max(1, int((next_hour - current).total_seconds()))

    time.sleep(sleep_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trading Central MCP hourly FX signal service")

    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit",
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=3600,
        help="Loop interval in seconds",
    )

    parser.add_argument(
        "--output",
        default="latest_signal.json",
        help="Output JSON file path",
    )

    parser.add_argument(
        "--watchlist",
        default=",".join(DEFAULT_WATCHLIST),
        help="Comma-separated FX symbols, e.g. USDJPY,EURUSD,GBPUSD",
    )

    parser.add_argument(
        "--align-to-hour",
        action="store_true",
        help="After each run, sleep until the next top of hour",
    )

    parser.add_argument(
        "--log-level",
        default="INFO",
        help="DEBUG, INFO, WARNING, ERROR",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    output_path = Path(args.output)

    watchlist = [
        normalize_symbol(x.strip())
        for x in args.watchlist.split(",")
        if x.strip()
    ]

    while True:
        try:
            logging.info("Generating Trading Central MCP signal")
            run_once(output_path=output_path, watchlist=watchlist)
            logging.info("Signal written to %s", output_path)
        except Exception as exc:
            logging.exception("Signal generation failed: %s", exc)

        if args.once:
            break

        sleep_until_next_run(
            interval_seconds=args.interval,
            align_to_hour=args.align_to_hour,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
