// View 1: the newest analyzed episode.
import { esc, fmtClock, fmtCount, fmtDate, fmtDuration, ytUrl } from "../format.js";
import { directionPill, emptyState, hostChip, predictionRow, sectionHead, stanceTag, tag, tsLink } from "../ui.js";

function chaptersBlock(ep) {
  const chapters = ep.chapters || [];
  if (!chapters.length) return "";
  const total = ep.duration_seconds || chapters[chapters.length - 1].end_time || 1;
  const segs = chapters
    .map((c, i) => {
      const end = c.end_time ?? chapters[i + 1]?.start_time ?? total;
      const w = Math.max(0.5, ((end - c.start_time) / total) * 100);
      return `<span class="seg seg-${i % 4}" style="width:${w.toFixed(2)}%" title="${esc(c.title)}"></span>`;
    })
    .join("");
  const items = chapters
    .map(
      (c) => `<li><a class="ts" href="${esc(ytUrl(ep.video_id, c.start_time))}" target="_blank" rel="noopener"
        aria-label="Watch ${esc(c.title)} from ${fmtClock(c.start_time)}"><span aria-hidden="true">▶</span> ${fmtClock(c.start_time)}</a>
        <span class="ch-title">${esc(c.title)}</span></li>`
    )
    .join("");
  return `<section class="card pad">${sectionHead("Chapters")}
    <div class="ch-bar" aria-hidden="true">${segs}</div>
    <ol class="chapters">${items}</ol></section>`;
}

function takesBlock(ctx, ep, takes) {
  if (!takes.length) return "";
  const cards = takes
    .map((t) => {
      const stanceCls = t.stance_type && t.stance_type !== "own_view" ? ` stance-${t.stance_type}` : "";
      const q = t.quote && t.quote_verified !== "dropped"
        ? `<blockquote>“${esc(t.quote)}”${t.quote_verified === "flagged" ? tag("quote not verbatim-checked", "tag-soft") : ""}</blockquote>`
        : "";
      return `<article class="take${stanceCls}">
        <div class="row-top">${hostChip(ctx, t.speaker, t.attribution_confidence, t)}${stanceTag(t.stance_type)}</div>
        <p class="take-summary">${esc(t.summary)}</p>${q}
        <div class="row-foot">${tsLink(ytUrl(ep.video_id, t.timestamp_seconds), t.timestamp_seconds, t.summary)}</div>
      </article>`;
    })
    .join("");
  return `<section>${sectionHead("Key takes", fmtCount(takes.length, "take"))}<div class="takes">${cards}</div></section>`;
}

function stockBoard(ctx, ep, implications) {
  if (!implications.length) return "";
  const tiles = implications
    .map((s) => {
      const label = s.tickers && s.tickers.length ? s.tickers.join(" · ") : s.company || "Private";
      const sub = s.tickers && s.tickers.length ? s.company || s.sector || "" : s.is_private ? "private" : "no ticker";
      return `<article class="stock-tile dir-b-${esc(s.direction)}">
        <div class="tile-head"><span class="ticker">${esc(label)}</span>${directionPill(s.direction)}</div>
        ${sub ? `<div class="muted small">${esc(sub)}</div>` : ""}
        <p>${esc(s.rationale)}</p>
        <div class="row-foot">${hostChip(ctx, s.speaker, s.attribution_confidence, s)}
          <span class="muted small">${esc(s.timeframe)}</span>
          ${tsLink(ytUrl(ep.video_id, s.timestamp_seconds), s.timestamp_seconds, label)}</div>
      </article>`;
    })
    .join("");
  return `<section>${sectionHead("Stock board", "What this episode implies for tickers")}<div class="tiles">${tiles}</div></section>`;
}

export async function render(root, ctx) {
  const latest = await ctx.load("latest.json");
  const a = latest && latest.analysis;
  if (!a) {
    root.innerHTML = sectionHead("Latest episode") + emptyState("No episodes analyzed yet",
      "The pipeline has not analyzed an All-In episode yet. Once it does, the newest one shows up here.");
    return;
  }
  const ep = a.episode;
  const ledger = latest.ledger || [];
  const matches = latest.polymarket_matches || [];
  const hosts = (a.hosts_present || []).map((h) => hostChip(ctx, h)).join("");
  const guests = (a.guests || []).map((g) => hostChip(ctx, `Guest: ${g}`)).join("");
  const stats = [
    [a.key_takes.length, "key takes"],
    [ledger.length || a.predictions.length, "predictions"],
    [a.stock_implications.length, "stock calls"],
    [matches.length, "markets matched"],
  ]
    .map(([n, l]) => `<div class="stat"><span class="num">${n}</span><span class="lbl">${l}</span></div>`)
    .join("");
  const preds = ledger.length
    ? `<div class="rows">${ledger.map((e) => predictionRow(ctx, e, { showEpisode: false })).join("")}</div>`
    : `<p class="muted">No forward-looking claims were extracted from this episode.</p>`;

  root.innerHTML = `
    <section class="card hero pad">
      <p class="eyebrow">Latest episode · ${fmtDate(ep.published_at)}</p>
      <h1>${esc(ep.title)}</h1>
      <p class="muted">${fmtDuration(ep.duration_seconds)}${a.guests?.length ? ` · with ${esc(a.guests.join(", "))}` : ""}</p>
      <div class="chips">${hosts}${guests}</div>
      <a class="btn" href="${esc(ep.url)}" target="_blank" rel="noopener">Watch on YouTube <span aria-hidden="true">↗</span></a>
    </section>
    <div class="stats">${stats}</div>
    ${chaptersBlock(ep)}
    ${takesBlock(ctx, ep, a.key_takes || [])}
    <section>${sectionHead("Predictions made", "Every forward-looking claim from this episode")}${preds}</section>
    ${stockBoard(ctx, ep, a.stock_implications || [])}
  `;
}
