/**
 * Page extraction, running against the live DOM.
 *
 * This is a port of backend/extractor.py. The two must be kept in step: same
 * sections, same filtering rules, same output shape, so a page extracted in
 * the browser matches one extracted by the server.
 *
 * Everything here is pure DOM reading. No fetch, no network, no eval.
 */

const LIMITS = {
  headings: 500,
  links: 500,
  images: 300,
  metadata: 200,
  tables: 25,
  jsonLd: 20,
  emails: 25,
  text: 20000,
};

const SKIPPED_LINK_PREFIXES = ["#", "mailto:", "tel:", "javascript:", "data:", "sms:"];
const HTTP_SCHEMES = new Set(["http:", "https:"]);
const META_NAME_RE =
  /^(description|keywords|author|robots|og:[a-z0-9_:]+|twitter:[a-z0-9_:]+|article:[a-z0-9_:]+)$/i;
const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;

// Whole class/id tokens that mark a table as page furniture rather than data.
const LAYOUT_CLASS_HINTS = new Set([
  "navbox",
  "navigation",
  "nav",
  "toc",
  "menu",
  "footer",
  "breadcrumb",
  "sidebar",
  "banner",
  "wrapper",
  "layout",
]);

function clean(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function absolute(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return null;
  try {
    const resolved = new URL(raw, document.baseURI);
    return HTTP_SCHEMES.has(resolved.protocol) ? resolved.href : null;
  } catch {
    return null;
  }
}

function extractTitle(doc) {
  if (doc.title) return clean(doc.title);
  const og = doc.querySelector('meta[property="og:title"]');
  return og ? clean(og.getAttribute("content")) : "";
}

function extractDescription(doc) {
  const selectors = [
    'meta[name="description"]',
    'meta[property="og:description"]',
    'meta[name="twitter:description"]',
  ];
  for (const selector of selectors) {
    const tag = doc.querySelector(selector);
    const value = tag && clean(tag.getAttribute("content"));
    if (value) return value;
  }
  return "";
}

function extractCanonical(doc) {
  for (const link of doc.querySelectorAll('link[rel]')) {
    const rel = (link.getAttribute("rel") || "").toLowerCase().split(/\s+/);
    if (!rel.includes("canonical")) continue;
    const url = absolute(link.getAttribute("href"));
    if (url) return url;
  }
  const og = doc.querySelector('meta[property="og:url"]');
  return og ? clean(og.getAttribute("content")) : "";
}

function extractLanguage(doc) {
  const value = doc.documentElement.getAttribute("lang") || doc.documentElement.getAttribute("xml:lang");
  return value ? clean(value).slice(0, 16).toLowerCase() : "";
}

function extractAuthor(doc) {
  const selectors = ['meta[name="author"]', 'meta[property="article:author"]', 'meta[name="twitter:creator"]'];
  for (const selector of selectors) {
    const tag = doc.querySelector(selector);
    const value = tag && clean(tag.getAttribute("content"));
    if (value) return value;
  }
  return "";
}

function extractHeadings(doc) {
  const headings = [];
  for (const tag of doc.querySelectorAll("h1, h2, h3, h4, h5, h6")) {
    const text = clean(tag.textContent);
    if (text) headings.push({ level: tag.tagName.toLowerCase(), text });
    if (headings.length >= LIMITS.headings) break;
  }
  return headings;
}

function extractLinks(doc) {
  const links = [];
  const seen = new Set();
  for (const tag of doc.querySelectorAll("a[href]")) {
    const href = tag.getAttribute("href").trim();
    if (!href) continue;
    if (SKIPPED_LINK_PREFIXES.some((prefix) => href.toLowerCase().startsWith(prefix))) continue;
    const url = absolute(href);
    if (!url || seen.has(url)) continue;
    seen.add(url);
    links.push({ text: clean(tag.textContent) || url, url });
    if (links.length >= LIMITS.links) break;
  }
  return links;
}

function extractImages(doc) {
  const images = [];
  const seen = new Set();
  const attributes = ["src", "data-src", "data-original", "data-lazy-src"];
  for (const tag of doc.querySelectorAll("img")) {
    let url = null;
    for (const attribute of attributes) {
      const candidate = tag.getAttribute(attribute);
      if (candidate && candidate.trim()) {
        url = absolute(candidate);
        if (url) break;
      }
    }
    if (!url || seen.has(url)) continue;
    seen.add(url);
    const width = Number.parseInt(tag.getAttribute("width"), 10);
    const height = Number.parseInt(tag.getAttribute("height"), 10);
    images.push({
      alt: clean(tag.getAttribute("alt")),
      url,
      width: Number.isFinite(width) ? width : null,
      height: Number.isFinite(height) ? height : null,
    });
    if (images.length >= LIMITS.images) break;
  }
  return images;
}

function extractMetadata(doc) {
  const metadata = [];
  for (const tag of doc.querySelectorAll("meta")) {
    const key = tag.getAttribute("name") || tag.getAttribute("property") || tag.getAttribute("http-equiv");
    const content = tag.getAttribute("content");
    if (!key || !content) continue;
    const name = clean(key);
    if (!META_NAME_RE.test(name)) continue;
    metadata.push({ name, content: clean(content) });
    if (metadata.length >= LIMITS.metadata) break;
  }
  return metadata;
}

function isLayoutTable(table) {
  if (table.getAttribute("role") === "presentation") return true;

  const tokens = new Set(
    [table.getAttribute("class"), table.getAttribute("id")]
      .filter(Boolean)
      .flatMap((value) => value.toLowerCase().split(/\s+/))
  );
  for (const token of tokens) {
    if (LAYOUT_CLASS_HINTS.has(token)) return true;
  }

  const rows = Array.from(table.querySelectorAll("tr"))
    .map((tr) => Array.from(tr.querySelectorAll("th, td")))
    .filter((cells) => cells.length > 0);
  if (rows.length < 2) return true;
  if (rows[0].length < 2) return true;

  const widths = rows.map((cells) => cells.length);
  const counts = new Map();
  for (const width of widths) counts.set(width, (counts.get(width) || 0) + 1);
  let common = 0;
  let frequency = 0;
  for (const [width, count] of counts) {
    if (count > frequency) {
      common = width;
      frequency = count;
    }
  }
  return frequency / widths.length < 0.8 || common < 2;
}

function extractTables(doc) {
  const tables = [];
  for (const table of doc.querySelectorAll("table")) {
    const rows = [];
    for (const tr of table.querySelectorAll("tr")) {
      const cells = Array.from(tr.querySelectorAll("th, td")).map((cell) => clean(cell.textContent));
      if (cells.some(Boolean)) rows.push(cells);
    }
    if (rows.length >= 2 && !isLayoutTable(table)) {
      tables.push({ headers: rows[0], rows: rows.slice(1) });
    }
    if (tables.length >= LIMITS.tables) break;
  }
  return tables;
}

function extractJsonLd(doc) {
  const results = [];
  for (const script of doc.querySelectorAll('script[type="application/ld+json"]')) {
    const raw = (script.textContent || "").trim();
    if (!raw) continue;
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      continue;
    }
    for (const item of Array.isArray(data) ? data : [data]) {
      if (item && typeof item === "object" && !Array.isArray(item)) results.push(item);
    }
    if (results.length >= LIMITS.jsonLd) break;
  }
  return results;
}

