const state = { data: null, query: "" };
const $ = (id) => document.getElementById(id);

const CACHE_KEY = "scrapely_theme";
const PLACEHOLDER = "—";

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c])
  );
}

function status(message, isError = false) {
  const el = $("status");
  el.textContent = message;
  el.style.color = isError ? "var(--danger)" : "var(--muted)";
}

function setBusy(busy) {
  const button = $("extractForm").querySelector("button");
  button.disabled = busy;
  button.textContent = busy ? "Extracting…" : "Extract data";
}

async function extract() {
  const url = $("url").value.trim();
  if (!/^https?:\/\//i.test(url)) {
    status("Enter a complete http:// or https:// URL.", true);
    return;
  }

  status("Extracting page data…");
  setBusy(true);

  try {
    const response = await fetch(`/api/extract?url=${encodeURIComponent(url)}`);
    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(payload.detail || `Extraction failed (HTTP ${response.status}).`);
    }

    state.data = payload;
    state.query = "";
    $("search").value = "";
    render(payload);

    const total = Object.entries(payload.summary || {})
      .filter(([key]) => key !== "characters")
      .reduce((sum, [, value]) => sum + value, 0);

    status(`Extracted ${total} items.`);
    $("results").scrollIntoView({ behavior: "smooth" });
  } catch (error) {
    status(error.message || "Extraction failed.", true);
  } finally {
    setBusy(false);
  }
}

function render(data) {
  $("empty").hidden = true;
  $("data").hidden = false;
  $("resultTitle").textContent = data.title || "Untitled page";

  const summary = data.summary || {};
  $("headingCount").textContent = summary.headings ?? 0;
  $("linkCount").textContent = summary.links ?? 0;
  $("imageCount").textContent = summary.images ?? 0;
  $("metaCount").textContent = summary.metadata ?? 0;
  $("tableCount").textContent = summary.tables ?? 0;
  $("jsonLdCount").textContent = summary.jsonLd ?? 0;
  $("emailCount").textContent = summary.emails ?? 0;
  $("charCount").textContent = summary.characters ?? 0;

  $("json").disabled = false;
  $("csv").disabled = false;
  renderAll();
}

function matches(...values) {
  return values.join(" ").toLowerCase().includes(state.query);
}

function infoRow(label, value) {
  return `<div class="info-row"><span>${esc(label)}</span><span>${value || esc(PLACEHOLDER)}</span></div>`;
}

function box(items) {
  return items.length
    ? `<div class="scrollbox">${items.join("")}</div>`
    : '<p class="empty-small">No matching data.</p>';
}

