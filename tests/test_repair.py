from __future__ import annotations

import pytest

from template_strings_demo import CodeRepairManager, RepairError


def test_repair_requires_approval_and_applies_transactionally(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("value = 1\n")
    manager = CodeRepairManager(tmp_path)
    proposal = manager.propose("Correct value", {"app.py": "value = 2\n"})

    assert "-value = 1" in proposal.changes[0].diff
    assert "+value = 2" in proposal.changes[0].diff
    with pytest.raises(RepairError, match="approval"):
        manager.apply(proposal, approval_token="wrong")

    result = manager.apply(
        proposal,
        approval_token=proposal.approval_token,
        validator=lambda: target.read_text() == "value = 2\n",
    )
    assert result.applied is True
    assert target.read_text() == "value = 2\n"
    with pytest.raises(RepairError, match="already been used"):
        manager.apply(proposal, approval_token=proposal.approval_token)


def test_failed_validation_rolls_back_all_files(tmp_path):
    first = tmp_path / "first.txt"
    first.write_text("old")
    manager = CodeRepairManager(tmp_path)
    proposal = manager.propose(
        "Two changes",
        {"first.txt": "new", "created.txt": "created"},
    )

    with pytest.raises(RepairError, match="validation"):
        manager.apply(
            proposal,
            approval_token=proposal.approval_token,
            validator=lambda: False,
        )
    assert first.read_text() == "old"
    assert not (tmp_path / "created.txt").exists()


def test_stale_unsafe_and_oversized_repairs_are_rejected(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("old")
    manager = CodeRepairManager(tmp_path, max_files=1, max_file_bytes=4)
    proposal = manager.propose("Change", {"app.py": "new"})
    target.write_text("changed elsewhere")

    with pytest.raises(RepairError, match="changed after proposal"):
        manager.apply(proposal, approval_token=proposal.approval_token)
    with pytest.raises(RepairError, match="escapes workspace"):
        manager.propose("Escape", {"../outside": "x"})
    with pytest.raises(RepairError, match="size limit"):
        manager.propose("Large", {"large.txt": "12345"})
    with pytest.raises(RepairError, match="number of files"):
        manager.propose("Empty", {})
