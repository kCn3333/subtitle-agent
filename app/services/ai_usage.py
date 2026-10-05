"""Per-request accounting independent of bounded or cleared diagnostic logs."""
import json
import math
import os
import re
import sqlite3
import uuid
from decimal import Decimal

from app.services.artifact_retention import validated_job_directory


class UsageStore:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS ai_usage (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, operation TEXT NOT NULL, usage_json TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS ai_usage_job ON ai_usage(job_id)')
            db.execute('CREATE TABLE IF NOT EXISTS ai_usage_migrations (name TEXT PRIMARY KEY)')
            if not db.execute("SELECT 1 FROM ai_usage_migrations WHERE name='console'").fetchone():
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='ai_console'").fetchone():
                    for row in db.execute("SELECT * FROM ai_console WHERE job_id IS NOT NULL AND level='INFO'").fetchall():
                        match = re.fullmatch(r'Czas żądania: .*? s · tokeny: wejście (\d+|—), wyjście (\d+|—), razem (\d+|—)(?: · koszt: ([\d.]+) (USD|\(jednostki API\)))?', row['message'])
                        if not match:
                            continue
                        usage = {key:int(value) for key,value in zip(('prompt_tokens','completion_tokens','total_tokens'), match.groups()[:3]) if value != '—'}
                        if match[4] is not None:
                            usage.update(cost=float(match[4]), cost_currency=match[5])
                        self.insert(db, 'console:'+str(row['id']), row['job_id'], row['operation'], usage)
                db.execute("INSERT INTO ai_usage_migrations VALUES ('console')")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def insert(db, identity, job_id, operation, usage):
        clean = {key:value for key,value in usage.items() if key in ('prompt_tokens','completion_tokens','total_tokens')
                 and type(value) is int and value >= 0}
        cost = usage.get('cost')
        if type(cost) in (int,float) and math.isfinite(cost) and cost >= 0:
            clean.update(cost=cost, cost_currency=usage.get('cost_currency', '(jednostki API)'))
        db.execute('INSERT OR IGNORE INTO ai_usage VALUES (?,?,?,?)',
                   (identity,job_id,operation,json.dumps(clean)))

    def record(self, job_id, operation, usage):
        if job_id and operation in {'SYNC','TRANSLATE'}:
            with self.connect() as db:
                self.insert(db, str(uuid.uuid4()), job_id, operation, usage or {})

    def backfill(self, jobs, jobs_root):
        """Recover the last saved result only when its operation has no console metrics."""
        with self.connect() as db:
            if db.execute("SELECT 1 FROM ai_usage_migrations WHERE name='results'").fetchone():
                return
            for job in jobs:
                directory = validated_job_directory(jobs_root, jobs_root/job['id'])
                if directory is None:
                    continue
                for stem,operation in [('ai-sync','SYNC'),('ai-translation','TRANSLATE')]:
                    if db.execute('SELECT 1 FROM ai_usage WHERE job_id=? AND operation=?', (job['id'],operation)).fetchone():
                        continue
                    path = directory/(stem+'.json')
                    if path.is_symlink() or not path.is_file():
                        continue
                    try:
                        usage = json.loads(path.read_text()).get('usage')
                        if isinstance(usage,dict):
                            self.insert(db, 'result:'+job['id']+':'+operation, job['id'], operation, usage)
                    except (OSError,ValueError,AttributeError):
                        pass
            db.execute("INSERT INTO ai_usage_migrations VALUES ('results')")

    def totals(self):
        totals = {}
        with self.connect() as db:
            for row in db.execute('''SELECT jobs.media_path,ai_usage.usage_json FROM ai_usage
                                     JOIN jobs ON jobs.id=ai_usage.job_id'''):
                key = os.path.normpath(row['media_path'])
                total = totals.setdefault(key, {'requests':0,'total_tokens':None,'usd':None,
                                               'tokens_partial':False,'usd_partial':False})
                usage = json.loads(row['usage_json'])
                total['requests'] += 1
                tokens = usage.get('total_tokens')
                if tokens is None and all(name in usage for name in ('prompt_tokens','completion_tokens')):
                    tokens = usage['prompt_tokens'] + usage['completion_tokens']
                if tokens is None:
                    total['tokens_partial'] = True
                else:
                    total['total_tokens'] = (total['total_tokens'] or 0) + tokens
                if 'cost' in usage and usage.get('cost_currency') == 'USD':
                    total['usd'] = (total['usd'] or Decimal('0')) + Decimal(str(usage['cost']))
                else:
                    total['usd_partial'] = True
        for total in totals.values():
            if total['usd'] is not None:
                total['usd'] = format(total['usd'], 'f')
        return totals
