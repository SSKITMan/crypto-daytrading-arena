# Repository Guidelines

## Project Structure & Module Organization
This repository is a flat Python project focused on runnable service scripts at the root.
- Core runtime scripts: `coinbase_connector.py`, `tools_and_dashboard.py`, `deploy_chat_node.py`, `deploy_router_node.py`, `response_viewer.py`.
- Trading/tool logic: `trading_tools.py` and Coinbase/Kafka integration helpers (`coinbase_consumer.py`, `coinbase_kafka_connector.py`).
- Docs and references: `README.md` (architecture + quickstart), `CLI_REFERENCE.md` (flags), `assets/demo.gif`.
- Dependency manifests: `pyproject.toml`, `uv.lock`, `requirements.txt`.

## Build, Test, and Development Commands
- `uv sync`: install pinned dependencies from `uv.lock`.
- `pip install -r requirements.txt`: alternative environment setup without `uv`.
- `uv run python coinbase_connector.py --bootstrap-servers <broker>`: stream Coinbase data into Kafka.
- `uv run python tools_and_dashboard.py --bootstrap-servers <broker>`: start shared trading tools + dashboard.
- `uv run python deploy_chat_node.py --name <node> --model-id <model> --bootstrap-servers <broker>`: deploy inference node.
- `uv run python deploy_router_node.py --name <agent> --chat-node-name <node> --strategy <strategy> --bootstrap-servers <broker>`: deploy agent router.

## Coding Style & Naming Conventions
- Target Python `>=3.10` and use 4-space indentation.
- Use `snake_case` for functions/variables/files, `UPPER_SNAKE_CASE` for constants, and descriptive CLI flag names.
- Keep modules focused on one runtime responsibility (connector, router, chat node, dashboard).
- Prefer explicit type hints and short docstrings on non-trivial functions.

## Testing Guidelines
There is currently no committed automated test suite in this repo.
- For new logic, add tests under `tests/` using `pytest` (file pattern: `test_*.py`).
- Minimum validation before PR: run the main scripts you changed and verify broker connectivity, agent responses, and trade execution paths.
- Include exact commands used for manual verification in the PR description.

## Commit & Pull Request Guidelines
- Recent history favors short, imperative commit subjects (for example, `Update README.md`), with occasional prefixes like `docs:`.
- Keep commits focused and scoped to one change.
- PRs should include: what changed, why, impacted scripts, verification steps, and screenshots/log snippets for dashboard or CLI behavior changes.

## Security & Configuration Tips
- Copy `.env.example` to `.env` and set `KAFKA_BOOTSTRAP_SERVERS` and `OPENAI_API_KEY`.
- Never commit secrets or broker credentials.
- Use environment variables instead of hardcoding API keys in scripts.
