import sys

import pytest

from app.services.process_runner import ProcessTimeoutError, run_process


@pytest.mark.anyio
async def test_process_timeout_is_safe():
    with pytest.raises(ProcessTimeoutError):
        await run_process([sys.executable, "-c", "import time; time.sleep(2)"], 0.01)
