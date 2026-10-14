# Pergamon

**See what a pull request really touches before you merge it.**

Pergamon is a GitHub Action that comments on every pull request with the code that depends on what changed: other files, HTTP routes and tests. It builds a code knowledge graph on your own runner with [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp), so no code leaves the runner and no API key is needed.

```yaml
# .github/workflows/pergamon.yml
name: Pergamon
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  impact:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: chrisarseno/pergamon@v0
```

## What the comment looks like

From the [demo repository](https://github.com/chrisarseno/pergamon-demo/pulls), a pull request that adds a timeout to the shared `query()` database helper:

> 🟡 **Medium impact.** This pull request changes **1 file**. Within 2 hops of the changed code, **4 other files** depend on it, including **5 HTTP routes** and **2 test files**.
>
> | Method | Route | Handler | File |
> |---|---|---|---|
> | GET | `/users` | `users_index` | `app/api/main.py` |
> | GET | `/me/orders` | `my_orders` | `app/api/main.py` |
> | POST | `/orders` | `orders_create` | `app/api/main.py` |
> | … | | | |

Routes count only when their handler function itself reaches the changed code, or when the handler's own lines were edited. `POST /orders` is listed because it calls `place_order`, which calls `get_user`, which calls `query()`. `GET /health` lives in the same file but never touches the database, so it goes in a collapsed "other routes in dependent files" list instead.

The comment is sticky: Pergamon edits the same comment on each push instead of adding a new one. The full review also appears in the job summary.

Speed: on a private 2,800-file Python monorepo, full-mode indexing plus analysis takes about a minute on a 2-vCPU machine (about 35 seconds in `fast` mode, which skips some folders). Small repositories finish in seconds; this repository's own review runs in about 30 seconds end to end on `ubuntu-latest`.

## Why

Code review shows you the diff. It doesn't show you the 136 files that import the function you changed, or the routes that run through it. Pergamon answers "what else could this break?" from a real call and import graph, not from text search or an LLM guess.

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `depth` | `2` | Hops of callers, importers and users to follow from the changed code |
| `mode` | `full` | Indexing mode. `full` indexes every source file. `fast` and `moderate` are quicker but skip folders such as `scripts/`, `tools/`, `bin/`, `migrations/`, `e2e/`, `integration/`, `examples/` and `docs/` |
| `comment` | `true` | Post the sticky comment. Set `false` to only write the job summary |
| `base-ref` | PR base | Branch to compare against |
| `max-rows` | `25` | Rows per table or list |
| `medium-threshold` | `10` | Dependent files for Medium impact |
| `high-threshold` | `50` | Dependent files for High impact |
| `engine-version` | `v0.11.0` | codebase-memory-mcp release |

Impact is also rated Medium when any route is affected, and High at 10 or more affected routes.

## Outputs

`risk` (`low`/`medium`/`high`), `impacted-files`, `routes` and `tests`. Use them to gate or label pull requests:

```yaml
      - uses: chrisarseno/pergamon@v0
        id: pergamon
      - if: steps.pergamon.outputs.risk == 'high'
        run: gh pr edit ${{ github.event.pull_request.number }} --add-label "high-impact"
        env:
          GH_TOKEN: ${{ github.token }}
```

## Limits

- Static analysis. It follows calls, imports and usages in the code; it can miss dynamic dispatch, reflection, string-built imports and runtime configuration.
- "Depends on" means the file references the changed code, not that the change breaks it.
- Linux runners only (x64 and arm64).
- Pull requests from forks get a read-only token, so Pergamon writes the job summary instead of commenting.
- `actions/checkout` needs `fetch-depth: 0`, or Pergamon fetches history itself.

## How it works

1. Downloads a pinned codebase-memory-mcp release and verifies its SHA-256 checksum.
2. Indexes the checked-out pull request into a local knowledge graph (158 languages via tree-sitter).
3. Runs `detect_changes` against the merge base to find the changed code and everything that depends on it.
4. Looks up HTTP routes defined in those files with a Cypher query.
5. Writes one Markdown comment and the job summary.

The engine is unmodified upstream codebase-memory-mcp by DeusData, MIT licensed. Pergamon is the pull-request layer on top.

## Local use

```bash
python3 scripts/pergamon_review.py --binary /path/to/codebase-memory-mcp \
  --repo-path . --base origin/main --comment false --output review.md
```

## Status

v0. The comment format and inputs may change before v1. Feedback and issues welcome.

## License

MIT. See [LICENSE](LICENSE). codebase-memory-mcp is MIT licensed by DeusData.
