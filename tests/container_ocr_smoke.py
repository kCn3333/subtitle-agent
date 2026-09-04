import base64
import json
import sys
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path


root = Path(sys.argv[1])
worker_url = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8090/v1/ocr"
buffer = BytesIO()
with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    archive.write(root / "selected.eng.idx", "selected.eng.idx")
    archive.write(root / "selected.eng.sub", "selected.eng.sub")
request = urllib.request.Request(
    worker_url, data=buffer.getvalue(), method="POST",
    headers={"Content-Type": "application/zip", "X-OCR-Language": "eng"},
)
with urllib.request.urlopen(request, timeout=120) as response:
    result = json.load(response)
content = base64.b64decode(result["srtBase64"], validate=True)
assert result["cueCount"] >= 1
assert result["emptyCueCount"] < result["cueCount"]
assert result["firstMs"] == 0
assert result["lastStartMs"] >= result["firstMs"]
assert content and not content.startswith(b"\xef\xbb\xbf")
print(json.dumps({key: result[key] for key in ("cueCount", "emptyCueCount", "firstMs", "lastStartMs", "lastMs")}))
