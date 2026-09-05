def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "ffmpeg": True, "ffprobe": True, "mkvextract": True}


def test_index_is_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Subtitle Agent" in response.text
    assert "WORKPACK · PACZKA DO CHATGPT" not in response.text
    assert "Bezpiecznie zbierz referencję" not in response.text
    assert 'role="progressbar"' in response.text
    assert 'id="download"' in response.text
    assert response.text.index('id="download"') < response.text.index('id="results"')
