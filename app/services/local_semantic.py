"""Text-only worker client and bounded global monotonic content matching."""
import asyncio
import math
import time
from collections import Counter
from dataclasses import asdict, dataclass

import httpx
import numpy as np

from app.services.alignment import Anchor, Cue, coverage_report, fit_models, public_model, select_model, transform

MODEL = 'sentence-transformers/paraphrase-multilingual-mpnet-base-v2'
REVISION = '4328cf26390c98c5e3c738b4460a05b95f4911f5'
# Experimental search thresholds, not calibrated automatic acceptance thresholds.
THRESHOLDS = {'similarity': .62, 'margin': .035, 'maxGroup': 3, 'candidates': 5,
              'automaticAcceptance': False, 'version': 'local-experiment-v1'}


class LocalUnavailable(RuntimeError): pass
class LocalProtocolError(RuntimeError): pass


class EmbeddingClient:
    def __init__(self, settings, transport=None, url=None, expected_device=None):
        self.settings = settings; self.url = (url or settings.local_worker_url or '').rstrip('/')
        self.expected_device = expected_device or settings.local_worker_expected_device
        self.transport = transport
        self.metadata = None; self.batches = []; self.http_ms = 0.0

    def headers(self):
        token = self.settings.local_worker_token
        return {'Authorization': 'Bearer '+(token.get_secret_value() if token else '')}

    async def ready(self):
        if not self.url: raise LocalUnavailable('LOCAL_WORKER_URL nie jest skonfigurowany')
        try:
            async with httpx.AsyncClient(timeout=min(5,self.settings.local_worker_timeout_seconds), transport=self.transport,
                                          trust_env=False) as client:
                response = await client.get(self.url+'/ready', headers=self.headers())
                response.raise_for_status(); metadata = response.json()
            self._metadata(metadata)
            return metadata
        except (httpx.HTTPError, ValueError) as exc:
            raise LocalUnavailable('Worker niedostępny lub model niegotowy') from exc

    def _metadata(self, metadata):
        if (metadata.get('model') != MODEL or metadata.get('revision') != REVISION
            or metadata.get('dimension') != 768 or metadata.get('device') not in {'cpu','cuda'}
            or metadata.get('dtype') not in {'float32','float16'} or metadata.get('maxTokens') != 128):
            raise LocalProtocolError('Niezgodny kontrakt modelu workera')
        if self.expected_device and metadata["device"] != self.expected_device:
            raise LocalProtocolError("Urządzenie workera nie odpowiada wybranemu wariantowi")
        signature = tuple(metadata.get(k) for k in ('model','revision','device','dtype','dimension'))
        if self.metadata and signature != tuple(self.metadata.get(k) for k in ('model','revision','device','dtype','dimension')):
            raise LocalProtocolError('Worker zmienił model lub urządzenie w trakcie zadania')
        self.metadata = metadata

    async def embed(self, segments):
        if len({s['id'] for s in segments}) != len(segments): raise LocalProtocolError('Duplicate input IDs')
        vectors = {}
        async with httpx.AsyncClient(timeout=self.settings.local_worker_timeout_seconds, transport=self.transport,
                                      trust_env=False) as client:
            for start in range(0,len(segments),self.settings.local_worker_batch_size):
                batch = segments[start:start+self.settings.local_worker_batch_size]
                for attempt in range(self.settings.local_worker_retries+1):
                    began = time.perf_counter()
                    try:
                        async with client.stream('POST', self.url+'/embeddings', headers=self.headers(), json={'segments':batch}) as response:
                            response.raise_for_status()
                            raw = bytearray()
                            async for chunk in response.aiter_bytes():
                                raw.extend(chunk)
                                if len(raw)>8*1024*1024: raise LocalProtocolError('Odpowiedź workera zbyt duża')
                        import json
                        data = json.loads(raw)
                        self.http_ms += (time.perf_counter()-began)*1000
                        break
                    except (ValueError, TypeError) as exc:
                        raise LocalProtocolError("Worker zwrócił nieprawidłowy JSON") from exc
                    except httpx.HTTPStatusError as exc:
                        self.http_ms += (time.perf_counter()-began)*1000
                        if exc.response.status_code not in {429,502,503,504} or attempt == self.settings.local_worker_retries:
                            raise LocalUnavailable(f'Worker HTTP {exc.response.status_code}') from exc
                    except httpx.TransportError as exc:
                        self.http_ms += (time.perf_counter()-began)*1000
                        if attempt == self.settings.local_worker_retries:
                            raise LocalUnavailable('Timeout lub brak połączenia z workerem') from exc
                    await asyncio.sleep(min(2, .25*2**attempt))
                try:
                    self._metadata(data['metadata'])
                    items = data['embeddings']; expected = {s['id'] for s in batch}
                    ids = [item['id'] for item in items]
                    if len(ids) != len(expected) or set(ids) != expected: raise ValueError('Missing, duplicate or foreign ID')
                    for item in items:
                        vector = np.asarray(item['vector'], dtype=np.float32)
                        if vector.shape != (768,) or not np.isfinite(vector).all() or np.linalg.norm(vector) <= 0:
                            raise ValueError('Invalid vector')
                        vectors[item['id']] = vector/np.linalg.norm(vector)
                    elapsed = float(data['metadata']['inferenceMs'])
                    if not math.isfinite(elapsed) or elapsed < 0: raise ValueError('Invalid timing')
                    self.batches.append(data['metadata'])
                except (KeyError, TypeError, ValueError) as exc:
                    raise LocalProtocolError('Nieprawidłowa odpowiedź embeddingów') from exc
        return vectors


