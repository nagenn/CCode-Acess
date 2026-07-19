#!/usr/bin/env python3
"""
Runs the test suite and renders tests/report.html from the results.

Everything dynamic (pass/fail, durations, parametrize cases) comes from a
fresh JUnit XML run. Test descriptions come from each test function's own
docstring (via static AST parsing, no import needed) so the report can never
drift from what the test actually says it verifies.

Usage: python3.9 tests/generate_report.py
"""
import ast
import html
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(PROJECT_ROOT, "tests")
REPORT_PATH = os.path.join(TESTS_DIR, "report.html")


# ---------------------------------------------------------------------------
# Run pytest, get JUnit XML
# ---------------------------------------------------------------------------

def run_pytest():
    fd, xml_path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", f"--junitxml={xml_path}"],
        cwd=PROJECT_ROOT,
    )
    root = ET.parse(xml_path).getroot()
    os.remove(xml_path)
    return root, proc.returncode


# ---------------------------------------------------------------------------
# Static introspection of test source (no import, no side effects)
# ---------------------------------------------------------------------------

def extract_module_doc(filepath):
    tree = ast.parse(open(filepath).read())
    return ast.get_docstring(tree)


def extract_test_metadata(filepath):
    """Returns {test_name: {"doc": str|None, "argnames": [str, ...]|None}}."""
    tree = ast.parse(open(filepath).read())
    meta = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test_")):
            continue
        argnames = None
        for dec in node.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "parametrize"
                and dec.args
                and isinstance(dec.args[0], ast.Constant)
            ):
                argnames = [a.strip() for a in dec.args[0].value.split(",")]
        meta[node.name] = {"doc": ast.get_docstring(node), "argnames": argnames}
    return meta


# ---------------------------------------------------------------------------
# Parse JUnit XML into a per-file, per-test-group structure
# ---------------------------------------------------------------------------

def classname_to_file(classname):
    return classname.replace(".", "/") + ".py"


def case_status(testcase):
    if testcase.find("failure") is not None or testcase.find("error") is not None:
        return "fail"
    if testcase.find("skipped") is not None:
        return "skip"
    return "pass"


def base_name_and_bracket(name):
    if "[" in name and name.endswith("]"):
        base, bracket = name.split("[", 1)
        return base, bracket[:-1]
    return name, None


def collect_results(xml_root):
    """Returns {file_path: {"doc": ..., "groups": [{"base": ..., "doc": ..., "argnames": ..., "cases": [...]}]}}"""
    files = {}
    for testcase in xml_root.iter("testcase"):
        classname = testcase.get("classname")
        file_path = classname_to_file(classname)
        module_path = os.path.join(PROJECT_ROOT, file_path)

        if file_path not in files:
            files[file_path] = {
                "doc": extract_module_doc(module_path) if os.path.exists(module_path) else None,
                "meta": extract_test_metadata(module_path) if os.path.exists(module_path) else {},
                "groups": [],
                "group_index": {},
            }
        entry = files[file_path]

        name = testcase.get("name")
        base, bracket = base_name_and_bracket(name)
        status = case_status(testcase)
        time_ms = round(float(testcase.get("time", 0)) * 1000)

        if base not in entry["group_index"]:
            meta = entry["meta"].get(base, {"doc": None, "argnames": None})
            group = {"base": base, "doc": meta["doc"], "argnames": meta["argnames"], "cases": []}
            entry["group_index"][base] = group
            entry["groups"].append(group)
        entry["group_index"][base]["cases"].append({"bracket": bracket, "status": status, "time_ms": time_ms})

    for entry in files.values():
        del entry["group_index"]
        del entry["meta"]
    return files


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def esc(s):
    return html.escape(s) if s else ""


def unescape_pytest_id(s):
    """pytest's default parametrize ids render non-ASCII as literal \\uXXXX text."""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)


