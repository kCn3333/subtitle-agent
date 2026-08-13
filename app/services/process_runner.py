import asyncio
import subprocess


class ProcessTimeoutError(RuntimeError):
    pass


class ProcessExecutionError(RuntimeError):
    pass


async def run_process(arguments: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
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
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "brak szczegółów"
        raise ProcessExecutionError(f"Proces zakończył się kodem {result.returncode}: {detail[:300]}")
    return result