function extractText(doc) {
  // Clone so script and style contents stay out of the text without mutating
  // the page the user is looking at.
  const clone = doc.body ? doc.body.cloneNode(true) : doc.documentElement.cloneNode(true);
  for (const tag of clone.querySelectorAll("script, style, noscript, template")) tag.remove();
  const main = clone.querySelector("main") || clone.querySelector("article") || clone;
  return clean(main.textContent).slice(0, LIMITS.text);
}

function extractEmails(doc, text) {
  const found = [];
  const seen = new Set();

  for (const tag of doc.querySelectorAll("a[href]")) {
    const href = tag.getAttribute("href").trim();
    if (!href.toLowerCase().startsWith("mailto:")) continue;
    const address = href.slice(7).split("?")[0].trim();
    if (address && !seen.has(address.toLowerCase())) {
      seen.add(address.toLowerCase());
      found.push(address);
    }
  }

  if (found.length < LIMITS.emails) {
    for (const match of text.match(EMAIL_RE) || []) {
      if (seen.has(match.toLowerCase())) continue;
      seen.add(match.toLowerCase());
      found.push(match);
      if (found.length >= LIMITS.emails) break;
    }
  }
  return found;
}

/** Extract every supported section from the current page. */
function extractPage() {
  const doc = document;
  const headings = extractHeadings(doc);
  const links = extractLinks(doc);
  const images = extractImages(doc);
  const metadata = extractMetadata(doc);
  const tables = extractTables(doc);
  const jsonLd = extractJsonLd(doc);
  const text = extractText(doc);
  const emails = extractEmails(doc, text);

  return {
    url: location.href,
    title: extractTitle(doc),
    description: extractDescription(doc),
    canonical: extractCanonical(doc),
    language: extractLanguage(doc),
    author: extractAuthor(doc),
    text,
    emails,
    fetchedAt: new Date().toISOString(),
    headings,
    links,
    images,
    metadata,
    tables,
    jsonLd,
    summary: {
      headings: headings.length,
      links: links.length,
      images: images.length,
      metadata: metadata.length,
      tables: tables.length,
      jsonLd: jsonLd.length,
      emails: emails.length,
      characters: text.length,
    },
  };
}

// Exposed for the popup, which injects this file into the active tab.
globalThis.ScrapelyExtract = extractPage;