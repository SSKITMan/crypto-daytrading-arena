"""
Coinbase-to-Kafka connector that streams real-time market data from
Coinbase and invokes an AgentRouterNode for each market-data batch.

Publishes raw ticker updates to Kafka topic `market_data.prices` and sends an
enriched prompt payload (including microstructure fields) to the router.
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time

import websockets
from pydantic import BaseModel

from calfkit.broker.broker import BrokerClient
from calfkit.nodes.agent_router_node import AgentRouterNode
from calfkit.runners.service_client import RouterServiceClient
from coinbase_consumer import CandleBook, poll_rest

logger = logging.getLogger(__name__)

COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"

DEFAULT_PRODUCTS = [
    "BTC-USD",
    "FARTCOIN-USD",
    "SOL-USD",
]

RECONNECT_DELAY_SECONDS = 3

PRICE_TOPIC = "market_data.prices"
LEVEL2_DEPTH_LEVELS = 10


class TickerMessage(BaseModel):
    """Coinbase ticker message published to Kafka."""

    product_id: str
    price: str
    best_bid: str
    best_bid_size: str
    best_ask: str
    best_ask_size: str
    side: str
    last_size: str
    open_24h: str
    high_24h: str
    low_24h: str
    volume_24h: str
    volume_30d: str
    trade_id: int
    sequence: int
    time: str


class Level2Book:
    """Maintains a lightweight in-memory L2 book for a single product."""

    def __init__(self) -> None:
        self._bids: dict[float, float] = {}
        self._asks: dict[float, float] = {}

    def apply_snapshot(self, bids: list[list[str]], asks: list[list[str]]) -> None:
        self._bids.clear()
        self._asks.clear()

        for level in bids:
            if len(level) < 2:
                continue
            try:
                price = float(level[0])
                size = float(level[1])
            except (TypeError, ValueError):
                continue
            if size > 0:
                self._bids[price] = size

        for level in asks:
            if len(level) < 2:
                continue
            try:
                price = float(level[0])
                size = float(level[1])
            except (TypeError, ValueError):
                continue
            if size > 0:
                self._asks[price] = size

    def apply_changes(self, changes: list[list[str]]) -> None:
        for change in changes:
            if len(change) < 3:
                continue
            side, price_str, size_str = change[0], change[1], change[2]
            try:
                price = float(price_str)
                size = float(size_str)
            except (TypeError, ValueError):
                continue
            side_book = self._bids if side == "buy" else self._asks
            if size <= 0:
                side_book.pop(price, None)
            else:
                side_book[price] = size

    def depth(self, side: str, levels: int = LEVEL2_DEPTH_LEVELS) -> float:
        if side == "buy":
            prices = sorted(self._bids.keys(), reverse=True)[:levels]
            return sum(self._bids[p] for p in prices)
        prices = sorted(self._asks.keys())[:levels]
        return sum(self._asks[p] for p in prices)


class CoinbaseKafkaConnector:
    """Streams Coinbase data to an AgentRouterNode."""

    def __init__(
        self,
        broker: BrokerClient,
        router_node: AgentRouterNode,
        products: list[str],
        min_publish_interval: float = 0.0,
        candle_book: CandleBook | None = None,
    ) -> None:
        self._broker = broker
        self._client = RouterServiceClient(broker, router_node)
        self._products = products
        self._min_interval = min_publish_interval
        self._running = True
        self._candle_book = candle_book

        self._latest: dict[str, TickerMessage] = {}
        self._l2_books: dict[str, Level2Book] = {}
        self._ofi_state: dict[str, float] = {}
        self._depth_imbalance_state: dict[str, float] = {}

    async def start(self) -> None:
        """Start the connector. Blocks until shutdown is triggered."""
        await self._broker.start()
        logger.info("Kafka broker connected")

        try:
            while self._running:
                try:
                    await self._consume_and_publish()
                except websockets.ConnectionClosed:
                    if not self._running:
                        break
                    logger.warning(
                        "WebSocket connection lost. Reconnecting in %ds...",
                        RECONNECT_DELAY_SECONDS,
                    )
                    await asyncio.sleep(RECONNECT_DELAY_SECONDS)
                except Exception:
                    if not self._running:
                        break
                    logger.exception(
                        "Unexpected error. Reconnecting in %ds...",
                        RECONNECT_DELAY_SECONDS,
                    )
                    await asyncio.sleep(RECONNECT_DELAY_SECONDS)
        finally:
            await self._broker.close()
            logger.info("Kafka broker closed")

    def stop(self) -> None:
        """Signal the connector to shut down gracefully."""
        self._running = False

    async def _publish_latest(self) -> None:
        """Snapshot and publish the current latest tickers as a single batch."""
        if not self._latest:
            return

        batch = list(self._latest.values())

        enriched_batch = []
        for ticker in batch:
            product_id = ticker.product_id
            price = float(ticker.price)
            best_bid = float(ticker.best_bid)
            best_ask = float(ticker.best_ask)
            best_bid_size = float(ticker.best_bid_size)
            best_ask_size = float(ticker.best_ask_size)
            spread = max(best_ask - best_bid, 0.0)
            mid = (best_bid + best_ask) / 2 if (best_bid + best_ask) > 0 else price
            spread_bps = (spread / mid * 10_000) if mid > 0 else 0.0

            high_24h = float(ticker.high_24h)
            low_24h = float(ticker.low_24h)
            range_24h_bps = ((high_24h - low_24h) / price * 10_000) if price > 0 else 0.0

            l2 = self._l2_books.get(product_id)
            bid_depth_10 = l2.depth("buy", LEVEL2_DEPTH_LEVELS) if l2 is not None else 0.0
            ask_depth_10 = l2.depth("sell", LEVEL2_DEPTH_LEVELS) if l2 is not None else 0.0
            total_depth = bid_depth_10 + ask_depth_10
            depth_imbalance_10 = (
                (bid_depth_10 - ask_depth_10) / total_depth if total_depth > 0 else 0.0
            )

            prev_depth_imbalance = self._depth_imbalance_state.get(product_id, 0.0)
            depth_imbalance_delta = depth_imbalance_10 - prev_depth_imbalance
            self._depth_imbalance_state[product_id] = depth_imbalance_10

            top_total = best_bid_size + best_ask_size
            tob_imbalance = (
                (best_bid_size - best_ask_size) / top_total if top_total > 0 else 0.0
            )

            signed_trade_size = float(ticker.last_size) * (
                1.0 if ticker.side == "buy" else (-1.0 if ticker.side == "sell" else 0.0)
            )
            trade_pressure = signed_trade_size / max(top_total, 1e-9)
            ofi_raw = trade_pressure + depth_imbalance_delta

            prev_ofi = self._ofi_state.get(product_id, 0.0)
            ofi_ema = prev_ofi * 0.8 + ofi_raw * 0.2
            self._ofi_state[product_id] = ofi_ema

            enriched_batch.append(
                {
                    "product_id": product_id,
                    "price": price,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "best_bid_size": best_bid_size,
                    "best_ask_size": best_ask_size,
                    "spread": spread,
                    "spread_bps": spread_bps,
                    "side": ticker.side,
                    "last_size": float(ticker.last_size),
                    "high_24h": high_24h,
                    "low_24h": low_24h,
                    "range_24h_bps": range_24h_bps,
                    "volume_24h": float(ticker.volume_24h),
                    "bid_depth_10": bid_depth_10,
                    "ask_depth_10": ask_depth_10,
                    "depth_imbalance_10": depth_imbalance_10,
                    "depth_imbalance_delta": depth_imbalance_delta,
                    "tob_imbalance": tob_imbalance,
                    "ofi_ema": ofi_ema,
                    "time": ticker.time,
                }
            )

        batch_json = json.dumps(enriched_batch)

        prompt_parts = [
            "Here is the latest ticker information. You should view your "
            "portfolio first before making any decisions to trade.\n"
            "price = last traded price, best_bid = price you sell at, "
            "best_ask = price you buy at, spread_bps = transaction friction in basis points.\n"
            "bid_depth_10/ask_depth_10/depth_imbalance_10/ofi_ema are short-horizon "
            "microstructure signals from level2_batch + ticker flow.\n\n"
            f"{batch_json}",
        ]

        if self._candle_book is not None and self._candle_book.has_data():
            prompt_parts.append(
                "\n## Price History (OHLCV candlesticks)\n"
                "Below are candlesticks at three granularities - coarser for "
                "broader trend context, finer for recent price action.\n\n"
                f"{self._candle_book.format_prompt(self._products)}"
            )

        await self._client.invoke(
            user_prompt="\n".join(prompt_parts),
            deps={"invoked_at": time.time()},
        )

        summary = ", ".join(f"{t.product_id} @ ${t.price}" for t in batch)
        logger.info(
            "Published batch of %d ticker(s) to router: %s",
            len(batch),
            summary,
        )

    async def _periodic_publish(self) -> None:
        """Publish the latest snapshot on a fixed interval."""
        interval = max(self._min_interval, 1.0)
        while self._running:
            await asyncio.sleep(interval)
            await self._publish_latest()

    async def _consume_and_publish(self) -> None:
        """Connect to Coinbase WebSocket and buffer market data for periodic publish."""
        self._latest.clear()
        self._l2_books.clear()

        async with websockets.connect(COINBASE_WS_URL, max_size=None) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "subscribe",
                        "product_ids": self._products,
                        "channels": ["ticker_batch", "level2_batch"],
                    }
                )
            )
            logger.info(
                "Subscribed to %d products on ticker_batch + level2_batch: %s",
                len(self._products),
                ", ".join(self._products),
            )

            flush_task = asyncio.create_task(self._periodic_publish())

            candle_task: asyncio.Task | None = None
            if self._candle_book is not None:
                from coinbase_consumer import PriceBook

                candle_task = asyncio.create_task(
                    poll_rest(
                        products=self._products,
                        price_book=PriceBook(),
                        candle_book=self._candle_book,
                        interval=60.0,
                    )
                )

            try:
                async for raw in ws:
                    if not self._running:
                        break

                    data = json.loads(raw)
                    message_type = data.get("type")

                    if message_type == "ticker":
                        ticker = TickerMessage.model_validate(data)
                        self._latest[ticker.product_id] = ticker
                        await self._broker.publish(ticker, PRICE_TOPIC)
                        continue

                    if message_type == "snapshot":
                        product_id = data.get("product_id")
                        if product_id not in self._products:
                            continue
                        book = self._l2_books.setdefault(product_id, Level2Book())
                        book.apply_snapshot(data.get("bids", []), data.get("asks", []))
                        continue

                    if message_type == "l2update":
                        product_id = data.get("product_id")
                        if product_id not in self._products:
                            continue
                        book = self._l2_books.setdefault(product_id, Level2Book())
                        book.apply_changes(data.get("changes", []))
                        continue

                    if message_type == "error":
                        logger.warning("Coinbase WS error: %s", data.get("message", data))
            finally:
                flush_task.cancel()
                try:
                    await flush_task
                except asyncio.CancelledError:
                    pass

                if candle_task is not None:
                    candle_task.cancel()
                    try:
                        await candle_task
                    except asyncio.CancelledError:
                        pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stream Coinbase market data to a Kafka topic.",
    )
    parser.add_argument(
        "--bootstrap-servers",
        default=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        help="Kafka bootstrap servers (default: $KAFKA_BOOTSTRAP_SERVERS or localhost:9092).",
    )
    parser.add_argument(
        "--products",
        nargs="+",
        default=DEFAULT_PRODUCTS,
        help="Coinbase product IDs to subscribe to (default: %(default)s).",
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=0.0,
        help=(
            "Minimum seconds between publishes per product. "
            "Incoming data is buffered and only the latest per product is published "
            "when the interval elapses. 0 = publish immediately (default: 0)."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO).",
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace, router_node: AgentRouterNode) -> None:
    broker = BrokerClient(bootstrap_servers=args.bootstrap_servers)
    connector = CoinbaseKafkaConnector(
        broker=broker,
        router_node=router_node,
        products=args.products,
        min_publish_interval=args.min_interval,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, connector.stop)

    logger.info("Starting Coinbase -> Kafka connector")
    logger.info("  Router topic:  %s", router_node.subscribed_topic)
    logger.info("  Broker:        %s", args.bootstrap_servers)
    logger.info("  Products:      %s", ", ".join(args.products))
    logger.info("  Min interval:  %ss", args.min_interval)

    await connector.start()


def main() -> None:
    from calfkit.nodes.chat_node import ChatNode
    from calfkit.stores.in_memory import InMemoryMessageHistoryStore

    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    router_node = AgentRouterNode(
        chat_node=ChatNode(),
        tool_nodes=[],
        message_history_store=InMemoryMessageHistoryStore(),
        system_prompt="You are a market data consumer.",
    )
    asyncio.run(run(args, router_node))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
