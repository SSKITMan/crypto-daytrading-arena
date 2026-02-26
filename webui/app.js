const POLL_MS = 1200;
const previousPrices = new Map();

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function formatIso(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "-";
  return date.toLocaleTimeString();
}

function formatUptime(seconds) {
  if (!Number.isFinite(seconds)) return "0s";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function buildSparkline(history, direction) {
  const values = history.length ? history : [0, 0];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = values
    .map((value, index) => {
      const x = values.length === 1 ? 100 : (index / (values.length - 1)) * 100;
      const y = 100 - ((value - min) / span) * 100;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");

  return `
    <svg class="sparkline" viewBox="0 0 100 100" preserveAspectRatio="none">
      <polyline class="line-${direction}" points="${points}"></polyline>
    </svg>
  `;
}

function updateStatus(snapshot) {
  const kafkaPill = document.getElementById("kafkaPill");
  const messagesSeen = document.getElementById("messagesSeen");
  const uptime = document.getElementById("uptime");

  if (snapshot.kafka_connected) {
    kafkaPill.className = "pill pill-on";
    kafkaPill.textContent = "Kafka: connected";
  } else {
    kafkaPill.className = "pill pill-off";
    kafkaPill.textContent = "Kafka: disconnected";
  }

  messagesSeen.textContent = String(snapshot.messages_seen ?? 0);
  uptime.textContent = formatUptime(snapshot.uptime_sec ?? 0);
}

function renderMarket(prices) {
  const grid = document.getElementById("marketGrid");
  if (!prices.length) {
    grid.innerHTML = `<div class="empty">Waiting for market_data.prices...</div>`;
    return;
  }

  grid.innerHTML = prices
    .map((row) => {
      const id = row.product_id;
      const price = Number(row.price || 0);
      const previous = previousPrices.get(id);
      let direction = "flat";
      if (previous !== undefined) {
        if (price > previous) direction = "up";
        if (price < previous) direction = "down";
      } else if (row.momentum) {
        direction = row.momentum;
      }
      previousPrices.set(id, price);

      return `
        <article class="market-card ${direction}">
          <div class="market-top">
            <strong class="ticker">${escapeHtml(id)}</strong>
            <span class="updated mono">${formatIso(row.updated_at)}</span>
          </div>
          <div class="price">${money.format(price)}</div>
          <div class="metrics">
            <span>Bid: ${money.format(Number(row.best_bid || 0))}</span>
            <span>Ask: ${money.format(Number(row.best_ask || 0))}</span>
            <span>Spread: ${money.format(Number(row.spread || 0))}</span>
            <span>24h Vol: ${Number(row.volume_24h || 0).toLocaleString()}</span>
          </div>
          ${buildSparkline(row.history || [], direction)}
        </article>
      `;
    })
    .join("");
}

function renderAgents(agents) {
  const body = document.getElementById("agentRows");
  if (!agents.length) {
    body.innerHTML = `<tr><td class="empty" colspan="6">No agent activity yet.</td></tr>`;
    return;
  }

  body.innerHTML = agents
    .map((agent) => {
      const agentName = escapeHtml(agent.agent || "unknown");
      const lastEvent = escapeHtml(agent.last_event || "-");
      return `
        <tr>
          <td><span class="chip">${agentName}</span></td>
          <td>${agent.events ?? 0}</td>
          <td>${agent.tool_calls ?? 0}</td>
          <td>${agent.trade_attempts ?? 0}</td>
          <td class="mono">${formatIso(agent.last_seen)}</td>
          <td class="mono">${lastEvent}</td>
        </tr>
      `;
    })
    .join("");
}

function renderPortfolios(portfolios) {
  const grid = document.getElementById("portfolioCards");
  if (!portfolios.length) {
    grid.innerHTML = `<div class="empty">No portfolio snapshots yet. Agents need to call get_portfolio first.</div>`;
    return;
  }

  grid.innerHTML = portfolios
    .map((portfolio) => {
      const direction = portfolio.direction || "flat";
      const unrealized = Number(portfolio.unrealized_pnl || 0);
      const unrealizedClass = unrealized > 0 ? "pnl-up" : unrealized < 0 ? "pnl-down" : "";
      const pnlPrefix = unrealized > 0 ? "+" : "";
      const positions = portfolio.positions || [];

      const positionRows = positions.length
        ? positions
            .map((position) => {
              const posPnl = Number(position.pnl || 0);
              const posClass = posPnl > 0 ? "pnl-up" : posPnl < 0 ? "pnl-down" : "";
              const posPrefix = posPnl > 0 ? "+" : "";
              return `
                <div class="position-row">
                  <div>
                    <div class="ticker">${escapeHtml(position.ticker || "-")}</div>
                    <div class="mono">Qty ${Number(position.qty || 0).toLocaleString()}</div>
                  </div>
                  <div class="mono">${money.format(Number(position.market_value || 0))}</div>
                  <div class="mono ${posClass}">${posPrefix}${money.format(posPnl)}</div>
                </div>
              `;
            })
            .join("")
        : `<div class="empty">No open positions</div>`;

      return `
        <article class="portfolio-card ${direction}">
          <div class="portfolio-head">
            <div class="portfolio-agent">${escapeHtml(portfolio.agent || "unknown")}</div>
            <div class="portfolio-status ${direction}">${direction === "up" ? "UP" : direction === "down" ? "DOWN" : "FLAT"}</div>
          </div>
          <div class="portfolio-metrics">
            <span>Coin Value: ${money.format(Number(portfolio.position_value || 0))}</span>
            <span class="${unrealizedClass}">Unrealized: ${pnlPrefix}${money.format(unrealized)}</span>
            <span>Cash: ${money.format(Number(portfolio.cash || 0))}</span>
            <span>Total: ${money.format(Number(portfolio.total_value || 0))}</span>
          </div>
          <div class="position-list">${positionRows}</div>
        </article>
      `;
    })
    .join("");
}

function renderEvents(events) {
  const feed = document.getElementById("eventFeed");
  if (!events.length) {
    feed.innerHTML = `<div class="empty">No events yet. Waiting for agent_router.output...</div>`;
    return;
  }

  feed.innerHTML = events
    .map((event) => {
      const type = escapeHtml(event.type || "event");
      const agent = escapeHtml(event.agent || "unknown");
      const detail = escapeHtml(event.detail || "");
      const timestamp = formatIso(event.timestamp);
      return `
        <article class="event-card ${type}">
          <div class="event-head">
            <strong>${agent}</strong>
            <span class="event-kind">${type.replaceAll("_", " ")}</span>
            <span class="mono">${timestamp}</span>
          </div>
          <p class="event-detail mono">${detail}</p>
        </article>
      `;
    })
    .join("");
}

async function refresh() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const snapshot = await response.json();
    updateStatus(snapshot);
    renderMarket(snapshot.prices || []);
    renderPortfolios(snapshot.portfolios || []);
    renderAgents(snapshot.agents || []);
    renderEvents(snapshot.events || []);
  } catch (error) {
    const kafkaPill = document.getElementById("kafkaPill");
    kafkaPill.className = "pill pill-off";
    kafkaPill.textContent = "Kafka: error";
  }
}

refresh();
window.setInterval(refresh, POLL_MS);
