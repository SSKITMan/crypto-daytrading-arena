# CLI Reference

## deploy_chat_node.py

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--name` | Yes | - | ChatNode name (becomes topic `ai_prompted.<name>`) |
| `--model-id` | Yes | - | Model ID (e.g. `gpt-5-nano`, `deepseek-chat`) |
| `--bootstrap-servers` | Yes | - | Kafka broker address |
| `--base-url` | No | OpenAI | Base URL for OpenAI-compatible providers |
| `--api-key` | No | `$OPENAI_API_KEY` | API key for the provider |
| `--max-workers` | No | `1` | Concurrent inference workers |
| `--reasoning-effort` | No | `None` | For reasoning models (e.g. `"low"`) |

## deploy_router_node.py

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--name` | Yes | - | Agent name (consumer group + identity) |
| `--chat-node-name` | Yes | - | Name of the deployed ChatNode to target |
| `--strategy` | Yes | - | Trading strategy: `default`, `cutting_edge`, `volatility_managed_momentum`, `intraday_hybrid`, `momentum`, `scalper`, or `brainrot` |
| `--bootstrap-servers` | Yes | - | Kafka broker address |

## web_dashboard.py

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--bootstrap-servers` | Yes | - | Kafka broker address |
| `--host` | No | `127.0.0.1` | HTTP bind host |
| `--port` | No | `8088` | HTTP bind port |
