# ContractIQ

## What this project does

ContractIQ is a contract review demo app. PDFs dropped into `contracts/` are
registered in a queue and can be reviewed two ways: a human works through a
manual checklist, or an AI agent (GPT-4o-mini) reads the PDF, checks it
against `compliance_rules.json`, and returns a structured risk judgment. Both
paths converge on the same `contracts` table, so the queue shows a mix of
manual and agent-reviewed contracts side by side.

The app is built for a live "before/after" demo: `static/index.html` is
swapped between a manual-only UI and a full agent UI to show the same
workflow with and without the agent in the loop (see
`ContractIQ_Demo_Guide.md`).

## Architecture: FastAPI + SQLite

- `app.py` — single FastAPI app, no routers/blueprints. Routes are grouped
  under comment banners (`# --- DB SETUP ---`, `# --- ROUTES ---`, etc.)
  rather than split into modules.
- `contracts.db` — a single SQLite file, one `contracts` table. No ORM —
  raw `sqlite3` with `conn.row_factory = sqlite3.Row`.
- Every request that touches the DB opens its own connection via `get_db()`
  and closes it before returning — no pooling or shared connection.
- List-valued result fields (`missing_clauses`, `problematic_terms`,
  `key_obligations`) are stored as TEXT columns holding `json.dumps()`
  output, not normalized into separate tables.
- Status is a free-text human-readable string on the row itself (e.g.
  `"Escalated for Legal Review"`, `"Agent Cleared"`), not an enum — set by
  `status_from_risk()` from a `risk_score` of Low/Medium/High.
- `analyze_contract.py` is a separate module (PDF extraction + OpenAI call +
  prompt building) imported lazily inside the `/analyse` route handler, after
  `load_dotenv()` has already run.
- `static/` is mounted directly and `index.html` is served with
  no-cache headers so UI swaps during the demo take effect immediately.

## Agent review vs. manual review

Both paths write to the same columns (`status`, `risk_score`,
`missing_clauses`, etc.) but are distinguished by `review_type` and
`reviewer`:

- **Agent path** — `POST /api/contracts/{id}/analyse`. Runs
  `analyze_contract.run_analysis()`: extracts PDF text, prompts the LLM
  against `compliance_rules.json`, parses the JSON result. Writes
  `review_type='Agent'`, `reviewer='AI Agent'`, plus `confidence`.
- **Manual path** — `POST /api/contracts/{id}/manual-review`. Accepts a
  `ManualReview` payload (clause checkboxes + a human-assigned
  `risk_score` + free-text notes). Writes `review_type='Manual'`,
  `reviewer=<name from the form>`. No `confidence` value — that field only
  ever comes from the agent path.
- A legacy `POST /api/contracts/{id}/agent-review` endpoint exists that
  accepts a pre-built `AgentReview` payload directly, bypassing
  `analyze_contract.py` entirely — kept for compatibility, not used by the
  current UI.

Both paths funnel their `risk_score` through the same `status_from_risk()`
mapping, so "Escalated for Legal Review" means the same thing regardless of
which path produced it.

## Known gaps

1. **Payment terms is not a dedicated checked field on the agent side.**
   `ManualReview` has an explicit `payment_terms_ok: bool` checkbox, and
   `compliance_rules.json` defines a `risk_thresholds.payment_terms`
   threshold (Net 30 max) — but `AgentReview` / the JSON schema in
   `build_prompt()` has no dedicated payment-terms field. A payment-terms
   violation only surfaces if the LLM happens to mention it inside
   `problematic_terms` or `key_obligations` freeform text, so it's not
   reliably checked or easy to query/filter on the agent path the way it is
   on the manual path.
2. **Unhandled exceptions from the OpenAI call.** `run_analysis()` only
   catches `json.JSONDecodeError` around parsing the model's response. A
   failure in the API call itself (network error, auth error, rate limit)
   is not caught, so it propagates up through `/api/contracts/{id}/analyse`
   as an uncaught exception → FastAPI 500, instead of degrading to the same
   "Unknown" placeholder result used for empty/unreadable PDFs.

## Coding conventions observed

- snake_case for functions and files; type hints on function signatures
  (`-> dict`, `-> str`, etc.), but no dataclasses for internal data — plain
  dicts passed around, with Pydantic models reserved for request bodies
  (`ManualReview`, `AgentReview`).
- Docstrings are short and only added to functions whose behavior isn't
  obvious from the name (e.g. `derive_vendor`, `scan_contracts_folder`,
  `run_analysis`) — not on every function.
- DB schema (`CREATE TABLE IF NOT EXISTS ...`) is duplicated verbatim in
  both `app.py` and `seed_contracts.py` rather than shared — keep them in
  sync manually if the schema changes.
- Fallback-over-exception style in `analyze_contract.py`: parsing/extraction
  failures return a canned "Unknown" result dict rather than raising, so
  callers can assume `run_analysis()` always returns a well-formed dict
  (the OpenAI-call gap above is the one place this pattern isn't followed
  through).
- `load_dotenv()` is called at the top of every entry-point script
  (`app.py`, `analyze_contract.py`) rather than assumed to be loaded once
  globally.
