# SignalService

Automated FX signal generation pipeline that combines [Trading Central](https://www.tradingcentral.com/) market intelligence with real-time price feeds from MetaTrader 5.

## Architecture

```
Trading Central MCP API
        │
        ▼
┌──────────────────┐     ┌──────────────────┐
│  Signal Service   │◄────│   MT5 (Docker)   │
│  (Python)         │     │   Price Feeds    │
└────────┬─────────┘     └──────────────────┘
         │
         ▼
  latest_signal.json
```

The system runs in two parts:

- **`mcpService/`** — Python signal generator that connects to Trading Central's MCP endpoint, pulls volatility data for major FX pairs, scores trading opportunities, and outputs structured JSON signals.
- **`mt5docker/`** — Dockerized MetaTrader 5 terminal running via Wine on Linux, exposing a Python API over TCP for live bid/ask quotes.

## Signal Generation

Each hourly cycle:

1. Connects to Trading Central MCP (JSON-RPC 2.0 over Streamable HTTP)
2. Fetches available forex pairs and their volatility profiles
3. Scores each watchlist pair using hourly movement, weekday bias, and event volatility
4. Selects the highest-scoring pair with an available quote
5. Computes entry, stop-loss, and take-profit levels
6. Writes the signal to `latest_signal.json`

### Scoring Model

The confidence score (0.50–0.85) combines:

- **Directional strength** — ratio of price movement bias to true range
- **Volatility strength** — normalized hourly true range
- **Alignment bonus** — agreement between hourly, weekday, and event biases

Risk levels use a configurable risk-reward ratio (default 1.8:1) with stop-loss derived from the pair's true range.

## Quick Start

### Prerequisites

- Python 3.9+
- Docker and Docker Compose (for MT5 price feeds)
- Trading Central MCP API token

### 1. Set Up MT5 (Optional — only needed for live quotes)

```bash
cd mt5docker
cp .env.example .env
# Edit .env with your MT5 broker credentials
docker compose up -d
```

Access the MT5 terminal UI at `http://localhost:6081/vnc.html` and complete the interactive installation.

See [`mt5docker/README.md`](mt5docker/README.md) for full setup instructions.

### 2. Run the Signal Service

```bash
cd mcpService

# Set your Trading Central token
export TC_MCP_TOKEN="your-token-here"

# Option A: Use MT5 for live quotes
export QUOTE_PROVIDER=mt5
python tc_signal_service.py --once

# Option B: Use manual quotes for testing
export QUOTE_PROVIDER=manual
export QUOTE_USDJPY=161.50
python tc_signal_service.py --once

# Option C: Use an HTTP quote endpoint
export QUOTE_PROVIDER=http
export QUOTE_HTTP_URL_TEMPLATE="https://your-broker/api/quote?symbol={symbol}"
python tc_signal_service.py --once
```

### 3. Run Continuously

```bash
# Run hourly, aligned to the top of each hour
python tc_signal_service.py --align-to-hour

# Custom interval (seconds)
python tc_signal_service.py --interval 1800
```

## Configuration

All configuration is via environment variables:

| Variable | Default | Description |
|---|---|---|
| `TC_MCP_TOKEN` | — | Trading Central API token (required) |
| `TC_MCP_URL` | `https://api.tradingcentral.com/mcp/v1` | MCP endpoint URL |
| `TC_MCP_TIMEOUT_SECONDS` | `30` | HTTP timeout for MCP calls |
| `TC_REGION_CODE` | `US` | Region code for forex pair discovery |
| `TC_LANG` | `en` | Language for TC responses |
| `TC_USE_EVENT_VOLATILITY` | `true` | Include economic event volatility in scoring |
| `TC_EVENT_TYPE` | `US:S&P Global Composite PMI Final` | Event type for volatility context |
| `QUOTE_PROVIDER` | `manual` | Quote source: `manual`, `mt5`, or `http` |
| `QUOTE_HTTP_URL_TEMPLATE` | — | URL template for HTTP quotes (use `{symbol}`) |
| `QUOTE_HTTP_BEARER_TOKEN` | — | Bearer token for HTTP quote endpoint |
| `MIN_STOP_PIPS` | `15` | Minimum stop-loss distance in pips |
| `MAX_STOP_PIPS` | `100` | Maximum stop-loss distance in pips |
| `RISK_REWARD_RATIO` | `1.8` | Take-profit to stop-loss ratio |

## Output Format

```json
{
  "signal_id": "tc-usdjpy-20260624-2351",
  "generated_at": "2026-06-24T23:51:24+08:00",
  "valid_until": "2026-06-25T00:51:24+08:00",
  "symbol": "USDJPY",
  "direction": "BUY",
  "confidence": 0.77,
  "entry_type": "market",
  "stop_loss": 161.20,
  "take_profit": 162.05,
  "source": "Trading Central MCP - Forex Volatility",
  "rationale": "USDJPY selected from Trading Central volatility scan; buy bias based on hourly movement of -0.24 pips; expected hourly true range around 25.17 pips; event-volatility bias of 9.88 pips"
}
```

## Default Watchlist

USDJPY, EURUSD, GBPUSD, AUDUSD, NZDUSD, USDCAD, USDCHF

Override with `--watchlist`:

```bash
python tc_signal_service.py --once --watchlist USDJPY,EURUSD,GBPJPY
```

## License

The mt5docker component is based on [hpdeandrade/mt5docker](https://github.com/hpdeandrade/mt5docker) and is licensed under the MIT License. See [`mt5docker/LICENSE.txt`](mt5docker/LICENSE.txt).
