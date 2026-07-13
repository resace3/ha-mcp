"""Redacted DAG Studio audit log."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path


class AuditLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(
        self,
        *,
        operation: str,
        document_id: str,
        revision: int | None,
        result: str,
        correlation_id: str,
        actor: str = "mcp-client",
    ) -> None:
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "actor": actor[:100],
            "operation": operation,
            "document_id": document_id,
            "revision": revision,
            "result": result,
            "correlation_id": correlation_id,
        }
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Fail closed: an audit record is not considered complete unless its
        # file is restricted to the add-on process owner.
        os.chmod(self.path, 0o600)
