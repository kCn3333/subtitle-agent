import asyncio
import re
import subprocess
from collections.abc import Collection


ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


class ProcessTimeoutError(RuntimeError):
    pass


class ProcessExecutionError(RuntimeError):
    pass


def safe_process_detail(value: str) -> str:
    cleaned = ANSI_ESCAPE.sub("", value)
    cleaned = "".join(character if character in "\n\r\t" or ord(character) >= 32 else " " for character in cleaned)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    return (lines[-1] if lines else "brak szczegółów")[:300]


async def run_process(arguments: list[str], timeout: float,
                      accepted_returncodes: Collection[int] = (0,)) -> subprocess.CompletedProcess[str]:
    def execute() -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                arguments,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProcessTimeoutError(f"Proces przekroczył limit {timeout:g} s") from exc

    result = await asyncio.to_thread(execute)
    if result.returncode not in accepted_returncodes:
        detail = safe_process_detail(result.stderr)
        raise ProcessExecutionError(f"Proces zakończył się kodem {result.returncode}: {detail}")
    return result
