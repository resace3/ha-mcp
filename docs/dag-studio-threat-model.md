# DAG Studio threat model

## Scope and assets

The protected Home Assistant ingress UI, dedicated DAG-only MCP profile, add-on storage, and Nabu Casa webhook boundary are in scope. Assets include Home Assistant entity history, DAG documents and revisions, add-on storage, Home Assistant and Supervisor credentials, the webhook identifier, OAuth credentials, and client MCP configuration.

## Trust boundaries

Home Assistant history crosses from Core into the add-on and is reduced locally to counts, missingness summaries, coarse diagnostics, nodes, edges, and provenance. Raw rows must not cross the MCP boundary. The Studio crosses Home Assistant Supervisor ingress and is never mounted beneath the public MCP webhook path. The webhook identifier is a bearer secret unless OAuth is successfully negotiated.

## Threats and controls

- Stolen or guessed webhook: high-entropy secret path, HTTPS, DAG-only tool allowlist, confirmation tokens, conservative limits, and rotation after exposure. Residual risk remains for bearer-secret theft.
- Prompt injection and tool poisoning: sensor names, states, labels, imports, webpages, and tool output are treated as data. Server instructions forbid generic HA operations.
- Unauthorized UI access: Supervisor ingress plus `panel_admin`; direct Studio mounting beneath the public MCP secret path is prohibited.
- CSRF, malicious origins, and host spoofing: mutations require a per-process CSRF token; add-on ingress root routes also require the Supervisor ingress peer. The proxy must expose MCP only.
- XSS: UI uses `textContent`; CSP blocks external scripts and untrusted HTML.
- Traversal: document IDs are restricted and repository paths reject separators and `..`.
- SSRF: entity references are identifiers, not URLs. AI base URLs are operator configuration and AI is disabled.
- Denial of service: request size, graph size, variables, history window, and retained row counts are bounded. Deployment should also rate-limit at the application/proxy edge.
- Races: optimistic revision matching and atomic replacement prevent silent stale overwrites.
- Secret leakage: audits contain operation metadata only; no raw document, prompt, history, token, or authorization header. Screenshots and CI artifacts must be reviewed before publication.
- Supply chain: pinned base-image digests, lockfile, SBOM/provenance, dependency and container scanning. GitHub Actions should be pinned to immutable SHAs before production acceptance.
- Excess privilege: the dedicated profile exposes only DAG tools. The current add-on retains Supervisor manager and host networking inherited from upstream; this is residual risk and must be justified or reduced after live compatibility testing.
- Data exfiltration: external server-side AI is disabled; the history tool returns summaries and the proposed graph only.

## Retention and deletion

DAGs and bounded revisions persist in `/data/dag_studio`. Delete creates a recovery backup. Audit metadata persists until an operator removes it. Encryption at rest is not added because the add-on has no independent key-management boundary; Home Assistant backup encryption and host storage protections apply.

## Acceptance boundary

Structural validation is not proof of causality or clinical validity. Production acceptance requires protocol tests proving no raw rows cross MCP, public requests cannot reach the Studio, confirmations are single-use and revision-bound, and no unresolved high or critical finding remains.