@dataclass(frozen=True)
class Group:
    id: str
    start: int
    end: int  # exclusive
    text: str


def groups(cues, prefix):
    result = []
    for start in range(len(cues)):
        for count in range(1,4):
            end = start+count
            if end > len(cues): break
            if count>1 and any(cues[i].start_ms-cues[i-1].end_ms>4000 for i in range(start+1,end)): break
            text = ' '.join(c.normalized_text for c in cues[start:end]).strip()
            if text: result.append(Group(f'{prefix}:{start}:{end}',start,end,text))
    return result


def monotonic_chain(candidates, polish_count):
    """Maximum-weight non-overlapping chain, sparse 2D interval DP.

    Sweep EN start/end; Fenwick prefix maximum on PL endpoints. O(K log N)
    memory/time bounds; skips are implicit and cues cannot be reused.
    """
    import heapq
    candidates = sorted(candidates, key=lambda x: (x['enStart'],x['plStart']))
    tree = [(0.0,-1)]*(polish_count+2); scores = []; parents = []; pending = []
    def query(end):
        best=(0.0,-1); i=end+1
        while i>0:
            if tree[i][0]>best[0]: best=tree[i]
            i -= i & -i
        return best
    def update(end, value):
        i=end+1
        while i<len(tree):
            if value[0]>tree[i][0]: tree[i]=value
            i += i & -i
    for index,item in enumerate(candidates):
        while pending and pending[0][0] <= item['enStart']:
            _, prior = heapq.heappop(pending)
            update(candidates[prior]['plEnd'], (scores[prior],prior))
        score,parent=query(item['plStart'])
        scores.append(score+item['weight']); parents.append(parent)
        heapq.heappush(pending,(item['enEnd'],index))
    if not scores: return []
    current=max(range(len(scores)),key=scores.__getitem__); chain=[]
    while current>=0:
        chain.append(candidates[current]); current=parents[current]
    return list(reversed(chain))


def match_groups(english, polish, en_groups, pl_groups, vectors):
    right = np.asarray([vectors[g.id] for g in pl_groups], dtype=np.float32)
    pl_starts = np.array([g.start for g in pl_groups]); pl_ends = np.array([g.end for g in pl_groups])
    candidates = []; frequencies = Counter(g.text for g in en_groups+pl_groups if g.end-g.start==1)
    # Global retrieval over the entire movie. No timestamp window excludes a cut
    # or a large offset. Only a 64 x (3*N) similarity block is resident.
    for start in range(0,len(en_groups),64):
        block = en_groups[start:start+64]
        similarities = np.asarray([vectors[g.id] for g in block]) @ right.T
        for group,row in zip(block,similarities):
            allowed = (pl_ends-pl_starts == 1) if group.end-group.start>1 else np.ones(len(pl_groups),dtype=bool)
            row = np.where(allowed,row,-2)
            best_indexes = np.argsort(row)[-THRESHOLDS['candidates']:][::-1]
            for index in best_indexes:
                other=pl_groups[index]; score=float(row[index])
                if score<THRESHOLDS['similarity']: continue
                # Overlapping alternative groups describe the same location and
                # are not semantic competitors. Repeated lines elsewhere are.
                # Include the best competitor outside top-k as necessary.
                mask=(pl_ends<=other.start) | (pl_starts>=other.end)
                competitor=float(np.max(row[mask])) if mask.any() else -1
                margin=score-competitor
                if margin<THRESHOLDS['margin']: continue
                short=min(len(group.text.split()),len(other.text.split()))<3
                repeat=max(frequencies[group.text],frequencies[other.text])>1
                context=[]
                for delta in (-1,1):
                    ei,pi=group.start+delta,other.start+delta
                    if 0<=ei<len(english) and 0<=pi<len(polish):
                        ev=vectors.get(f'en:{ei}:{ei+1}'); pv=vectors.get(f'pl:{pi}:{pi+1}')
                        if ev is not None and pv is not None: context.append(float(ev@pv))
                context_score=sum(context)/len(context) if context else 0
                if (short or repeat) and context_score<.65: continue
                grouped=group.end-group.start>1 or other.end-other.start>1
                weight=max(0.01,(score-.5)*min(1,margin/.15))
                if short or repeat: weight*=.3
                if grouped: weight*=.35
                weight *= max(.5, min(1.2, .8+context_score*.4))
                candidates.append({'enStart':group.start,'enEnd':group.end,'plStart':other.start,'plEnd':other.end,
                    'similarity':score,'margin':margin,'contextSimilarity':context_score,'weight':weight,
                    'relation':'ONE_TO_ONE' if not grouped else 'ONE_TO_MANY' if group.end-group.start==1 else 'MANY_TO_ONE',
                    'boundaryUncertaintyMs':max(english[group.end-1].end_ms-english[group.start].start_ms,
                        polish[other.end-1].end_ms-polish[other.start].start_ms) if grouped else 0})
    return monotonic_chain(candidates,len(polish))


