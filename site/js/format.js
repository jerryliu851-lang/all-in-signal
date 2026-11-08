// Formatting helpers. Every number, date and time on the page goes through these.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** "2026-10-31" -> "Oct 31, 2026". Accepts full ISO timestamps too. */
export function fmtDate(iso) {
  if (!iso) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
  if (!m) return String(iso);
  return `${MONTHS[Number(m[2]) - 1]} ${Number(m[3])}, ${m[1]}`;
}

/** "2026-11-07T12:00:00Z" -> "Nov 7, 2026 12:00 UTC". */
export function fmtDateTime(iso) {
  if (!iso) return "—";
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/.exec(String(iso));
  return m ? `${fmtDate(m[1])} ${m[2]}:${m[3]} UTC` : fmtDate(iso);
}

/** Seconds -> "m:ss" or "h:mm:ss". */
export function fmtClock(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** Seconds -> "1 h 34 min". */
export function fmtDuration(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}

/** Probability 0..1 -> "62%"; null -> "—". */
export function fmtProb(p) {
  if (p === null || p === undefined || Number.isNaN(Number(p))) return "—";
  return `${Math.round(Number(p) * 100)}%`;
}

/** Accuracy 0..1 -> "67%"; null -> "—". */
export const fmtAccuracy = fmtProb;

/** Divergence points -> "47 pts". */
export function fmtPoints(p) {
  if (p === null || p === undefined) return "—";
  return `${Math.round(Number(p))} pts`;
}

/** Signed integer -> "+2", "−1", "0". */
export function fmtSigned(n) {
  const v = Number(n) || 0;
  if (v > 0) return `+${v}`;
  if (v < 0) return `−${Math.abs(v)}`;
  return "0";
}

export function fmtCount(n, singular, plural = `${singular}s`) {
  const v = Number(n) || 0;
  return `${v} ${v === 1 ? singular : plural}`;
}

const ACRONYMS = new Set(["ai", "ipo", "us", "gdp", "cpi", "ev", "etf", "m&a", "eu", "uk"]);

/** "ai capex" -> "AI Capex". */
export function titleCase(s) {
  return String(s || "")
    .split(/(\s+)/)
    .map((w) => (ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : w.replace(/^([a-z])/, (c) => c.toUpperCase())))
    .join("");
}

export function ytUrl(videoId, seconds) {
  const base = `https://www.youtube.com/watch?v=${encodeURIComponent(videoId)}`;
  return seconds === undefined || seconds === null ? base : `${base}&t=${Math.floor(seconds)}s`;
}

export const STATUS_LABEL = {
  pending: "Pending",
  resolved_correct: "Correct",
  resolved_incorrect: "Incorrect",
  unresolvable: "Unresolvable",
};

export const STANCE_LABEL = {
  own_view: "Own view",
  reported_view: "Reported view",
  devils_advocate: "Devil's advocate",
};

export const CATEGORY_ORDER = ["macro", "markets", "politics", "tech", "AI", "crypto", "company-specific", "other"];
export const BUCKET_ORDER = ["high", "medium", "low", "unstated"];
