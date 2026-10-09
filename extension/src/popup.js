/**
 * Popup: inject the extractor into the active tab, render the result, export.
 *
 * The active tab is only reachable after the user clicks the toolbar icon,
 * which is what `activeTab` grants. Nothing is fetched from a server and the
 * page's own content is never modified.
 */

const $ = (id) => document.getElementById(id);

const state = { data: null, query: "" };

function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[c]
  );
}

function status(message, isError = false) {
  const el = $("status");
  el.textContent = message;
  el.classList.toggle("error", isError);
}

const RESTRICTED_URLS = ["chrome://", "edge://", "about:", "chrome-extension://", "devtools://", "view-source:"];

function unsupportedPageReason(url) {
  for (const prefix of RESTRICTED_URLS) {
    if (url.startsWith(prefix)) return "This page cannot be read by extensions.";
  }
  if (url.startsWith("https://chrome.google.com/webstore")) return "The Web Store blocks extensions from reading it.";
  if (url.startsWith("https://chromewebstore.google.com")) return "The Web Store blocks extensions from reading it.";
  return null;
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) throw new Error("No active tab found.");
  return tab;
}

/** Drop any previously extracted data so an error cannot leave stale results on screen. */
function showError(message) {
  status(message, true);
  state.data = null;
  state.query = "";
  $("data").hidden = true;
  $("loading").hidden = true;
  $("empty").hidden = false;
}

async function extract() {
  status("Reading page…");
  $("loading").hidden = false;
  $("empty").hidden = true;
  $("data").hidden = true;

  let tab;
  try {
    tab = await activeTab();
  } catch (error) {
    showError(error.message || "Could not read the active tab.");
    return;
  }

  const blocked = unsupportedPageReason(tab.url || "");
  if (blocked) {
    showError(blocked);
    return;
  }

  try {
    // Inject the same file the tests exercise, then read the value it sets.
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["src/extractor.js"],
    });
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => globalThis.ScrapelyExtract(),
    });

    const data = injection && injection.result;
    if (!data) throw new Error("The page returned no data.");

    state.data = data;
    state.query = "";
    $("search").value = "";
    render(data);
    status("");
  } catch (error) {
    showError(error.message || "Could not read this page.");
  }
}

function render(data) {
  $("loading").hidden = true;
  $("empty").hidden = true;
  $("data").hidden = false;

  $("pageTitle").textContent = data.title || "Untitled page";
  $("pageUrl").textContent = data.url;
  $("pageUrl").href = data.url;

  const summary = data.summary || {};
  const stats = [
    ["Headings", summary.headings],
    ["Links", summary.links],
    ["Images", summary.images],
    ["Metadata", summary.metadata],
    ["Tables", summary.tables],
    ["JSON-LD", summary.jsonLd],
    ["Emails", summary.emails],
    ["Characters", summary.characters],
  ];
  $("stats").innerHTML = stats
    .map(([label, value]) => `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? 0)}</strong></article>`)
    .join("");

  renderAll();
}

function matches(...values) {
  return values.join(" ").toLowerCase().includes(state.query);
}

/**
 * Render one label/value line.
 *
 * `value` is inserted as-is because callers pass markup (a link) they have
 * already escaped. Anything else must go through escapeHtml at the call site.
 */
function infoRow(label, value) {
  return `<div class="info-row"><span>${escapeHtml(label)}</span><span>${value}</span></div>`;
}

function box(items) {
  return items.length ? `<div class="scrollbox">${items.join("")}</div>` : '<p class="empty-small">No matching data.</p>';
}

