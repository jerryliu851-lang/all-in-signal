// View 5: Polymarket matches (hosts' implied odds vs market odds).
import { esc, fmtCount, fmtDate, fmtPoints, fmtProb } from "../format.js";
import { emptyState, hostChip, sectionHead, tag, tsLink } from "../ui.js";

function outcomeLine(m) {
  if (!m.closed) return `<span class="pill st-pending">Open</span>`;
  if (!m.resolution_outcome) return `<span class="pill st-unresolvable">Closed, awaiting resolution</span>`;
  const right = m.resolution_outcome.toLowerCase() === m.hosts_answer.toLowerCase();
  return `<span class="pill ${right ? "st-resolved_correct" : "st-resolved_incorrect"}">Resolved ${esc(m.resolution_outcome)} · hosts ${
    right ? "right" : "wrong"
  }</span>`;
}

function matchCard(ctx, m, entry) {
  const flagged = m.flagged_divergence || (m.divergence_points ?? 0) > ctx.flagPoints;
  const speaker = entry ? hostChip(ctx, entry.speaker, entry.attribution_confidence) : "";
  const link = m.slug
    ? `<a class="btn btn-ghost" href="https://polymarket.com/event/${encodeURIComponent(m.slug)}" target="_blank" rel="noopener">Polymarket <span aria-hidden="true">↗</span></a>`
    : "";
  const marketQ = m.market_question && m.market_question !== m.candidate_question
    ? `<p class="muted small">Market: ${esc(m.market_question)}</p>`
    : "";
  return `<article class="card pad pm-card${flagged ? " flagged" : ""}">
    <div class="row-top">${speaker}<span class="pill ans-${esc(m.hosts_answer)}">Says ${esc(m.hosts_answer.toUpperCase())}</span>${outcomeLine(m)}</div>
    <h3 class="pm-q">${esc(m.candidate_question)}</h3>${marketQ}
    <div class="duel">
      <div class="side"><span class="big-num">${fmtProb(m.implied_probability)}</span><span class="lbl">hosts imply yes</span></div>
      <div class="vs" aria-hidden="true">vs</div>
      <div class="side"><span class="big-num">${fmtProb(m.market_yes_probability)}</span><span class="lbl">market yes</span></div>
    </div>
    <p class="div-line">${flagged ? tag("big divergence", "tag-warn") : ""}<span>${
      m.divergence_points === null || m.divergence_points === undefined ? "No divergence computed" : `${fmtPoints(m.divergence_points)} apart`
    }</span></p>
    <div class="row-foot"><span class="muted small">Ends ${fmtDate(m.end_date)} · match confidence ${esc(m.match_confidence)}</span>
      ${entry ? tsLink(entry.youtube_timestamp_url, entry.timestamp_seconds, entry.claim) : ""}${link}</div>
  </article>`;
}

export async function render(root, ctx) {
  const [pm, ledger] = await Promise.all([ctx.load("polymarket.json"), ctx.load("predictions.json")]);
  const byId = new Map(((ledger && ledger.predictions) || []).map((e) => [e.id, e]));
  const matches = [...((pm && pm.matches) || [])];
  const unmatched = (pm && pm.unmatched) || [];
  const intro = sectionHead("Polymarket board",
    "Host predictions that look like a prediction market, matched against live Polymarket odds. Big gaps mean the hosts disagree with the crowd.");
  if (!matches.length && !unmatched.length) {
    root.innerHTML = intro + emptyState("No market matches yet", "Candidates are matched against Polymarket after each analysis run.");
    return;
  }
  // Flagged divergence first, then open before closed, then by gap size.
  matches.sort((a, b) => Number(b.flagged_divergence) - Number(a.flagged_divergence)
    || Number(a.closed) - Number(b.closed) || (b.divergence_points ?? 0) - (a.divergence_points ?? 0));
  const flaggedN = matches.filter((m) => m.flagged_divergence).length;
  const resolvedN = matches.filter((m) => m.closed && m.resolution_outcome).length;
  const stats = [[matches.length, "matched"], [flaggedN, "big divergences"], [resolvedN, "resolved"], [unmatched.length, "unmatched"]]
    .map(([n, l]) => `<div class="stat"><span class="num">${n}</span><span class="lbl">${l}</span></div>`)
    .join("");
  const um = unmatched
    .map((u) => {
      const e = byId.get(u.prediction_id);
      return `<li>${e ? hostChip(ctx, e.speaker, e.attribution_confidence) : ""}<span class="um-q">${esc(u.question)}</span>
        <span class="muted small">says ${esc(u.hosts_answer)} · implied ${fmtProb(u.implied_probability)} · ${fmtCount(u.attempts, "search", "searches")}</span></li>`;
    })
    .join("");
  root.innerHTML = `${intro}<div class="stats">${stats}</div>
    <div class="pm-grid">${matches.map((m) => matchCard(ctx, m, byId.get(m.prediction_id))).join("")}</div>
    ${unmatched.length ? `<section class="card pad">${sectionHead("No market found yet", "Candidates still waiting for a matching market")}<ul class="um-list">${um}</ul></section>` : ""}`;
}
