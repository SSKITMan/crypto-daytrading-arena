# Improvements Log

Last updated: 2026-02-26

## Completed

1. Strategy framework upgraded in `deploy_router_node.py`.
- `default` is now regime-adaptive and volatility-managed.
- Added new strategy profiles:
  - `cutting_edge`
  - `volatility_managed_momentum`
  - `intraday_hybrid`
- Added stricter hard constraints in prompts (portfolio-first, spot-only, spread awareness, microstructure-aware timing).

2. Market payload enrichment in `coinbase_kafka_connector.py`.
- Added enriched fields in router prompt payload:
  - `spread`, `spread_bps`
  - `best_bid_size`, `best_ask_size`, `last_size`
  - `high_24h`, `low_24h`, `range_24h_bps`, `volume_24h`
  - `bid_depth_10`, `ask_depth_10`
  - `depth_imbalance_10`, `depth_imbalance_delta`
  - `tob_imbalance`, `ofi_ema`

3. L2 microstructure feed support added.
- Connector now subscribes to `level2_batch` in addition to `ticker_batch`.
- Added lightweight in-memory L2 order book model and imbalance calculations.
- Fixed websocket frame-size issue for large L2 snapshots (`max_size=None`).

4. Deterministic execution guardrails added in `trading_tools.py`.
- Cost gate rejects trades when spread is too wide relative to volatility-adjusted thresholds.
- Volatility-targeted max trade notional per order.
- Volatility-adjusted max gross exposure cap.
- Rejections return explicit reasons and suggested max quantity.

5. Risk-state wiring added in live runtime.
- `tools_and_dashboard.py` now updates shared risk state on each price tick.
- `trading_tools.py` now uses risk state in `execute_trade`.

6. Web dashboard created and upgraded.
- Added browser-based dashboard (`web_dashboard.py`, `webui/` assets).
- Added live sections for:
  - Market pulse
  - Agent heatboard
  - Live event tape
  - Agent live position cards (coin value and up/down state)
- Added API endpoints (`/api/state`, `/api/health`) and deduped event feed.

7. Documentation updates completed.
- Updated `README.md` with new strategy options and web dashboard usage.
- Updated `CLI_REFERENCE.md` with expanded strategy options and web dashboard flags.
- Added `STRATEGY_RESEARCH.md` with research references and mapping to implementation.

## In Pipeline

1. Fee and slippage model in execution gate.
- Add exchange fee assumptions and dynamic slippage estimate to pre-trade edge checks.
- Current gate is spread + volatility aware; fee/slippage modeling is the next step.

2. Event-driven decision triggers.
- Add trigger-based decisioning (microstructure shocks, spread compression/expansion, volatility jumps) instead of relying mostly on fixed cycle cadence.

3. Strategy ensemble allocator.
- Run multiple router strategies concurrently and allocate influence/capital by recent out-of-sample utility metrics.

4. Risk kill-switches.
- Add hard daily drawdown caps and cooldown logic (time-based and volatility-based) for automatic de-risking.

5. Robust validation framework.
- Add walk-forward paper-trading harness and benchmark comparison for strategy promotion/rejection.
