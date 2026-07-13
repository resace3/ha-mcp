from __future__ import annotations

from collections.abc import Sequence

from .models import DagDocument
from .repository import JsonDagRepository
from .validation import Finding, validate_dag


class DagStudioService:
    def __init__(self, repository: JsonDagRepository, read_only: bool = False):
        self.repository = repository
        self.read_only = read_only

    def _write(self) -> None:
        if self.read_only:
            raise PermissionError("READ_ONLY_MODE")

    def list(self) -> list[DagDocument]:
        return self.repository.list_documents()

    def get(self, id: str) -> DagDocument:  # noqa: A002
        return self.repository.get_document(id)

    def create(self, doc: DagDocument) -> DagDocument:
        self._write()
        return self.repository.create_document(doc)

    def save(self, doc: DagDocument, expected_revision: int) -> DagDocument:
        self._write()
        return self.repository.update_document(doc, expected_revision)

    def delete(self, id: str, confirmed: bool) -> None:  # noqa: A002
        self._write()
        if not confirmed:
            raise ValueError("explicit confirmation required")
        self.repository.delete_document(id)

    def validate(self, doc: DagDocument) -> Sequence[Finding]:
        return validate_dag(doc)

    def revisions(self, id: str) -> Sequence[int]:  # noqa: A002
        self.get(id)
        return self.repository.list_revisions(id)

    def restore(self, id: str, revision: int, expected_revision: int) -> DagDocument:  # noqa: A002
        self._write()
        current = self.get(id)
        if current.revision != expected_revision:
            from .repository import RevisionConflict

            raise RevisionConflict(
                f"expected {expected_revision}, current {current.revision}"
            )
        return self.repository.restore_revision(id, revision)

    def approve(self, id: str, revision: int, approved_by: str) -> DagDocument:  # noqa: A002
        self._write()
        doc = self.get(id)
        findings = self.validate(doc)
        if any(f.severity == "error" for f in findings):
            raise ValueError("approval blocked by structural errors")
        from .models import utcnow

        return self.save(
            doc.model_copy(
                update={
                    "status": "user_approved",
                    "approved_at": utcnow(),
                    "approved_by": approved_by,
                }
            ),
            revision,
        )
