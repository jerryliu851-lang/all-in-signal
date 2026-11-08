// View 2: the full prediction ledger with filters.
import { CATEGORY_ORDER, esc, STATUS_LABEL } from "../format.js";
import { emptyState, predictionRow, sectionHead } from "../ui.js";

const state = { host: "all", category: "all", status: "all", q: "" };

function matches(ctx, e) {
  if (state.host !== "all") {
    const h = ctx.host(e.speaker);
    const key = h.isHost ? h.key : "others";
    if (key !== state.host) return false;
  }
  if (state.category !== "all" && e.category !== state.category) return false;
  if (state.status === "overdue") {
    if (!e.timeframe_passed) return false;
  } else if (state.status !== "all" && e.status !== state.status) return false;
  if (state.q) {
    const hay = `${e.claim} ${e.speaker} ${e.episode_title} ${e.timeframe?.text || ""} ${e.polymarket_question || ""}`;
    if (!hay.toLowerCase().includes(state.q.toLowerCase())) return false;
  }
  return true;
}

function options(values, current, labels = {}) {
  return values
    .map((v) => `<option value="${esc(v)}"${v === current ? " selected" : ""}>${esc(labels[v] || v)}</option>`)
    .join("");
}

export async function render(root, ctx) {
  const ledger = await ctx.load("predictions.json");
  const all = (ledger && ledger.predictions) || [];
  if (!all.length) {
    root.innerHTML = sectionHead("Prediction ledger") + emptyState("No predictions yet",
      "Predictions appear here after the first episode is analyzed.");
    return;
  }
  const cats = CATEGORY_ORDER.filter((c) => all.some((e) => e.category === c));
  const hostChips = [["all", "All"], ...ctx.hosts.map((h) => [h.key, h.short]), ["others", "Guests & other"]]
    .map(([k, l]) => `<button type="button" class="fchip${k === "all" ? "" : ` h-${k === "others" ? "other" : k}`}" data-host="${esc(k)}"
        aria-pressed="${state.host === k}">${k === "all" ? "" : '<span class="dot" aria-hidden="true"></span>'}${esc(l)}</button>`)
    .join("");

  root.innerHTML = `
    ${sectionHead("Prediction ledger", `Every forward-looking claim, newest episode first. Dashed rows are reported views or devil's advocate; a dashed or dotted chip means the speaker was inferred.`)}
    <div class="card pad filters">
      <label class="search"><span class="sr-only">Search predictions</span>
        <input type="search" id="lq" placeholder="Search claims, hosts, episodes" value="${esc(state.q)}" autocomplete="off"></label>
      <div class="fchips" role="group" aria-label="Filter by host">${hostChips}</div>
      <div class="selects">
        <label>Category<select id="lcat"><option value="all">All categories</option>${options(cats, state.category)}</select></label>
        <label>Status<select id="lst"><option value="all">All statuses</option>${options(
          [...Object.keys(STATUS_LABEL), "overdue"], state.status, { ...STATUS_LABEL, overdue: "Overdue (pending, past date)" }
        )}</select></label>
      </div>
    </div>
    <p class="count muted" id="lcount" aria-live="polite"></p>
    <div class="rows" id="lrows"></div>`;

  const rows = root.querySelector("#lrows");
  const count = root.querySelector("#lcount");
  const draw = () => {
    const shown = all.filter((e) => matches(ctx, e));
    count.textContent = `Showing ${shown.length} of ${all.length} predictions`;
    rows.innerHTML = shown.length
      ? shown.map((e) => predictionRow(ctx, e)).join("")
      : emptyState("Nothing matches", "Try clearing a filter or the search box.");
  };
  root.querySelector("#lq").addEventListener("input", (ev) => {
    state.q = ev.target.value.trim();
    draw();
  });
  root.querySelector("#lcat").addEventListener("change", (ev) => {
    state.category = ev.target.value;
    draw();
  });
  root.querySelector("#lst").addEventListener("change", (ev) => {
    state.status = ev.target.value;
    draw();
  });
  root.querySelector(".fchips").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-host]");
    if (!b) return;
    state.host = b.dataset.host;
    root.querySelectorAll(".fchips button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    draw();
  });
  draw();
}
