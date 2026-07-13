# Local DAG Studio

Local DAG Studio is an optional offline causal-DAG editor. It provides schema-versioned JSON documents, nodes and directed edges, structural validation, revision conflicts, backups, export, and explicit approval. It is not an effect estimator, medical device, or proof that real-world assumptions are true.

## Standalone development mode

```console
uv run ha-mcp-dag-studio --port 8765 --open-browser
```

The offline editor needs no Home Assistant credentials. Data uses HA-MCP's persistent data directory under `dag-studio`; `--data-dir PATH` overrides it and port `0` selects an ephemeral port. Non-loopback binding is refused unless `--allow-remote` is supplied. This command is a development utility; the production add-on uses authenticated Home Assistant ingress and the main HA-MCP runtime.

Open `http://127.0.0.1:8765/dag-studio/`. Create nodes and directed edges in the inspector, save, validate, and export JSON. Updates send the revision last read; stale saves return HTTP 409.

Validation reports cycles and their path, missing or identical exposure/outcome, missing directed exposure-to-outcome paths, isolated nodes, and collider-like structures. Possible colliders require domain review. Approval is blocked while errors remain.

## Production add-on

Install the custom repository `https://github.com/resace3/ha-mcp`, then install **Home Assistant MCP Server - DAG Studio**. Its slug is `ha_mcp_dag`, its image is owned by `ghcr.io/resace3`, and it is intentionally distinct from the official add-on. The DAG fork defaults to its dedicated ten-tool profile; generic Home Assistant device, service, automation, and configuration tools are not registered.

Keep the official MCP add-on installed as the rollback target. Stop it only immediately before starting the DAG fork because both use the same local MCP port. Do not expose port 9583 through a router. Use the existing Nabu Casa / Webhook Proxy and set its `mcp_server_url` to the fork's local secret-path endpoint. The proxy must publish MCP only; the Studio remains reachable solely through authenticated, admin-only Supervisor ingress.

The production editor supports document, node, and edge CRUD; positions and roles; optimistic saves; revision history and confirmed restore; deterministic validation; confirmed approval and deletion; bounded JSON import; and JSON/DOT export. All destructive actions are explicit and deletion keeps a recovery backup.

## Privacy and security

AI is disabled by default. An OpenAI-compatible provider requires a configured model; API keys remain server-side and entity values are excluded by default. AI review is advisory and suggestions may not mutate a graph without preview and explicit confirmation.

Keep the loopback default for standalone mode. In the add-on, Studio routes are registered separately from the MCP secret path and reject non-ingress peers. The add-on declares `panel_admin: true`. Mutations require CSRF protection; Host and Origin checks, conservative rate and payload limits, restrictive response headers, safe DOM rendering, canonical document IDs, atomic persistence, and `READ_ONLY_MODE` enforcement apply server-side.

Home Assistant history proposals accept only an explicit list of 2-8 non-sensitive `sensor.*` entities, a 1-168 hour window, 5-1440 minute aggregation, and a bounded lag. The operator must confirm temporal and causal-direction assumptions. The Green requests bounded pre-aggregated recorder statistics, counts the returned bins locally, and discards statistic values; raw observations and statistic values are never returned, logged, or stored in the proposed DAG. Server-side AI stays disabled.

Data lives in `/data/dag_studio`. Documents and revision snapshots are mode 0600 and directories mode 0700 where the platform supports POSIX permissions. Audit events contain only operation metadata, actor/session identifiers when available, document ID, revision, result, timestamp, and correlation ID.

## Rollback

1. Stop **Home Assistant MCP Server - DAG Studio**.
2. Start the still-installed official **Home Assistant MCP Server**.
3. Restore the existing proxy's `mcp_server_url` to the official add-on endpoint and restart only the proxy if its configuration requires it.
4. Confirm the official tool list and one read-only MCP call. Do not delete the DAG fork, proxy, official add-on, or Home Assistant backup.

To return to DAG Studio, stop the official MCP add-on, start the fork, restore the fork target in the proxy, and repeat the MCP discovery smoke test.

## Development

```console
uv sync --group dev
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run pytest tests/src/unit/dag_studio
python -m build
```

Browser smoke tests should cover desktop and mobile-width layouts, hostile labels, create/save/reload, validation, revision conflict and restore, approval, JSON/DOT export, and deletion. See `docs/dag-studio-threat-model.md` for the security boundary and residual risks.
