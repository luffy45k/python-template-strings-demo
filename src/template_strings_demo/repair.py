"""Approval-gated, transactional workspace repair primitives for Mrx."""

from __future__ import annotations

import difflib
import hashlib
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional


class RepairError(RuntimeError):
    """Raised when a repair is invalid, stale, unapproved, or fails validation."""


@dataclass(frozen=True)
class FileRepair:
    path: str
    content: str
    expected_sha256: Optional[str]
    diff: str


@dataclass(frozen=True)
class RepairProposal:
    """Immutable proposal that must be explicitly approved before application."""

    summary: str
    changes: tuple[FileRepair, ...]
    approval_token: str


@dataclass(frozen=True)
class RepairResult:
    applied: bool
    files: tuple[str, ...]
    validation: str


Validator = Callable[[], bool]


class CodeRepairManager:
    """Create and atomically apply bounded repairs inside one workspace.

    This class never invokes a shell. A host-supplied validator may run tests, but
    applying changes still requires the proposal's one-time approval token.
    """

    def __init__(
        self,
        workspace: Path | str,
        *,
        max_files: int = 10,
        max_file_bytes: int = 1_000_000,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        if max_files < 1 or max_file_bytes < 1:
            raise ValueError("repair limits must be positive")
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self._used_tokens: set[str] = set()

    def propose(self, summary: str, replacements: Mapping[str, str]) -> RepairProposal:
        if not summary.strip():
            raise ValueError("repair summary must not be empty")
        if not replacements or len(replacements) > self.max_files:
            raise RepairError("repair contains an invalid number of files")

        changes = []
        for relative, content in replacements.items():
            if not isinstance(content, str):
                raise TypeError("replacement content must be text")
            if len(content.encode("utf-8")) > self.max_file_bytes:
                raise RepairError(f"replacement exceeds size limit: {relative}")
            path = self._safe_path(relative)
            old = path.read_text() if path.exists() else ""
            digest = self._digest(path) if path.exists() else None
            diff = "".join(
                difflib.unified_diff(
                    old.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"a/{relative}",
                    tofile=f"b/{relative}",
                )
            )
            changes.append(FileRepair(relative, content, digest, diff))
        return RepairProposal(summary.strip(), tuple(changes), secrets.token_urlsafe(24))

    def apply(
        self,
        proposal: RepairProposal,
        *,
        approval_token: str,
        validator: Optional[Validator] = None,
    ) -> RepairResult:
        if not secrets.compare_digest(proposal.approval_token, approval_token):
            raise RepairError("explicit approval token is required")
        if approval_token in self._used_tokens:
            raise RepairError("approval token has already been used")

        originals: dict[Path, Optional[bytes]] = {}
        for change in proposal.changes:
            path = self._safe_path(change.path)
            current = self._digest(path) if path.exists() else None
            if current != change.expected_sha256:
                raise RepairError(f"file changed after proposal: {change.path}")
            originals[path] = path.read_bytes() if path.exists() else None

        self._used_tokens.add(approval_token)
        try:
            for change in proposal.changes:
                path = self._safe_path(change.path)
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(f".{path.name}.mrx-repair")
                temporary.write_text(change.content)
                temporary.replace(path)
            if validator is not None and not validator():
                raise RepairError("repair validation failed")
        except Exception:
            self._rollback(originals)
            raise

        return RepairResult(True, tuple(change.path for change in proposal.changes), "passed")

    def _safe_path(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise RepairError("repair paths must be non-empty and relative")
        path = (self.workspace / relative).resolve()
        if path != self.workspace and self.workspace not in path.parents:
            raise RepairError(f"repair path escapes workspace: {relative}")
        if path == self.workspace:
            raise RepairError("repair path must identify a file")
        return path

    @staticmethod
    def _digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _rollback(originals: Mapping[Path, Optional[bytes]]) -> None:
        for path, content in originals.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