async def synchronize(english, polish, duration_ms, client, max_cues=12000, progress=None):
    began=time.perf_counter()
    if not english or not polish or max(len(english),len(polish))>max_cues:
        raise LocalProtocolError('Pusta lub zbyt długa lista napisów')
    en_groups,pl_groups=groups(english,'en'),groups(polish,'pl')
    await client.ready()
    vectors=await client.embed([{'id':g.id,'text':g.text} for g in en_groups+pl_groups])
    if progress: await progress('matching')
    match_started=time.perf_counter()
    relations=await asyncio.to_thread(match_groups,english,polish,en_groups,pl_groups,vectors)
    matching_ms=(time.perf_counter()-match_started)*1000
    if progress: await progress('fitting')
    fit_started=time.perf_counter()
    anchors=[Anchor(r['enStart'],r['plStart'],english[r['enStart']].start_ms,polish[r['plStart']].start_ms,
                    r['weight'],'local','first group boundary; grouped boundaries weak') for r in relations]
    # Never promote uncertain grouped boundaries to strong time evidence.
    reliable=[a for a,r in zip(anchors,relations) if r['relation']=='ONE_TO_ONE' and len(english[a.english_index].normalized_text.split())>=3]
    controls=[a for i,a in enumerate(reliable) if i%4==2]
    training=[a for i,a in enumerate(reliable) if i%4!=2]
    training.extend(a for a,r in zip(anchors,relations) if r['relation']!='ONE_TO_ONE')
    models=fit_models(training,duration_ms=duration_ms); model=select_model(models)
    coverage=coverage_report(reliable,english,duration_ms)
    independent=[abs(a.reference_time-model['predict'](a.source_time)) for a in controls] if model else []
    fitting_ms=(time.perf_counter()-fit_started)*1000
    if progress: await progress('validation')
    validation_started=time.perf_counter()
    transformed,validation=transform(polish,model or {'predict':lambda t:t},duration_ms,0)
    match_ms=(time.perf_counter()-match_started)*1000
    matched_pl={i for r in relations for i in range(r['plStart'],r['plEnd'])}
    uncertain=[{'startMs':c.start_ms,'endMs':c.end_ms,'cueId':c.cue_id,'reason':'NO_CONTENT_MATCH'} for i,c in enumerate(polish) if i not in matched_pl]
    uncertain.extend({'startMs':polish[r['plStart']].start_ms,'endMs':polish[r['plEnd']-1].end_ms,
                      'reason':'GROUP_BOUNDARY_UNCERTAINTY','uncertaintyMs':r['boundaryUncertaintyMs']}
                     for r in relations if r['boundaryUncertaintyMs'])
    if model: uncertain.extend(dict(x,reason='EDIT_GAP') for x in model.get('uncertainRanges',[]))
    warnings=['Eksperymentalne progi; brak kalibracji na niezależnych rzeczywistych napisach.']
    if not model: warnings.append('Brak wiarygodnych kotwic czasu; podgląd zachowuje czasy wejściowe.')
    if independent and max(independent)>1500: warnings.append('Punkty kontrolne nie potwierdzają modelu czasu.')
    if len(controls)<3: warnings.append('Za mało niezależnych punktów kontrolnych.')
    report={'status':'REVIEW_REQUIRED','quality':'EXPERIMENTAL','readyForPublication':False,
        'model':public_model(model) if model else None,'models':[public_model(m) for m in models],
        'anchorCount':len(reliable),'trainingAnchorCount':len(training),'controlAnchorCount':len(controls),
        'controls':{'residualMs':independent,'independentOfFit':True,'groundTruth':False},
        'coverage':coverage,'uncertainFragments':uncertain,'relations':relations,
        'anchors':[asdict(a) for a in reliable],'thresholds':THRESHOLDS,'validation':validation,'warnings':warnings,
        'worker':client.metadata,'batches':client.batches,
        'timings':{'httpMs':client.http_ms,'inferenceMs':sum(b['inferenceMs'] for b in client.batches),
                   'matchingAndValidationMs':match_ms, 'matchingMs':matching_ms, 'fittingMs':fitting_ms,
                   'validationMs':(time.perf_counter()-validation_started)*1000,'fromPreparedSrtMs':(time.perf_counter()-began)*1000},
        'similarityMeaning':'cosine similarity, not a probability', 'mediaDirectoryModified':False}
    return transformed,report