function renderAll() {
  const data = state.data;
  if (!data) return;

  $("pageInfo").innerHTML =
    infoRow("URL", `<a href="${esc(data.url)}" target="_blank" rel="noopener noreferrer">${esc(data.url)}</a>`) +
    infoRow("Title", esc(data.title)) +
    infoRow("Description", esc(data.description)) +
    infoRow("Canonical", esc(data.canonical)) +
    infoRow("Language", esc(data.language)) +
    infoRow("Author", esc(data.author)) +
    infoRow("Extracted at", esc(data.fetchedAt));

  $("headings").innerHTML = box(
    (data.headings || [])
      .filter((h) => matches(h.text, h.level))
      .map((h) => `<div class="item"><strong>${esc(h.level.toUpperCase())}</strong> ${esc(h.text)}</div>`)
  );

  $("links").innerHTML = box(
    (data.links || [])
      .filter((l) => matches(l.text, l.url))
      .map(
        (l) =>
          `<div class="item"><a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(
            l.text
          )}</a><span class="muted">${esc(l.url)}</span></div>`
      )
  );

  $("images").innerHTML = box(
    (data.images || [])
      .filter((i) => matches(i.alt, i.url))
      .map(
        (i) =>
          `<div class="image-item"><img src="${esc(i.url)}" alt="" loading="lazy" onerror="this.style.display='none'">` +
          `<a href="${esc(i.url)}" target="_blank" rel="noopener noreferrer">${esc(i.alt || i.url)}</a></div>`
      )
  );

  $("tables").innerHTML = box(
    (data.tables || [])
      .filter((t) => matches(...t.headers, ...t.rows.flat()))
      .map((table, index) => {
        const head = `<tr>${table.headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr>`;
        const body = table.rows
          .map((row) => `<tr>${row.map((cell) => `<td>${esc(cell)}</td>`).join("")}</tr>`)
          .join("");
        return `<div class="item"><strong>Table ${index + 1}</strong> (${
          table.rows.length
        } rows)<table class="data-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
      })
  );

  $("metadata").innerHTML = box(
    (data.metadata || [])
      .filter((m) => matches(m.name, m.content))
      .map((m) => `<div class="item"><strong>${esc(m.name)}</strong><span class="muted">${esc(m.content)}</span></div>`)
  );

  $("jsonLd").innerHTML = box(
    (data.jsonLd || [])
      .filter((item) => matches(JSON.stringify(item)))
      .map(
        (item) =>
          `<div class="item"><strong>${esc(item["@type"] || item["@context"] || "structured data")}</strong>` +
          `<pre class="code">${esc(JSON.stringify(item, null, 2))}</pre></div>`
      )
  );

  const text = data.text || "";
  $("pageText").innerHTML = matches(text)
    ? `<div class="scrollbox"><p class="prose">${esc(text)}</p></div>`
    : '<p class="empty-small">No matching data.</p>';

  if (state.query) {
    const emailBox = (data.emails || []).filter((e) => matches(e));
    if (emailBox.length) {
      $("pageText").innerHTML =
        `<div class="item"><strong>Emails</strong><span class="muted">${emailBox
          .map((e) => esc(e))
          .join(", ")}</span></div>` + $("pageText").innerHTML;
    }
  }
}

function download(filename, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 500);
}

function toCsv(data) {
  const rows = [["type", "name", "value"]];

  rows.push(["page", "url", data.url], ["page", "title", data.title], ["page", "description", data.description]);
  rows.push(["page", "canonical", data.canonical], ["page", "language", data.language], ["page", "author", data.author]);

  (data.headings || []).forEach((h) => rows.push(["heading", h.level, h.text]));
  (data.links || []).forEach((l) => rows.push(["link", l.text, l.url]));
  (data.images || []).forEach((i) => rows.push(["image", i.alt, i.url]));
  (data.metadata || []).forEach((m) => rows.push(["metadata", m.name, m.content]));
  (data.emails || []).forEach((e) => rows.push(["email", "email", e]));

  (data.tables || []).forEach((table, index) => {
    table.rows.forEach((row) => {
      row.forEach((cell, cellIndex) =>
        rows.push([`table-${index + 1}`, table.headers[cellIndex] || "", cell])
      );
    });
  });

  (data.jsonLd || []).forEach((item) => rows.push(["json-ld", item["@type"] || "", JSON.stringify(item)]));

  return rows
    .map((row) => row.map((value) => `"${String(value ?? "").replace(/"/g, '""')}"`).join(","))
    .join("\n");
}

function applyTheme(theme) {
  document.body.classList.toggle("dark", theme === "dark");
  $("theme").textContent = theme === "dark" ? "☀" : "☾";
  localStorage.setItem(CACHE_KEY, theme);
}

$("extractForm").addEventListener("submit", (event) => {
  event.preventDefault();
  extract();
});

$("search").addEventListener("input", (event) => {
  state.query = event.target.value.trim().toLowerCase();
  renderAll();
});

$("json").addEventListener("click", () =>
  download("scrapely-data.json", JSON.stringify(state.data, null, 2), "application/json")
);

$("csv").addEventListener("click", () =>
  download("scrapely-data.csv", toCsv(state.data), "text/csv")
);

$("theme").addEventListener("click", () => {
  applyTheme(document.body.classList.contains("dark") ? "light" : "dark");
});

applyTheme(localStorage.getItem(CACHE_KEY) === "dark" ? "dark" : "light");