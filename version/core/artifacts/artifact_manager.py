"""
core/artifacts/artifact_manager.py — Artifact Lifecycle & Delivery Guard

Manages validation, checksum generation, duplicate-delivery protection,
and SQLite persistence for all generated office deliverables (Word, Excel, PDF, PPT).
"""

import os
import hashlib
import re
from typing import Any, Dict, List, Optional
from core.database.sqlite_db import record_artifact, get_artifacts_for_task, mark_artifact_delivered


class ArtifactLifecycleManager:
    """Oversees validation, integrity hashing, and delivery tracking of files."""

    def sanitize_filename(self, filename: str) -> str:
        """Prevent path traversal and unsafe filesystem characters."""
        clean = os.path.basename(filename)
        clean = re.sub(r'[\\/*?:"<>|]', "_", clean)
        return clean.strip() or "deliverable"

    def calculate_checksum(self, file_path: str) -> str:
        """Compute SHA-256 hash of a file for integrity and idempotency."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                sha256.update(chunk)
        return sha256.hexdigest()

    def validate_artifact(self, file_path: str) -> Dict[str, Any]:
        """
        Validate that a generated file exists on disk, is non-empty,
        and is readable.
        """
        if not file_path or not os.path.exists(file_path):
            return {
                "valid": False,
                "error": f"Artifact file does not exist on disk: {file_path}",
                "size_bytes": 0,
            }

        if not os.path.isfile(file_path):
            return {
                "valid": False,
                "error": f"Artifact path is not a regular file: {file_path}",
                "size_bytes": 0,
            }

        size = os.path.getsize(file_path)
        if size == 0:
            return {
                "valid": False,
                "error": f"Artifact file is 0 bytes (empty): {file_path}",
                "size_bytes": 0,
            }

        checksum = self.calculate_checksum(file_path)
        return {
            "valid": True,
            "error": None,
            "size_bytes": size,
            "checksum_sha256": checksum,
        }

    def register_artifact(
        self,
        parent_task_id: str,
        file_path: str,
        artifact_type: str,
        subtask_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validate and record an artifact in SQLite.
        Guards against registering corrupt or 0-byte files.
        """
        val = self.validate_artifact(file_path)
        if not val["valid"]:
            raise ValueError(val["error"])

        return record_artifact(
            parent_task_id=parent_task_id,
            file_path=file_path,
            artifact_type=artifact_type,
            subtask_id=subtask_id,
            validation_status="passed",
        )

    def is_already_delivered(self, parent_task_id: str, file_path: str) -> bool:
        """
        Check if this exact file (or identical checksum) was already marked delivered
        for this task to prevent duplicate delivery on retries.
        """
        artifacts = get_artifacts_for_task(parent_task_id)
        if not artifacts or not os.path.isfile(file_path):
            return False

        current_checksum = self.calculate_checksum(file_path)
        for art in artifacts:
            if art.get("delivery_status") == "delivered":
                if art.get("checksum_sha256") == current_checksum or art.get("file_path") == file_path:
                    return True
        return False

    def mark_delivered(self, file_path_or_id: str):
        """Mark an artifact as safely delivered."""
        mark_artifact_delivered(file_path_or_id)
