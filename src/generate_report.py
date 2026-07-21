#!/usr/bin/env python3
"""Consolidates the per-run companion JSON files under suites/<suite>/outputs/logs/
(written by `run_probe.py batch` alongside each .log, one per K8s/VM run) into
a single per-suite summary: which test got which verdict, a short comment for
weak/inconclusive verdicts, and a pointer to the raw .log for full detail.

Produces two views of the same data:
  suites/<suite>/outputs/logs/summary-report.txt   -- plain-text table
  suites/<suite>/outputs/logs/summary-report.html  -- self-contained HTML, color-coded
    by verdict severity, with expandable per-row detail (full diagnostic text
    + exact commands used), so the raw .log doesn't need to be opened separately.

Runs on the dev PC with `uv run src/generate_report.py --suite NAME`.
Only uses the standard library.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

from suite_common import ROOT, check_suite_exists, resolve_suite

# Report-only status: a spec test that has no matching result in any log yet
# (not a real run_probe.py verdict, so it's intentionally absent from that
# script's VERDICT_ICON).
NOT_RUN_YET = "NOT_RUN_YET"
NOT_RUN_ICON = "❔"

# Severity bucket per verdict, used for the HTML row color. Verdicts not
# listed here (there shouldn't be any) fall back to "unknown".
VERDICT_SEVERITY = {
    "PASS": "pass",
    "PORT_REFUSED_NETWORK_OPEN": "pass",
    "UDP_REFUSED_NETWORK_OPEN": "pass",
    "PORT_CLOSED_HOST_REACHABLE": "weak",
    "UDP_SENT_HOST_REACHABLE": "weak",
    "UDP_SENT_HOST_UNREACHABLE": "weak",
    "HOST_UNREACHABLE": "blocked",
    "UDP_SEND_FAILED": "blocked",
    "SKIPPED_MANUAL_TEST_REQUIRED": "skipped",
    NOT_RUN_YET: "not_run",
}


def discover_result_files(suite: str) -> list[Path]:
    logs_dir = ROOT / "suites" / suite / "outputs" / "logs"
    return sorted(logs_dir.glob("*.json")) if logs_dir.is_dir() else []


def merge_results(result_files: list[Path]) -> dict[str, dict]:
    """Returns {test_id: record}, keeping the record with the most recent
    finished_at across all files (safe as a plain string comparison because
    run_probe.py's now() always emits fixed-width, zero-padded UTC ISO-8601)."""
    merged: dict[str, dict] = {}
    for path in result_files:
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"⚠️  Skipping unreadable results file {path}: {e}", file=sys.stderr)
            continue
        for record in data.get("results", []):
            test_id = record.get("id")
            if test_id is None:
                continue
            existing = merged.get(test_id)
            if existing is None or record.get("finished_at", "") > existing.get("finished_at", ""):
                merged[test_id] = record
    return merged


def destination_name(test: dict) -> str:
    dst = test["destination"]
    label = dst.get("description") or test.get("service") or dst["ip"]
    return f"{label} ({dst['ip']}:{test['port']}/{test['protocol_label']})"


def classify_test(spec_test: dict, merged: dict[str, dict]) -> dict:
    """Builds one report row for a spec test, merging in a matching logged
    result if one exists."""
    test_id = spec_test["id"]
    record = merged.get(test_id)
    if record is not None:
        return {
            "id": test_id,
            "direction": spec_test["direction"],
            "name": destination_name(spec_test),
            "verdict": record["verdict"],
            "verdict_icon": record.get("verdict_icon", "?"),
            "comment": record.get("comment"),
            "log_pointer": f"{record.get('log_file', '?')}#{test_id}",
            "detail": record.get("detail", ""),
            "commands": record.get("commands", []),
        }
    if not spec_test.get("automatable"):
        return {
            "id": test_id,
            "direction": spec_test["direction"],
            "name": destination_name(spec_test),
            "verdict": "SKIPPED_MANUAL_TEST_REQUIRED",
            "verdict_icon": "⏭️",
            "comment": spec_test.get("note"),
            "log_pointer": "-",
            "detail": "",
            "commands": [],
        }
    return {
        "id": test_id,
        "direction": spec_test["direction"],
        "name": destination_name(spec_test),
        "verdict": NOT_RUN_YET,
        "verdict_icon": NOT_RUN_ICON,
        "comment": "no result found under suites/<suite>/outputs/logs/ for this test id",
        "log_pointer": "-",
        "detail": "",
        "commands": [],
    }


def build_rows(spec: dict, merged: dict[str, dict]) -> list[dict]:
    return [classify_test(t, merged) for t in spec.get("tests", [])]


def render_text_table(rows: list[dict]) -> str:
    if not rows:
        return "No test cases found in the spec.\n"
    headers = ("ID", "DIRECTION", "NAME", "VERDICT", "COMMENT", "LOG")
    columns = [
        [r["id"] for r in rows],
        [r["direction"] for r in rows],
        [r["name"] for r in rows],
        [f"{r['verdict_icon']} {r['verdict']}" for r in rows],
        [r["comment"] or "" for r in rows],
        [r["log_pointer"] for r in rows],
    ]
    widths = [max(len(h), *(len(v) for v in col)) for h, col in zip(headers, columns)]

    def fmt_row(values: tuple[str, ...]) -> str:
        return "  ".join(v.ljust(w) for v, w in zip(values, widths))

    lines = [fmt_row(headers), fmt_row(tuple("-" * w for w in widths))]
    for i in range(len(rows)):
        lines.append(fmt_row(tuple(col[i] for col in columns)))
    lines.append("")
    lines.append(f"{len(rows)} test case(s).")
    return "\n".join(lines) + "\n"


