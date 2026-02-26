"""Deploy a single named AgentRouterNode for the daytrading arena.

Each router subscribes to the shared ``agent_router.input`` topic with its
own consumer group, so every agent receives every market tick independently.
The ``--chat-node-name`` flag targets a specific named ChatNode for LLM
inference.

Example:
    uv run python deploy_router_node.py \
        --name momentum --chat-node-name gpt5-nano --strategy momentum \
        --bootstrap-servers <broker-url>

    uv run python deploy_router_node.py \
        --name adaptive --chat-node-name gpt5-nano --strategy cutting_edge \
        --bootstrap-servers <broker-url>
"""

import argparse
import asyncio
import sys

from calfkit.broker.broker import BrokerClient
from calfkit.nodes.agent_router_node import AgentRouterNode
from calfkit.nodes.chat_node import ChatNode
from calfkit.runners.service import NodesService
from calfkit.stores.in_memory import InMemoryMessageHistoryStore
from trading_tools import calculator, execute_trade, get_portfolio

_REASONING_ADDENDUM = (
    "\n\nRespond with this exact ending format:\n"
    "Decision: BUY | SELL | HOLD\n"
    "Reasoning: <1-3 lines>\n"
    "Risk: <position size, invalidation, and why now>"
)

_BASE_CONSTRAINTS = (
    "Hard constraints:\n"
    "- You are spot-only in this arena. You cannot short. Selling is only valid for assets you hold.\n"
    "- Always call get_portfolio first before trading.\n"
    "- Prefer liquid coins and avoid over-concentration in one asset.\n"
    "- Skip trades when spread is wide relative to recent ranges.\n"
    "- Use microstructure fields (spread_bps, depth_imbalance_10, ofi_ema, tob_imbalance) for timing.\n"
    "- Use calculator for sizing when needed.\n"
)

