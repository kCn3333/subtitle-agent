import json
from argparse import Namespace

import pytest

from benchmarks.generate import generate
from benchmarks.run import evaluate, run, verified_cases
from app.services.alignment import parse_cues


def test_annotation_metrics_are_independent_and_hashes_enforced(tmp_path):
    manifest=generate(tmp_path/'cases');cases=verified_cases(manifest)
    case=cases[0];before=parse_cues(case['polish'],'polish')
    annotations=json.loads(case['annotationsPath'].read_text())
    result=evaluate(before,before,annotations,[])
    assert result['beforeStart']['medianMs']==8000 and result['afterStart']['within1000']==0
    assert result['textPreserved'] and result['segmentCountPreserved']
    case['polish'].write_text('changed')
    with pytest.raises(ValueError,match='hash mismatch'):verified_cases(manifest)


@pytest.mark.anyio
async def test_not_run_benchmark_still_reports_input_and_baseline(tmp_path):
    manifest=generate(tmp_path/'cases');output=tmp_path/'results'
    await run(Namespace(manifest=manifest,output=output,url=None,token_file=None,batch_size=32,repetitions=3,environment=None))
    data=json.loads((output/'results.json').read_text())
    assert len(data['measurements'])==24
    local=[r for r in data['measurements'] if r['method']=='local']
    assert all(r['status']=='NOT_RUN' and r['reason'] for r in local)
    assert (output/'results.csv').is_file() and (output/'report.md').is_file()
