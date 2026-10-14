# Changelog

## v0.1.0 — 2026-10-14

First release of Pergamon as a pull-request impact review built on unmodified codebase-memory-mcp v0.11.0.

- Sticky pull request comment listing dependent files, HTTP routes and tests within N hops of the changed code.
- Low, Medium and High impact rating with configurable thresholds; exposed as Action outputs.
- Folder grouping that goes deeper automatically in monorepos.
- Engine download pinned to a release and verified against upstream SHA-256 checksums.
- Falls back to the job summary when the token can't comment (fork pull requests).

The earlier Pergamon fork of codebase-memory-mcp (Aug 2026) is archived at `chrisarseno/pergamon-fork-2026`; upstream has since shipped its own memory fix and the CBM Lens interface.
