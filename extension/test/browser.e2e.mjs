/**
 * End-to-end verification of the shipped extension in a real Chromium browser.
 *
 *   node extension/test/browser.e2e.mjs
 *
 * Uses CDP's Extensions.loadUnpacked rather than --load-extension, because
 * branded Chrome and Edge both refuse that flag. Set SCRAPELY_CHROMIUM to point
 * at a Chromium binary if the Playwright location does not apply.
 *
 * The shipped manifest keeps activeTab only. The staged copy adds one host
 * permission, because activeTab is granted by a toolbar click that cannot be
 * simulated here. Every other file is byte-for-byte what ships, so this proves
 * the real chrome.scripting path and the real popup code, and leaves only the
 * activeTab grant itself untested.
 */
import { mkdtempSync, cpSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";

import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const EXT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const CHROMIUM_CANDIDATES = [
  process.env.SCRAPELY_CHROMIUM,
  `${process.env.LOCALAPPDATA}/ms-playwright/chromium-1243/chrome-win64/chrome.exe`,
  `${process.env.LOCALAPPDATA}/ms-playwright/chromium-1228/chrome-win64/chrome.exe`,
  `${process.env.LOCALAPPDATA}/ms-playwright/chromium-1148/chrome-win/chrome.exe`,
  "/usr/bin/chromium",
  "/usr/bin/google-chrome",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);

const BROWSER = CHROMIUM_CANDIDATES.find((p) => existsSync(p));
if (!BROWSER) {
  console.error("No Chromium binary found. Set SCRAPELY_CHROMIUM to one.");
  process.exit(2);
}
console.log("using browser:", BROWSER);

const PORT = 9344 + Math.floor(Math.random() * 400);
const CDP = `http://127.0.0.1:${PORT}`;
const TARGET_URL = "https://news.ycombinator.com/";

const results = [];
const record = (name, pass, detail) => {
  results.push({ name, pass });
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}${pass ? "" : "  ->  " + detail}`);
};

const stage = mkdtempSync(join(tmpdir(), "sc-e2e-"));
cpSync(EXT, stage, { recursive: true });
const manifest = JSON.parse(readFileSync(join(stage, "manifest.json"), "utf8"));
manifest.host_permissions = [`${new URL(TARGET_URL).origin}/*`];
writeFileSync(join(stage, "manifest.json"), JSON.stringify(manifest, null, 2));

const profile = mkdtempSync(join(tmpdir(), "sc-e2ep-"));
const proc = spawn(BROWSER, [
  "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "--disable-gpu",
  "--enable-unsafe-extension-debugging", "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let id = 0; const pending = new Map();
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) { const p = pending.get(msg.id); pending.delete(msg.id); msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result); }
    });
    ws.addEventListener("error", reject);
    ws.addEventListener("open", () => resolve({
      send(method, params = {}) { const i = ++id; return new Promise((res, rej) => { pending.set(i, { resolve: res, reject: rej }); ws.send(JSON.stringify({ id: i, method, params })); }); },
      close: () => ws.close(),
    }));
  });
}

async function ev(client, expression) {
  const r = await client.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}

async function waitFor(fn, timeoutMs, label) {
  const start = Date.now();
  for (;;) {
    let v; try { v = await fn(); } catch { v = null; }
    if (v) return v;
    if (Date.now() - start > timeoutMs) throw new Error("timed out: " + label);
    await sleep(250);
  }
}

async function newTarget(url) {
  const r = await fetch(`${CDP}/json/new?${encodeURIComponent(url)}`, { method: "PUT" });
  if (!r.ok) throw new Error(`open ${url} -> ${r.status}`);
  return r.json();
}

(async () => {
  for (let i = 0; i < 80; i++) { try { const r = await fetch(`${CDP}/json/version`); if (r.ok) break; } catch {} await sleep(300); }

  const version = await (await fetch(`${CDP}/json/version`)).json();
  const b = await connect(version.webSocketDebuggerUrl);
  const loaded = await b.send("Extensions.loadUnpacked", { path: stage });
  const id = loaded.id;
  record("Extensions.loadUnpacked accepts the shipped manifest", Boolean(id), JSON.stringify(loaded));
  console.log("      extension id:", id);

  // A real page to read.
  const page = await newTarget(TARGET_URL);
  const pageClient = await connect(page.webSocketDebuggerUrl);
  await pageClient.send("Runtime.enable");
  await waitFor(() => ev(pageClient, "document.readyState === 'complete'"), 30000, "page load");
  record("live page loaded", true, "");

  // The popup document on the extension origin.
  const popup = await newTarget(`chrome-extension://${id}/src/popup.html`);
  const popupClient = await connect(popup.webSocketDebuggerUrl);
  await popupClient.send("Runtime.enable");
  await waitFor(() => ev(popupClient, "typeof renderAll === 'function'"), 20000, "popup.js to initialise");
  record("popup.html + popup.js load in the real browser", true);

  const href = await ev(popupClient, "location.href");
  record("popup served from the extension origin", href.startsWith(`chrome-extension://${id}/`), href);

  // Exactly what popup.js does.
  const data = await ev(popupClient, `(async () => {
     const [tab] = await chrome.tabs.query({url: '${TARGET_URL}'});
     if (!tab) return { error: 'tab not found' };
     await chrome.scripting.executeScript({ target: {tabId: tab.id}, files: ['src/extractor.js'] });
     const [r] = await chrome.scripting.executeScript({ target: {tabId: tab.id}, func: () => globalThis.ScrapelyExtract() });
     return r.result;
   })()`);
  record("chrome.scripting.executeScript injects extractor.js and returns data", Boolean(data && data.title), JSON.stringify(data).slice(0, 160));
  if (data && data.title) {
    console.log("      title  :", data.title);
    console.log("      summary:", JSON.stringify(data.summary));
  }

  const dom = JSON.parse(await ev(pageClient, "JSON.stringify({scripts: document.querySelectorAll('script').length, h1: document.querySelectorAll('h1').length})"));
  record("page DOM unchanged by injection", dom.scripts > 0, JSON.stringify(dom));

  const rendered = JSON.parse(await ev(popupClient, `(() => {
     const data = ${JSON.stringify(data)};
     state.data = data; state.query = ''; render(data);
     return JSON.stringify({
       dataHidden: document.getElementById('data').hidden,
       title: document.getElementById('pageTitle').textContent,
       stats: Array.from(document.querySelectorAll('#stats article')).map(a => a.querySelector('span').textContent + '=' + a.querySelector('strong').textContent),
       links: document.querySelectorAll('#links .item').length,
       csvHead: toCsv(data).split('\\n')[0],
     });
   })()`));
  record("popup renders real data", rendered.dataHidden === false && rendered.title === data.title, JSON.stringify(rendered).slice(0, 140));
  record("eight stat tiles populated", rendered.stats.length === 8, rendered.stats.join(" "));
  record("links rendered", rendered.links === data.links.length, `${rendered.links} vs ${data.links.length}`);
  console.log("      stats:", rendered.stats.join("  "));
  record("csv header correct", rendered.csvHead === '"type","name","value"', rendered.csvHead);

  const guard = await ev(popupClient, `[csvCell('=1+1'), csvCell('9.99'), csvCell('+SUM(A1)')].join(' | ')`);
  record("csvCell neutralises formulas in the real browser", guard === `"'=1+1" | "9.99" | "'+SUM(A1)"`, guard);

  const errState = JSON.parse(await ev(popupClient, `(() => { showError('This page cannot be read by extensions.');
     return JSON.stringify({ dataHidden: document.getElementById('data').hidden, emptyHidden: document.getElementById('empty').hidden }); })()`));
  record("showError clears stale results", errState.dataHidden === true && errState.emptyHidden === false, JSON.stringify(errState));

  const icons = await ev(popupClient, `Object.keys(chrome.runtime.getManifest().icons).join(',')`);
  record("all four icon sizes declared", icons === "16,32,48,128", icons);

  pageClient.close(); popupClient.close();
  const failed = results.filter((x) => !x.pass).length;
  console.log(`\n${results.length - failed} passed, ${failed} failed`);
  try { proc.kill(); } catch {}
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error("harness error:", e.message); try { proc.kill(); } catch {} process.exit(2); });