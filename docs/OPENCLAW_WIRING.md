# OpenClaw Wiring

How the four-agent pipeline maps to OpenClaw concepts and how to run each agent
as an independent OpenClaw-managed process.

## Architecture overview

```
Discord  ──►  Listener  ──signals──►  Interpreter  ──intents──►  Risk Officer  ──decisions──►  Executor
  bot          Agent 1                   Agent 2                    Agent 3                    Agent 4
              SignalEvent               TradeIntent                RiskDecision             ExecutionReceipt
                                                                   PortfolioSnapshot
```

The critical path (Interpreter → Risk Officer → Executor) is **fully deterministic**:
no LLM calls for any sizing, validation, or order decision. The LLM path in the
Interpreter is limited to diagnostic/advisory output only (see Batch 3 notes).

---

## Profiles

Each strategy configuration is a TOML profile in `config/profiles/<name>.toml`.

```
config/profiles/
├── discord_equities.toml   # standard equity signals, 3–7% sizing, TWS paper port
└── paper.toml              # conservative sizing for testing
```

Profiles contain **only non-secret** parameters (risk %, IBKR port, client IDs).
Secrets (tokens, account IDs) stay in `.env` and are never loaded from TOML.

Switch profile with `--profile <name>` or set `PROFILE=<name>` in `.env`.

Profile directory can be overridden via `OPENCLAWTRADER_CONFIG_DIR=<abs-path>` — useful
when running from a different working directory or after a system-level install.

---

## Running as separate processes

### Option A — `oct-*` CLI entrypoints (recommended for production)

Each agent runs in its own process with its own IBKR `clientId`.

```bash
# In separate terminal tabs / tmux panes:
oct-listener    --profile discord_equities
oct-interpreter --profile discord_equities
oct-risk        --profile discord_equities
oct-executor    --profile discord_equities
```

### Option B — Procfile (Honcho / Foreman)

```bash
honcho start      # or: foreman start
```

The `Procfile` at the repo root defines all four processes.

### Option C — Single process (paper / dev only)

```bash
oct-paper --profile discord_equities
```

All four agents run in the same asyncio event loop. **Do not use in production.**

---

## OpenClaw skills setup

To create SKILL.md files for each oct-* entrypoint:

```bash
# From the package root:
./scripts/setup_openclaw_wiring.sh

# Or with custom state dir and venv:
./scripts/setup_openclaw_wiring.sh --state-dir ~/.openclawdiscord --venv .venv
```

This writes skills to `$OPENCLAW_STATE_DIR/workspace/skills/` (default `~/.openclawdiscord`):

- `run-listener` — Discord Listener agent
- `run-interpreter` — Interpreter agent
- `run-risk` — Risk Officer agent
- `run-executor` — IBKR Executor agent
- `run-paper` — All agents in one process (paper mode)
- `run-review` — Review Dispatcher (NEEDS_APPROVAL delivery)

Each skill documents the exact command and prerequisites.

---

## OpenClaw workspace mapping

Each agent maps to an OpenClaw workspace / service:

| Agent | OpenClaw workspace / service | `clientId` |
|-------|------------------------------|-----------|
| Discord Listener | `workspace/` or `workspace-listener/` | — |
| Interpreter | `workspace/` (same process, or split) | — |
| Risk Officer | `workspace-sentinel/` | `ibkr_client_id + 10` |
| IBKR Executor | `workspace-forge/` | `ibkr_client_id` (default 1) |

When running under OpenClaw with `--profile <name>`, set `PROFILE=<name>` in the
workspace's environment or `.env` file. OpenClaw's profile isolation maps naturally
to this pattern.

---

## IBKR client ID allocation

Agents use separate IBKR `clientId` values to avoid session conflicts:

| Agent | clientId |
|-------|---------|
| Executor | `IBKR_CLIENT_ID` (default 1) |
| Risk Officer (PortfolioAdapter) | `IBKR_CLIENT_ID + 10` (default 11) |

If running multiple strategy profiles simultaneously, assign non-overlapping
`IBKR_CLIENT_ID` values in each profile TOML (e.g. `ibkr_client_id = 1`, `21`, `41`).

---

## Paper mode gate

`PAPER_MODE=true` is the default. The executor writes `is_paper: true` on every
`ExecutionReceipt`. **Do not set `PAPER_MODE=false`** until all items in the
"Live Mode Gate" checklist (see `README.md`) are satisfied.

---

## Correlation ID flow

A single `correlation_id` (UUID) is created at Discord message ingest and
propagated unchanged through every downstream event:

```
SignalEvent.correlation_id
  └──► TradeIntent.correlation_id
         └──► RiskDecision.correlation_id
                └──► ExecutionReceipt.correlation_id
```

Every log line emitted by any agent includes `correlation_id`, enabling full
pipeline tracing in any log aggregation system (e.g. `grep correlation_id=<id>`).

---

## Audit hash chain

Every pipeline event is stamped with a Blake2b-32 digest (`event_hash`) via
`audit.hasher.stamp()`. Verify tamper-evidence on any event:

```python
from audit.hasher import verify
assert verify(receipt)  # True if ExecutionReceipt was not modified after stamping
```

---

## Environment variable reference (quick)

| Variable | Purpose |
|----------|---------|
| `OPENCLAW_STATE_DIR` | OpenClaw home (default `~/.openclawdiscord`) |
| `LEDGER_PATH` | JSONL file for persistent audit ledger (optional) |
| `HEARTBEAT_INTERVAL_SECONDS` | Agent heartbeat log interval (default 30) |
| `DISCORD_BOT_TOKEN` | Official bot token |
| `IBKR_HOST` / `IBKR_PORT` / `IBKR_CLIENT_ID` / `IBKR_ACCOUNT_ID` | IBKR connection |
| `SLEEVE_VALUE` | USD value of the trading sleeve |
| `MIN_POSITION_PCT` / `MAX_POSITION_PCT` | Risk sizing bounds (e.g. 0.03 / 0.07) |
| `MAX_OPEN_POSITIONS` | Position count cap |
| `MAX_DAILY_DRAWDOWN_PCT` | Circuit-breaker threshold |
| `PAPER_MODE` | `true` (default) or `false` (live — requires checklist) |
| `PROFILE` | Strategy profile name (default `discord_equities`) |
| `OPENCLAWTRADER_CONFIG_DIR` | Absolute path to profiles dir (optional override) |
| `LOG_LEVEL` / `LOG_FORMAT` / `LOG_FILE` | Logging config |

See `.env.example` for the full list with defaults and comments.
