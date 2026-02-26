"""Web dashboard for live arena monitoring.

Serves a browser UI and streams state from Kafka topics:
- market_data.prices
- agent_router.output

Usage:
    uv run python web_dashboard.py --bootstrap-servers localhost:9092
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from aiokafka import AIOKafkaConsumer
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

PRICE_TOPIC = "market_data.prices"
ROUTER_OUTPUT_TOPIC = "agent_router.output"
MAX_EVENTS = 200
MAX_PRICE_POINTS = 90


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run web dashboard for arena monitoring.")
    parser.add_argument(
        "--bootstrap-servers",
        required=True,
        help="Kafka bootstrap servers address",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="HTTP bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8088,
        help="HTTP bind port (default: 8088)",
    )
    return parser.parse_args()


def _as_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _to_iso(timestamp_ms: int | float | None) -> str:
    if not timestamp_ms:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(float(timestamp_ms) / 1000.0, tz=timezone.utc).isoformat()


def _decode_json(value: bytes | str | None) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, bytes):
        text = value.decode("utf-8", "replace")
    else:
        text = value
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _truncate(text: str, max_len: int = 180) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _format_args(value: Any) -> str:
    if isinstance(value, str):
        return _truncate(value, 120)
    if isinstance(value, dict):
        parts: list[str] = []
        for idx, (key, val) in enumerate(value.items()):
            if idx >= 4:
                parts.append("...")
                break
            parts.append(f"{key}={_truncate(json.dumps(val), 30)}")
        return ", ".join(parts)
    return _truncate(str(value), 120)


def _parse_money(value: str) -> float | None:
    text = value.strip()
    if not text or text.upper() == "N/A":
        return None
    cleaned = text.replace("$", "").replace(",", "").replace("+", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


class DashboardState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._boot_time = time.time()
        self._kafka_connected = False
        self._messages_seen = 0
        self._prices: dict[str, dict[str, Any]] = {}
        self._price_history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=MAX_PRICE_POINTS)
        )
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._agent_stats: dict[str, dict[str, Any]] = {}
        self._agent_portfolios: dict[str, dict[str, Any]] = {}
        self._seen_event_ids: set[str] = set()
        self._seen_event_order: deque[str] = deque()

    def set_kafka_connected(self, value: bool) -> None:
        with self._lock:
            self._kafka_connected = value

    def mark_message(self) -> None:
        with self._lock:
            self._messages_seen += 1

    def update_price(self, payload: dict[str, Any], timestamp_ms: int | None) -> None:
        product_id = str(payload.get("product_id", "")).strip()
        if not product_id:
            return

        price = _as_float(payload.get("price"))
        best_bid = _as_float(payload.get("best_bid"))
        best_ask = _as_float(payload.get("best_ask"))

        with self._lock:
            previous = self._prices.get(product_id, {}).get("price")
            momentum = "flat"
            if previous is not None:
                if price > previous:
                    momentum = "up"
                elif price < previous:
                    momentum = "down"

            self._prices[product_id] = {
                "product_id": product_id,
                "price": price,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": max(best_ask - best_bid, 0.0),
                "volume_24h": _as_float(payload.get("volume_24h")),
                "updated_at": _to_iso(timestamp_ms),
                "momentum": momentum,
            }
            self._price_history[product_id].append(price)

    def record_router_payload(self, payload: dict[str, Any], timestamp_ms: int | None) -> None:
        agent_name = str(payload.get("agent_name") or "unknown")
        history = payload.get("message_history") or []
        if not history:
            return

        last_message = history[-1] if isinstance(history, list) else {}
        parts = last_message.get("parts") or []
        if not isinstance(parts, list):
            return

        trace_id = str(payload.get("trace_id") or "")
        history_len = len(history)

        for idx, part in enumerate(parts):
            if not isinstance(part, dict):
                continue

            part_kind = str(part.get("part_kind") or "")
            event_type = ""
            detail = ""

            if part_kind == "tool-call":
                tool_name = str(part.get("tool_name") or "tool")
                detail = f"{tool_name}({_format_args(part.get('args'))})"
                event_type = "tool_call"
            elif part_kind == "tool-return":
                tool_name = str(part.get("tool_name") or "tool")
                raw_content = str(part.get("content", ""))
                if tool_name == "get_portfolio":
                    self._update_agent_portfolio_from_text(
                        agent_name=agent_name,
                        content=raw_content,
                        timestamp_ms=timestamp_ms,
                    )
                content = _truncate(raw_content, 200)
                detail = f"{tool_name} -> {content}"
                event_type = "tool_result"
            elif part_kind == "text":
                detail = _truncate(str(part.get("content", "")), 220)
                event_type = "response"
            elif part_kind == "user-prompt":
                prompt = str(part.get("content", ""))
                if prompt.startswith("Here is the latest ticker information."):
                    continue
                detail = _truncate(prompt, 180)
                event_type = "prompt"
            else:
                continue

            event_id = f"{trace_id}:{history_len}:{idx}:{part_kind}"
            event = {
                "id": event_id,
                "type": event_type,
                "agent": agent_name,
                "detail": detail,
                "timestamp": _to_iso(timestamp_ms),
            }
            self._record_event(event)

    def _record_event(self, event: dict[str, Any]) -> None:
        agent_name = event["agent"]
        event_type = event["type"]
        event_id = str(event.get("id", ""))

        with self._lock:
            if event_id:
                if event_id in self._seen_event_ids:
                    return
                self._seen_event_ids.add(event_id)
                self._seen_event_order.append(event_id)
                while len(self._seen_event_order) > 600:
                    stale_id = self._seen_event_order.popleft()
                    self._seen_event_ids.discard(stale_id)

            self._events.append(event)

            stats = self._agent_stats.setdefault(
                agent_name,
                {
                    "agent": agent_name,
                    "events": 0,
                    "tool_calls": 0,
                    "trade_attempts": 0,
                    "last_event": "",
                    "last_seen": None,
                },
            )
            stats["events"] += 1
            stats["last_event"] = event["detail"]
            stats["last_seen"] = event["timestamp"]

            if event_type == "tool_call":
                stats["tool_calls"] += 1
                if event["detail"].startswith("execute_trade("):
                    stats["trade_attempts"] += 1

    def _update_agent_portfolio_from_text(
        self,
        agent_name: str,
        content: str,
        timestamp_ms: int | None,
    ) -> None:
        lines = [line.strip() for line in content.splitlines() if line.strip()]

        cash = None
        total_value = None

        cash_match = re.search(r"Cash:\s*\$([0-9,.\-]+)", content)
        if cash_match:
            cash = _parse_money(cash_match.group(1))

        total_match = re.search(r"Total portfolio value:\s*\$([0-9,.\-]+)", content)
        if total_match:
            total_value = _parse_money(total_match.group(1))

        positions: list[dict[str, Any]] = []
        for line in lines:
            if not line.startswith("|"):
                continue
            columns = [column.strip() for column in line.strip("|").split("|")]
            if len(columns) < 8:
                continue
            ticker = columns[0]
            if ticker.lower() in ("ticker", "---"):
                continue

            qty = _as_float(columns[1], 0.0)
            current_price = _parse_money(columns[4]) or 0.0
            market_value = _parse_money(columns[5]) or 0.0
            pnl = _parse_money(columns[6]) or 0.0

            positions.append(
                {
                    "ticker": ticker,
                    "qty": qty,
                    "current_price": current_price,
                    "market_value": market_value,
                    "pnl": pnl,
                    "direction": "up" if pnl > 0 else ("down" if pnl < 0 else "flat"),
                }
            )

        position_value = sum(position["market_value"] for position in positions)
        unrealized_pnl = sum(position["pnl"] for position in positions)
        direction = "up" if unrealized_pnl > 0 else ("down" if unrealized_pnl < 0 else "flat")

        with self._lock:
            self._agent_portfolios[agent_name] = {
                "agent": agent_name,
                "cash": cash,
                "total_value": total_value,
                "position_value": position_value,
                "unrealized_pnl": unrealized_pnl,
                "direction": direction,
                "positions": positions,
                "updated_at": _to_iso(timestamp_ms),
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            prices = []
            for product_id in sorted(self._prices):
                row = dict(self._prices[product_id])
                row["history"] = list(self._price_history[product_id])
                prices.append(row)

            agents = sorted(
                (dict(value) for value in self._agent_stats.values()),
                key=lambda row: (row["trade_attempts"], row["tool_calls"], row["events"]),
                reverse=True,
            )

            events = list(self._events)[-80:]
            events.reverse()

            portfolios = sorted(
                (dict(value) for value in self._agent_portfolios.values()),
                key=lambda row: (row["unrealized_pnl"], row["position_value"]),
                reverse=True,
            )

            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "uptime_sec": int(time.time() - self._boot_time),
                "kafka_connected": self._kafka_connected,
                "messages_seen": self._messages_seen,
                "prices": prices,
                "agents": agents,
                "portfolios": portfolios,
                "events": events,
            }


async def kafka_consumer_loop(
    state: DashboardState,
    bootstrap_servers: str,
    stop_event: threading.Event,
) -> None:
    group_id = f"web-dashboard-{uuid.uuid4().hex[:8]}"

    while not stop_event.is_set():
        consumer = AIOKafkaConsumer(
            PRICE_TOPIC,
            ROUTER_OUTPUT_TOPIC,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=True,
        )

        try:
            await consumer.start()
            state.set_kafka_connected(True)
            logger.info("Kafka consumer connected (group_id=%s)", group_id)

            while not stop_event.is_set():
                batch = await consumer.getmany(timeout_ms=1000, max_records=200)
                for topic_partition, messages in batch.items():
                    for message in messages:
                        state.mark_message()
                        payload = _decode_json(message.value)
                        if not payload:
                            continue
                        if topic_partition.topic == PRICE_TOPIC:
                            state.update_price(payload, message.timestamp)
                        elif topic_partition.topic == ROUTER_OUTPUT_TOPIC:
                            state.record_router_payload(payload, message.timestamp)
        except Exception:
            state.set_kafka_connected(False)
            logger.exception("Kafka stream interrupted. Retrying in 3 seconds.")
            await asyncio.sleep(3)
        finally:
            try:
                await consumer.stop()
            except Exception:
                logger.exception("Failed to stop Kafka consumer cleanly.")

    state.set_kafka_connected(False)


def start_consumer_thread(
    state: DashboardState,
    bootstrap_servers: str,
    stop_event: threading.Event,
) -> threading.Thread:
    def runner() -> None:
        asyncio.run(kafka_consumer_loop(state, bootstrap_servers, stop_event))

    thread = threading.Thread(target=runner, daemon=True, name="web-dashboard-kafka")
    thread.start()
    return thread


class DashboardRequestHandler(BaseHTTPRequestHandler):
    state: DashboardState
    assets_dir: Path

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._serve_file(self.assets_dir / "index.html", "text/html; charset=utf-8")
            return
        if path == "/app.css":
            self._serve_file(self.assets_dir / "app.css", "text/css; charset=utf-8")
            return
        if path == "/app.js":
            self._serve_file(self.assets_dir / "app.js", "application/javascript; charset=utf-8")
            return
        if path == "/api/state":
            self._write_json(self.state.snapshot())
            return
        if path == "/api/health":
            self._write_json({"status": "ok"})
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("HTTP %s - %s", self.address_string(), format % args)

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _write_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )

    assets_dir = Path(__file__).resolve().parent / "webui"
    if not assets_dir.exists():
        raise FileNotFoundError(f"Missing web assets directory: {assets_dir}")

    state = DashboardState()
    stop_event = threading.Event()
    consumer_thread = start_consumer_thread(state, args.bootstrap_servers, stop_event)

    DashboardRequestHandler.state = state
    DashboardRequestHandler.assets_dir = assets_dir

    server = ThreadingHTTPServer((args.host, args.port), DashboardRequestHandler)

    logger.info("Web dashboard listening at http://%s:%d", args.host, args.port)
    logger.info("Reading Kafka topics: %s, %s", PRICE_TOPIC, ROUTER_OUTPUT_TOPIC)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down web dashboard...")
    finally:
        stop_event.set()
        server.shutdown()
        server.server_close()
        consumer_thread.join(timeout=5)


if __name__ == "__main__":
    main()
