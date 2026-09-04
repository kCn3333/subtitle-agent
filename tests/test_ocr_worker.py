import io
import json
import zipfile
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from ocr_worker.main import app
from ocr_worker.main import _run_ocr


def _archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_worker_converts_complete_vobsub_pair(monkeypatch):
    async def run(source, output, language):
        assert source.name == "selected.eng.idx" and language == "eng"
        return b"1\n00:00:01,000 --> 00:00:02,000\nHello\n", ""

    monkeypatch.setattr("ocr_worker.main._run_ocr", run)
    with TestClient(app) as client:
        response = client.post("/v1/ocr", content=_archive({
            "selected.eng.idx": b"idx", "selected.eng.sub": b"sub",
        }), headers={"Content-Type": "application/zip", "X-OCR-Language": "eng"})
    assert response.status_code == 200
    assert response.json()["cueCount"] == 1
    assert response.json()["firstMs"] == 1000 and response.json()["lastMs"] == 2000
    assert response.json()["lastStartMs"] == 1000


def test_worker_rejects_incomplete_vobsub_pair():
    with TestClient(app) as client:
        response = client.post("/v1/ocr", content=_archive({"selected.eng.idx": b"idx"}),
                               headers={"Content-Type": "application/zip"})
    assert response.status_code == 422
    assert "kompletnej pary" in response.text


def test_worker_rejects_archive_paths():
    with TestClient(app) as client:
        response = client.post("/v1/ocr", content=_archive({"../selected.eng.sup": b"sup"}),
                               headers={"Content-Type": "application/zip"})
    assert response.status_code == 422
    assert "niedozwolone" in response.text


def test_worker_always_removes_temporary_directory(tmp_path, monkeypatch):
    temporary = tmp_path / "worker-temp"

    async def fail(*args, **kwargs):
        raise RuntimeError("forced failure")

    monkeypatch.setattr("ocr_worker.main.tempfile.mkdtemp", lambda **kwargs: str(temporary))
    monkeypatch.setattr("ocr_worker.main._run_ocr", fail)
    with TestClient(app) as client:
        response = client.post("/v1/ocr", content=_archive({"selected.eng.sup": b"sup"}),
                               headers={"Content-Type": "application/zip"})
    assert response.status_code == 422
    assert not temporary.exists()


@pytest.mark.anyio
async def test_seconv_uses_pinned_machine_readable_cli_contract(tmp_path, monkeypatch):
    calls = []

    class Process:
        returncode = 0

        async def communicate(self):
            return json.dumps({"files": [{"success": True}]}).encode(), b""

    async def create(*arguments, **kwargs):
        calls.append(arguments)
        output = Path(next(item.split(":", 1)[1] for item in arguments if item.startswith("--output-folder:")))
        output.mkdir(exist_ok=True)
        (output / "selected.eng.ocr.srt").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nEnglish text\n", encoding="utf-8"
        )
        return Process()

    monkeypatch.setattr("ocr_worker.main.asyncio.create_subprocess_exec", create)
    source = tmp_path / "selected.eng.idx"
    source.write_text("idx")
    content, warning = await _run_ocr(source, tmp_path / "output", "eng")
    assert content.startswith(b"1\n") and warning == ""
    assert calls == [(str(Path("/opt/seconv/seconv")), str(source), "subrip", "--ocr-engine:tesseract",
                      "--ocr-language:eng", "--output-filename:selected.eng.ocr.srt",
                      f"--output-folder:{tmp_path / 'output'}", "--encoding:utf-8-no-bom", "--overwrite",
                      "--json")]


@pytest.mark.anyio
async def test_seconv_exit_one_is_an_error(tmp_path, monkeypatch):
    class Process:
        returncode = 1

        async def communicate(self):
            return b'{"errors":["bad input"]}', b"bad input"

    async def create(*arguments, **kwargs):
        return Process()

    monkeypatch.setattr("ocr_worker.main.asyncio.create_subprocess_exec", create)
    with pytest.raises(RuntimeError, match="kodem 1"):
        await _run_ocr(tmp_path / "selected.eng.sup", tmp_path / "output", "eng")
