// Small shared render helpers that return HTML strings.
import { esc, fmtClock, fmtDate, fmtPoints, fmtProb, STANCE_LABEL, STATUS_LABEL } from "./format.js";

const ATTR_TITLE = {
  high: "Attribution confidence: high",
  medium: "Attribution confidence: medium (speaker inferred)",
  low: "Attribution confidence: low (speaker uncertain)",
};

const BASIS_LABEL = {
  named_handoff: "handed the floor by name",
  addressed_by_name: "addressed by name",
  self_reference: "self-reference",
  segment_role: "segment role",
  guest_intro: "introduced as guest",
  indirect: "indirect cues",
  topic_or_style: "topic or style only",
  none: "no cue",
};

/** Why the speaker was attributed: the basis and, when it was found in the transcript, the cited cue. */
export function attributionWhy(item) {
  if (!item) return "";
  if (!item.attribution_basis) return "Not cue-checked (analyzed before cited cues existed).";
  const basis = BASIS_LABEL[item.attribution_basis] || item.attribution_basis;
  const cue = item.attribution_evidence;
  if (!cue) return `Basis: ${basis}.`;
  const checked = item.attribution_evidence_verified === "verified" ? "" : " (cue not counted: far from the claim or not exact)";
  return `Basis: ${basis}: “${cue}”${checked}`;
}

export function hostChip(ctx, speaker, attribution = "high", item = null) {
  const h = ctx.host(speaker);
  const att = attribution && attribution !== "high" ? ` att-${esc(attribution)}` : "";
  const label = h.key === "guest" ? `${h.short} (guest)` : h.short;
  const title = `${h.name}. ${ATTR_TITLE[attribution] || ""}. ${attributionWhy(item)}`.replace(/\. \./g, ".").trim();
  return `<span class="chip h-${esc(h.key)}${att}" title="${esc(title)}"><span class="dot" aria-hidden="true"></span>${esc(label)}${
    attribution === "low" ? '<span class="sr-only"> (attribution uncertain)</span><span class="att-mark" aria-hidden="true">?</span>' : ""
  }</span>`;
}

export function statusPill(status) {
  return `<span class="pill st-${esc(status)}">${esc(STATUS_LABEL[status] || status)}</span>`;
}

export function directionPill(direction) {
  const arrow = { bullish: "↗", bearish: "↘", neutral: "→" }[direction] || "";
  return `<span class="pill dir-${esc(direction)}"><span aria-hidden="true">${arrow}</span> ${esc(direction)}</span>`;
}

export function stanceTag(stance) {
  if (!stance || stance === "own_view") return "";
  return `<span class="tag tag-stance">${esc(STANCE_LABEL[stance] || stance)}</span>`;
}

export function tag(text, cls = "") {
  return `<span class="tag ${cls}">${esc(text)}</span>`;
}

export function tsLink(url, seconds, label = "") {
  if (!url) return "";
  const aria = `Watch on YouTube at ${fmtClock(seconds)}${label ? `: ${label}` : ""}`;
  return `<a class="ts" href="${esc(url)}" target="_blank" rel="noopener" aria-label="${esc(aria)}"><span aria-hidden="true">▶</span> ${fmtClock(seconds)}</a>`;
}

export function marketOdds(entry, threshold = 25) {
  if (entry.polymarket_yes_probability === null || entry.polymarket_yes_probability === undefined) return "";
  const div = entry.polymarket_divergence_points;
  const flagged = div !== null && div !== undefined && div > threshold;
  return `<span class="odds${flagged ? " flagged" : ""}" title="${esc(entry.polymarket_question || "")}">
    Polymarket <b>${fmtProb(entry.polymarket_yes_probability)}</b> yes${
      div !== null && div !== undefined ? ` <span class="div">Δ ${fmtPoints(div)}</span>` : ""
    }</span>`;
}

export function emptyState(title, body) {
  return `<div class="card empty"><div class="empty-mark" aria-hidden="true"></div><h3>${esc(title)}</h3><p>${esc(body)}</p></div>`;
}

export function sectionHead(title, sub = "") {
  return `<div class="section-head"><h2>${esc(title)}</h2>${sub ? `<p class="muted">${sub}</p>` : ""}</div>`;
}

/** One prediction row (ledger + latest view). */
export function predictionRow(ctx, e, { showEpisode = true } = {}) {
  const stanceCls = e.stance_type && e.stance_type !== "own_view" ? ` stance-${e.stance_type}` : "";
  const tf = e.timeframe || {};
  return `<article class="row${stanceCls}">
    <div class="row-top">${hostChip(ctx, e.speaker, e.attribution_confidence, e)}${stanceTag(e.stance_type)}
      ${tag(e.category, "tag-cat")}${statusPill(e.status)}</div>
    <p class="claim">${esc(e.claim)}</p>
    <div class="row-meta">
      <span class="tf"><span class="sr-only">Timeframe: </span>${esc(tf.text || "no timeframe")}</span>
      ${tf.inferred ? tag("inferred", "tag-soft") : ""}
      ${e.timeframe_passed ? tag("overdue", "tag-warn") : ""}
      ${marketOdds(e, ctx.flagPoints)}
    </div>
    <div class="row-foot">
      ${tsLink(e.youtube_timestamp_url, e.timestamp_seconds, e.claim)}
      ${showEpisode ? `<span class="muted small ep-ref">${fmtDate(e.episode_published_at)} \u00B7 ${esc(e.episode_title || "")}</span>` : ""}
    </div>
  </article>`;
}
