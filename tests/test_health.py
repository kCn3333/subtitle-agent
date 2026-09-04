def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "ffmpeg": True, "ffprobe": True, "mkvextract": True}


def test_index_is_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Subtitle Agent" in response.text
