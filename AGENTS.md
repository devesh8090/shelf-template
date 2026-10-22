# Working on citeshelf itself

This repository is the **citeshelf package** (the `shelf` command), not a
library. The rules agents follow *inside* a library are the template
`src/citeshelf/templates/AGENTS.md`, copied into each library by
`shelf init` and refreshed by `shelf upgrade`.

- Code: `src/citeshelf/cli.py`. Format spec: `src/citeshelf/templates/SPEC.md`.
- Tests: `uv run pytest -q`. They never touch `~/.citeshelf` or real tools.
- Try it: `uv run shelf init /tmp/lib && cd /tmp/lib && uv run --project <repo> shelf doctor`.
- The extraction tool is pinned (`MINERU_VERSION` in cli.py). Moving to a new
  version or a different tool means: run `shelf extract` for real on a born-digital
  PDF and a scan, then bump the pin in a release.
- Keep `README.md`, the AGENTS.md template and SPEC.md in step with the code.
