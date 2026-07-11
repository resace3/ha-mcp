from pathlib import Path

import pytest
from pydantic import ValidationError

from ha_mcp.dag_studio.models import DagDocument, DagEdge, DagNode
from ha_mcp.dag_studio.repository import JsonDagRepository, RevisionConflict
from ha_mcp.dag_studio.validation import validate_dag


def node(id: str) -> DagNode:  # noqa: A002
    return DagNode(id=id, label=id)


def test_model_rejects_bad_edges():
    with pytest.raises(ValidationError):
        DagDocument(
            id="x",
            title="x",
            nodes=[node("a")],
            edges=[DagEdge(id="e", source_node_id="a", target_node_id="b")],
        )


def test_cycle_and_missing_endpoints():
    d = DagDocument(
        id="x",
        title="x",
        nodes=[node("a"), node("b")],
        edges=[
            DagEdge(id="e1", source_node_id="a", target_node_id="b"),
            DagEdge(id="e2", source_node_id="b", target_node_id="a"),
        ],
    )
    assert {f.code for f in validate_dag(d)} >= {
        "cycle",
        "missing_exposure",
        "missing_outcome",
    }


def test_repository_revisions(tmp_path: Path):
    r = JsonDagRepository(tmp_path)
    d = r.create_document(DagDocument(id="x", title="x"))
    assert d.revision == 1
    d = r.update_document(d.model_copy(update={"title": "y"}), 1)
    assert d.revision == 2 and r.list_revisions("x") == [1]
    with pytest.raises(RevisionConflict):
        r.update_document(d, 1)
