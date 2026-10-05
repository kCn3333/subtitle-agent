import json

import pytest

from app.services.ai_console import AiConsoleStore, capture, record_metrics
from app.services.ai_sync import AiSyncError, ApiSettings
from app.services.ai_usage import UsageStore
from tests.test_archive import add_job, manager_at


def run(store, job, operation, usage, fail=False):
    with capture(store, ApiSettings(model='test'), operation, job):
        record_metrics(1, usage)
        if fail:
            raise AiSyncError('Invalid model response')


def test_archive_sums_retries_failures_modes_and_jobs_survives_console_clear(tmp_path):
    manager = manager_at(tmp_path)
    first, _ = add_job(manager, 'Film')
    second, _ = add_job(manager, 'Film', 1)
    other, _ = add_job(manager, 'Other', 2)
    store = AiConsoleStore(manager.db_path)
    run(store, first, 'SYNC', {'total_tokens':10,'cost':0.1,'cost_currency':'USD'})
    with pytest.raises(AiSyncError):
        run(store, first, 'SYNC', {'total_tokens':20,'cost':0.2,'cost_currency':'USD'}, True)
    run(store, second, 'TRANSLATE', {'prompt_tokens':20,'completion_tokens':10,'cost':0,'cost_currency':'USD'})
    run(store, None, 'TEST', {'total_tokens':999,'cost':999,'cost_currency':'USD'})
    run(store, other, 'SYNC', {'total_tokens':7,'cost':4,'cost_currency':'USD'})
    store.clear()
    restarted = manager_at_existing(manager)
    titles = {title['title']:title for title in restarted.archive.list_titles(restarted.list_jobs())}
    assert titles['Film']['cost'] == {'requests':3,'total_tokens':60,'usd':'0.3',
                                    'tokens_partial':False,'usd_partial':False}
    assert titles['Other']['cost']['usd'] == '4'


def manager_at_existing(manager):
    from app.services.job_manager import JobManager
    return JobManager(manager.db_path, manager.settings)


def test_missing_usage_is_not_free_and_non_usd_not_converted(tmp_path):
    manager = manager_at(tmp_path)
    job, _ = add_job(manager, 'Film')
    store = AiConsoleStore(manager.db_path)
    run(store, job, 'SYNC', {})
    total = manager.archive.list_titles(manager.list_jobs())[0]['cost']
    assert total['usd'] is None and total['total_tokens'] is None
    run(store, job, 'SYNC', {'total_tokens':5,'cost':9,'cost_currency':'(jednostki API)'})
    run(store, job, 'SYNC', {'total_tokens':10,'cost':0.02,'cost_currency':'USD'})
    total = manager.archive.list_titles(manager.list_jobs())[0]['cost']
    assert total['usd'] == '0.02' and total['total_tokens'] == 15
    assert total['usd_partial'] and total['tokens_partial']


def test_legacy_console_backfill_and_saved_result_no_double_count(tmp_path):
    manager = manager_at(tmp_path)
    job, directory = add_job(manager, 'Film')
    store = AiConsoleStore(manager.db_path)
    store.append('SYNC',job,'INFO','Czas żądania: 1 s · tokeny: wejście 10, wyjście 5, razem 15 · koszt: 0.01 USD')
    store.append('SYNC',job,'RESPONSE',json.dumps({'usage':{'total_tokens':15,'cost':0.01}}))
    (directory/'ai-sync.json').write_text(json.dumps({'usage':{'total_tokens':15,'cost':0.01,'cost_currency':'USD'}}))
    (directory/'ai-translation.json').write_text(json.dumps({'usage':{'total_tokens':25,'cost':0.02,'cost_currency':'USD'}}))
    with store.usage.connect() as db:
        db.execute('DELETE FROM ai_usage_migrations')
    migrated = UsageStore(manager.db_path)
    migrated.backfill(manager.list_jobs(), manager.settings.data_root/'work'/'jobs')
    assert migrated.totals()['/media/Film.mkv']['requests'] == 2
    assert migrated.totals()['/media/Film.mkv']['usd'] == '0.03'
    run(store, job, 'SYNC', {'total_tokens':8,'cost':0.04,'cost_currency':'USD'})
    again = UsageStore(manager.db_path)
    again.backfill(manager.list_jobs(), manager.settings.data_root/'work'/'jobs')
    assert again.totals()['/media/Film.mkv']['requests'] == 3


def test_usage_removed_with_thirty_title_retention(tmp_path):
    manager = manager_at(tmp_path)
    old, _ = add_job(manager, 'Old')
    store = AiConsoleStore(manager.db_path)
    run(store, old, 'SYNC', {'total_tokens':10,'cost':1,'cost_currency':'USD'})
    for index in range(30):
        add_job(manager, f'Film{index}', index+1)
    manager.cleanup_expired_artifacts()
    with store.usage.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM ai_usage').fetchone()[0] == 0
