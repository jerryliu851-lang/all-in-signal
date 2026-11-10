// View 4: stock implications grouped by ticker, with a cumulative stance step chart.
import { esc, fmtCount, fmtDate, fmtSigned } from "../format.js";
import { directionPill, emptyState, hostChip, sectionHead, tsLink } from "../ui.js";

function tone(n) {
  return n > 0 ? "pos" : n < 0 ? "neg" : "zero";
}

/** Inline SVG step chart of the cumulative stance after each episode. */
export function stepChart(series, label) {
  if (!series || !series.length) return "";
  const W = 260;
  const H = 64;
  const pad = 8;
  const values = [0, ...series.map((p) => p.cumulative)];
  const lo = Math.min(-1, ...values);
  const hi = Math.max(1, ...values);
  const x = (i) => pad + (i / Math.max(1, values.length - 1)) * (W - pad * 2);
  const y = (v) => pad + ((hi - v) / (hi - lo)) * (H - pad * 2);
  let d = `M${x(0).toFixed(1)},${y(0).toFixed(1)}`;
  for (let i = 1; i < values.length; i++) {
    d += ` H${x(i).toFixed(1)} V${y(values[i]).toFixed(1)}`;
  }
  const dots = values
    .slice(1)
    .map((v, i) => `<circle cx="${x(i + 1).toFixed(1)}" cy="${y(v).toFixed(1)}" r="3.5" class="pt-${tone(v)}"/>`)
    .join("");
  const final = values[values.length - 1];
  const desc = series.map((p) => `${fmtDate(p.published_at)}: ${fmtSigned(p.cumulative)}`).join(", ");
  return `<svg class="step step-${tone(final)}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(`${label} cumulative stance: ${desc}`)}">
    <line x1="${pad}" x2="${W - pad}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}" class="zero"/>
    <path d="${d}" class="line"/>${dots}</svg>`;
}

function callItem(ctx, c) {
  return `<li class="call">
    <div class="row-top">${directionPill(c.direction)}${hostChip(ctx, c.speaker, c.attribution_confidence, c)}
      <span class="muted small">${fmtDate(c.published_at)}</span></div>
    <p>${esc(c.rationale)}</p>
    <div class="row-foot"><span class="muted small">${esc(c.timeframe)}</span>${tsLink(c.timestamp_url, c.timestamp_seconds, c.claim)}</div>
  </li>`;
}

function tickerCard(ctx, t) {
  const calls = [...t.calls].reverse(); // newest first
  const head = calls.slice(0, 3).map((c) => callItem(ctx, c)).join("");
  const rest = calls.slice(3);
  const more = rest.length
    ? `<details class="more"><summary>${fmtCount(rest.length, "earlier call")}</summary><ul class="calls">${rest
        .map((c) => callItem(ctx, c))
        .join("")}</ul></details>`
    : "";
  const sub = [t.companies.join(", "), t.sectors.join(", ")].filter(Boolean).join(" · ");
  return `<article class="card pad ticker-card">
    <div class="tk-head">
      <div><h3 class="ticker">${esc(t.ticker)}</h3>${sub ? `<p class="muted small">${esc(sub)}</p>` : ""}</div>
      <div class="net net-${tone(t.net_score)}"><span class="big-num">${fmtSigned(t.net_score)}</span><span class="lbl">net stance</span></div>
    </div>
    ${stepChart(t.series, t.ticker)}
    <p class="muted small">${t.bullish} bullish · ${t.bearish} bearish · ${t.neutral} neutral across ${fmtCount(t.series.length, "episode")}</p>
    <ul class="calls">${head}</ul>${more}
  </article>`;
}

function privateBucket(ctx, items) {
  if (!items.length) return "";
  const rows = [...items]
    .reverse()
    .map(
      (c) => `<li class="call">
      <div class="row-top"><b>${esc(c.company || "Unnamed")}</b>${directionPill(c.direction)}${hostChip(ctx, c.speaker, c.attribution_confidence, c)}</div>
      <p>${esc(c.rationale)}</p>
      ${c.note ? `<p class="muted small">${esc(c.note)}</p>` : ""}
      <div class="row-foot"><span class="muted small">${fmtDate(c.published_at)} · ${esc(c.timeframe)}</span>${tsLink(c.timestamp_url, c.timestamp_seconds, c.company || "")}</div>
    </li>`
    )
    .join("");
  return `<section class="card pad">${sectionHead("Private or no ticker", "Calls on private companies or themes with no liquid instrument")}<ul class="calls">${rows}</ul></section>`;
}

export async function render(root, ctx) {
  const doc = await ctx.load("stocks.json");
  const tickers = (doc && doc.tickers) || [];
  const priv = (doc && doc.private_or_no_ticker) || [];
  const intro = sectionHead("Stock implications",
    "Each bullish call counts +1, bearish −1, neutral 0. The big number is the running total; the chart shows how it moved episode by episode.");
  if (!tickers.length && !priv.length) {
    root.innerHTML = intro + emptyState("No stock calls yet", "Ticker implications show up once episodes are analyzed.");
    return;
  }
  root.innerHTML = `${intro}<div class="ticker-grid">${tickers.map((t) => tickerCard(ctx, t)).join("")}</div>${privateBucket(ctx, priv)}`;
}
