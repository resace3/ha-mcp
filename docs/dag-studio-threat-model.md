# DAG Studio threat model

## Scope and assets

The protected Home Assistant ingress UI, dedicated DAG-only MCP profile, add-on storage, and Nabu Casa webhook boundary are in scope. Assets include Home Assistant entity history, DAG documents and revisions, add-on storage, Home Assistant and Supervisor credentials, the webhook identifier, OAuth credentials, and client MCP configuration.

## Trust boundaries

Bounded pre-aggregated Home Assistant recorder statistics cross from Core into the add-on and are reduced locally to counts, missingness summaries, coarse diagnostics, nodes, edges, and provenance. Raw rows and statistic values must not cross the MCP boundary. The Studio crosses Home Assistant Supervisor ingress and is never mounted beneath the public MCP webhook path. The webhook identifier is a bearer secret unless OAuth is successfully negotiated.

## Threats and controls

- Stolen or guessed webhook: high-entropy secret path, HTTPS, DAG-only tool allowlist, confirmation tokens, conservative limits, and rotation after exposure. Residual risk remains for bearer-secret theft.
- Prompt injection and tool poisoning: sensor names, states, labels, imports, webpages, and tool output are treated as data. Server instructions forbid generic HA operations.
- Unauthorized UI access: Supervisor ingress plus `panel_admin`; direct Studio mounting beneath the public MCP secret path is prohibited.
- CSRF, malicious origins, and host spoofing: mutations require a per-process CSRF token; add-on ingress root routes also require the Supervisor ingress peer. The proxy must expose MCP only.
- XSS: UI uses `textContent`; CSP blocks external scripts and untrusted HTML.
- Traversal: document IDs are restricted and repository paths reject separators and `..`.
- SSRF: entity references are identifiers, not URLs. AI base URLs are operator configuration and AI is disabled.
- Denial of service: request size, graph size, variables, history window, retained row counts, processing time, and per-session request rate are bounded. The application currently allows 120 Studio requests per minute per actor/session; the public proxy never routes Studio requests.
- Races: optimistic revision matching and atomic replacement prevent silent stale overwrites.
- Secret leakage: audits contain operation metadata only; no raw document, prompt, history, token, or authorization header. Screenshots and CI artifacts must be reviewed before publication.
- Supply chain: pinned base-image digests, lockfile, per-architecture SBOM/provenance, an attested OCI manifest, dependency and container scanning, and GitHub Actions pinned to immutable SHAs.
- Excess privilege: the dedicated profile exposes only DAG tools, uses an enforcing custom AppArmor profile, requests no privileged container capabilities, and does not mount Home Assistant configuration. The add-on retains `hassio_role: manager` because the shared upstream runtime reads Supervisor add-on/service logs, and retains host networking plus the local 9583 mapping because the existing proxy targets the HA-MCP secret-path listener through the Supervisor host. These are material residual risks. The listener still requires the high-entropy secret path, is not routed by Nabu Casa except through the existing proxy, and must not have a router port-forward. A future dedicated transport should remove manager access, host networking, and the host port.
- Data exfiltration: external server-side AI is disabled; the history tool returns summaries and the proposed graph only.

## Retention and deletion

DAGs and bounded revisions persist in `/data/dag_studio`. Delete creates a recovery backup. Audit metadata persists until an operator removes it. Encryption at rest is not added because the add-on has no independent key-management boundary; Home Assistant backup encryption and host storage protections apply.

## Network and authorization boundary

The Supervisor authenticates ingress and exposes the panel only to administrators. Every Studio route is wrapped by an ingress-peer check; Studio files and APIs are not mounted beneath the public webhook. The existing Nabu Casa proxy is the only intended public path and forwards only MCP to the secret-path listener. The server-side dedicated profile is an authorization boundary: only the ten DAG tools are registered, regardless of client-side hiding. TLS verification remains enabled for outbound Home Assistant calls and DAG logic makes no external AI or arbitrary URL requests.

The public webhook path is a bearer secret when OAuth is unavailable. Theft of that full URL permits calls to the restricted DAG tool surface until rotation. Rate limiting, single-use confirmation tokens, local-store-only writes, and the absence of generic HA mutation tools reduce impact but do not eliminate it.

## Prompt-injection handling

Webpage text, entity IDs, entity states, imported labels, descriptions, notes, and tool results are untrusted data. They never select tools, alter policy, execute templates, import code, run shell commands, or define network destinations. Sensitive entity terms are rejected before history access, raw state content is discarded locally, UI labels use `textContent`, and DOT output escapes quotes, backslashes, and newlines.

## Acceptance boundary

Structural validation is not proof of causality or clinical validity. Production acceptance requires protocol tests proving no raw rows cross MCP, public requests cannot reach the Studio, confirmations are single-use and revision-bound, and no unresolved high or critical finding remains.
