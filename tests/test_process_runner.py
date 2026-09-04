import sys

import pytest

from app.services.process_runner import ProcessExecutionError, ProcessTimeoutError, run_process, safe_process_detail


@pytest.mark.anyio
async def test_process_timeout_is_safe():
    with pytest.raises(ProcessTimeoutError):
        await run_process([sys.executable, "-c", "import time; time.sleep(2)"], 0.01)


@pytest.mark.anyio
async def test_accepted_returncode_is_explicit():
    result = await run_process([sys.executable, "-c", "raise SystemExit(1)"], 1,
                               accepted_returncodes=(0, 1))
    assert result.returncode == 1
    with pytest.raises(ProcessExecutionError):
        await run_process([sys.executable, "-c", "raise SystemExit(1)"], 1)


def test_process_detail_removes_ansi_controls_and_uses_last_line():
    assert safe_process_detail("first\n\x1b[31mfinal\x07 line\x1b[0m\n") == "final  line"
