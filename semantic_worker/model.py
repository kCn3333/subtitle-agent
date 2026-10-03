"""Pinned embedding model; never downloads during serving or inference."""
import os
import platform
import resource
import time
from pathlib import Path

MODEL = 'sentence-transformers/paraphrase-multilingual-mpnet-base-v2'
REVISION = '4328cf26390c98c5e3c738b4460a05b95f4911f5'
DIMENSION = 768


class ConfigurationError(RuntimeError): pass


class Encoder:
    def __init__(self):
        import torch
        from sentence_transformers import SentenceTransformer
        self.torch = torch
        self.device = os.getenv('AI_DEVICE', 'cpu')
        self.dtype = os.getenv('AI_DTYPE', 'float32')
        if self.device not in {'cpu', 'cuda'} or self.dtype not in {'float32', 'float16'}:
            raise ValueError('Unsupported device or dtype')
        if self.device == 'cuda' and not torch.cuda.is_available():
            raise ConfigurationError('CUDA requested but unavailable; CPU fallback is disabled')
        if self.device == 'cpu' and self.dtype != 'float32':
            raise ValueError('CPU requires float32')
        threads = int(os.getenv('AI_THREADS', '2'))
        if not 1 <= threads <= 32: raise ValueError('Invalid thread count')
        torch.set_num_threads(threads)
        torch.set_num_interop_threads(1)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        started = time.perf_counter()
        self.model = SentenceTransformer(MODEL, revision=REVISION, device=self.device,
            cache_folder=os.getenv('HF_HOME', '/cache'), local_files_only=True, trust_remote_code=False)
        if self.dtype == 'float16': self.model.half()
        else: self.model.float()
        self.load_ms = (time.perf_counter()-started)*1000
        self.max_tokens = int(self.model.max_seq_length)
        if self.max_tokens != 128 or self.model.get_sentence_embedding_dimension() != DIMENSION:
            raise RuntimeError('Unexpected model contract')
        self.batch_size = int(os.getenv('AI_BATCH_SIZE', '16'))
        if not 1 <= self.batch_size <= 128: raise ValueError('Invalid batch size')

    def metadata(self):
        import transformers, sentence_transformers, numpy
        return {'model': MODEL, 'revision': REVISION, 'device': self.device, 'dtype': self.dtype,
            'dimension': DIMENSION, 'maxTokens': self.max_tokens, 'loadMs': self.load_ms,
            'threads': self.torch.get_num_threads(), 'interopThreads': self.torch.get_num_interop_threads(),
            'batchSize': self.batch_size, 'tf32': False, 'torch': self.torch.__version__,
            'cuda': self.torch.version.cuda, 'transformers': transformers.__version__,
            'sentenceTransformers': sentence_transformers.__version__, 'numpy': numpy.__version__,
            'system': platform.platform(), 'cpu': next((line.split(':',1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')), platform.processor()),
            'gpu': self.torch.cuda.get_device_name() if self.device == 'cuda' else None}

    def _chunks(self, text):
        # Split consciously at tokenizer boundaries. Re-tokenization is checked,
        # so SentenceTransformer.encode never silently truncates a long cue/group.
        tokenizer = self.model.tokenizer
        ids = tokenizer.encode(text, add_special_tokens=False, truncation=False)
        budget = self.max_tokens - tokenizer.num_special_tokens_to_add(pair=False)
        def split(part):
            decoded = tokenizer.decode(part, skip_special_tokens=True)
            if len(tokenizer.encode(decoded, truncation=False)) <= self.max_tokens: return [decoded]
            if len(part) <= 1: raise ValueError('Token cannot fit model limit')
            middle = len(part)//2
            return split(part[:middle])+split(part[middle:])
        return [chunk for i in range(0,len(ids),budget) for chunk in split(ids[i:i+budget])] or ['']

    def encode(self, segments):
        import numpy as np
        chunks, spans = [], []
        for item in segments:
            start = len(chunks); chunks.extend(self._chunks(item['text'])); spans.append((start,len(chunks)))
        torch = self.torch
        if self.device == 'cuda':
            torch.cuda.synchronize(); torch.cuda.reset_peak_memory_stats()
        cgroup_before = cgroup_stats()
        device_before = torch.cuda.mem_get_info() if self.device == 'cuda' else None
        started = time.perf_counter(); cpu_started = time.process_time()
        vectors = self.model.encode(chunks, batch_size=self.batch_size, normalize_embeddings=True,
                                    convert_to_numpy=True, show_progress_bar=False)
        if self.device == 'cuda': torch.cuda.synchronize()
        inference_ms = (time.perf_counter()-started)*1000
        results = []
        for item, (start,end) in zip(segments,spans):
            vector = np.mean(vectors[start:end].astype(np.float32), axis=0)
            norm = np.linalg.norm(vector)
            if not np.isfinite(vector).all() or norm == 0: raise ValueError('Invalid embedding')
            results.append({'id': item['id'], 'vector': (vector/norm).tolist(), 'chunks': end-start})
        return {'embeddings': results, 'metadata': {**self.metadata(), 'inferenceMs': inference_ms,
            'cpuSeconds': time.process_time()-cpu_started, 'peakRssBytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024,
            'gpuPeakAllocatedBytes': torch.cuda.max_memory_allocated() if self.device == 'cuda' else None,
            'gpuPeakReservedBytes': torch.cuda.max_memory_reserved() if self.device == 'cuda' else None,
            'cgroupBefore': cgroup_before, 'cgroupAfter': cgroup_stats(),
            'gpuDeviceFreeTotalBefore': device_before,
            'gpuDeviceFreeTotalAfter': torch.cuda.mem_get_info() if self.device == 'cuda' else None,
            'longTextPolicy': 'token chunks, mean normalized chunk vectors', 'chunkCount': len(chunks)}}


def cgroup_stats():
    result = {'method': 'cgroup v2 memory.peak (container lifetime) and cpu.stat cumulative; null if unavailable'}
    for name in ['memory.peak', 'memory.current', 'memory.max', 'cpu.max', 'cpu.stat']:
        try: result[name] = (Path('/sys/fs/cgroup')/name).read_text().strip()
        except OSError: result[name] = None
    return result
