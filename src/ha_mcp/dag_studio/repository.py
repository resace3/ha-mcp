from __future__ import annotations

import os
import threading
from pathlib import Path

from .models import DagDocument, utcnow


class RevisionConflict(ValueError):
    pass


class JsonDagRepository:
    def __init__(self, root: Path, history_limit: int = 50):
        self.root = root
        self.history = root / "revisions"
        self.backups = root / "backups"
        self.limit = history_limit
        self._lock = threading.RLock()
        for p in (root, self.history, self.backups):
            p.mkdir(parents=True, exist_ok=True)

    def _path(self, id: str) -> Path:  # noqa: A002
        if "/" in id or "\\" in id or ".." in id:
            raise ValueError("invalid DAG ID")
        return self.root / f"{id}.json"

    def list_documents(self) -> list[DagDocument]:
        return [
            DagDocument.model_validate_json(p.read_text("utf-8"))
            for p in self.root.glob("*.json")
        ]

    def get_document(self, id: str) -> DagDocument:  # noqa: A002
        return DagDocument.model_validate_json(self._path(id).read_text("utf-8"))

    def create_document(self, doc: DagDocument) -> DagDocument:
        with self._lock:
            if self._path(doc.id).exists():
                raise FileExistsError(doc.id)
            doc = doc.model_copy(update={"revision": 1, "updated_at": utcnow()})
            self._write(doc)
            return doc

    def update_document(self, doc: DagDocument, expected_revision: int) -> DagDocument:
        with self._lock:
            current = self.get_document(doc.id)
            if current.revision != expected_revision:
                raise RevisionConflict(
                    f"expected {expected_revision}, current {current.revision}"
                )
            self._snapshot(current)
            updated = doc.model_copy(
                update={
                    "revision": current.revision + 1,
                    "created_at": current.created_at,
                    "updated_at": utcnow(),
                }
            )
            self._write(updated)
            return updated

    def delete_document(self, id: str) -> None:  # noqa: A002
        with self._lock:
            p = self._path(id)
            current = self.get_document(id)
            self._snapshot(current, self.backups)
            p.unlink()

    def list_revisions(self, id: str) -> list[int]:  # noqa: A002
        return sorted(int(p.stem) for p in (self.history / id).glob("*.json"))

    def restore_revision(self, id: str, revision: int) -> DagDocument:  # noqa: A002
        return self.update_document(
            DagDocument.model_validate_json(
                (self.history / id / f"{revision}.json").read_text("utf-8")
            ),
            self.get_document(id).revision,
        )

    def _snapshot(self, doc: DagDocument, base: Path | None = None) -> None:
        d = (base or self.history) / doc.id
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{doc.revision}.json").write_text(doc.model_dump_json(indent=2), "utf-8")

    def _write(self, doc: DagDocument) -> None:
        target = self._path(doc.id)
        tmp = target.with_suffix(".tmp")
        data = doc.model_dump_json(indent=2)
        with tmp.open("w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
