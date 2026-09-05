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
    assert 'id="download-label"' in response.text
    assert 'aria-disabled="true"' in response.text
    assert '<svg viewBox="0 0 24 24"' in response.text
    assert response.text.index('id="download"') < response.text.index('id="results"')

    styles = client.get("/static/styles.css").text
    assert "font-size: 38px" in styles
    assert "linear-gradient(90deg, #20b96a, #62e69a)" in styles
    assert "box-shadow: 0 2px 7px rgba(3, 9, 18, .12)" in styles
    assert '.download-button[aria-disabled="true"]' in styles
