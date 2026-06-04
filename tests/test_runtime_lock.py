from __future__ import annotations

import pytest

from kalshi_bot.runtime_lock import ProcessLockError, exclusive_process_lock


def test_process_lock_blocks_a_second_holder(tmp_path):
    lock_path = tmp_path / "bot.loop.lock"

    with exclusive_process_lock(lock_path):
        with pytest.raises(ProcessLockError, match="Another bot loop is already running"):
            with exclusive_process_lock(lock_path):
                pass


def test_process_lock_releases_after_context(tmp_path):
    lock_path = tmp_path / "bot.loop.lock"

    with exclusive_process_lock(lock_path):
        pass

    with exclusive_process_lock(lock_path):
        pass
