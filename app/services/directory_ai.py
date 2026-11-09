"""One AI queue operation per directory; each item retains isolated inputs and output."""
import asyncio
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.services.alignment import sha256
from app.services.artifact_retention import validated_job_directory
from app.services.operation_queue import JobBusy
from app.services.workpack import build_zip, safe_filename, write_json


class ItemRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_id: str
    reference_source_id: str
    polish_file: str | None = None


class DirectoryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    items: list[ItemRequest] = Field(min_length=1, max_length=10000)


def settings_digest(settings):
    data = settings.model_dump(mode='json', exclude={'api_key', 'timeout_seconds'})
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def prepared_directory(request, job_id, pipeline=None):
    from app.api.ai_sync import error
    manager = request.app.state.jobs
    job = manager.get(job_id)
    if not job or job['job_type'] != 'DIRECTORY_WORKPACK':
        raise error('Nie znaleziono zadania katalogowego', 404)
    report = job.get('report') or {}
    if (job['status'] not in {'WORKPACK_READY', 'WORKPACK_INCOMPLETE'}
            or report.get('mode') not in {'PREPARE_SYNC', 'PREPARE_TRANSLATION'}
            or (pipeline and report['mode'] != pipeline) or not report.get('workpack')):
        raise error('Najpierw zakończ przygotowanie workpacka katalogu')
    root = manager.settings.data_root / 'work' / 'jobs'
    directory = validated_job_directory(root, root / job_id)
    if directory is None or not (directory / 'manifest.json').is_file():
        raise error('Materiały katalogu wygasły; przygotuj je ponownie', 410)
    return directory, report


def describe_inputs(request, job_id):
    _, report = prepared_directory(request, job_id)
    translation = report['mode'] == 'PREPARE_TRANSLATION'
    rows = []
    for item in report['batch']['items']:
        child = request.app.state.jobs.get(item['jobId']) if item.get('jobId') else None
        data = (child or {}).get('report') or {}
        selected = data.get('selectedReference') or data.get('selectedEnglish') or {}
        candidates = [{'archiveName': entry['archiveName'], 'originalName': entry.get('originalName') or entry['archiveName']}
                      for entry in data.get('polishCandidates', []) if entry['archiveName'].lower().endswith('.srt') and entry.get('eligibleByDefault', True)]
        reference = (f"external:{selected.get('name')}" if selected.get('sourceType') == 'external'
                     else f"embedded:{selected.get('streamIndex')}")
        available = (item['state'] == 'ready' and child and child['status'] == 'WORKPACK_READY'
                     and data.get('pipeline') == report['mode'] and bool(selected)
                     and not data.get('requiresOcr') and not data.get('externalReferenceConfirmationRequired')
                     and (translation or bool(candidates)))
        reason = ('Pominięty przez użytkownika' if item['state'] == 'skipped' else
                  'Materiały nie są gotowe do AI — sprawdź raport pliku') if not available else None
        rows.append({'job_id': item.get('jobId'), 'name': item['name'], 'available': bool(available),
                     'reference_source_id': reference, 'polish_candidates': candidates, 'reason': reason})
    return {'mode': report['mode'], 'items': rows}


def validate_directory(request, job_id, payload, pipeline):
    from app.api.ai_sync import error, validate_inputs, SyncRequest
    directory, _ = prepared_directory(request, job_id, pipeline)
    described = describe_inputs(request, job_id)
    expected = {row['job_id']: row for row in described['items'] if row['available']}
    ids = [item.job_id for item in payload.items]
    if not expected or len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise error('Wybierz dane dla wszystkich gotowych plików katalogu, bez duplikatów ani obcych zadań')
    fingerprints = {}
    for item in payload.items:
        if item.reference_source_id != expected[item.job_id]['reference_source_id']:
            raise error('Referencja pliku nie zgadza się z przygotowanym katalogiem')
        single = SyncRequest(reference_source_id=item.reference_source_id, polish_file=item.polish_file)
        child_dir, *_ = validate_inputs(item.job_id, single, request, pipeline, batch_parent=job_id)
        fingerprints[item.job_id] = sha256(child_dir / 'manifest.json')
    return directory, described, fingerprints


