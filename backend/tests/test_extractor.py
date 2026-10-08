"""Extraction tests over fixture HTML."""

import pytest

import extractor

HTML = """
<!doctype html>
<html lang="en-GB">
<head>
  <title>  Example   Domain  </title>
  <meta name="description" content="A reserved example domain.">
  <meta property="og:title" content="Example Domain OG">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="canonical" href="https://example.com/canonical">
  <script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","name":"Example"}</script>
  <script type="application/ld+json">{not json}</script>
</head>
<body>
  <nav><a href="/nav">Nav</a></nav>
  <main>
    <h1>Main heading</h1>
    <h2>Sub   heading</h2>
    <h3></h3>
    <a href="/about">About us</a>
    <a href="https://example.com/about">Duplicate</a>
    <a href="mailto:hello@example.com">Email</a>
    <a href="javascript:void(0)">JS</a>
    <a href="#top">Top</a>
    <a href="tel:+15551234">Call</a>
    <a href="ftp://example.com/file">FTP</a>
    <img src="https://example.com/logo.png" alt="Logo" width="120" height="40">
    <img data-src="https://example.com/hero.jpg" alt="Hero">
    <img src="javascript:void(0)" alt="Tracker">
    <img src="https://example.com/logo.png" alt="Duplicate">
    <p>Contact hello@example.com or sales@example.org.</p>
  </main>
  <table>
    <tr><th>Name</th><th>Price</th></tr>
    <tr><td>Widget</td><td>9.99</td></tr>
  </table>
  <script>console.log("ignored");</script>
</body>
</html>
"""


@pytest.fixture(scope="module")
def data():
    return extractor.extract(HTML, "https://example.com/page")


def test_title_and_description(data):
    assert data["title"] == "Example Domain"
    assert data["description"] == "A reserved example domain."
    assert data["canonical"] == "https://example.com/canonical"
    assert data["language"] == "en-gb"


def test_headings_skip_empty(data):
    assert [h["text"] for h in data["headings"]] == ["Main heading", "Sub heading"]
    assert data["headings"][0]["level"] == "h1"


def test_links_are_absolute_deduplicated_and_filtered(data):
    urls = [link["url"] for link in data["links"]]
    assert urls == ["https://example.com/nav", "https://example.com/about"]
    assert len(urls) == len(set(urls))
    assert all(link["text"] for link in data["links"])


def test_images_use_lazy_attributes_and_skip_non_http(data):
    urls = [img["url"] for img in data["images"]]
    assert urls == ["https://example.com/logo.png", "https://example.com/hero.jpg"]
    assert data["images"][0]["width"] == 120


def test_metadata_filters_noise(data):
    names = {m["name"] for m in data["metadata"]}
    assert "description" in names
    assert "og:title" in names
    assert "viewport" not in names


def test_json_ld_skips_malformed_blocks(data):
    assert len(data["jsonLd"]) == 1
    assert data["jsonLd"][0]["@type"] == "Organization"


def test_emails_from_mailto_and_text(data):
    assert "hello@example.com" in data["emails"]
    assert "sales@example.org" in data["emails"]


def test_tables_extracted(data):
    assert data["tables"][0]["headers"] == ["Name", "Price"]
    assert data["tables"][0]["rows"] == [["Widget", "9.99"]]


LAYOUT_TABLE_HTML = """
<html><body>
<table role="presentation">
  <tr><td>site header</td></tr>
  <tr><td><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table></td></tr>
</table>
<table><tr><td>nav link</td></tr><tr><td>another</td></tr></table>
<table><tr><td>x</td><td>y</td></tr><tr><td>1</td><td>2</td></tr></table>
<table id="page"><tr><td>title bar</td></tr><tr><td><a>nav</a></td><td><a>login</a></td></tr>
<tr><td>row</td><td><a>story</a></td><td>pts</td></tr></table>
</body></html>
"""


def test_layout_tables_are_skipped():
    """Chrome built from single-cell tables must not appear as data.

    A real data table nested inside a presentation wrapper is still reported,
    just not merged with the wrapper's own rows.
    """
    tables = extractor.extract(LAYOUT_TABLE_HTML, "https://example.com/")["tables"]

    assert len(tables) == 2
    assert [t["headers"] for t in tables] == [["A", "B"], ["x", "y"]]
    assert all("site header" not in row for table in tables for row in table["headers"] + table["rows"])
    assert all("nav link" not in row for table in tables for row in table["headers"] + table["rows"])

    # Rows of ragged widths are page chrome, not a data table.
    assert all("title bar" not in row for table in tables for row in table["headers"] + table["rows"])


NAVBOX_HTML = """
<html><body>
<table class="navbox-inner"><tr><th>Lists of countries</th></tr>
<tr><td><a>Nominal</a></td><td><a>Per capita</a></td></tr></table>
<table class="wikitable"><tr><th>Country</th><th>GDP</th></tr>
<tr><td>World</td><td>100</td></tr></table>
</body></html>
"""


def test_named_navigation_tables_are_skipped():
    tables = extractor.extract(NAVBOX_HTML, "https://example.com/")["tables"]
    assert len(tables) == 1
    assert tables[0]["headers"] == ["Country", "GDP"]


def test_single_cell_header_is_not_treated_as_columns():
    html = "<table><tr><th>Section</th></tr><tr><td>a</td></tr><tr><td>b</td></tr></table>"
    assert extractor.extract(html, "https://example.com/")["tables"] == []


def test_text_excludes_script_content(data):
    assert "ignored" not in data["text"]
    assert "Main heading" in data["text"]


def test_summary_counts_match_lists(data):
    summary = data["summary"]
    assert summary["headings"] == len(data["headings"])
    assert summary["links"] == len(data["links"])
    assert summary["images"] == len(data["images"])


def test_handles_empty_and_broken_markup():
    for html in ("", "<html>", "not html at all", "<html><body><h1>unclosed"):
        result = extractor.extract(html, "https://example.com/")
        assert result["title"] == ""
        assert isinstance(result["links"], list)


def test_relative_links_resolve_against_final_url():
    data = extractor.extract('<a href="rel/page">Rel</a>', "https://example.com/dir/index.html")
    assert data["links"][0]["url"] == "https://example.com/dir/rel/page"