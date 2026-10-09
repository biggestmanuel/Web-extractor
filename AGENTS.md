# AGENTS.md

Working agreements for coding agents on this repository.

## Commit and push rule

**After every file change, commit and push.**

Concretely, for any edit to a tracked file:

1. Run the tests: `python -m pytest`
2. Review your own diff: `git diff`
3. Commit with a message explaining *why*, not just what changed
4. Push: `git push origin main`

Do not batch several unrelated changes into one commit. Do not leave a change
uncommitted at the end of a task. Never push straight to a branch other than
`main` unless asked.

If a push fails, report the failure rather than retrying silently. On Windows
PowerShell, git writes progress messages to stderr, so `git push` can look like
an error even when it succeeded. Confirm with `git status -sb` and
`git log --oneline origin/main -1` before reporting a problem.

## Code review before committing

Before every commit, review the pending diff for problems that tests do not
catch. Specifically:

- **Leaked secrets.** No API keys, tokens, passwords, private keys or
  connection strings, in code, tests, fixtures, comments or the README. No
  live URLs pointing at internal infrastructure.
- **Unintended debug output.** No `print`, `console.log`, `TODO`, commented-out
  code or `pdb`/`breakpoint()` left behind.
- **Unused imports, dead code and stray files.** Check that every import is
  used and nothing scratch was written into the repo.
- **Error handling that swallows failures.** Every `except` either returns a
  useful message to the caller or is deliberately broad with a comment saying
  why. Resources opened in a `try` are released in a `finally`.
- **Security regressions.** Any change touching `safety.py` or `fetcher.py`
  must keep the SSRF guarantees intact, including per-redirect revalidation.
- **Behaviour that changed silently.** If a change alters extraction output,
  update the README and add a test that pins the new behaviour.
- **Encoding.** Handle text as UTF-8. Do not trust a charset the server did not
  declare (see `fetcher._decode`).

## Project layout

```
index.html, app.js, styles.css   Web tool frontend, served by the backend at /
backend/main.py                  FastAPI app, routes, configuration
backend/safety.py                URL validation and SSRF protection
backend/fetcher.py               Redirect-aware HTTP with size and time limits
backend/extractor.py             Pure HTML parsing, no I/O
backend/ratelimit.py             Token bucket limiter and TTL cache
backend/tests/                   pytest suite
extension/                       Browser extension (Manifest V3)
extension/src/extractor.js       Extraction against the live DOM
extension/test/                  Browser tests, served over HTTP
```

`extractor.py` must stay free of network and framework imports so extraction
rules can be tested against HTML fixtures.

`extension/src/extractor.js` is a port of `backend/extractor.py` and must keep
the same filtering rules and output shape. Changing one without the other is a
bug: the two front ends would disagree about the same page.

## Commands

```
python -m pytest                          run the suite (86 tests)
cd backend && uvicorn main:app --reload   run the app on :8000
```

The suite uses stub sessions and fixtures, so tests must not touch the network.

Extension tests are static pages that must be served over HTTP, because they
load the extractor with `fetch` and `new Function`. With the backend running:

- http://127.0.0.1:8000/extension/test/extractor.test.html
- http://127.0.0.1:8000/extension/test/popup.test.html

Both print a pass/fail summary. Run them after touching anything in
`extension/src`. Opening them as `file://` fails: `fetch` and the injected
script are blocked on opaque origins.

## Testing expectations

- New behaviour needs a test. Bug fixes need a test that fails before the fix.
- Cover the failure path, not just the happy path. For fetchers that means time
  outs, oversized bodies, redirects and non-HTML responses.
- Never assert against a live external site; it makes the suite flaky.

## Conventions

- Python modules import as top-level names (`import safety`), which is why
  `pytest.ini` sets `pythonpath = backend`.
- Keep `requirements.txt` pins in sync with what is actually tested.
- Configuration comes from `SCRAPELY_*` environment variables and must degrade
  to the default on a malformed value rather than raising at import time.
- Comments should explain *why* a non-obvious decision was made. Do not
  narrate what the code plainly does.

## Security posture

The service fetches URLs supplied by anonymous users. Treat that as hostile
input on every path.

Known limitation: DNS is resolved during validation and resolved again by the
HTTP client when it connects, so a hostname with a very short TTL could
rebind in between. Closing that fully requires pinning the resolved IP and
setting `Host` on the connection. The current checks (scheme, port,
credentials, all resolved answers, and revalidation on every redirect hop)
block the straightforward attacks.

The tool must not gain the ability to bypass CAPTCHAs, authentication,
paywalls, bot protection or access controls. It only extracts from publicly
accessible HTML.
