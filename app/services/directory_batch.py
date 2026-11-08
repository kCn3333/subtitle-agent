"""Durable directory workpacks, using the existing single-file pipeline sequentially."""
import asyncio
import json
import os
import re
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from app.models.job import JobStatus, WorkpackTaskType
from app.services.media_analysis import (SUPPORTED_MEDIA, UserInputError, parse_media_identity,
                                         reference_source_id, validate_media_path)
from app.services.operation_queue import JobBusy
from app.services.workpack import (SCHEMA_VERSION, build_zip, safe_filename,
                                  validate_zip, write_json)

EXTRAS = {"extras", "trailers", "samples", "sample", "behind the scenes", "deleted scenes",
          "interviews", "scenes", "shorts", "featurettes", "clips", "other", "theme-music", "backdrops"}
EXTRA_SUFFIX = re.compile(r"(?i)(?:[ ._-](?:trailer|sample|scene|clip|interview|behindthescenes|deletedscene|featurette|short|extra))$")


def scan_directory(root: Path, maximum: int) -> tuple[list[dict], int]:
    items, ignored = [], 0
    for current, directories, files in os.walk(root, followlinks=False):
        base = Path(current)
        directories[:] = sorted(name for name in directories if not name.startswith('.')
                                and name.casefold() not in EXTRAS and not (base / name).is_symlink())
        for name in sorted(files):
            path = base / name
            if path.suffix.lower() not in SUPPORTED_MEDIA:
                continue
            if path.is_symlink() or name.startswith('.') or path.stem.casefold() in EXTRAS or EXTRA_SUFFIX.search(path.stem):
                ignored += 1
                continue
            if not path.is_file():
                continue
            info = parse_media_identity(name).model_dump(mode='json')
            stat = path.stat()
            items.append({'path': str(path), 'name': path.relative_to(root).as_posix(), 'identity': info,
                          'sizeBytes': stat.st_size, 'mtimeNs': stat.st_mtime_ns,
                          'state': 'pending', 'progress': 0, 'jobId': None})
            if len(items) > maximum:
                raise UserInputError(f'Katalog przekracza limit {maximum} plików wideo')
    def order(item):
        identity = item['identity']
        series = identity.get('normalized_title') or str(Path(item['name']).parent.parent)
        return (series.casefold(), identity.get('year') or 0,
                identity.get('season') or 0, identity.get('episode') or 0,
                identity.get('episode_end') or 0, item['name'].casefold())
    items.sort(key=order)
    return items, ignored


def compact_summary(reports: list[tuple[str, dict]]) -> dict:
    common, differences = {}, {}
    fields = ('container', 'width', 'height', 'video_codec', 'fps',
              'referenceCodec', 'referenceLanguage', 'referenceType', 'referenceForced')
    reference_keys = {'referenceCodec': 'codec', 'referenceLanguage': 'language',
                      'referenceType': 'type', 'referenceForced': 'forced'}
    for key in fields:
        values = [(name, (report.get('selectedReference') or {}).get(reference_keys[key]) if key in reference_keys
                   else (report.get('mediaInspection') or {}).get(key)) for name, report in reports]
        if not values:
            continue
        if all(value == values[0][1] for _, value in values):
            common[key] = values[0][1]
        else:
            groups = {}
            for name, value in values:
                encoded = json.dumps(value, sort_keys=True)
                groups.setdefault(encoded, {'value': value, 'items': []})['items'].append(name)
            differences[key] = list(groups.values())
    warnings = {}
    for name, report in reports:
        for warning in dict.fromkeys(report.get('warnings', []) + report.get('incompleteReasons', [])):
            warnings.setdefault(warning, []).append(name)
    return {'common': common, 'differences': differences,
            'warnings': [{'message': message, 'items': names} for message, names in warnings.items()]}


