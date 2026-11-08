// View 6: macro themes over time.
import { esc, fmtCount, fmtDate, titleCase } from "../format.js";
import { emptyState, hostChip, sectionHead } from "../ui.js";

function entryItem(ctx, e) {
  const speakers = (e.speakers || []).map((s) => hostChip(ctx, s)).join("");
  return `<li class="tl-item">
    <div class="tl-date">${fmtDate(e.published_at)}</div>
    <p class="tl-text">${esc(e.stance_summary)}</p>
    <div class="row-foot">${speakers}<a class="ts" href="${esc(e.timestamp_url)}" target="_blank" rel="noopener"
      aria-label="Watch this discussion on YouTube"><span aria-hidden="true">▶</span> watch</a></div>
    <p class="muted small">${esc(e.episode_title)}</p>
  </li>`;
}

export async function render(root, ctx) {
  const doc = await ctx.load("themes.json");
  const themes = (doc && doc.themes) || [];
  const intro = sectionHead("Themes over time", "Macro themes ranked by how often they come up. Open one to see how the stance shifted episode by episode.");
  if (!themes.length) {
    root.innerHTML = intro + emptyState("No themes yet", "Recurring macro themes show up once episodes are analyzed.");
    return;
  }
  const max = Math.max(...themes.map((t) => t.frequency));
  const items = themes
    .map(
      (t, i) => `<details class="card theme"${i === 0 ? " open" : ""}>
      <summary>
        <span class="rank" aria-hidden="true">${i + 1}</span>
        <span class="theme-main"><span class="theme-name">${esc(titleCase(t.theme))}</span>
          <span class="muted small">${fmtCount(t.frequency, "mention")} · ${fmtDate(t.first_seen)} → ${fmtDate(t.last_seen)}</span>
          <span class="freq" aria-hidden="true"><span style="width:${((t.frequency / max) * 100).toFixed(1)}%"></span></span></span>
        <span class="chev" aria-hidden="true"></span>
      </summary>
      <ol class="timeline">${t.entries.map((e) => entryItem(ctx, e)).join("")}</ol>
    </details>`
    )
    .join("");
  root.innerHTML = `${intro}<div class="themes">${items}</div>`;
}
