# OpenClaw Trader

An OpenClaw-compatible agent system for automated equity trading via Discord signals.

```
Discord ──► Listener ──signals──► Interpreter ──intents──► Risk Officer ──decisions──► Executor ──► Audit
   bot        filter/dedup          regex parse              sleeve sizing              bracket orders
              SignalEvent           TradeIntent             PortfolioSnapshot          ExecutionReceipt
                                                            RiskDecision
```

The critical execution path is **fully deterministic** — no LLM calls for parsing, sizing, or order decisions.

---

## Architecture

| # | Agent | Entrypoint | Input → Output |
|---|-------|-----------|----------------|
| 1 | Discord Listener | `oct-listener` | Discord message → `SignalEvent` |
| 2 | Interpreter | `oct-interpreter` | `SignalEvent` → `TradeIntent` (regex, no LLM) |
| 3 | Risk Officer | `oct-risk` | `TradeIntent` + IBKR portfolio → `RiskDecision` |
| 4 | IBKR Executor | `oct-executor` | `RiskDecision` → bracket order → `ExecutionReceipt` |

### Event contracts

All events carry a shared `correlation_id` (UUID) that propagates through the full pipeline.
Every event is hashed with Blake2b-32 on creation (`event_hash`) for tamper-evident audit.

```
SignalEvent
  event_id, correlation_id, event_hash, created_at
  source_guild_id, source_channel_id, source_message_id, source_author_id
  raw_text, profile

TradeIntent
  + source_signal_id, ticker, asset_class, direction
  + entry_price, stop_price, take_profit_price, confidence, template_name

RiskDecision
  + source_intent_id, outcome (APPROVED | NEEDS_APPROVAL | REJECTED)
  + approved_ticker, approved_direction, approved_quantity
  + approved_entry/stop/take_profit, position_size_pct, risk_reward_ratio
  + rejection_reasons

ExecutionReceipt
  + source_decision_id, ibkr_order_id, ibkr_perm_id
  + status, filled_quantity, avg_fill_price, commission
  + stop_order_id, take_profit_order_id, is_paper
```

### Signal format

The Interpreter parses plain-English messages using deterministic regex templates.
Recognised patterns (all case-insensitive):

```
BUY AAPL @ 175.50 stop 172.00 target 181.00
LONG MSFT entering 420 sl 415 tp 430
SELL TSLA at 200.00, stop loss 205, take profit 190
SHORT NVDA 800 / 790 / 820
BUY GOOG 140                         # entry only — confidence MEDIUM
```

---

## Prerequisites

- Python 3.11+
- IBKR TWS or Gateway running locally
  - Paper port: `7497` (TWS) / `4002` (Gateway)
  - Live port:  `7496` (TWS) / `4001` (Gateway)
- Discord bot with **MESSAGE CONTENT** privileged intent enabled
- `pip install -e ".[dev]"`

---

## Setup

```bash
# 1. Clone and create virtual environment
git clone <repo>
cd openclawtrader
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure secrets
cp .env.example .env
# Edit .env — at minimum set:
#   DISCORD_BOT_TOKEN, IBKR_ACCOUNT_ID, SLEEVE_VALUE

# 3. Verify install
ruff check .
mypy agents/ cli/ --strict
pytest tests/ -q
```

---

## Running (Paper Mode)

### Single-process (development / paper trial)

Runs all four agents in one asyncio event loop.  Easiest way to verify the
full pipeline before switching to separate processes.

```bash
python -m cli.run_paper --profile discord_equities
# or, after pip install -e .
oct-paper --profile discord_equities
```

### Individual agents (separate processes — recommended for production)

Each agent runs independently.  Use separate terminal tabs, `tmux`, or a
process manager.

```bash
oct-listener    --profile discord_equities   # tab 1
oct-interpreter --profile discord_equities   # tab 2
oct-risk        --profile discord_equities   # tab 3
oct-executor    --profile discord_equities   # tab 4
```

### Process manager (Honcho / Foreman / OpenClaw)

```bash
honcho start     # uses Procfile
foreman start    # same
```

---

## Profiles

Profiles live in `config/profiles/<name>.toml`.  They contain non-secret
strategy configuration that overlays `.env` defaults — never secrets.

```toml
# config/profiles/discord_equities.toml
[risk]
min_position_pct = 0.03
max_position_pct = 0.07
max_open_positions = 10
max_daily_drawdown_pct = 0.05
sleeve_value = 100000.00

[ibkr]
ibkr_port = 7497   # TWS paper
ibkr_client_id = 1
```