async def process_directory(request, job_id, payload, pipeline):
    queue = request.app.state.ai_queue
    intent = {'pipeline': pipeline, **payload.model_dump()}
    duplicate = queue.existing(job_id, intent)
    if duplicate:
        return duplicate
    if job_id in request.app.state.jobs.reserved_jobs:
        raise JobBusy('Katalog jest już używany przez operację w kolejce lub w toku.')
    directory, described, fingerprints = validate_directory(request, job_id, payload, pipeline)
    digest = sha256(directory / 'manifest.json')
    settings = request.app.state.ai_settings.get().model_copy(deep=True)
    submitted = payload.model_copy(deep=True)
    # Preserve episode order even if the client sends selections in a different order.
    order = {row['job_id']: index for index, row in enumerate(described['items'])}
    submitted.items.sort(key=lambda entry: order[entry.job_id])
    names = {row['job_id']: row['name'] for row in described['items']}
    state = {'job_id': job_id, 'request': intent, 'batch': True,
             'mode': 'translation' if pipeline == 'PREPARE_TRANSLATION' else 'sync',
             'title': request.app.state.jobs.get(job_id)['display_title'],
             'items': [{'job_id': item.job_id, 'name': names[item.job_id], 'status': 'pending',
                        'polish_file': item.polish_file, 'reference_source_id': item.reference_source_id} for item in submitted.items]}
    return queue.submit(state, lambda: execute_directory(request, job_id, submitted, pipeline, settings, digest, fingerprints))


