// Loads JSON documents written by `allin build-site`. Paths are RELATIVE so the page works
// under any GitHub Pages sub-path (e.g. /all-in-signal/).

const cache = new Map();

export function loadJSON(name) {
  if (!cache.has(name)) {
    const p = fetch(`data/${name}`, { cache: "no-cache" })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null);
    cache.set(name, p);
  }
  return cache.get(name);
}

const DEFAULT_HOSTS = [
  { name: "Chamath Palihapitiya", short: "Chamath", key: "chamath" },
  { name: "David Sacks", short: "Sacks", key: "sacks" },
  { name: "David Friedberg", short: "Friedberg", key: "friedberg" },
  { name: "Jason Calacanis", short: "Jason", key: "jason" },
];

/** Context shared by every view. */
export async function createContext() {
  const manifest = (await loadJSON("manifest.json")) || {};
  const hosts = Array.isArray(manifest.hosts) && manifest.hosts.length ? manifest.hosts : DEFAULT_HOSTS;
  const byName = new Map();
  for (const h of hosts) {
    byName.set(h.name.toLowerCase(), h);
    byName.set(h.short.toLowerCase(), h);
  }
  /** speaker string -> { key, short, name, isHost } */
  function host(speaker) {
    const s = String(speaker || "Unknown").trim();
    const h = byName.get(s.toLowerCase());
    if (h) return { ...h, isHost: true };
    if (/^guest\s*:/i.test(s)) {
      const n = s.replace(/^guest\s*:\s*/i, "");
      return { key: "guest", short: n || "Guest", name: s, isHost: false };
    }
    return { key: "other", short: s || "Unknown", name: s, isHost: false };
  }
  const flagPoints = Number(manifest.divergence_flag_points ?? 25);
  return { manifest, hosts, host, flagPoints, load: loadJSON };
}
