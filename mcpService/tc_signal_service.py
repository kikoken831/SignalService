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
import time
import math
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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
    rationale: str


class SignalEngine:
    def __init__(self, mcp: MCPClient, quote_provider: QuoteProvider, lang: str = "en") -> None:
        self.mcp = mcp
        self.quote_provider = quote_provider
        self.lang = lang

    def discover_pairs(self, region_code: str = "US") -> Dict[str, ForexPair]:
        raw = self.mcp.call_tool(
            TC_TOOL_GET_FOREX_PAIRS,
            {"region_code": region_code, "lang": self.lang},
        )

        logging.debug("RAW get_forex_pairs response: %s", json.dumps(raw, indent=2, default=str))

        if isinstance(raw, dict):
            pairs_raw = raw.get("forexPairs", [])
        elif isinstance(raw, list):
            pairs_raw = raw
        else:
            raise RuntimeError(f"Unexpected get_forex_pairs response: {raw}")

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

        return pairs

def score_pair(self, pair: ForexPair) -> CandidateScore:
    volatility = self.mcp.call_tool(
        TC_TOOL_GET_CURRENCY_VOLATILITY,
        {"pair_id": pair.instrument_id, "lang": self.lang},
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

    combined_bias = hourly_move + (0.25 * weekday_move)
    direction = "BUY" if combined_bias >= 0 else "SELL"

    directional_strength = abs(combined_bias) / max(hourly_true_range, 1.0)
    volatility_strength = min(hourly_true_range / 30.0, 1.0)

    alignment_bonus = 0.0
    if same_sign(combined_bias, weekday_move):
        alignment_bonus += 0.05

    confidence = 0.52
    confidence += min(directional_strength * 0.25, 0.20)
    confidence += volatility_strength * 0.08
    confidence += alignment_bonus
    confidence = clamp(confidence, 0.50, 0.85)

    score = directional_strength + (volatility_strength * 0.25) + alignment_bonus

    rationale = "; ".join([
        f"{pair.symbol} selected from Trading Central volatility scan",
        f"{direction.lower()} bias based on hourly movement of {hourly_move:.2f} pips",
        f"expected hourly true range around {hourly_true_range:.2f} pips",
    ])

    return CandidateScore(
        pair=pair,
        direction=direction,
        confidence=confidence,
        score=score,
        hourly_move_pips=hourly_move,
        hourly_true_range_pips=hourly_true_range,
        weekday_move_pips=weekday_move,
        rationale=rationale,
    )

def build_risk_levels(
    self, symbol: str, direction: str, entry: float, true_range_pips: float,
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
                pair.symbol, candidate.direction,
                candidate.confidence, candidate.score,
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
            logging.warning("Could not quote %s, trying next: %s", candidate.pair.symbol, exc)

    if selected_candidate is None or selected_quote is None:
        raise RuntimeError("No candidate had an available quote.")

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

    return {
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


def get_mcp_token() -> str:
    env_token = os.getenv("TC_MCP_TOKEN", "").strip()
    if env_token:
        return env_token
    token = TC_MCP_TOKEN.strip()
    if not token or token == "PASTE_YOUR_TOKEN_HERE":
        raise RuntimeError("Missing Trading Central MCP token. Set TC_MCP_TOKEN.")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def run_once(output_path: Path, watchlist: List[str]) -> Dict[str, Any]:
    mcp_url = os.getenv("TC_MCP_URL", TC_MCP_URL).strip()
    token = get_mcp_token()

    mcp = MCPClient(
        base_url=mcp_url, token=token,
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    watchlist = [normalize_symbol(s) for s in DEFAULT_WATCHLIST]
    run_once(output_path=Path("latest_signal.json"), watchlist=watchlist)
