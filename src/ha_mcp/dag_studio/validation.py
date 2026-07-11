from __future__ import annotations

from pydantic import BaseModel, Field

from .models import DagDocument


class Finding(BaseModel):
    code: str
    severity: str
    message: str
    node_ids: list[str] = Field(default_factory=list)
    edge_ids: list[str] = Field(default_factory=list)


def validate_dag(doc: DagDocument) -> list[Finding]:  # noqa: C901
    out: list[Finding] = []
    adj: dict[str, list[tuple[str, str]]] = {node.id: [] for node in doc.nodes}
    incoming: dict[str, list[str]] = {node.id: [] for node in doc.nodes}
    for e in doc.edges:
        adj[e.source_node_id].append((e.target_node_id, e.id))
        incoming[e.target_node_id].append(e.source_node_id)
    seen: set[str] = set()
    stack: list[str] = []
    active: set[str] = set()

    def visit(n: str) -> None:
        seen.add(n)
        active.add(n)
        stack.append(n)
        for nxt, eid in adj[n]:
            if nxt in active:
                i = stack.index(nxt)
                out.append(
                    Finding(
                        code="cycle",
                        severity="error",
                        message="Cycle: " + " -> ".join(stack[i:] + [nxt]),
                        node_ids=stack[i:] + [nxt],
                        edge_ids=[eid],
                    )
                )
            elif nxt not in seen:
                visit(nxt)
        stack.pop()
        active.remove(n)

    for node_id in adj:
        if node_id not in seen:
            visit(node_id)
    if not doc.exposure_node_id:
        out.append(
            Finding(
                code="missing_exposure",
                severity="error",
                message="Set an exposure before approval.",
            )
        )
    if not doc.outcome_node_id:
        out.append(
            Finding(
                code="missing_outcome",
                severity="error",
                message="Set an outcome before approval.",
            )
        )
    if doc.exposure_node_id and doc.exposure_node_id == doc.outcome_node_id:
        out.append(
            Finding(
                code="same_exposure_outcome",
                severity="error",
                message="Exposure and outcome must be distinct.",
            )
        )
    for node in doc.nodes:
        if not adj[node.id] and not incoming[node.id]:
            out.append(
                Finding(
                    code="isolated_node",
                    severity="warning",
                    message=f"{node.label} is isolated.",
                    node_ids=[node.id],
                )
            )
        if len(incoming[node.id]) >= 2:
            out.append(
                Finding(
                    code="possible_collider",
                    severity="warning",
                    message=f"{node.label} may be a collider; requires domain review.",
                    node_ids=[node.id],
                )
            )
    if doc.exposure_node_id and doc.outcome_node_id:
        reachable: set[str] = set()
        todo = [doc.exposure_node_id]
        while todo:
            x = todo.pop()
            reachable.add(x)
            todo += [y for y, _ in adj[x] if y not in reachable]
        if doc.outcome_node_id not in reachable:
            out.append(
                Finding(
                    code="no_causal_path",
                    severity="error",
                    message="No directed exposure-to-outcome path exists.",
                )
            )
    return out