function renderAll() {
  const data = state.data;
  if (!data) return;

  $("pageInfo").innerHTML =
    infoRow("Title", escapeHtml(data.title)) +
    infoRow("Description", escapeHtml(data.description)) +
    infoRow("Canonical", escapeHtml(data.canonical)) +
    infoRow("Language", escapeHtml(data.language)) +
    infoRow("Author", escapeHtml(data.author));

  $("headings").innerHTML = box(
    data.headings
      .filter((h) => matches(h.text, h.level))
      .map((h) => `<div class="item"><strong>${escapeHtml(h.level.toUpperCase())}</strong> ${escapeHtml(h.text)}</div>`)
  );

  $("links").innerHTML = box(
    data.links
      .filter((l) => matches(l.text, l.url))
      .map(
        (l) =>
          `<div class="item"><a href="${escapeHtml(l.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(
            l.text
          )}</a><span class="muted">${escapeHtml(l.url)}</span></div>`
      )
  );

  $("images").innerHTML = box(
    data.images
      .filter((i) => matches(i.alt, i.url))
      .map(
        (i) =>
          `<div class="image-item"><img src="${escapeHtml(i.url)}" alt="" loading="lazy">` +
          `<a href="${escapeHtml(i.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(i.alt || i.url)}</a></div>`
      )
  );

  $("tables").innerHTML = box(
    data.tables
      .filter((t) => matches(...t.headers, ...t.rows.flat()))
      .map((table, index) => {
        const head = `<tr>${table.headers.map((h) => `<th>${escapeHtml(h)}</th>`).join("")}</tr>`;
        const body = table.rows.map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`).join("");
        return (
          `<div class="item"><strong>Table ${index + 1}</strong> (${table.rows.length} rows)` +
          `<table class="data-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>`
        );
      })
  );

  $("metadata").innerHTML = box(
    data.metadata
      .filter((m) => matches(m.name, m.content))
      .map((m) => `<div class="item"><strong>${escapeHtml(m.name)}</strong><span class="muted">${escapeHtml(m.content)}</span></div>`)
  );

  $("jsonLd").innerHTML = box(
    data.jsonLd
      .filter((item) => matches(JSON.stringify(item)))
      .map(
        (item) =>
          `<div class="item"><strong>${escapeHtml(item["@type"] || item["@context"] || "structured data")}</strong>` +
          `<pre class="code">${escapeHtml(JSON.stringify(item, null, 2))}</pre></div>`
      )
  );

  const emails = data.emails || [];
  const panel = $("emails");
  panel.hidden = !(emails.length && matches(...emails));
  $("emailsCount").textContent = `${emails.length} found`;
  $("emailsList").textContent = emails.join(", ");

  $("pageText").innerHTML = matches(data.text)
    ? `<div class="scrollbox"><p class="prose">${escapeHtml(data.text)}</p></div>`
    : '<p class="empty-small">No matching data.</p>';
}

function download(filename, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 500);
}

/**
 * Neutralise a cell that a spreadsheet would evaluate as a formula.
 *
 * Page content is untrusted, and a scraped cell beginning with =, +, - or @
 * runs as a formula when the exported CSV is opened in Excel or Sheets. A
 * leading apostrophe keeps the text and stops the evaluation.
 */
function csvCell(value) {
  const text = String(value ?? "");
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return `"${safe.replace(/"/g, '""')}"`;
}

function toCsv(data) {
  const rows = [["type", "name", "value"]];
  rows.push(
    ["page", "url", data.url],
    ["page", "title", data.title],
    ["page", "description", data.description],
    ["page", "canonical", data.canonical],
    ["page", "language", data.language],
    ["page", "author", data.author]
  );

  data.headings.forEach((h) => rows.push(["heading", h.level, h.text]));
  data.links.forEach((l) => rows.push(["link", l.text, l.url]));
  data.images.forEach((i) => rows.push(["image", i.alt, i.url]));
  data.metadata.forEach((m) => rows.push(["metadata", m.name, m.content]));
  data.emails.forEach((e) => rows.push(["email", "email", e]));

  data.tables.forEach((table, index) => {
    table.rows.forEach((row) => {
      row.forEach((cell, cellIndex) => rows.push([`table-${index + 1}`, table.headers[cellIndex] || "", cell]));
    });
  });

  data.jsonLd.forEach((item) => rows.push(["json-ld", item["@type"] || "", JSON.stringify(item)]));

  return rows.map((row) => row.map(csvCell).join(",")).join("\n");
}

function filenameFor(data, extension) {
  const host = (() => {
    try {
      return new URL(data.url).hostname.replace(/[^a-z0-9.-]/gi, "_") || "page";
    } catch {
      return "page";
    }
  })();
  return `scrapely-${host}.${extension}`;
}

$("refresh").addEventListener("click", extract);

$("search").addEventListener("input", (event) => {
  state.query = event.target.value.trim().toLowerCase();
  renderAll();
});

$("json").addEventListener("click", () =>
  download(filenameFor(state.data, "json"), JSON.stringify(state.data, null, 2), "application/json")
);

$("csv").addEventListener("click", () => download(filenameFor(state.data, "csv"), toCsv(state.data), "text/csv"));

extract();