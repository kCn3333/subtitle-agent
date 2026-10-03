"""Local runner. Ground truth is read only after alignment has finished."""
import argparse
import asyncio
import csv
import json
import platform
import statistics
import subprocess
import time
from pathlib import Path

from app.core.config import Settings
from app.services.alignment import (StructuralAnchorProvider, fit_models, parse_cues, select_model, sha256,
                                     transform, write_preview, percentile)
from app.services.local_semantic import EmbeddingClient, LocalUnavailable, LocalProtocolError, THRESHOLDS, synchronize


def errors(values):
    if not values:return {'n':0,'medianMs':None,'p95Ms':None,'maxMs':None,'within250':None,'within500':None,'within1000':None}
    return {'n':len(values),'medianMs':statistics.median(values),'p95Ms':percentile(values,.95),'maxMs':max(values),
            **{f'within{x}':sum(v<=x for v in values)/len(values) for x in [250,500,1000]}}


def evaluate(before,after,annotations,relations):
    left={c.cue_id:c for c in before};right={c.cue_id:c for c in after}
    values={'beforeStart':[],'afterStart':[],'beforeEnd':[],'afterEnd':[]};expected=set()
    for point in annotations['points']:
        if point.get('uncertain'):continue
        ids=point['polishIds']
        if not ids or any(i not in left or i not in right for i in ids):raise ValueError('Unknown annotation cue ID')
        for side,lookup in [('before',left),('after',right)]:
            if 'startMs' in point:values[side+'Start'].append(abs(min(lookup[i].start_ms for i in ids)-point['startMs']))
            if 'endMs' in point:values[side+'End'].append(abs(max(lookup[i].end_ms for i in ids)-point['endMs']))
        if point.get('englishIds') and not point.get('relationExcluded'):
            expected.add((tuple(point['englishIds']),tuple(ids)))
    predicted={(tuple(f'english:{i+1}' for i in range(r['enStart'],r['enEnd'])),
                tuple(f'polish:{i+1}' for i in range(r['plStart'],r['plEnd']))) for r in relations}
    intersection=expected & predicted
    return {**{name:errors(value) for name,value in values.items()},
        'relations':{'precision':len(intersection)/len(predicted) if predicted else None,
            'recall':len(intersection)/len(expected) if expected else None,'truePositive':len(intersection),
            'predicted':len(predicted),'annotated':len(expected),'policy':'exact grouped relation; uncertain excluded'},
        'scope':annotations['scope'],'uncertainAnnotations':sum(bool(p.get('uncertain')) for p in annotations['points']),
        'textPreserved':[c.raw_text for c in before]==[c.raw_text for c in after],
        'segmentCountPreserved':len(before)==len(after),
        'invalidIntervals':sum(c.start_ms<0 or c.end_ms<=c.start_ms for c in after),
        '_errors':values}


def verified_cases(manifest_path):
    data=json.loads(manifest_path.read_text());base=manifest_path.parent;result=[]
    for case in data['cases']:
        if not case.get('identity',{}).get('title') or not case['identity'].get('edition'):
            raise ValueError('Case identity and edition required')
        case=dict(case)
        for key in ['english','polish']:
            path=(base/case[key]).resolve()
            if sha256(path)!=case[key+'Sha256']:raise ValueError('Subtitle hash mismatch: '+case['id'])
            case[key]=path
        case['annotationsPath']=(base/case['annotations']).resolve()
        result.append(case)
    return result


def aggregate(rows):
    groups={}
    for row in rows:
        if row['status']!='RUN':continue
        groups.setdefault((row['method'],row['subset']),[]).append(row)
    output=[]
    for (method,subset),items in groups.items():
        output.append({'method':method,'subset':subset,'cases':len(items),
            'metrics':{key:errors([value for item in items for value in item['quality']['_errors'][key]])
                       for key in ['beforeStart','afterStart','beforeEnd','afterEnd']},
            'reviewFraction':sum(i['reviewRequired'] for i in items)/len(items),
            'falseAccepts':sum(i['falseAccept'] for i in items)})
    return output


