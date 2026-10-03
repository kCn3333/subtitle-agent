"""Explicit one-time initialization; serving is always offline."""
import os
import time
from huggingface_hub import snapshot_download
from semantic_worker.model import MODEL, REVISION

if __name__ == '__main__':
    started = time.perf_counter()
    snapshot_download(MODEL, revision=REVISION, cache_dir=os.getenv('HF_HOME', '/cache'),
        allow_patterns=['*.json', '*.safetensors', '*.model', '1_Pooling/*', '*.txt'],
        ignore_patterns=['onnx/*', 'openvino/*', '*.h5', '*.ot'])
    print({'phase': 'weights_download_only', 'model': MODEL, 'revision': REVISION,
           'elapsedSeconds': time.perf_counter()-started})
