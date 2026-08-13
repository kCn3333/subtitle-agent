# Subtitle Agent

Lekka usługa do bezpiecznej analizy mediów i napisów. Etap 2 sprawdza wskazany plik przez `ffprobe`, odkrywa wbudowane i zewnętrzne napisy, analizuje SRT, tworzy deterministyczne rankingi oraz przygotowuje angielską kopię roboczą wyłącznie pod `/data/work/jobs/<job_id>`. Nie synchronizuje napisów i niczego nie zapisuje w katalogu mediów.

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
| `SUBTITLE_AGENT_MEDIA_ROOTS` | `/media/movies:/media/shows` | Dozwolone korzenie mediów rozdzielone dwukropkiem; starsze `MEDIA_ROOTS` pozostaje obsługiwane |
| `MAX_CONCURRENT_JOBS` | `1` | Liczba równoległych zadań |
| `FFPROBE_TIMEOUT_SECONDS` | `30` | Limit czasu analizy ffprobe |
| `FFMPEG_TIMEOUT_SECONDS` | `600` | Limit czasu bezpiecznej ekstrakcji roboczej; odczyt dużego pliku z NAS może potrwać kilka minut |

`.env.example` nie zawiera sekretów. Klucz OpenAI nie jest obecnie obsługiwany.

## API

- `GET /` — GUI.
- `GET /health` — gotowość aplikacji i narzędzi.
- `POST /api/jobs` — tworzy trwałe zadanie analizy (`{"mediaPath":"/media/shows/example.mkv"}`).
- `GET /api/jobs` — lista ostatnich zadań.
- `GET /api/jobs/{job_id}` — stan i pełny raport zadania.
- `GET /api/jobs/{job_id}/events` — historia i zdarzenia SSE; `Last-Event-ID` umożliwia wznowienie, heartbeat pojawia się po 15 s ciszy.

Zdarzenia mają rosnącą sekwencję, czas ze strefą, etap, poziom, wiadomość i postęp 0–100. SQLite pracuje w WAL; maksymalnie 500 zdarzeń pozostaje na zadanie. Zadania niedokończone podczas restartu stają się `INTERRUPTED`. Raport obejmuje dane techniczne, ścieżki napisów, analizę zewnętrznych SRT, rankingi z powodami punktacji, wybory oraz ostrzeżenia.

## GHCR i CI

GitHub Actions buduje `linux/amd64`, testuje `/health`, `ffmpeg` i `ffprobe`, a poza pull requestem publikuje `latest`, `sha-*` i tagi semver przy użyciu `GITHUB_TOKEN` — bez PAT i sekretów aplikacji. Publiczny obraz można pobierać anonimowo. Po pierwszej publikacji może być konieczne jednorazowe ustawienie pakietu jako **Public** w GitHub Packages; publiczne repozytorium nie gwarantuje automatycznie publicznego pakietu GHCR.

## Bezpieczeństwo i ograniczenia etapu 1

Kontener działa jako UID/GID 10001, bez capabilities i z `no-new-privileges`. Walidacja używa `Path.resolve(strict=True)` i odrzuca ścieżki względne, NUL, symlinki poza rootem, brak pliku, katalogi i nieobsługiwane rozszerzenia. Media pozostają `read-only`; tylko kopie robocze trafiają do `/data`. Repozytorium nie przechowuje `.env`, mediów ani sekretów. Brak jeszcze OpenAI, autonomicznego agenta, synchronizacji i publikowania napisów oraz Jellyfina.

## Kolejne etapy

Kolejny etap może dodać właściwą synchronizację w katalogu roboczym, podgląd różnic czasowych oraz atomowy i jawnie zatwierdzany mechanizm publikacji SRT. Integracje z OpenAI i Jellyfinem powinny wejść dopiero po osobnej decyzji i testach bezpieczeństwa zapisu.