class DirectoryBatch:
    def __init__(self, manager):
        self.manager = manager

    def save(self, job_id, report):
        with self.manager._lock, self.manager._connect() as db:
            db.execute('UPDATE jobs SET report_json=? WHERE id=?',
                       (json.dumps(report, ensure_ascii=False), job_id))

    async def create(self, path, task_type):
        manager = self.manager
        root = validate_media_path(path, manager.settings.media_roots, allow_directory=True)
        manager.require_queue_space()
        job_id = str(uuid.uuid4())
        report = {'jobType': 'DIRECTORY_WORKPACK', 'pipeline': 'DIRECTORY', 'mode': task_type.value,
                  'warnings': [], 'batch': {'items': [], 'scanned': False, 'ignoredFiles': 0, 'current': None}}
        with manager._lock, manager._connect() as db:
            db.execute('INSERT INTO jobs (id,media_path,status,progress,created_at,job_type,task_type,report_json) '
                       "VALUES (?,?,?,0,?,'DIRECTORY_WORKPACK',?,?)",
                       (job_id, str(root), JobStatus.QUEUED, datetime.now().astimezone().isoformat(),
                        task_type.value, json.dumps(report)))
            manager._insert_event(db, job_id, 'INFO', JobStatus.QUEUED, 'Katalog oczekuje na skanowanie', 0)
        manager._queue.put_nowait((job_id, None, None))
        return manager.get(job_id)

    async def forward(self, child, level, stage, message, progress):
        manager = self.manager
        parent = manager.get(child['batch_parent_id'])
        if not parent:
            return
        report = parent['report']
        batch = report['batch']
        item = next(item for item in batch['items'] if item['jobId'] == child['id'])
        item.update(progress=progress, stage=str(stage))
        self.save(parent['id'], report)
        finished = sum(item['state'] in {'ready', 'failed', 'skipped'} for item in batch['items'])
        overall = min(94, round((finished + progress / 100) / max(1, len(batch['items'])) * 94))
        await manager._emit(parent['id'], level, JobStatus.BATCH_PROCESSING,
                            f"{item['name']} · {stage}: {message}", overall)

    async def continue_batch(self, job_id, action, selected=None):
        manager = self.manager
        job = manager.get(job_id)
        if not job or job['job_type'] != 'DIRECTORY_WORKPACK':
            raise UserInputError('Nie znaleziono zadania katalogowego')
        allowed = {JobStatus.BATCH_PAUSED, JobStatus.INTERRUPTED, JobStatus.FAILED}
        if action == 'retry':
            allowed.add(JobStatus.WORKPACK_INCOMPLETE)
        if job['status'] not in allowed:
            raise JobBusy('Zadanie katalogowe nie oczekuje na wznowienie')
        manager.require_queue_space()
        report = job['report']
        items = report['batch']['items']
        current = report['batch'].get('current')
        item = items[current] if current is not None else None
        if action == 'retry':
            failed = [entry for entry in items if entry['state'] == 'failed']
            if not failed:
                raise UserInputError('Brak błędnych plików do ponowienia')
            for entry in failed:
                entry.update(state='pending', progress=0)
                entry.pop('error', None)
        elif action == 'reference':
            ranking = (report.get('attention') or {}).get('referenceRanking', [])
            if not item or not selected or selected not in {reference_source_id(source) for source in ranking}:
                raise UserInputError('Wybrana referencja nie została wykryta w bieżącym pliku')
            item['requestedReference'] = selected
            item['state'] = 'pending'
        elif action == 'skip':
            if not item:
                raise UserInputError('Brak bieżącego pliku do pominięcia')
            item['state'] = 'skipped'
        elif job['status'] == JobStatus.BATCH_PAUSED:
            raise UserInputError('Potwierdź referencję lub pomiń bieżący plik')
        report.pop('attention', None)
        report.pop('workpack', None)
        self.save(job_id, report)
        with manager._lock, manager._connect() as db:
            after = db.execute('SELECT COALESCE(MAX(sequence),0) FROM events WHERE job_id=?', (job_id,)).fetchone()[0]
            db.execute('UPDATE jobs SET status=?,finished_at=NULL,error_message=NULL WHERE id=?', (JobStatus.QUEUED, job_id))
            manager._insert_event(db, job_id, 'INFO', JobStatus.QUEUED, 'Wznowienie kolejki katalogu', job['progress'])
        manager._queue.put_nowait((job_id, None, None))
        return after

    async def run(self, job_id):
        from app.services.job_manager import workpack_service
        manager = self.manager
        job = manager.get(job_id)
        root = validate_media_path(job['media_path'], manager.settings.media_roots, allow_directory=True)
        report = job['report']
        batch = report['batch']
        task_type = WorkpackTaskType(job['task_type'])
        if not batch['scanned']:
            await manager._emit(job_id, 'INFO', JobStatus.SCANNING_DIRECTORY, 'Skanowanie katalogu i rozpoznawanie plików', 1)
            batch['items'], batch['ignoredFiles'] = await asyncio.to_thread(scan_directory, root, manager.settings.directory_max_items)
            if not batch['items']:
                raise UserInputError('Nie znaleziono obsługiwanych plików wideo w katalogu')
            batch['scanned'] = True
            self.save(job_id, report)
        await manager._emit(job_id, 'INFO', JobStatus.BATCH_PROCESSING,
                            f"Kolejka katalogu: {len(batch['items'])} plików", job['progress'])
        for index in range(len(batch['items'])):
            report = manager.get(job_id)['report']
            batch = report['batch']
            item = batch['items'][index]
            if item['state'] in {'ready', 'failed', 'skipped'}:
                continue
            batch['current'] = index
            item['state'] = 'running'
            if not item['jobId']:
                item['jobId'] = str(uuid.uuid4())
                with manager._lock, manager._connect() as db:
                    db.execute('INSERT INTO jobs (id,media_path,status,progress,created_at,job_type,task_type,batch_parent_id) '
                               "VALUES (?,?,?,0,?,'PREPARE_WORKPACK',?,?)",
                               (item['jobId'], item['path'], JobStatus.QUEUED,
                                datetime.now().astimezone().isoformat(), task_type.value, job_id))
            self.save(job_id, report)
            child_id = item['jobId']
            child = manager.get(child_id)
            try:
                source = validate_media_path(item['path'], manager.settings.media_roots)
                stat = source.stat()
                if (stat.st_size, stat.st_mtime_ns) != (item['sizeBytes'], item['mtimeNs']):
                    raise UserInputError('Plik wideo zmienił się od skanowania; utwórz nową partię')
                if child['status'] not in {JobStatus.WORKPACK_READY, JobStatus.WORKPACK_INCOMPLETE, JobStatus.INSPECTION_READY}:
                    with manager._lock, manager._connect() as db:
                        db.execute('UPDATE jobs SET finished_at=NULL,error_message=NULL WHERE id=?', (child_id,))
                    await workpack_service(task_type).prepare(manager, child_id, child.get('report'), item.get('requestedReference'))
                child = manager.get(child_id)
                child_report = child.get('report') or {}
                report = manager.get(job_id)['report']
                item = report['batch']['items'][index]
                if child_report.get('batchReferencePending'):
                    item['state'] = 'attention'
                    report['attention'] = child_report
                    self.save(job_id, report)
                    await manager._emit(job_id, 'WARNING', JobStatus.BATCH_PAUSED,
                                        f"{item['name']} · wybierz referencję lub pomiń plik; kolejka wstrzymana",
                                        round(index / len(batch['items']) * 94))
                    return
                result_status = (JobStatus.WORKPACK_INCOMPLETE if (child_report.get('workpack') or {}).get('omittedFiles')
                                 else child['status'])
                item.update(state='ready', progress=100, resultStatus=result_status)
                item.pop('error', None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # One failed item never removes successful siblings from the workpack.
                report = manager.get(job_id)['report']
                item = report['batch']['items'][index]
                item.update(state='failed', error=str(exc) if isinstance(exc, UserInputError) else
                            f'Przetwarzanie nie powiodło się ({type(exc).__name__})')
                await manager._emit(child_id, 'ERROR', JobStatus.FAILED, item['error'], 100)
            self.save(job_id, report)
        report = manager.get(job_id)['report']
        report['batch']['current'] = None
        reports = [(item['name'], manager.get(item['jobId'])['report']) for item in report['batch']['items']
                   if item['state'] == 'ready']
        report['summary'] = compact_summary(reports)
        partial = any(item['state'] != 'ready' or item.get('resultStatus') not in
                      {JobStatus.WORKPACK_READY, JobStatus.INSPECTION_READY} for item in report['batch']['items'])
        report['warnings'] = ['Paczka zawiera pozycje niekompletne, pominięte lub błędne; sprawdź manifest.'] if partial else []
        self.save(job_id, report)
        if task_type in {WorkpackTaskType.INSPECT, WorkpackTaskType.INSPECT_SUBTITLES}:
            terminal = JobStatus.INSPECTION_READY
        else:
            await manager._emit(job_id, 'INFO', JobStatus.BUILDING_WORKPACK, 'Budowanie wspólnego workpacka katalogu', 96)
            try:
                report['workpack'] = await asyncio.to_thread(self.build_workpack, job_id, root, report)
            except ValueError as exc:
                raise UserInputError(str(exc)) from exc
            terminal = JobStatus.WORKPACK_INCOMPLETE if partial else JobStatus.WORKPACK_READY
        manager._save_report(job_id, report, str(root))
        await manager._emit(job_id, 'WARNING' if partial else 'SUCCESS', terminal, 'Przetwarzanie katalogu zakończone', 100)

    def build_workpack(self, job_id, root, report):
        manager = self.manager
        directory = manager.settings.data_root / 'work' / 'jobs' / job_id
        directory.mkdir(parents=True, exist_ok=True)
        included = {'manifest.json', 'REQUEST.md', 'analysis/summary.json'}
        manifests = []
        for index, item in enumerate(report['batch']['items'], 1):
            entry = {'name': item['name'], 'state': item['state'], 'status': item.get('resultStatus'), 'error': item.get('error')}
            manifests.append(entry)
            if item['state'] != 'ready':
                continue
            child = manager.get(item['jobId'])
            # Validate originals again before copying; source mutations must fail the whole ZIP.
            validate_zip(Path(child['report']['workpack']['path']))
            source_archive = Path(child['report']['workpack']['path'])
            with zipfile.ZipFile(source_archive) as source_bundle:
                manifest = json.loads(source_bundle.read('manifest.json'))
            prefix = f'items/{index:03d}-{safe_filename(Path(item["name"]).stem)}/'
            files = [name for name in manifest['files'] if name != 'checksums.sha256']
            with zipfile.ZipFile(source_archive) as source_bundle:
                for name in files:
                    target = directory / (prefix + name)
                    if not target.resolve().is_relative_to(directory.resolve()):
                        raise ValueError('Unsafe workpack artifact')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source_bundle.read(name))
                    included.add(prefix + name)
            def prefix_paths(value, file_list=False):
                if isinstance(value, list):
                    return [prefix + child if file_list and isinstance(child, str) else prefix_paths(child)
                            for child in value if not (file_list and child == 'checksums.sha256')]
                if isinstance(value, dict):
                    result = {key: prefix_paths(child, key == 'files') for key, child in value.items()}
                    for key in ('archiveName', 'name'):
                        if key in result and (key == 'archiveName' or 'sha256' in result):
                            result[key] = prefix + result[key]
                    return result
                return value
            manifest = prefix_paths(manifest)
            write_json(directory / (prefix + 'manifest.json'), manifest)
            entry['manifest'] = manifest
        write_json(directory / 'analysis/summary.json', report['summary'])
        (directory / 'REQUEST.md').write_text(
            '# Zadanie zbiorcze\n\nPrzetwarzaj każdy materiał oddzielnie według jego REQUEST.md w items/. '
            'Nie mieszaj referencji ani polskich napisów pomiędzy pozycjami. '
            'Ścieżki w manifestach odnoszą się do katalogu głównego paczki. '
            'Pozycje pominięte i błędne są jawnie oznaczone w manifest.json.\n', encoding='utf-8')
        write_json(directory / 'manifest.json', {'schema_version': SCHEMA_VERSION, 'kind': 'DIRECTORY',
                   'mode': report['mode'], 'name': root.name, 'items': manifests,
                   'warnings': report['warnings'], 'files': sorted(included | {'checksums.sha256'})})
        archive, version, digest, _ = build_zip(directory, root.name, manager.settings.directory_max_archive_bytes,
                                               manager.settings.directory_max_files, included, require_complete=True)
        return {'path': str(archive), 'filename': archive.name, 'sha256': digest, 'version': version,
                'sizeBytes': archive.stat().st_size, 'files': sorted(included | {'checksums.sha256'}),
                'referenceAmbiguous': False}
