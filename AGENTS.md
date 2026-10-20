# Open Codex contributor guidelines

Open Codex is a generic host for the native Codex app-server. Keep platform-specific business
logic in external MCP servers, Apps or configured providers. Preserve the native runtime's
thread, turn, tool, permission and event contracts; inspect the relevant source and protocol
before changing behavior. Do not implement a second agent loop in the Host.

The Python package lives in `src/codex_agent`, React sources in `frontend/src`, tests in
`tests`, and deployment configuration in `config` and `deploy`. Keep the public Python package
and CLI names compatible. Use typed Python with four-space indentation and snake_case.

Install `python -m pip install -e '.[test]'` and run
`python -m unittest discover -s tests -p 'test_*.py'`. For frontend changes, run
`npm --prefix frontend ci`, `npm --prefix frontend test`, and
`npm --prefix frontend run build`. Use the focused browser fixtures described in
`docs/09-test-acceptance.md` for changed interactions. Record skipped native-runtime tests.

Never commit credentials, `.local/`, runtime state, environments, caches or node_modules.
Do not alter running services or user configuration as a side effect of verification.
Do not stage unrelated files. Use scoped imperative commit messages and describe behavior,
validation and compatibility impact in pull requests. See `docs/23-repository-maintenance.md`
for the source history and Galatea's pinned submodule integration.