STRATEGIES: dict[str, str] = {
    "default": (
        "You are a regime-adaptive crypto day trader focused on risk-adjusted returns. "
        "Use market regime detection + volatility-managed sizing + momentum/reversal switching.\n\n"
        "Research-aligned operating model:\n"
        "1) Regime classification per coin using 1m/5m/15m candles:\n"
        "- Trend regime: 1m, 5m, and 15m direction aligned.\n"
        "- Reversal regime: sharp 1m move against 5m/15m context, especially after jumps.\n"
        "- Panic regime: very high volatility and unstable direction; de-risk.\n"
        "2) Signal selection:\n"
        "- In persistent up-up trend, favor momentum continuation entries.\n"
        "- In jumpy/mean-reverting windows, reduce momentum exposure and favor trim/exit.\n"
        "- In panic/high-vol windows, prioritize capital protection and wait for clarity.\n"
        "3) Volatility-managed sizing:\n"
        "- Scale down size when short-horizon volatility or spread widens.\n"
        "- Scale up only when trend is clear and transaction friction is low.\n"
        "4) Execution discipline:\n"
        "- Do nothing when edge is weak.\n"
        "- Enter incrementally; avoid all-in behavior.\n"
        "- Define invalidation before every new position.\n\n"
        f"{_BASE_CONSTRAINTS}"
    )
    + _REASONING_ADDENDUM,
    "cutting_edge": (
        "You are a state-of-the-art crypto intraday trader combining:\n"
        "- time-series momentum in persistent trends,\n"
        "- intraday reversal handling after jumps,\n"
        "- volatility-managed exposure to reduce crash risk,\n"
        "- microstructure-aware execution using bid/ask spread and size.\n\n"
        "Process each cycle:\n"
        "A) Portfolio and risk state first (get_portfolio).\n"
        "B) Build a per-coin score from trend alignment, spread quality, and volatility state.\n"
        "C) Trade only top-scoring opportunities. If no clear edge, HOLD.\n"
        "D) Position sizing:\n"
        "- Max new position per trade: 10-20% of portfolio value depending on volatility.\n"
        "- Max gross invested capital: 70-85% (higher only in calm, high-conviction trends).\n"
        "E) Exit logic:\n"
        "- Trim or close when momentum weakens, spread deteriorates, or reversal signs appear.\n"
        "- Cut losers quickly; let winners run only while regime remains supportive.\n\n"
        f"{_BASE_CONSTRAINTS}"
    )
    + _REASONING_ADDENDUM,
    "volatility_managed_momentum": (
        "You are a volatility-managed momentum trader.\n\n"
        "Core objective: capture trend while avoiding momentum crashes.\n"
        "Rules:\n"
        "- Trade only with confirmed trend alignment (1m+5m+15m direction).\n"
        "- Reduce size aggressively when volatility rises or after large jumps.\n"
        "- In panic/rebound conditions, prefer HOLD or de-risking over fresh momentum entries.\n"
        "- Concentrate only when both trend and execution quality (tight spread) are strong.\n"
        "- Never average down into a broken trend.\n\n"
        f"{_BASE_CONSTRAINTS}"
    )
    + _REASONING_ADDENDUM,
    "intraday_hybrid": (
        "You are an intraday hybrid trader that switches between momentum and reversal.\n\n"
        "Regime switch logic:\n"
        "- Use momentum in smooth directional markets with stable spread.\n"
        "- Use mean-reversion behavior (trim profits / avoid chasing) after outsized short-term jumps.\n"
        "- Treat macro-event-like volatility spikes as risk-off periods.\n"
        "Execution:\n"
        "- Prefer smaller, more frequent adjustments instead of binary all-in/all-out flips.\n"
        "- Only trade when expected edge exceeds spread and slippage costs.\n\n"
        f"{_BASE_CONSTRAINTS}"
    )
    + _REASONING_ADDENDUM,
    "momentum": (
        "You are a momentum day trader in crypto markets.\n\n"
        "Trade plan:\n"
        "- Buy strong, persistent uptrends.\n"
        "- Hold winners while trend remains intact.\n"
        "- Exit quickly when momentum fades.\n"
        "- Stay in cash during noisy sideways periods.\n"
        "- Scale down size in high-volatility conditions.\n\n"
        f"{_BASE_CONSTRAINTS}"
    )
    + _REASONING_ADDENDUM,
    "scalper": (
        "You are a spread-aware crypto scalper.\n\n"
        "Trade small, frequent moves only when:\n"
        "- spread is tight,\n"
        "- near-term direction is clear,\n"
        "- expected move is larger than friction.\n"
        "Avoid overtrading in choppy high-volatility states.\n\n"
        f"{_BASE_CONSTRAINTS}"
    )
    + _REASONING_ADDENDUM,
    "brainrot": (
        "You are a high-energy trader persona for demo purposes only. "
        "Even in this persona, obey all hard constraints and basic risk controls.\n\n"
        f"{_BASE_CONSTRAINTS}"
    )
    + _REASONING_ADDENDUM,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy a named AgentRouterNode for the daytrading arena.",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Agent name (used as consumer group + identity)",
    )
    parser.add_argument(
        "--chat-node-name",
        required=True,
        help="Name of the deployed ChatNode to target (e.g. gpt5-nano)",
    )
    parser.add_argument(
        "--strategy",
        required=True,
        choices=list(STRATEGIES.keys()),
        help="Trading strategy (selects system prompt)",
    )
    parser.add_argument(
        "--bootstrap-servers",
        required=True,
        help="Kafka bootstrap servers address",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    system_prompt = STRATEGIES.get(args.strategy)
    if system_prompt is None:
        print(f"ERROR: Unknown strategy '{args.strategy}'")
        print(f"Available: {', '.join(STRATEGIES.keys())}")
        sys.exit(1)

    print("=" * 50)
    print(f"Router Node Deployment: {args.name}")
    print("=" * 50)

    print(f"\nConnecting to Kafka broker at {args.bootstrap_servers}...")
    broker = BrokerClient(bootstrap_servers=args.bootstrap_servers)
    service = NodesService(broker)

    # ChatNode reference for topic routing (deployed separately via deploy_chat_node.py)
    chat_node = ChatNode(name=args.chat_node_name)

    tools = [execute_trade, get_portfolio, calculator]
    router = AgentRouterNode(
        chat_node=chat_node,
        tool_nodes=tools,
        name=args.name,
        message_history_store=InMemoryMessageHistoryStore(),
        system_prompt=system_prompt,
    )
    service.register_node(router, group_id=args.name)

    tool_names = ", ".join(t.tool_schema.name for t in tools)
    print(f"  - Agent:    {args.name}")
    print(f"  - Strategy: {args.strategy}")
    print(f"  - ChatNode: {args.chat_node_name} (topic: {chat_node.entrypoint_topic})")
    print(f"  - Input:    {router.subscribed_topic}")
    print(f"  - Reply:    {router.entrypoint_topic}")
    print(f"  - Tools:    {tool_names}")

    print("\nRouter node ready. Waiting for requests...")
    await service.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nRouter node stopped.")