def render_case_table(argnames, cases):
    n = len(argnames)
    rows = []
    all_split = True
    for case in cases:
        parts = case["bracket"].split("-", n - 1) if case["bracket"] else []
        if len(parts) != n:
            all_split = False
            break
    if not all_split:
        return None

    header_cells = "".join(f"<th>{esc(a)}</th>" for a in argnames) + "<th class=\"num\">Result</th>"
    for case in cases:
        parts = case["bracket"].split("-", n - 1)
        cells = "".join(f"<td class=\"mono\">{esc(unescape_pytest_id(p))}</td>" for p in parts)
        pill = "check" if case["status"] == "pass" else "cross"
        symbol = "&#10003;" if case["status"] == "pass" else "&#10007;"
        color_cls = "check" if case["status"] == "pass" else "fail-mark"
        rows.append(f"<tr>{cells}<td class=\"num mono {color_cls}\">{symbol}</td></tr>")

    return f"""<table class="map-table" style="margin-top: 10px;">
      <thead><tr>{header_cells}</tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>"""


def render_case_chips(cases):
    chips = []
    for case in cases:
        cls = "pass" if case["status"] == "pass" else "fail"
        label = esc(unescape_pytest_id(case["bracket"])) if case["bracket"] else "case"
        chips.append(f'<span class="chip {cls}">{label}</span>')
    return f'<div class="case-chips">{"".join(chips)}</div>'


def render_group(group):
    cases = group["cases"]
    overall_pass = all(c["status"] == "pass" for c in cases)
    total_ms = sum(c["time_ms"] for c in cases)
    doc = esc(group["doc"]) or "&mdash;"
    pill_html = f'<span class="pill {"pass" if overall_pass else "fail"}">{"PASS" if overall_pass else "FAIL"}</span>'

    if len(cases) == 1 and group["argnames"] is None:
        return f"""<div class="test-row">
      {pill_html}
      <div class="test-body">
        <div class="test-id mono">{esc(group['base'])}</div>
        <div class="test-desc">{doc}</div>
      </div>
      <div class="test-time mono">{total_ms}ms</div>
    </div>"""

    table = render_case_table(group["argnames"], cases) if group["argnames"] else None
    detail = table if table else render_case_chips(cases)
    count_note = f'<span style="color:var(--muted); font-weight:400;">[&hellip;{len(cases)} cases]</span>'

    return f"""<div class="test-row">
      {pill_html}
      <div class="test-body">
        <div class="test-id mono">{esc(group['base'])} {count_note}</div>
        <div class="test-desc">{doc}</div>
        {detail}
      </div>
      <div class="test-time mono">{total_ms}ms</div>
    </div>"""


def render_file_section(file_path, entry):
    n_cases = sum(len(g["cases"]) for g in entry["groups"])
    doc = esc(entry["doc"]) or ""
    rows = "\n".join(render_group(g) for g in entry["groups"])
    return f"""<div class="file-section">
    <div class="file-head">
      <div class="file-name mono">{esc(file_path)}</div>
      <div class="file-count">{n_cases} test{'s' if n_cases != 1 else ''}{' &middot; ' + doc if doc else ''}</div>
    </div>
    {rows}
  </div>"""