_SEVERITY_COLORS = {
    # (background, border) -- chosen for readable contrast in both light and
    # dark themes since the report may be opened in either.
    "pass": ("#1f7a3f22", "#1f7a3f"),
    "weak": ("#a3760022", "#a37600"),
    "blocked": ("#a3222222", "#a32222"),
    "skipped": ("#6b6b6b22", "#6b6b6b"),
    "not_run": ("#6b6b6b11", "#9a9a9a"),
    "unknown": ("#6b6b6b11", "#9a9a9a"),
}

_HTML_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Connectivity report -- {suite}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    font-family: -apple-system, Segoe UI, Roboto, sans-serif;
    margin: 2rem; line-height: 1.4;
  }}
  h1 {{ font-size: 1.3rem; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #80808040; vertical-align: top; }}
  th {{ position: sticky; top: 0; background: canvas; }}
  tr.summary-row {{ cursor: pointer; }}
  tr.summary-row:hover {{ filter: brightness(0.97); }}
  tr.detail-row {{ display: none; }}
  tr.detail-row.open {{ display: table-row; }}
  tr.detail-row pre {{
    white-space: pre-wrap; word-break: break-word; margin: 0.5rem 0 0 0;
    font-size: 0.82rem; max-height: 24rem; overflow-y: auto;
  }}
  .toggle {{ display: inline-block; width: 1.2em; }}
  .count {{ color: #808080; font-weight: normal; }}
{severity_css}
</style>
<script>
  function toggleRow(id) {{
    var row = document.getElementById("detail-" + id);
    var toggle = document.getElementById("toggle-" + id);
    var open = row.classList.toggle("open");
    toggle.textContent = open ? "▾" : "▸";
  }}
</script>
</head>
<body>
<h1>Connectivity report -- suite <code>{suite}</code></h1>
<p class="count">{summary_line}</p>
<table>
<thead>
<tr><th></th><th>ID</th><th>Direction</th><th>Name</th><th>Verdict</th><th>Comment</th><th>Log</th></tr>
</thead>
<tbody>
{rows_html}
</tbody>
</table>
</body>
</html>
"""


def render_html(rows: list[dict], suite: str) -> str:
    severity_css = "\n".join(
        f'  tr.summary-row.sev-{sev} {{ background: {bg}; border-left: 4px solid {border}; }}'
        for sev, (bg, border) in _SEVERITY_COLORS.items()
    )
    counts: dict[str, int] = {}
    rows_html_parts = []
    for row in rows:
        severity = VERDICT_SEVERITY.get(row["verdict"], "unknown")
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
        row_id = html.escape(row["id"], quote=True)
        has_detail = bool(row["detail"] or row["commands"])
        toggle_html = f'<span class="toggle" id="toggle-{row_id}">▸</span>' if has_detail else ""
        onclick = f' onclick="toggleRow(\'{row_id}\')"' if has_detail else ""
        rows_html_parts.append(
            f'<tr class="summary-row sev-{severity}"{onclick}>'
            f'<td>{toggle_html}</td>'
            f'<td>{html.escape(row["id"])}</td>'
            f'<td>{html.escape(row["direction"])}</td>'
            f'<td>{html.escape(row["name"])}</td>'
            f'<td>{row["verdict_icon"]} {html.escape(row["verdict"])}</td>'
            f'<td>{html.escape(row["comment"] or "")}</td>'
            f'<td>{html.escape(row["log_pointer"])}</td>'
            f'</tr>'
        )
        if has_detail:
            commands_html = "".join(
                f"<li>{html.escape(c['display'])}</li>" for c in row["commands"]
            )
            commands_block = f"<ul>{commands_html}</ul>" if commands_html else ""
            rows_html_parts.append(
                f'<tr class="detail-row" id="detail-{row_id}">'
                f'<td colspan="7">{commands_block}<pre>{html.escape(row["detail"])}</pre></td>'
                f'</tr>'
            )
    summary_line = ", ".join(f"{v}: {c}" for v, c in sorted(counts.items()))
    return _HTML_TEMPLATE.format(
        suite=html.escape(suite),
        severity_css=severity_css,
        summary_line=html.escape(f"{len(rows)} test case(s) -- {summary_line}"),
        rows_html="\n".join(rows_html_parts),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        default=None,
        help="Suite name (subfolder under suites/). Falls back to the "
        "TEST_SUITE environment variable, and finally to the 'default' suite, if omitted.",
    )
    parser.add_argument("--spec", type=Path, default=None)
    args = parser.parse_args()

    suite = resolve_suite(args.suite)
    if args.spec is None:
        check_suite_exists(suite)
    spec_path = args.spec or ROOT / "suites" / suite / "connectivity-test-spec.json"
    if not spec_path.exists():
        raise SystemExit(
            f"❌ Spec not found: {spec_path}\n"
            f"   Generate it first with: uv run src/generate_test_spec.py --suite {suite}"
        )
    with spec_path.open(encoding="utf-8") as f:
        spec = json.load(f)

    result_files = discover_result_files(suite)
    merged = merge_results(result_files)
    rows = build_rows(spec, merged)

    logs_dir = ROOT / "suites" / suite / "outputs" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    text_path = logs_dir / "summary-report.txt"
    html_path = logs_dir / "summary-report.html"
    text_path.write_text(render_text_table(rows), encoding="utf-8")
    html_path.write_text(render_html(rows, suite), encoding="utf-8")

    print(f"ℹ️  Merged {len(result_files)} results file(s) under {logs_dir}")
    print(f"✅ Generated {text_path}")
    print(f"✅ Generated {html_path}")
    print()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    for verdict, count in sorted(counts.items()):
        icon = next((r["verdict_icon"] for r in rows if r["verdict"] == verdict), "?")
        print(f"  {icon} {verdict}: {count}")


if __name__ == "__main__":
    sys.exit(main())
