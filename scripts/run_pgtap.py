"""Run the pgTAP tests in supabase/tests against the linked project WITHOUT Docker.

    python scripts/run_pgtap.py                   # all files in supabase/tests
    python scripts/run_pgtap.py supabase/tests/rls.test.sql

`supabase test db` needs Docker even for a remote database. This runs the same files through
`supabase db query` instead. That command only shows the last result set, so each assertion
is rewritten to log its TAP line into a temporary table, and the lines are read back at the end.
The test files themselves are unchanged and still work with `supabase test db`.

The tests run inside `begin; ... rollback;`, so they leave no data behind.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPABASE = Path(r"C:\Users\Mara\tools\supabase\supabase.exe")
ASSERTION = re.compile(r"^select\s+((?:is|isnt|ok|lives_ok|throws_ok|results_eq|matches|cmp_ok|is_empty|isnt_empty)\s*\()", re.MULTILINE | re.IGNORECASE)

PRELUDE = """
create temp table tap_log (line text);
grant all on pg_temp.tap_log to public;
"""

EPILOGUE = """
select line from pg_temp.tap_log
 order by (regexp_match(line, '^(?:not )?ok (\\d+)'))[1]::int;
"""


def instrument(sql: str) -> str:
    """Log every assertion's TAP line, and read them all back before the final rollback."""
    sql = ASSERTION.sub(lambda m: "insert into pg_temp.tap_log (line) select " + m.group(1), sql)
    sql = re.sub(r"^select \* from finish\(\);\s*$", "", sql, flags=re.MULTILINE | re.IGNORECASE)
    # run the plan helper quietly, add the temp table right after `begin;`, read results before `rollback;`
    sql = re.sub(r"^select no_plan\(\);", "select no_plan() into temp table _plan;", sql, flags=re.MULTILINE | re.IGNORECASE)
    sql = re.sub(r"^begin;", lambda m: "begin;" + PRELUDE, sql, count=1, flags=re.MULTILINE | re.IGNORECASE)
    sql = re.sub(r"^rollback;", lambda m: EPILOGUE + "rollback;", sql, count=1, flags=re.MULTILINE | re.IGNORECASE)
    return sql


def run_file(path: Path) -> tuple[int, int, list[str]]:
    sql = instrument(path.read_text(encoding="utf-8"))
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as f:
        f.write(sql)
        tmp = f.name
    try:
        proc = subprocess.run(
            [str(SUPABASE), "db", "query", "--remote", "-f", tmp, "--yes", "--output-format", "json"],
            cwd=ROOT, capture_output=True, text=True, timeout=300,
        )
    finally:
        Path(tmp).unlink(missing_ok=True)
    text = proc.stdout + proc.stderr
    decoder, objs = json.JSONDecoder(), []
    for m in re.finditer(r"^\{", text, re.MULTILINE):  # the CLI may pretty-print over many lines
        try:
            objs.append(decoder.raw_decode(text[m.start():])[0])
        except ValueError:
            pass
    out = next((o for o in objs if "rows" in o), None) or next((o for o in objs if o.get("_tag") == "Error"), None)
    if out is None:
        return 0, 1, [f"no JSON from the CLI: {text[:400]}"]
    if out.get("_tag") == "Error":
        return 0, 1, [out["error"].get("message", str(out["error"]))[:1500]]
    rows = find_rows(out)
    lines = [r["line"] for r in rows if isinstance(r, dict) and "line" in r]
    failed = [l for l in lines if l.startswith("not ok")]
    return len(lines), len(failed), lines


def find_rows(obj) -> list:
    """The CLI wraps result rows differently across versions; find the list of rows holding `line`."""
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj) and any("line" in x for x in obj):
            return obj
        for x in obj:
            found = find_rows(x)
            if found:
                return found
    elif isinstance(obj, dict):
        for v in obj.values():
            found = find_rows(v)
            if found:
                return found
    return []


def main() -> int:
    files = [Path(a) for a in sys.argv[1:]] or sorted((ROOT / "supabase" / "tests").glob("*.test.sql"))
    total_bad = 0
    for f in files:
        total, bad, lines = run_file(f)
        print(f"\n{f.name}: {total - bad} of {total} assertions passed" + ("" if total else "  (no results)"))
        for line in lines:
            if line.startswith("not ok") or total == 0 or "--verbose" in sys.argv:
                print("  " + line)
        total_bad += bad
    return 1 if total_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
