// All-In Signal dashboard: hash router + shell. Plain ES modules, no build step.
import { createContext } from "./data.js";
import { fmtDateTime } from "./format.js";
import * as hosts from "./views/hosts.js";
import * as latest from "./views/latest.js";
import * as ledger from "./views/ledger.js";
import * as polymarket from "./views/polymarket.js";
import * as stocks from "./views/stocks.js";
import * as themes from "./views/themes.js";

const ROUTES = { latest, ledger, hosts, stocks, polymarket, themes };
const TITLES = {
  latest: "Latest episode",
  ledger: "Prediction ledger",
  hosts: "Host scorecard",
  stocks: "Stock implications",
  polymarket: "Polymarket board",
  themes: "Themes over time",
};

let ctx;
let renderToken = 0;

function currentRoute() {
  const m = /^#\/([a-z]+)/.exec(location.hash || "");
  return m && ROUTES[m[1]] ? m[1] : "latest";
}

async function route() {
  const name = currentRoute();
  const token = ++renderToken;
  document.querySelectorAll("[data-route]").forEach((a) => {
    if (a.dataset.route === name) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  document.title = `${TITLES[name]} · All-In Signal`;
  const main = document.getElementById("main");
  const view = document.createElement("div");
  view.className = "view";
  try {
    await ROUTES[name].render(view, ctx);
  } catch (err) {
    console.error(err);
    view.innerHTML = `<div class="card empty"><h3>Something went wrong</h3><p>This view could not be drawn from the data files.</p></div>`;
  }
  if (token !== renderToken) return;
  main.replaceChildren(view);
  window.scrollTo({ top: 0 });
}

async function boot() {
  ctx = await createContext();
  const m = ctx.manifest || {};
  if (m.synthetic) document.getElementById("banner").hidden = false;
  const built = document.getElementById("built");
  if (built) {
    built.textContent = m.built_at
      ? `Built ${fmtDateTime(m.built_at)} · ${m.episode_count ?? 0} episodes · ${m.prediction_count ?? 0} predictions`
      : "Build info unavailable";
  }
  if (!location.hash) history.replaceState(null, "", "#/latest");
  window.addEventListener("hashchange", route);
  await route();
}

boot();
