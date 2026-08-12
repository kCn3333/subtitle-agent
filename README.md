# Subtitle Agent

Lekki fundament usługi do przyszłego zarządzania napisami. Etap 1 udostępnia ciemne GUI, trwałe zadania demonstracyjne, konsolę Server-Sent Events i diagnostykę `ffmpeg`/`ffprobe`. Nie analizuje ani nie zapisuje napisów.

## GUI

Jedna responsywna strona zawiera ścieżkę materiału, przycisk zadania, status, postęp oraz terminalową konsolę. Konsola odtwarza historię z SQLite, śledzi nowe zdarzenia przez SSE i nie przewija użytkownika na dół, gdy czyta starsze wpisy.

## Wymagania i uruchomienie lokalne

- Python 3.12, ffmpeg/ffprobe oraz opcjonalnie Docker/Podman.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
DATA_ROOT=./data uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Otwórz `http://localhost:8080`. Testy: `pytest -q`. Obraz: `docker build --platform linux/amd64 -t subtitle-agent:local .`.

## Docker Compose i Portainer

Obraz: `ghcr.io/kcn3333/subtitle-agent:latest`. Lokalnie uruchom `docker compose -f compose.example.yml up -d`. W Portainerze wybierz **Stacks → Add stack → Web editor**, wklej zawartość `compose.example.yml`, dostosuj wyłącznie ścieżki hosta do bibliotek i wdroż stack. Katalogi mediów są w tym etapie obowiązkowo montowane `read-only`; aplikacja niczego w nich nie zapisuje.

## Konfiguracja

| Zmienna | Domyślna | Znaczenie |
|---|---|---|
| `APP_NAME` | `Subtitle Agent` | Nazwa GUI |
| `APP_HOST` | `0.0.0.0` | Adres nasłuchu (używany przez konfigurację uruchomieniową) |
| `APP_PORT` | `8080` | Port HTTP |
| `LOG_LEVEL` | `INFO` | Poziom logowania |
| `DATA_ROOT` | `/data` | Zapisywalne dane i SQLite |
| `MEDIA_ROOTS` | `/media/movies,/media/shows` | Dozwolone korzenie mediów |
| `MAX_CONCURRENT_JOBS` | `1` | Liczba równoległych zadań |

`.env.example` nie zawiera sekretów. Klucz OpenAI nie jest obecnie obsługiwany.

## API

- `GET /` — GUI.
- `GET /health` — gotowość aplikacji i narzędzi.
- `POST /api/jobs` — tworzy zadanie demonstracyjne (`{"mediaPath":"/media/shows/example.mkv"}`).
- `GET /api/jobs/{job_id}` — stan zadania.
- `GET /api/jobs/{job_id}/events` — historia i zdarzenia SSE; `Last-Event-ID` umożliwia wznowienie, heartbeat pojawia się po 15 s ciszy.

Zdarzenia mają rosnącą sekwencję, czas ze strefą, etap, poziom, wiadomość i postęp 0–100. SQLite pracuje w WAL; maksymalnie 500 zdarzeń pozostaje na zadanie. Zadania niedokończone podczas restartu stają się `INTERRUPTED`.

## GHCR i CI

GitHub Actions buduje `linux/amd64`, testuje `/health`, `ffmpeg` i `ffprobe`, a poza pull requestem publikuje `latest`, `sha-*` i tagi semver przy użyciu `GITHUB_TOKEN` — bez PAT i sekretów aplikacji. Publiczny obraz można pobierać anonimowo. Po pierwszej publikacji może być konieczne jednorazowe ustawienie pakietu jako **Public** w GitHub Packages; publiczne repozytorium nie gwarantuje automatycznie publicznego pakietu GHCR.

## Bezpieczeństwo i ograniczenia etapu 1

Kontener działa jako UID/GID 10001, bez capabilities i z `no-new-privileges`. Walidacja odrzuca ścieżki względne, NUL, traversal i wyjście poza `MEDIA_ROOTS`. Repozytorium nie przechowuje `.env`, mediów ani sekretów. Brak jeszcze OpenAI, autonomicznego agenta, ekstrakcji/synchronizacji napisów, zapisu do bibliotek i Jellyfina.

## Kolejne etapy

Etap 2 może dodać kontrolowane sondowanie pliku przez ffprobe, model domenowy ścieżek napisów, bezpieczny katalog roboczy i projekt procesu publikacji SRT. Integracje z OpenAI i Jellyfinem powinny wejść dopiero po osobnej decyzji i testach bezpieczeństwa zapisu.
