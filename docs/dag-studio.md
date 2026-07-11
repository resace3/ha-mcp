# Local DAG Studio

Local DAG Studio is an optional offline causal-DAG editor. It provides schema-versioned JSON documents, nodes and directed edges, structural validation, revision conflicts, backups, export, and explicit approval. It is not an effect estimator, medical device, or proof that real-world assumptions are true.

## Start

```console
uv run ha-mcp-dag-studio --port 8765 --open-browser
```

The offline editor needs no Home Assistant credentials. Data uses HA-MCP's persistent data directory under `dag-studio`; `--data-dir PATH` overrides it and port `0` selects an ephemeral port. Non-loopback binding is refused unless `--allow-remote` is supplied.

Open `http://127.0.0.1:8765/dag-studio/`. Create nodes and directed edges in the inspector, save, validate, and export JSON. Updates send the revision last read; stale saves return HTTP 409.

Validation reports cycles and their path, missing or identical exposure/outcome, missing directed exposure-to-outcome paths, isolated nodes, and collider-like structures. Possible colliders require domain review. Approval is blocked while errors remain.

## Privacy and security

AI is disabled by default. An OpenAI-compatible provider requires a configured model; API keys remain server-side and entity values are excluded by default. AI review is advisory and suggestions may not mutate a graph without preview and explicit confirmation.

Keep the loopback default. Integrated HTTP routes must live beneath HA-MCP's existing secret/authenticated prefix. Responses have restrictive security headers, requests are bounded, IDs cannot traverse paths, and the shared service blocks mutation in `READ_ONLY_MODE`.

## Development and limitations

```console
uv sync --group dev
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run pytest tests/src/unit/dag_studio
python -m build
```

The initial standalone editor does not yet include drag positioning, undo/redo, DOT export, AI proposals, MCP tools, or Home Assistant panel/shared-sidecar integration. Those integrations must reuse the existing protected settings route and sidecar security boundaries before enabling the feature in those runtimes.
