// View 3: per-host accuracy scorecard.
import { BUCKET_ORDER, CATEGORY_ORDER, esc, fmtAccuracy, fmtCount, fmtDateTime, titleCase } from "../format.js";
import { emptyState, hostChip, sectionHead } from "../ui.js";

const EMPTY = { total: 0, resolved: 0, correct: 0, incorrect: 0, pending: 0, unresolvable: 0, accuracy: null };

function counts(s) {
  return `<dl class="counts">
    <div><dt>Resolved</dt><dd>${s.resolved}</dd></div>
    <div><dt>Correct</dt><dd class="c-good">${s.correct}</dd></div>
    <div><dt>Incorrect</dt><dd class="c-bad">${s.incorrect}</dd></div>
    <div><dt>Pending</dt><dd>${s.pending}</dd></div>
  </dl>`;
}

function categoryBars(card) {
  const cats = Object.entries(card.by_category || {}).filter(([, s]) => s.total > 0);
  if (!cats.length) return `<p class="muted small">No predictions yet.</p>`;
  cats.sort((a, b) => CATEGORY_ORDER.indexOf(a[0]) - CATEGORY_ORDER.indexOf(b[0]));
  const max = Math.max(...cats.map(([, s]) => s.total));
  const rows = cats
    .map(([cat, s]) => {
      const seg = (n, cls) => (n ? `<span class="${cls}" style="width:${((n / max) * 100).toFixed(1)}%"></span>` : "");
      return `<li><div class="bar-label"><span>${esc(cat)}</span><span class="muted">${s.correct}/${s.resolved} right · ${s.total} total</span></div>
        <div class="bar" role="img" aria-label="${esc(cat)}: ${s.correct} correct, ${s.incorrect} incorrect, ${s.pending} pending, ${s.unresolvable} unresolvable">
        ${seg(s.correct, "b-good")}${seg(s.incorrect, "b-bad")}${seg(s.pending, "b-pend")}${seg(s.unresolvable, "b-unres")}</div></li>`;
    })
    .join("");
  return `<ul class="bars">${rows}</ul>`;
}

function calibration(card) {
  const cells = BUCKET_ORDER.map((b) => {
    const s = (card.calibration || {})[b] || EMPTY;
    const acc = s.accuracy;
    const tone = acc === null || acc === undefined ? "none" : acc >= 0.67 ? "good" : acc >= 0.34 ? "mid" : "bad";
    return `<div class="cal cal-${tone}"><span class="cal-b">${esc(titleCase(b))}</span>
      <span class="cal-v">${fmtAccuracy(acc)}</span><span class="cal-n">${s.resolved}/${s.total} resolved</span></div>`;
  }).join("");
  return `<div class="cal-strip" aria-label="Accuracy by stated confidence">${cells}</div>`;
}

function hostCard(ctx, h, card) {
  const s = card.overall || EMPTY;
  return `<article class="card host-card h-${esc(h.key)}">
    <div class="host-head"><span class="dot" aria-hidden="true"></span><h2 class="host-name">${esc(h.name)}</h2></div>
    <div class="big-num">${fmtAccuracy(s.accuracy)}</div>
    <p class="muted">${s.resolved ? `accuracy on ${fmtCount(s.resolved, "resolved call")}` : "nothing resolved yet"} · ${fmtCount(s.total, "prediction")}</p>
    ${counts(s)}
    <h3 class="mini-h">By category</h3>${categoryBars(card)}
    <h3 class="mini-h">Calibration <span class="muted small">(accuracy by how sure they said they were)</span></h3>${calibration(card)}
  </article>`;
}

export async function render(root, ctx) {
  const sc = await ctx.load("scorecard.json");
  const hostsMap = (sc && sc.hosts) || {};
  const others = Object.entries((sc && sc.others) || {});
  const anything = Object.values(hostsMap).some((c) => c.overall?.total) || others.length;
  const cards = ctx.hosts.map((h) => hostCard(ctx, h, hostsMap[h.name] || { host: h.name, overall: EMPTY })).join("");
  const otherRows = others
    .map(([name, c]) => {
      const s = c.overall || EMPTY;
      return `<li>${hostChip(ctx, name)}<span class="muted small">${s.total} predictions · ${s.correct}/${s.resolved} right</span>
        <span class="o-acc">${fmtAccuracy(s.accuracy)}</span></li>`;
    })
    .join("");
  root.innerHTML = `
    ${sectionHead("Host scorecard", `Scoring rule: ${esc((sc?.counting_rule || "own-view predictions only").replace(/own_view/g, "own-view"))}. Updated ${fmtDateTime(sc?.updated_at)}.`)}
    ${anything ? "" : emptyState("No scored predictions yet", "Accuracy fills in as predictions resolve. Every host shows a dash until then.")}
    <div class="host-grid">${cards}</div>
    ${others.length ? `<section class="card pad others">${sectionHead("Guests and unattributed")}<ul class="other-list">${otherRows}</ul></section>` : ""}
  `;
}