def sum_usage(metrics):
    result = {}
    if not metrics:
        return {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        if all(type(metric.get(key)) is int for metric in metrics):
            result[key] = sum(metric[key] for metric in metrics)
    currencies = {metric.get('cost_currency', '(jednostki API)') for metric in metrics}
    if len(currencies) == 1 and all(type(metric.get('cost')) in (int, float) for metric in metrics):
        result.update(cost=float(sum(Decimal(str(metric['cost'])) for metric in metrics)),
                      cost_currency=currencies.pop())
    return result


async def execute_directory(request, job_id, payload, pipeline, settings, digest, fingerprints):
    from app.api.ai_sync import error, execute, saved_result, SyncRequest
    manager, queue = request.app.state.jobs, request.app.state.ai_queue
    directory, _, current = validate_directory(request, job_id, payload, pipeline)
    if sha256(directory / 'manifest.json') != digest or current != fingerprints:
        raise error('Dane katalogu zmieniły się podczas oczekiwania; przygotuj je ponownie.', 409)
    stem = 'ai-translation' if pipeline == 'PREPARE_TRANSLATION' else 'ai-sync'
    (directory / f'{stem}-batch.json').unlink(missing_ok=True)
    state = request.app.state.ai_operations[job_id]
    metrics, elapsed, outputs = [], 0, []
    for index, item in enumerate(payload.items):
        if sha256(directory / 'manifest.json') != digest:
            raise error('Workpack katalogu zmienił się podczas pracy AI', 409)
        row = state['items'][index]
        row['status'] = 'running'
        state.update(current_index=index, current_item=row['name'],
                     phase=f"{index + 1}/{len(payload.items)} · {row['name']} · Przygotowanie danych")
        queue.save(state)
        child_dir = directory.parent / item.job_id
        try:
            cached = saved_result(child_dir, stem)
            expected_polish = item.polish_file if pipeline == 'PREPARE_SYNC' else None
            reuse = bool(cached and cached.get('settings_sha256') == settings_digest(settings)
                         and (pipeline == 'PREPARE_TRANSLATION' or cached['inputs'][1]['name'] == expected_polish))
            if reuse:
                summary = cached
            else:
                single = SyncRequest(reference_source_id=item.reference_source_id, polish_file=item.polish_file)
                summary = await execute(item.job_id, single, request, pipeline, settings, fingerprints[item.job_id], batch_parent=job_id)
                metrics.append(summary.get('usage') or {})
                elapsed += summary.get('elapsed_seconds') or 0
            row.update(status='completed', reused=reuse, cue_count=summary['cue_count'])
            outputs.append({'job_id': item.job_id, 'name': row['name'], 'summary': summary})
        except asyncio.CancelledError:
            raise
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {'message': str(exc.detail)}
            row.update(status='failed', error=detail.get('message', 'Przetwarzanie AI nie powiodło się'))
            metrics.append(detail.get('usage') or {})
            elapsed += detail.get('elapsed_seconds') or 0
        except Exception:
            row.update(status='failed', error='Przetwarzanie AI nie powiodło się; sprawdź logi serwera')
            metrics.append({})
        queue.save(state)
    state['phase'] = 'Pakowanie wyników AI katalogu'
    queue.save(state)
    result = await asyncio.to_thread(build_results, request, job_id, stem, outputs, state['items'], digest)
    result.update(model=settings.model, elapsed_seconds=elapsed, usage=sum_usage(metrics),
                  reused_count=sum(row.get('reused', False) for row in state['items']))
    result['total_cost'] = result['usage'].get('cost')
    temporary = directory / f'.{stem}-batch.json.tmp'
    write_json(temporary, result)
    temporary.replace(directory / f'{stem}-batch.json')
    return result


def build_results(request, job_id, stem, outputs, rows, digest):
    from app.api.ai_sync import saved_result
    manager = request.app.state.jobs
    parent = manager.get(job_id)
    directory = manager.settings.data_root / 'work' / 'jobs' / job_id
    target = directory / 'ai-results' / stem
    target.mkdir(parents=True, exist_ok=True)
    files, items, bindings = {'manifest.json'}, [], []
    by_id = {output['job_id']: output for output in outputs}
    for index, row in enumerate(rows, 1):
        item = {'source': row['name'], 'status': row['status'], 'error': row.get('error')}
        items.append(item)
        if row['status'] != 'completed':
            continue
        output = by_id[row['job_id']]
        child_dir = directory.parent / row['job_id']
        saved = saved_result(child_dir, stem)
        if not saved or saved['sha256'] != output['summary']['sha256']:
            raise ValueError('Wynik AI zmienił się podczas pakowania')
        basename = safe_filename(Path(saved['filename']).name.removesuffix('.pl.srt'))[:160]
        name = f"items/{index:03d}-{basename}.pl.srt"
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((child_dir / f'{stem}.pl.srt').read_bytes())
        item['output'] = {'name': name, 'sha256': sha256(destination)}
        bindings.append({'job_id': row['job_id'], 'manifest_sha256': saved['manifest_sha256'], 'sha256': saved['sha256'],
                         'polish_file': saved['inputs'][1]['name'] if stem == 'ai-sync' else None})
        files.add(name)
    failed = sum(row['status'] == 'failed' for row in rows)
    write_json(target / 'manifest.json', {'schema_version': 'subtitle-ai-results-v1', 'kind': 'DIRECTORY_AI',
               'sourceManifestSha256': digest, 'complete': failed == 0, 'items': items,
               'files': sorted(files | {'checksums.sha256'})})
    archive, _, output_digest, _ = build_zip(target, Path(parent['media_path']).name,
                manager.settings.directory_max_archive_bytes, manager.settings.directory_max_files, files, require_complete=True)
    kind = 'batch-translation' if stem == 'ai-translation' else 'batch-synchronization'
    with manager._lock:
        filename = manager.archive.record(parent, archive, kind, output_digest)
    if not filename:
        raise ValueError('Nie udało się zarchiwizować wyników AI katalogu')
    return {'batch': True, 'mode': 'translation' if stem == 'ai-translation' else 'sync',
            'filename': filename, 'relative_path': archive.relative_to(directory).as_posix(),
            'sha256': output_digest, 'manifest_sha256': digest, 'outputs': bindings,
            'file_count': len(outputs), 'failed_count': failed,
            'cue_count': sum(output['summary']['cue_count'] for output in outputs)}


def result_path(request, job_id, result):
    from app.api.ai_sync import error
    directory = request.app.state.settings.data_root / 'work' / 'jobs' / job_id
    relative = result.get('relative_path') or ''
    path = directory / relative
    if (not relative or Path(relative).is_absolute() or '..' in Path(relative).parts
            or not path.resolve().is_relative_to(directory.resolve()) or path.suffix != '.zip'
            or any(part.is_symlink() for part in (path, *path.parents) if part != directory.parent)
            or not path.is_file() or sha256(path) != result.get('sha256')):
        raise error('Wynik AI katalogu jest niedostępny lub zmienił się', 410)
    return path


def directory_result(request, job_id, pipeline):
    from app.api.ai_sync import error, saved_result
    directory, _ = prepared_directory(request, job_id, pipeline)
    stem = 'ai-translation' if pipeline == 'PREPARE_TRANSLATION' else 'ai-sync'
    pointer = directory / f'{stem}-batch.json'
    if not pointer.is_file() or pointer.is_symlink():
        return None
    try:
        result = json.loads(pointer.read_text())
    except (OSError, ValueError):
        return None
    if result.get('manifest_sha256') != sha256(directory / 'manifest.json'):
        return None
    for output in result.get('outputs', []):
        child = request.app.state.jobs.get(output['job_id'])
        if not child or child.get('batch_parent_id') != job_id:
            raise error('Wynik nie należy do tego katalogu')
        saved = saved_result(directory.parent / child['id'], stem)
        if not saved or saved['sha256'] != output['sha256'] or saved['manifest_sha256'] != output['manifest_sha256']:
            return None
    result_path(request, job_id, result)
    return result
