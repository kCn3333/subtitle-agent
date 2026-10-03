from pathlib import Path

import yaml


def test_worker_compose_isolation_and_opt_in():
    cpu=yaml.safe_load(Path('compose.semantic-cpu.yml').read_text())
    gpu=yaml.safe_load(Path('compose.semantic-gpu.yml').read_text())
    for config in [cpu,gpu]:
        worker=config['services']['subtitle-semantic-worker']
        assert worker['volumes']==['semantic_model_cache:/cache']
        assert worker['read_only'] and worker['cap_drop']==['ALL']
        assert worker['environment']['HF_HUB_OFFLINE']=='1'
        assert 'mem_limit' not in worker
    worker=cpu['services']['subtitle-semantic-worker']
    assert worker['profiles']==['local-ai'] and 'ports' not in worker
    assert 'depends_on' not in cpu['services']['subtitle-agent']
    assert gpu['services']['subtitle-semantic-worker']['environment']['AI_DEVICE']=='cuda'
    assert ':?' in gpu['services']['subtitle-semantic-worker']['ports'][0]