async def run(args):
    cases=verified_cases(args.manifest);args.output.mkdir(parents=True,exist_ok=True)
    settings=Settings(local_worker_url=args.url,local_worker_token_file=args.token_file,
                      local_worker_batch_size=args.batch_size,local_worker_retries=1)
    rows=[]
    for case in cases:
        # Only SRT and duration enter the algorithm. No annotations/labels/time truth.
        en,pl=parse_cues(case['english'],'english',True),parse_cues(case['polish'],'polish',True)
        structural=StructuralAnchorProvider().provide(en,pl,case['durationMs'],{})
        model=select_model(fit_models(structural,duration_ms=case['durationMs']))
        baseline,validation=transform(pl,model or {'predict':lambda t:t},case['durationMs'],0)
        methods=[('input',pl,{},[],None),('structural_baseline',baseline,{'validation':validation},
            [{'enStart':a.english_index,'enEnd':a.english_index+1,'plStart':a.polish_index,'plEnd':a.polish_index+1} for a in structural],None)]
        local_runs=[];reason=None
        for repeat in range(args.repetitions+1):
            try:
                repeat_started=time.perf_counter()
                parse_started=time.perf_counter()
                run_en=parse_cues(case['english'],'english',True)
                run_pl=parse_cues(case['polish'],'polish',True)
                parse_ms=(time.perf_counter()-parse_started)*1000
                client=EmbeddingClient(settings)
                after,report=await synchronize(run_en,run_pl,case['durationMs'],client,settings.local_worker_max_cues)
                write_started=time.perf_counter()
                warm_file=args.output/'warm-preview.srt'
                write_preview(after,warm_file)
                report['timings']['writeSrtMs']=(time.perf_counter()-write_started)*1000
                report['timings']['parseMs']=parse_ms
                report['timings']['fromPreparedSrtMs']=(time.perf_counter()-repeat_started)*1000
                warm_file.unlink()
                if repeat:local_runs.append((after,report))
            except (LocalUnavailable,LocalProtocolError) as exc:
                reason=str(exc);break
        if local_runs and not reason:
            after,report=local_runs[-1]
            durations=[r['timings']['fromPreparedSrtMs'] for _,r in local_runs]
            performance={'warmups':1,'repetitions':len(local_runs),'cache':'none; fresh embedding requests',
                'medianMs':statistics.median(durations),'minMs':min(durations),'maxMs':max(durations),
                'runs':[r['timings'] for _,r in local_runs], 'worker':report['worker'],
                'resources':[r['batches'] for _,r in local_runs],
                'prepareOcrMs':None,'queueMs':0,'phase':'prepared SRT to aligned SRT',
                'missingMeasurements':['OCR not part of SRT runner','external device load/driver: supply environment file']}
            methods.append(('local',after,report,report['relations'],performance))
        # Independent truth is loaded here, after both algorithms.
        annotations=json.loads(case['annotationsPath'].read_text())
        if case['kind']=='real' and annotations['scope']=='sampled' and sum(not p.get('uncertain') for p in annotations['points'])<20:
            raise ValueError('Real sampled case requires at least 20 certain points')
        for method,after,report,relations,performance in methods:
            score=evaluate(pl,after,annotations,relations)
            accepted=report.get('status')=='COMPLETED'
            row={'case':case['id'],'category':case['category'],'split':case['split'],'kind':case['kind'],
                 'method':method,'status':'RUN','subset':'all','quality':score,'performance':performance,
                 'reviewRequired':not accepted,'falseAccept':accepted and case.get('expectedReject',False),
                 'coverage':report.get('coverage'),'reviewFragmentFraction':len(report.get('uncertainFragments',[]))/max(1,len(pl)),
                 'hashes':{'english':case['englishSha256'],'polish':case['polishSha256']}}
            rows.append(row)
            if accepted:rows.append(dict(row,subset='accepted'))
            case_dir=args.output/case['id']/method;case_dir.mkdir(parents=True,exist_ok=True)
            write_preview(after,case_dir/'result.srt')
            (case_dir/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
        if reason:rows.append({'case':case['id'],'category':case['category'],'split':case['split'],'kind':case['kind'],
            'method':'local','status':'NOT_RUN','subset':'all','reason':reason})
    try:commit=getattr(args,'commit',None) or subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    except (OSError,subprocess.CalledProcessError):commit='unavailable'
    results={'schema':'subtitle-benchmark-results-v1','commit':commit,'thresholds':THRESHOLDS,
        'runnerEnvironment':{'system':platform.platform(),'processor':platform.processor(),'python':platform.python_version()},
        'declaredEnvironment':json.loads(args.environment.read_text()) if args.environment else None,
        'measurements':rows,'aggregate':aggregate(rows),
        'acceptedSubsetNotice':'No automatic acceptance until thresholds are calibrated and frozen; empty accepted subset is explicit.',
        'targetHardware':'NOT_RUN unless declaredEnvironment verifies target host; never extrapolate timing',
        'downloadTime':'excluded; use explicit worker initialization log',
        'modelLoadTime':'worker metadata.loadMs; process start time must be measured separately'}
    (args.output/'results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False,allow_nan=False))
    metric_fields = [f'{side}_{metric}' for side in ['beforeStart','afterStart','beforeEnd','afterEnd']
                     for metric in ['n','medianMs','p95Ms','maxMs','within250','within500','within1000']]
    fields=['case','category','split','kind','method','status','subset','reviewRequired','falseAccept',
            'precision','recall','dialogueCoverage','reviewFragmentFraction','textPreserved','segmentCountPreserved',
            'invalidIntervals','elapsedMedianMs','reason',*metric_fields]
    with (args.output/'results.csv').open('w',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=fields,lineterminator="\n");writer.writeheader()
        for row in rows:
            quality=row.get('quality',{});relations=quality.get('relations',{})
            values={k:row.get(k) for k in fields}
            values.update({f'{side}_{metric}':quality.get(side,{}).get(metric)
                for side in ['beforeStart','afterStart','beforeEnd','afterEnd']
                for metric in ['n','medianMs','p95Ms','maxMs','within250','within500','within1000']})
            values.update({'precision':relations.get('precision'),'recall':relations.get('recall'),
                'dialogueCoverage':(row.get('coverage') or {}).get('dialogueCoverage'),
                'elapsedMedianMs':(row.get('performance') or {}).get('medianMs'),
                **{key:quality.get(key) for key in ['textPreserved','segmentCountPreserved','invalidIntervals']}})
            writer.writerow(values)
    lines=['# Subtitle benchmark','',f'Commit: `{commit}`. Thresholds: `{THRESHOLDS["version"]}`.',
        '', 'Synthetic controls are not real subtitle quality validation. CPU/GPU comparisons require identical hashes and FP32.',
        'Automatic acceptance is disabled; accepted subset has zero cases. NOT_RUN values are not measurements.',
        '', '| Case | Split | Method | Status | Start median / p95 / max (ms) | Start n | <=250 / 500 / 1000 | End median (ms) | Precision / recall | Warm median [min,max] ms |',
        '|---|---|---|---|---|---|---|---|---|---|']
    for row in rows:
        q=row.get('quality',{});s=q.get('afterStart',{});end=q.get('afterEnd',{});r=q.get('relations',{});p=row.get('performance') or {}
        lines.append(f"| {row['case']} | {row['split']} | {row['method']} | {row['status']} | {s.get('medianMs')} / {s.get('p95Ms')} / {s.get('maxMs')} | {s.get('n')} | {s.get('within250')} / {s.get('within500')} / {s.get('within1000')} | {end.get('medianMs')} | {r.get('precision')} / {r.get('recall')} | {p.get('medianMs')} [{p.get('minMs')},{p.get('maxMs')}] |")

    lines += ['', '## Before and after timing error', '',
        '| Case / method | Endpoint | Before median / p95 / max | After median / p95 / max | n | Before <=250 / 500 / 1000 | After <=250 / 500 / 1000 |',
        '|---|---|---|---|---|---|---|']
    for row in rows:
        if row['status']!='RUN':continue
        for endpoint in ['Start','End']:
            before=row['quality']['before'+endpoint];after=row['quality']['after'+endpoint]
            triples=lambda d: ' / '.join(str(d[k]) for k in ['medianMs','p95Ms','maxMs'])
            rates=lambda d: ' / '.join(f"{100*d[k]:.1f}%" if d[k] is not None else 'N/A' for k in ['within250','within500','within1000'])
            lines.append(f"| {row['case']} / {row['method']} | {endpoint} | {triples(before)} | {triples(after)} | {after['n']} | {rates(before)} | {rates(after)} |")
    lines += ['', *[f"NOT_RUN {row['case']}: {row['reason']}" for row in rows if row.get('reason')], '', 'All metrics including before/after start/end, counts, invariants, review, resources and per-run times are in results.json.',
              'Categories are reported per case; aggregate distributions include all evaluated points and never omit review cases.',
              'No real user subtitles were supplied. Target i5-8400 and RTX 4060 results remain NOT_RUN without access.']
    (args.output/'report.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('manifest',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--url');parser.add_argument('--token-file',type=Path);parser.add_argument('--environment',type=Path);parser.add_argument('--commit')
    parser.add_argument('--batch-size',type=int,default=32);parser.add_argument('--repetitions',type=int,default=3)
    args=parser.parse_args()
    if args.repetitions<1:parser.error('At least one repetition required')
    asyncio.run(run(args))
