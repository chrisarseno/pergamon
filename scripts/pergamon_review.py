#!/usr/bin/env python3
"""Pergamon impact review.

Indexes the checked-out repository with codebase-memory-mcp (unmodified,
DeusData, MIT), asks it which code depends on what this pull request changed,
and posts one sticky Markdown comment on the pull request.

Standard library only, so the Action needs nothing beyond Python 3.8+.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import PurePosixPath

MARKER = "<!-- pergamon-impact-review -->"
TEST_DIR_PARTS = {"test", "tests", "__tests__", "spec", "specs"}


# ── engine calls ────────────────────────────────────────────────────────────

def cbm(binary: str, tool: str, *args: str, timeout: int = 900) -> dict:
    """Run one codebase-memory-mcp CLI tool and return its JSON result."""
    cmd = [binary, "cli", tool, *args, "--format", "json"] if tool != "index_repository" else [binary, "cli", tool, *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out = proc.stdout.strip()
    # The CLI prints the JSON result as the last line; earlier lines are hints.
    last = out.splitlines()[-1] if out else ""
    try:
        return json.loads(last)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"{tool} returned no JSON (exit {proc.returncode}).\n"
            f"stdout tail: {out[-800:]}\nstderr tail: {proc.stderr[-800:]}"
        )


def index_repo(binary: str, repo: str, name: str, mode: str) -> dict:
    res = cbm(binary, "index_repository", "--repo-path", repo, "--name", name, "--mode", mode)
    if res.get("status") != "indexed":
        raise RuntimeError(f"Indexing failed: {json.dumps(res)[:800]}")
    return res


def detect_changes(binary: str, project: str, base: str, depth: int) -> dict:
    return cbm(
        binary, "detect_changes",
        "--project", project, "--base-branch", base, "--scope", "impact",
        "--depth", str(depth), "--limit", "2000", "--changed-limit", "500",
        "--module-limit", "500", "--max-output-tokens", "400000",
    )


def all_routes(binary: str, project: str) -> list[dict]:
    q = ("MATCH (f)-[:HANDLES]->(r:Route) "
         "RETURN r.name AS path, r.qualified_name AS qn, f.name AS handler, f.file_path AS file")
    res = cbm(binary, "query_graph", "--project", project, "--query", q,
              "--max-rows", "5000", "--max-output-tokens", "400000")
    cols = res.get("columns", [])
    return [dict(zip(cols, row)) for row in res.get("rows", [])]


# ── analysis ────────────────────────────────────────────────────────────────

def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    if any(part.lower() in TEST_DIR_PARTS for part in p.parts[:-1]):
        return True
    n = p.name.lower()
    return n.startswith("test_") or n.endswith(("_test.py", "_test.go", ".test.ts", ".test.js",
                                                ".spec.ts", ".spec.js", "test.java"))


def area_of(path: str, levels: int) -> str:
    parts = PurePosixPath(path).parts[:-1]
    return "/".join(parts[:levels]) + "/" if parts else "(repo root)"


def group_areas(files: list[str], start_levels: int, max_levels: int = 6) -> Counter:
    """Group files by folder, going deeper while one folder holds most of them.

    A monorepo where everything lives under src/app/ would otherwise collapse
    into a single useless row.
    """
    levels = max(1, start_levels)
    areas = Counter(area_of(f, levels) for f in files)
    while files and levels < max_levels:
        top = areas.most_common(1)[0][1]
        if len(areas) >= 3 or top / len(files) <= 0.6:
            break
        deeper = Counter(area_of(f, levels + 1) for f in files)
        if len(deeper) == len(areas):
            break  # no folder structure left to split on
        levels += 1
        areas = deeper
    return areas


def route_method(qn: str) -> str:
    # Route qualified names look like __route__GET__/health
    if qn and qn.startswith("__route__"):
        return qn[len("__route__"):].split("__", 1)[0]
    return "ANY"


def risk(total: int, routes: int, low: int, high: int) -> tuple[str, str]:
    if total >= high or routes >= 10:
        return "High", "🔴"
    if total >= low or routes > 0:
        return "Medium", "🟡"
    return "Low", "🟢"


def analyse(dc: dict, routes: list[dict], area_levels: int, low: int, high: int) -> dict:
    changed = list(dict.fromkeys(dc.get("changed_files") or []))
    impacted = dc.get("impacted") or []
    impacted_files = list(dict.fromkeys(i["file"] for i in impacted if i.get("file")))
    touched = set(changed) | set(impacted_files)
    hit_routes = [r for r in routes if r.get("file") in touched]
    tests = sorted(f for f in impacted_files if is_test_path(f))
    product = [f for f in impacted_files if not is_test_path(f) and f not in changed]
    areas = group_areas(product, area_levels)
    level, icon = risk(len(product), len(hit_routes), low, high)
    return {
        "changed": changed, "impacted": impacted, "impacted_files": impacted_files,
        "product_files": product, "tests": tests, "routes": hit_routes, "areas": areas,
        "risk": level, "icon": icon, "total": dc.get("impacted_total", len(impacted)),
        "truncated": bool(dc.get("truncated")), "merge_base": dc.get("merge_base", ""),
    }


# ── rendering ───────────────────────────────────────────────────────────────

def md_escape(s: str) -> str:
    return str(s).replace("|", "\\|")


def file_link(path: str, server: str, repo: str, sha: str) -> str:
    if server and repo and sha:
        return f"[`{md_escape(path)}`]({server}/{repo}/blob/{sha}/{path})"
    return f"`{md_escape(path)}`"


def render(a: dict, *, depth: int, engine_version: str, server: str, repo: str, sha: str,
           max_rows: int) -> str:
    L = [MARKER, "## Pergamon impact review", ""]
    n_prod, n_tests, n_routes = len(a["product_files"]), len(a["tests"]), len(a["routes"])
    L.append(f"{a['icon']} **{a['risk']} impact.** This pull request changes "
             f"**{len(a['changed'])} file{'s' if len(a['changed']) != 1 else ''}**. "
             f"Within {depth} hop{'s' if depth != 1 else ''} of the changed code, "
             f"**{n_prod} other file{'s' if n_prod != 1 else ''}** depend on it, including "
             f"**{n_routes} HTTP route{'s' if n_routes != 1 else ''}** and "
             f"**{n_tests} test file{'s' if n_tests != 1 else ''}**.")
    L.append("")

    if not a["changed"]:
        L += ["No indexed source files changed (docs, config or ignored paths only).", ""]

    if a["routes"]:
        L += [f"### HTTP routes that may be affected ({n_routes})", "",
              "| Method | Route | Handler | File |", "|---|---|---|---|"]
        for r in a["routes"][:max_rows]:
            L.append(f"| {route_method(r.get('qn', ''))} | `{md_escape(r.get('path', ''))}` | "
                     f"`{md_escape(r.get('handler', ''))}` | {file_link(r.get('file', ''), server, repo, sha)} |")
        if n_routes > max_rows:
            L.append(f"\n…and {n_routes - max_rows} more.")
        L.append("")

    if a["areas"]:
        L += ["### Most affected areas", "", "| Area | Dependent files |", "|---|---|"]
        for area, count in a["areas"].most_common(10):
            L.append(f"| `{md_escape(area)}` | {count} |")
        L.append("")

    if a["tests"]:
        L += [f"<details><summary><b>Tests that exercise affected code ({n_tests})</b></summary>", ""]
        L += [f"- {file_link(t, server, repo, sha)}" for t in a["tests"][:max_rows]]
        if n_tests > max_rows:
            L.append(f"- …and {n_tests - max_rows} more")
        L += ["", "</details>", ""]

    if a["product_files"]:
        L += [f"<details><summary><b>All dependent files ({n_prod})</b></summary>", ""]
        L += [f"- {file_link(f, server, repo, sha)}" for f in a["product_files"][:max_rows]]
        if n_prod > max_rows:
            L.append(f"- …and {n_prod - max_rows} more")
        L += ["", "</details>", ""]

    L += [f"<details><summary>Changed files ({len(a['changed'])})</summary>", ""]
    L += [f"- {file_link(f, server, repo, sha)}" for f in a["changed"][:max_rows]]
    L += ["", "</details>", ""]

    if a["truncated"]:
        L += ["> The engine truncated this result. Counts above are a lower bound.", ""]
    L += ["---",
          f"<sub>Static analysis by [Pergamon](https://github.com/chrisarseno/pergamon) on "
          f"[codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) {engine_version}. "
          f"It follows calls, imports and usages in the code; it can miss dynamic dispatch, "
          f"reflection and runtime configuration. Merge base `{a['merge_base'][:10]}`.</sub>"]
    return "\n".join(L) + "\n"


# ── GitHub ──────────────────────────────────────────────────────────────────

def gh(method: str, url: str, token: str, body: dict | None = None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
        return json.loads(data) if data else None


def post_sticky_comment(api: str, repo: str, pr: int, token: str, body: str) -> str:
    base = f"{api}/repos/{repo}/issues/{pr}/comments"
    page = 1
    while True:
        comments = gh("GET", f"{base}?per_page=100&page={page}", token) or []
        for c in comments:
            if MARKER in (c.get("body") or ""):
                gh("PATCH", f"{api}/repos/{repo}/issues/comments/{c['id']}", token, {"body": body})
                return f"updated comment {c['id']}"
        if len(comments) < 100:
            break
        page += 1
    c = gh("POST", base, token, {"body": body})
    return f"created comment {c['id']}"


# ── main ────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--binary", required=True)
    p.add_argument("--repo-path", default=".")
    p.add_argument("--base", required=True, help="Base ref, e.g. origin/main")
    p.add_argument("--depth", type=int, default=2)
    p.add_argument("--mode", default="fast", choices=["fast", "moderate", "full"])
    p.add_argument("--engine-version", default="")
    p.add_argument("--max-rows", type=int, default=25)
    p.add_argument("--area-levels", type=int, default=2)
    p.add_argument("--medium-threshold", type=int, default=10)
    p.add_argument("--high-threshold", type=int, default=50)
    p.add_argument("--output", default="pergamon-review.md")
    p.add_argument("--json-output", default="")
    p.add_argument("--comment", choices=["true", "false"], default="true")
    args = p.parse_args()

    repo_path = os.path.abspath(args.repo_path)
    name = "pergamon-review"
    print(f"Pergamon: indexing {repo_path} (mode={args.mode})", flush=True)
    idx = index_repo(args.binary, repo_path, name, args.mode)
    print(f"Pergamon: indexed {idx.get('nodes')} nodes, {idx.get('edges')} edges", flush=True)

    os.chdir(repo_path)  # detect_changes reads git state from the working directory
    dc = detect_changes(args.binary, name, args.base, args.depth)
    routes = all_routes(args.binary, name)
    a = analyse(dc, routes, args.area_levels, args.medium_threshold, args.high_threshold)

    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    gh_repo = os.environ.get("GITHUB_REPOSITORY", "")
    sha = os.environ.get("PERGAMON_HEAD_SHA") or os.environ.get("GITHUB_SHA", "")
    body = render(a, depth=args.depth, engine_version=args.engine_version, server=server,
                  repo=gh_repo, sha=sha, max_rows=args.max_rows)

    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(body)
    if args.json_output:
        with open(args.json_output, "w", encoding="utf-8") as fh:
            json.dump({k: (dict(v) if isinstance(v, Counter) else v) for k, v in a.items()
                       if k != "impacted"}, fh, indent=2)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(body)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"risk={a['risk'].lower()}\nimpacted-files={len(a['product_files'])}\n"
                     f"routes={len(a['routes'])}\ntests={len(a['tests'])}\n")
    print(f"Pergamon: risk={a['risk']} dependent_files={len(a['product_files'])} "
          f"routes={len(a['routes'])} tests={len(a['tests'])}", flush=True)

    pr = os.environ.get("PERGAMON_PR_NUMBER", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if args.comment == "true" and pr and token and gh_repo:
        api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
        try:
            print("Pergamon: " + post_sticky_comment(api, gh_repo, int(pr), token, body), flush=True)
        except urllib.error.HTTPError as e:
            # Fork pull requests get a read-only token; keep the job green and point at the summary.
            print(f"::warning::Pergamon could not comment (HTTP {e.code}); the review is in the job summary.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
