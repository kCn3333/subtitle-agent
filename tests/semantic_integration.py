"""Explicit image smoke: no model download, no GPU required, no host port."""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

os.environ['AI_TOKEN'] = 'isolated-test-token'
process = subprocess.Popen(['uvicorn','semantic_worker.main:app','--host','127.0.0.1','--port','8091','--no-access-log'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(100):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8091/health',timeout=1) as response:
                assert json.load(response)['status']=='ok';break
        except (OSError,urllib.error.URLError):time.sleep(.1)
    else: raise AssertionError('Health unavailable')
    request=urllib.request.Request('http://127.0.0.1:8091/ready',headers={'Authorization':'Bearer isolated-test-token'})
    for _ in range(300):
        try:
            with urllib.request.urlopen(request,timeout=10) as response: result=json.load(response)
            assert result['device']==os.environ.get('AI_DEVICE','cpu')
            assert result['revision']=='4328cf26390c98c5e3c738b4460a05b95f4911f5'
            print(json.dumps({'health':'PASS','ready':'PASS','device':result['device']}));break
        except urllib.error.HTTPError as exc:
            assert exc.code==503
            detail=json.load(exc)['detail']
            if detail.startswith('Loading'):
                time.sleep(.1);continue
            if os.environ.get('AI_DEVICE')=='cuda':assert 'CUDA requested but unavailable' in detail
            print(json.dumps({'health':'PASS','ready':'NOT_READY','reason':detail}));break
    else:raise AssertionError('Model initialization did not resolve within 30 seconds')

finally:
    process.terminate();process.wait(timeout=10)