```bash
# Switch strategy profile
oct-paper --profile paper          # conservative sizing
oct-paper --profile discord_equities  # standard sizing
```

---

## Environment Variable Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_BOT_TOKEN` | *(required)* | Discord bot token — keep secret |
| `DISCORD_ALLOWED_GUILD_IDS` | `""` (all) | Comma-separated guild IDs to accept |
| `DISCORD_ALLOWED_CHANNEL_IDS` | `""` (all) | Comma-separated channel IDs to accept |
| `DISCORD_ALLOWED_ROLE_IDS` | `""` (all) | Comma-separated role IDs to accept |
| `IBKR_HOST` | `127.0.0.1` | TWS / Gateway host |
| `IBKR_PORT` | `7497` | TWS paper port; `7496` = TWS live |
| `IBKR_CLIENT_ID` | `1` | Executor client ID; Risk Officer uses `+10` |
| `IBKR_ACCOUNT_ID` | *(required)* | IBKR account number — keep secret |
| `SLEEVE_VALUE` | `100000` | USD value of the trading sleeve |
| `MIN_POSITION_PCT` | `0.03` | Minimum position size (3% of sleeve) |
| `MAX_POSITION_PCT` | `0.07` | Maximum position size (7% of sleeve) |
| `MAX_OPEN_POSITIONS` | `10` | Hard cap on concurrent open positions |
| `MAX_DAILY_DRAWDOWN_PCT` | `0.05` | Circuit breaker: halt if daily P&L < −5% |
| `PAPER_MODE` | `true` | **Must be `true` until live checklist is complete** |
| `PROFILE` | `discord_equities` | Default strategy profile |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | `json` | `json` (production) or `console` (development) |
| `LOG_FILE` | *(none)* | Optional path for file logging |

---

## Risk Rules

The Risk Officer enforces all constraints as pure functions (no IBKR call until they pass):

1. **Circuit breaker** — manually halted state blocks all trades
2. **Drawdown** — daily P&L % must be above `−MAX_DAILY_DRAWDOWN_PCT`
3. **Position count** — open positions must be below `MAX_OPEN_POSITIONS`
4. **Ticker validation** — non-tradable symbols (currencies, crypto, generic words) rejected
5. **Entry price** — must be present for execution sizing
6. **Position sizing** — risk-based (1% of sleeve ÷ |entry − stop|), clamped to `[MIN, MAX]_POSITION_PCT`

Outcomes:
- `APPROVED` → auto-executed by Executor
- `NEEDS_APPROVAL` → logged; held for manual confirmation; Executor emits CANCELLED receipt
- `REJECTED` → discarded; Executor emits CANCELLED receipt

---

## Live Mode Gate

**Do not set `PAPER_MODE=false` until all of the following are true:**

- [ ] At least one end-to-end paper run completed with a real Discord signal
- [ ] `pytest tests/ -q` passes with 0 failures
- [ ] Audit log hash chain verified (`verify(receipt)` returns `True`)
- [ ] `IBKR_ACCOUNT_ID` confirmed as a live account (not DU-prefixed paper)
- [ ] `IBKR_PORT` changed to live port (`7496` TWS / `4001` Gateway)
- [ ] `SLEEVE_VALUE` explicitly set — do **not** use full account NAV
- [ ] `MAX_DAILY_DRAWDOWN_PCT` configured and circuit breaker tested
- [ ] Risk Officer manual halt/resume tested (`agent.halt("test")` → `agent.resume()`)
- [ ] Change `PAPER_MODE=false` in `.env` deliberately and with awareness

---

## Security

- Secrets (tokens, account IDs) live in `.env` only — never in code or TOML
- `.env` is in `.gitignore`; `.env.example` contains only placeholders
- IBKR connection is localhost-only (no remote exposure)
- Discord bot uses the official API — no self-bot / user-token scraping
- Each agent uses a dedicated IBKR `clientId` to avoid session conflicts

---

## Verification

```bash
# Linting
ruff check .

# Type checking
mypy agents/ cli/ --strict

# Unit + integration tests
pytest tests/ -q

# With coverage
pytest tests/ -q --cov=. --cov-report=term-missing

# Single batch
pytest tests/integration/test_e2e_paper.py -v
```

Expected output after a clean install:

```
ruff check .            → All checks passed
mypy agents/ cli/ ...   → Success: no issues found
pytest tests/ -q        → N passed, 0 failed
```
