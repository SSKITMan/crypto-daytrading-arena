# Crypto Day Trading Strategy Research Notes

This repository now uses a regime-adaptive strategy stack for router prompts.

## Key findings used

1. Crypto returns show strong time-series momentum and investor-attention effects.
- Source: https://www.nber.org/papers/w24877

2. Momentum strategies can crash in panic/high-volatility rebound states; dynamic risk scaling improves risk-adjusted performance.
- Source: https://www.nber.org/papers/w20439

3. Recent crypto-specific evidence shows momentum is crash-prone, and volatility management can mitigate crash risk.
- Source: https://link.springer.com/article/10.1007/s11408-025-00474-9

4. Intraday crypto predictability contains both momentum and reversal, and depends on jumps/liquidity/macro windows.
- Source: https://www.sciencedirect.com/science/article/pii/S1062940822000833

5. Market-state transitions matter: momentum appears concentrated in UP-UP regimes in newer crypto evidence.
- Source: https://www.sciencedirect.com/science/article/abs/pii/S1544612325016101

6. Coinbase market-data capabilities support intraday execution logic, including ticker_batch updates and order-book channels.
- Source: https://docs.cdp.coinbase.com/exchange/websocket-feed/channels

## System updates mapped to research

- `deploy_router_node.py`
  - `default` is now regime-adaptive and volatility-managed.
  - Added advanced strategies:
    - `cutting_edge`
    - `volatility_managed_momentum`
    - `intraday_hybrid`
  - Added stricter prompt constraints (spot-only behavior, portfolio-first, spread awareness).

- `coinbase_kafka_connector.py`
  - Enriched prompt payload with execution/microstructure features:
    - `spread`
    - `spread_bps`
    - `best_bid_size`
    - `best_ask_size`
    - `last_size`
    - `high_24h`
    - `low_24h`
    - `volume_24h`
    - `bid_depth_10`
    - `ask_depth_10`
    - `depth_imbalance_10`
    - `depth_imbalance_delta`
    - `tob_imbalance`
    - `ofi_ema`
  - Added `level2_batch` subscription and lightweight local order-book tracking.

- `trading_tools.py`
  - Added deterministic execution safeguards:
    - spread/cost gate (`spread_bps` vs volatility-adjusted threshold)
    - volatility-targeted per-trade notional cap
    - volatility-adjusted gross exposure cap

## Important caveat

This is still a prompt-driven autonomous system, not a guaranteed profitable trading model. Real-money deployment requires independent validation, slippage modeling, and strict operational risk controls.
