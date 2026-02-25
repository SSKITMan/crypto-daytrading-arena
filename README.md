# Crypto Daytrading Agents Arena

A multi-agent crypto trading arena where AI agents compete against each other using live crypto market data from Coinbase. Each agent consumes a livestream of market data, has access to its portfolio and calculator, and executes trades autonomously: coordinated through Calfkit's event-driven architecture.

## Architecture

```
                         ┌──────────────────┐
                         │ Agent Router(s)  │
                         └──────────────────┘
                                  ▲
                                  │
                                  ▼
Live Market          ┌────────────────┐      ┌──────────────────┐
Data Stream  ──▶     │  Kafka Broker  │◀────▶│  ChatNode(s)     │
                     └────────────────┘      │  (LLM Inference) │
                                  ▲          └──────────────────┘
                                  │
                                  ▼
                       ┌────────────────────────┐
                       │ Tools & Dashboard      │
                       │ (Trading Tools + UI)   │
                       └────────────────────────┘
```

Each box is an independent process communicating only through Kafka. These can run on the same machine, on separate servers, or across different cloud regions.

Key design points:
- **Per-agent model selection**: Each agent targets a named stateless ChatNode, so different agents can use different LLMs or share LLMs.
- **Fan-out via consumer groups**: Every agent independently receives every market data update.
- **Shared tools via ToolContext**: A single deployed set of trading tools serves all agents — each tool resolves the calling agent's identity at runtime.
- **Dynamic agent accounts**: Agents appear on the dashboard automatically on their first trade — no pre-registration needed.

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) — fast Python package manager
- A running Kafka broker (see [Calfkit docs](https://github.com/calf-ai/calf-sdk) for setup)
- An API key (and optionally base url) for your LLM provider

### Install uv

If you don't have `uv` installed:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Or via Homebrew
brew install uv
```

After installation, restart your terminal.

### Start the broker
- The broker is what orchestrates all the agents and allows for realtime data streaming between all components
- Follow instructions for broker setup here (see [Calfkit docs](https://github.com/calf-ai/calf-sdk) for setup)

## Quickstart

Install dependencies:

```bash
uv sync
```

Then launch each component in its own terminal. All components will access the same Kafka broker.

### 1. Start the Coinbase connector



```bash
uv run python coinbase_connector.py --bootstrap-servers <broker-url>
```

Optional: You can use the `--interval <seconds>` flag which controls how often agents are fed market data (default: 60s). Note that candle data is only updated every 60 seconds due to Coinbase API restrictions, so intervals below a minute mean agents will receive updated live pricing (bid/ask spread, ~5s granularity) but the same candle data.

### 2. Deploy tools & dashboard

```bash
uv run python tools_and_dashboard.py --bootstrap-servers <broker-url>
```

### 3. Deploy a ChatNode (LLM inference)

Deploy one ChatNode for each model you'd like to try.
Note: ChatNodes are stateless so multiple agents can share the same ChatNode.

```bash
# OpenAI model
uv run python deploy_chat_node.py \
    --name <unique-name-of-chatnode> --model-id <openai-model-id> --bootstrap-servers <broker-url> \
    --reasoning-effort <optional-reasoning-level> --api-key <api-key>

# Or, OpenAI-compatible provider (e.g. DeepInfra, Gemini, etc.)
uv run python deploy_chat_node.py \
    --name <unique-name-of-chatnode> --model-id <model-id> --bootstrap-servers <broker-url> \
    --base-url <llm-provider-base-url> --reasoning-effort <optional-reasoning-level> --api-key <api-key>
```

### 4. Deploy agent routers

Deploy one router per agent. Each targets a ChatNode you define by name and uses a trading strategy you can edit in `deploy_router_node.py`. See `deploy_router_node.py` for the full system prompts.

```bash
uv run python deploy_router_node.py \
    --name <unique-agent-name> --chat-node-name <name-of-chatnode> \
    --strategy <strategy> --bootstrap-servers <broker-url>
```

Once agent routers are deployed, market data flows to them agents and trades appear on the dashboard.

### 5. (Optional) Start the response viewer

A live dashboard that shows all agent activity — tool calls, text responses, and tool results — as they happen.

```bash
uv run python response_viewer.py --bootstrap-servers <broker-url>
```

## CLI Reference

### deploy_chat_node.py

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--name` | Yes | — | ChatNode name (becomes topic `ai_prompted.<name>`) |
| `--model-id` | Yes | — | Model ID (e.g. `gpt-5-nano`, `deepseek-chat`) |
| `--bootstrap-servers` | Yes | — | Kafka broker address |
| `--base-url` | No | OpenAI | Base URL for OpenAI-compatible providers |
| `--api-key` | No | `$OPENAI_API_KEY` | API key for the provider |
| `--max-workers` | No | `1` | Concurrent inference workers |
| `--reasoning-effort` | No | `None` | For reasoning models (e.g. `"low"`) |

### deploy_router_node.py

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--name` | Yes | — | Agent name (consumer group + identity) |
| `--chat-node-name` | Yes | — | Name of the deployed ChatNode to target |
| `--strategy` | Yes | — | Trading strategy: `default`, `momentum`, `brainrot`, or `scalper` |
| `--bootstrap-servers` | Yes | — | Kafka broker address |

## Available Tools

| Tool | Description |
|------|-------------|
| `execute_trade` | Buy or sell a crypto product at the current market price |
| `get_portfolio` | View cash, open positions, cost basis, P&L, and average time held |
| `calculator` | Evaluate math expressions for position sizing, P&L calculations, etc. |

## Configuration

| File | Constant | Default | Description |
|------|----------|---------|-------------|
| `trading_tools.py` | `INITIAL_CASH` | `100_000.0` | Starting cash balance per agent |
| `coinbase_kafka_connector.py` | `DEFAULT_PRODUCTS` | 3 products | Products tracked by the price feed |

