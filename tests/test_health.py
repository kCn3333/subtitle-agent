from app import __version__

def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_is_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Subtitle Agent" in response.text
    assert f'class="brand-version" href="https://github.com/kCn3333/subtitle-agent/releases/tag/v{__version__}"' in response.text
    assert f'>v{__version__}</a>' in response.text
    assert 'href="https://github.com/kCn3333/subtitle-agent"' in response.text
    assert client.app.version == __version__
    assert 'role="progressbar"' in response.text
    assert 'id="download"' in response.text
    assert 'id="download-label"' in response.text
    assert 'aria-disabled="true"' in response.text
    assert 'fontawesome.min.css' in response.text
    assert 'href="/archive"' in response.text
    assert response.text.index('id="ai-sync-panel"') < response.text.index('id="ai-sync-button"')
    assert 'id="ocr-health"' in response.text
    assert 'id="ai-sync-button"' in response.text
    assert "Przekaż do AI" in response.text
    assert response.text.index('id="ai-sync-button"') < response.text.index('id="console"')
    assert 'href="/settings"' in response.text
    assert response.text.index('id="download"') < response.text.index('id="results"')

    styles = client.get("/static/styles.css")
    assert styles.status_code == 200
    assert "text/css" in styles.headers["content-type"]


def test_ocr_health_reports_configuration_and_real_availability(client, settings, monkeypatch):
    assert client.get("/api/workpacks/ocr-health").json() == {
        "configured": False, "available": False,
    }
    settings.ocr_worker_url = "http://subtitle-ocr-worker:8090"

    async def available(worker_url):
        assert worker_url == settings.ocr_worker_url
        return True

    monkeypatch.setattr("app.api.workpacks.worker_available", available)
    assert client.get("/api/workpacks/ocr-health").json() == {
        "configured": True, "available": True,
    }
