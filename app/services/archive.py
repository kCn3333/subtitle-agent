"""Content-addressed downloads for the thirty most recently requested media titles."""
import hashlib
import json
import os
import re
import shutil
import sqlite3
from pathlib import Path

from app.services.artifact_retention import validated_job_directory
from app.services.ai_usage import UsageStore

ARCHIVE_LIMIT = 30


def title_key(job: dict) -> str:
    return os.path.normpath(job['media_path'])


class ArchiveStore:
    def __init__(self, db_path: Path, data_root: Path):
        self.db_path = db_path
        self.usage = UsageStore(db_path)
        self.root = data_root / 'archive'
        self.jobs_root = data_root / 'work' / 'jobs'
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError('Archive directory must not be a symlink')
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS archive_files (
                title_key TEXT NOT NULL, sha256 TEXT NOT NULL, kind TEXT NOT NULL,
                filename TEXT NOT NULL, suffix TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(title_key,sha256,kind))''')

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def record(self, job: dict, source: Path, kind: str, expected_sha: str | None = None):
        directory = validated_job_directory(self.jobs_root, self.jobs_root / job['id'])
        if directory is None or not source.is_file() or source.is_symlink():
            return
        if not source.resolve().is_relative_to(directory.resolve()):
            return
        suffix = '.zip' if kind == 'workpack' else '.srt'
        if source.suffix.lower() != suffix:
            return
        if expected_sha and re.fullmatch(r'[a-f0-9]{64}', expected_sha):
            with self.connect() as db:
                known = db.execute('SELECT 1 FROM archive_files WHERE title_key=? AND sha256=? AND kind=?',
                                   (title_key(job), expected_sha, kind)).fetchone()
            saved = self.root / (expected_sha + suffix)
            if known and saved.is_file() and not saved.is_symlink():
                return
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if expected_sha and digest != expected_sha:
            return
        target = self.root / (digest + suffix)
        if target.is_symlink():
            raise ValueError('Archive file must not be a symlink')
        if not target.exists():
            temporary = self.root / (digest + '.tmp')
            if temporary.is_symlink():
                raise ValueError('Archive temporary file must not be a symlink')
            shutil.copyfile(source, temporary)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
                temporary.unlink(missing_ok=True)
                raise ValueError('Artifact changed during archival')
            temporary.replace(target)
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO archive_files VALUES (?,?,?,?,?,?)',
                       (title_key(job), digest, kind, source.name, suffix, job['created_at']))

    def capture(self, job: dict):
        report = job.get('report') or {}
        workpack = report.get('workpack') or {}
        if workpack.get('path') and not workpack.get('artifactExpired'):
            self.record(job, Path(workpack['path']), 'workpack', workpack.get('sha256'))
        alignment = report.get('alignment') or {}
        if alignment.get('previewPath') and alignment.get('previewSha256'):
            self.record(job, Path(alignment['previewPath']), 'synchronization', alignment['previewSha256'])
        directory = self.jobs_root / job['id']
        if validated_job_directory(self.jobs_root, directory):
            for stem, kind in [('ai-sync', 'synchronization'), ('ai-translation', 'translation')]:
                summary = directory / (stem + '.json')
                if not summary.is_file() or summary.is_symlink():
                    continue
                try:
                    result = json.loads(summary.read_text())
                    if result.get('sha256'):
                        self.record(job, directory / (stem + '.pl.srt'), kind, result['sha256'])
                except (OSError, ValueError):
                    pass

    def prune(self, keys: set[str]):
        with self.connect() as db:
            for row in db.execute('SELECT DISTINCT title_key FROM archive_files').fetchall():
                if row['title_key'] not in keys:
                    db.execute('DELETE FROM archive_files WHERE title_key=?', (row['title_key'],))
            referenced = {r['sha256'] + r['suffix'] for r in db.execute('SELECT sha256,suffix FROM archive_files')}
        for path in self.root.iterdir():
            if re.fullmatch(r'[a-f0-9]{64}\.(zip|srt|tmp)', path.name) and path.name not in referenced and not path.is_symlink():
                path.unlink()

    def list_titles(self, jobs: list[dict]):
        titles = {}
        for job in sorted(jobs, key=lambda j: j['created_at'], reverse=True):
            key = title_key(job)
            if key not in titles:
                titles[key] = {'date': job['created_at'], 'title': Path(key).stem,
                               'workpacks': [], 'subtitles': []}
            if len(titles) == ARCHIVE_LIMIT:
                break
        with self.connect() as db:
            for file in db.execute('SELECT * FROM archive_files ORDER BY created_at DESC,filename'):
                if file['title_key'] not in titles:
                    continue
                path = self.root / (file['sha256'] + file['suffix'])
                if not path.is_file() or path.is_symlink():
                    continue
                item = {k: file[k] for k in ('filename', 'sha256', 'kind')}
                item['url'] = '/api/archive/files/' + file['sha256'] + file['suffix']
                titles[file['title_key']]['workpacks' if file['kind'] == 'workpack' else 'subtitles'].append(item)
        usage = self.usage.totals()
        for key, title in titles.items():
            title["cost"] = usage.get(key)
        return list(titles.values())

    def download(self, name: str):
        if not re.fullmatch(r'[a-f0-9]{64}\.(zip|srt)', name):
            return None
        with self.connect() as db:
            row = db.execute('SELECT filename FROM archive_files WHERE sha256=? AND suffix=? LIMIT 1',
                             (name[:64], name[64:])).fetchone()
        path = self.root / name
        if row is None or not path.is_file() or path.is_symlink():
            return None
        if hashlib.sha256(path.read_bytes()).hexdigest() != name[:64]:
            return None
        return path, row['filename']