def render_html(xml_root, files):
    suite = xml_root.find("testsuite") if xml_root.tag == "testsuites" else xml_root
    total = int(suite.get("tests", 0))
    failures = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
    skipped = int(suite.get("skipped", 0))
    passed = total - failures - skipped
    duration_ms = round(float(suite.get("time", 0)) * 1000)
    timestamp = suite.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(timestamp)
        run_when = dt.strftime("%b %-d, %Y, %H:%M:%S")
    except ValueError:
        run_when = timestamp

    all_pass = failures == 0
    stamp_word = "VERIFIED" if all_pass else "REVIEW"
    stamp_color_class = "" if all_pass else "stamp-fail"

    sections = "\n".join(render_file_section(fp, files[fp]) for fp in sorted(files))

    return f"""<title>ContractIQ &mdash; Agent Review Test Docket</title>
<style>
  :root {{
    --paper: #F4F5F3; --panel: #FFFFFF; --panel-border: #DFE2E4; --ink: #1B2438;
    --muted: #5B6478; --accent: #2A46C9; --accent-soft: #E8ECFB;
    --pass: #1E8E5A; --pass-soft: #E4F4EC; --fail: #C23B32; --fail-soft: #FBEAE8;
    --shadow: 0 1px 2px rgba(27,36,56,0.04), 0 8px 24px rgba(27,36,56,0.05);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --paper: #11141F; --panel: #171C2B; --panel-border: #2A3143; --ink: #E7E9F2;
      --muted: #8890A8; --accent: #7C93F5; --accent-soft: #22284A;
      --pass: #4CC98A; --pass-soft: #16302A; --fail: #E8776A; --fail-soft: #3A1F20;
      --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 8px 24px rgba(0,0,0,0.35);
    }}
  }}
  :root[data-theme="dark"] {{
    --paper: #11141F; --panel: #171C2B; --panel-border: #2A3143; --ink: #E7E9F2;
    --muted: #8890A8; --accent: #7C93F5; --accent-soft: #22284A;
    --pass: #4CC98A; --pass-soft: #16302A; --fail: #E8776A; --fail-soft: #3A1F20;
    --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 8px 24px rgba(0,0,0,0.35);
  }}
  :root[data-theme="light"] {{
    --paper: #F4F5F3; --panel: #FFFFFF; --panel-border: #DFE2E4; --ink: #1B2438;
    --muted: #5B6478; --accent: #2A46C9; --accent-soft: #E8ECFB;
    --pass: #1E8E5A; --pass-soft: #E4F4EC; --fail: #C23B32; --fail-soft: #FBEAE8;
    --shadow: 0 1px 2px rgba(27,36,56,0.04), 0 8px 24px rgba(27,36,56,0.05);
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--paper); color: var(--ink);
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    -webkit-font-smoothing: antialiased; line-height: 1.5; }}
  .page {{ max-width: 860px; margin: 0 auto; padding: 56px 24px 96px; }}
  .mono {{ font-family: ui-monospace, "SF Mono", "Cascadia Code", "Roboto Mono", Menlo, Consolas, monospace;
    font-variant-numeric: tabular-nums; }}
  .serif {{ font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, "Times New Roman", serif; }}
  .eyebrow {{ font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--accent); font-weight: 600; }}
  h1 {{ font-size: clamp(28px, 4vw, 38px); line-height: 1.15; margin: 10px 0 8px; text-wrap: balance; font-weight: 500; }}
  .run-meta {{ color: var(--muted); font-size: 13.5px; }}
  .run-meta .sep {{ margin: 0 8px; opacity: 0.5; }}
  .summary {{ position: relative; margin-top: 32px; padding: 28px; background: var(--panel);
    border: 1px solid var(--panel-border); border-radius: 4px; box-shadow: var(--shadow);
    display: flex; align-items: center; gap: 28px; overflow: hidden; }}
  .stats {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 20px; flex: 1; }}
  .stat-label {{ font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; }}
  .stat-value {{ font-size: 28px; font-weight: 600; }}
  .stat-value.pass {{ color: var(--pass); }}
  .stat-value.fail {{ color: var(--fail); }}
  .stat-unit {{ font-size: 14px; color: var(--muted); font-weight: 400; margin-left: 2px; }}
  .stamp {{ flex-shrink: 0; width: 108px; height: 108px; border-radius: 50%; border: 2px solid var(--pass);
    outline: 1px solid var(--pass); outline-offset: 4px; display: flex; flex-direction: column;
    align-items: center; justify-content: center; color: var(--pass); transform: rotate(-8deg);
    opacity: 0.92; text-align: center; }}
  .stamp.stamp-fail {{ border-color: var(--fail); outline-color: var(--fail); color: var(--fail); }}
  .stamp-verified {{ font-size: 10px; letter-spacing: 0.16em; font-weight: 700; }}
  .stamp-ratio {{ font-family: ui-monospace, "SF Mono", "Cascadia Code", "Roboto Mono", Menlo, Consolas, monospace;
    font-size: 20px; font-weight: 600; margin: 3px 0; }}
  .stamp-sub {{ font-size: 8px; letter-spacing: 0.1em; opacity: 0.85; }}
  .file-section {{ margin-top: 40px; }}
  .file-head {{ display: flex; align-items: baseline; justify-content: space-between;
    border-bottom: 1px solid var(--panel-border); padding-bottom: 10px; margin-bottom: 4px; }}
  .file-name {{ font-size: 19px; font-weight: 500; }}
  .file-count {{ font-size: 12.5px; color: var(--muted); }}
  .test-row {{ display: flex; align-items: flex-start; gap: 14px; padding: 14px 2px;
    border-bottom: 1px solid var(--panel-border); }}
  .test-row:last-child {{ border-bottom: none; }}
  .pill {{ flex-shrink: 0; display: inline-flex; align-items: center; gap: 5px; font-size: 11px;
    font-weight: 700; letter-spacing: 0.04em; padding: 3px 9px; border-radius: 3px; margin-top: 2px; }}
  .pill.pass {{ background: var(--pass-soft); color: var(--pass); }}
  .pill.fail {{ background: var(--fail-soft); color: var(--fail); }}
  .pill::before {{ content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }}
  .test-body {{ flex: 1; min-width: 0; }}
  .test-id {{ font-size: 13px; word-break: break-word; }}
  .test-desc {{ font-size: 13.5px; color: var(--muted); margin-top: 3px; }}
  .test-time {{ flex-shrink: 0; font-size: 12px; color: var(--muted); padding-top: 3px; min-width: 46px; text-align: right; }}
  .map-table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 4px; }}
  .map-table th {{ text-align: left; font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--muted); font-weight: 600; padding: 0 10px 8px; }}
  .map-table th.num, .map-table td.num {{ text-align: right; }}
  .map-table td {{ padding: 9px 10px; border-top: 1px solid var(--panel-border); vertical-align: middle; }}
  .map-table tr td:first-child, .map-table tr th:first-child {{ padding-left: 12px; }}
  .map-table tr td:last-child, .map-table tr th:last-child {{ padding-right: 12px; }}
  .map-table tbody tr:hover {{ background: var(--accent-soft); }}
  .check {{ color: var(--pass); font-weight: 700; }}
  .fail-mark {{ color: var(--fail); font-weight: 700; }}
  .case-chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }}
  .chip {{ font-family: ui-monospace, "SF Mono", "Cascadia Code", "Roboto Mono", Menlo, Consolas, monospace;
    font-size: 12px; padding: 3px 8px; border-radius: 3px; }}
  .chip.pass {{ background: var(--pass-soft); color: var(--pass); }}
  .chip.fail {{ background: var(--fail-soft); color: var(--fail); }}
  .footnote {{ margin-top: 44px; padding-top: 18px; border-top: 1px solid var(--panel-border);
    font-size: 12.5px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 6px 0; }}
  .footnote span:not(:last-child)::after {{ content: "\\00b7"; margin: 0 10px; opacity: 0.5; }}
  @media (max-width: 560px) {{
    .summary {{ flex-direction: column; align-items: stretch; }}
    .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .stamp {{ align-self: center; }}
  }}
  :focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
</style>

<div class="page">
  <div class="eyebrow">ContractIQ &middot; QA Docket</div>
  <h1 class="serif">Agent review test results</h1>
  <div class="run-meta mono">
    tests/ <span class="sep">&middot;</span> pytest {pytest.__version__} &middot; python {sys.version.split()[0]}
    <span class="sep">&middot;</span> {esc(run_when)}
  </div>

  <div class="summary">
    <div class="stats">
      <div><div class="stat-label">Total</div><div class="stat-value mono">{total}</div></div>
      <div><div class="stat-label">Passed</div><div class="stat-value mono pass">{passed}</div></div>
      <div><div class="stat-label">Failed</div><div class="stat-value mono {'fail' if failures else ''}">{failures}</div></div>
      <div><div class="stat-label">Duration</div><div class="stat-value mono">{duration_ms}<span class="stat-unit">ms</span></div></div>
    </div>
    <div class="stamp {stamp_color_class}" aria-hidden="true">
      <div class="stamp-verified">{stamp_word}</div>
      <div class="stamp-ratio">{passed}/{total}</div>
      <div class="stamp-sub">CONTRACTIQ QA</div>
    </div>
  </div>

  {sections}

  <div class="footnote">
    <span>OpenAI network calls mocked at <span class="mono">analyze_contract.client.chat.completions.create</span></span>
    <span>SQLite state isolated to a scratch DB per test</span>
    <span>Regenerate: <span class="mono">python3.9 tests/generate_report.py</span></span>
  </div>
</div>
"""


def main():
    xml_root, returncode = run_pytest()
    files = collect_results(xml_root)
    report = render_html(xml_root, files)

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\nReport written to {REPORT_PATH}")
    print(f"Open it: file://{REPORT_PATH}")
    return returncode


if __name__ == "__main__":
    sys.exit(main())
