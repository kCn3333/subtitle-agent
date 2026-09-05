# Subtitle Agent

Subtitle Agent analizuje napisy filmu lub odcinka i przygotowuje ZIP do synchronizacji albo tłumaczenia. Biblioteka mediów jest montowana tylko do odczytu, a aplikacja nie wymaga klucza OpenAI.

## Co potrafi

- sprawdza parametry materiału i dostępne ścieżki napisów;
- wybiera najlepszą angielską referencję;
- wykrywa niezgodne polskie napisy i inne wersje materiału;
- eksportuje napisy tekstowe, PGS oraz DVD/VobSub;
- opcjonalnie wykonuje OCR napisów graficznych na CPU;
- tworzy ZIP z manifestem, raportem, sumami SHA-256 i potrzebnymi napisami.

Tryby w GUI:

- **Sprawdź napisy** — raport bez ZIP-a;
- **Przygotuj do synchronizacji** — wymaga zgodnej referencji EN i kandydata PL;
- **Przygotuj do tłumaczenia** — wymaga referencji EN, ale nie napisów PL.

## Uruchomienie w Portainerze

1. Otwórz **Stacks → Add stack → Web editor**.
2. Wklej zawartość `compose.example.yml`.
3. Zmień hostowe ścieżki `/media/movies` i `/media/shows`.
4. Pozostaw mounty mediów jako `:ro` i wdroż stack.

Obrazy:

```text
ghcr.io/kcn3333/subtitle-agent:latest
ghcr.io/kcn3333/subtitle-agent-ocr:latest
```

Worker OCR nie potrzebuje portu hostowego ani dostępu do `/media` i `/data`. Otrzymuje wyłącznie pliki konkretnego zadania.

## Najważniejsza konfiguracja

| Zmienna | Domyślna | Znaczenie |
|---|---:|---|
| `APP_PORT` | `8080` | Port aplikacji w kontenerze |
| `DATA_ROOT` | `/data` | Baza i pliki robocze |
| `SUBTITLE_AGENT_MEDIA_ROOTS` | `/media/movies:/media/shows` | Dozwolone katalogi mediów |
| `MAX_CONCURRENT_JOBS` | `1` | Liczba równoległych zadań |
| `OCR_WORKER_URL` | brak | Np. `http://subtitle-ocr-worker:8090` |
| `OCR_TIMEOUT_SECONDS` | `900` | Limit czasu OCR |
| `WORKPACK_MAX_ARCHIVE_BYTES` | `104857600` | Maksymalny rozmiar ZIP |
| `WORKPACK_RETENTION_HOURS` | `72` | Czas dostępności ZIP-a |

Pełny zestaw bezpiecznych wartości znajduje się w `.env.example` i `compose.example.yml`.

## OCR

OCR działa na CPU przy użyciu Tesseracta i przypiętego `seconv v5.2.0-rc2`. Obsługiwane są referencje PGS (`.sup`) i DVD/VobSub (`.idx` + `.sub`).

Wynik zawiera osobne oceny:

- `structuralQuality` — poprawność SRT i zgodność timestampów;
- `textQuality` — podejrzane błędy rozpoznanego tekstu.

OCR może wymagać korekty językowej. Gdy worker jest niedostępny lub wynik jest niepoprawny, aplikacja zachowuje oryginalną referencję graficzną i zwraca status `NEEDS_OCR` zamiast błędu całego zadania.

## Zawartość paczki

```text
manifest.json          opis zadania i oczekiwanego wyniku
REQUEST.md             instrukcja dla agenta AI
checksums.sha256       sumy kontrolne
reference/selected/    wybrana referencja angielska
polish/                zakwalifikowane napisy PL
analysis/              raporty techniczne
```

ZIP nie zawiera filmu, audio, sekretów ani pełnych ścieżek hosta.

## Rozwiązania techniczne

- **Backend:** Python 3.12, FastAPI i Uvicorn.
- **Stan zadań:** SQLite w `/data`; zdarzenia i postęp są przesyłane do GUI przez SSE.
- **Analiza mediów:** `ffprobe`; ekstrakcja tekstu i PGS przez FFmpeg.
- **DVD/VobSub:** remuks wybranej ścieżki do tymczasowego MKS i eksport pary `.idx` + `.sub` przez `mkvextract`.
- **OCR:** osobny worker CPU-only z Tesseractem i `seconv`; przetwarza pojedynczą kolejkę z limitem czasu i rozmiaru.
- **Ocena OCR:** niezależna walidacja struktury/timestampów oraz heurystyki jakości tekstu; metryki trafiają do `ocr-quality-report.json`.
- **Synchronizacja:** deterministyczne modele `GLOBAL_OFFSET`, `AFFINE_DRIFT` i `PIECEWISE_LINEAR`; wynik jest hipotezą wymagającą weryfikacji.
- **Archiwum:** ZIP `subtitle-workpack-v2`, względne nazwy plików i SHA-256 każdego wpisu.
- **Izolacja:** kontenery bez dodatkowych capabilities, `no-new-privileges`, użytkownicy nieuprzywilejowani i media montowane jako read-only.
- **Worker OCR:** bez dostępu do `/media`, SQLite i całego `/data`; korzysta wyłącznie z katalogu tymczasowego konkretnego żądania.

Najważniejsze endpointy:

```text
POST /api/tasks                       utworzenie zadania
GET  /api/tasks/{job_id}              status i raport
GET  /api/tasks/{job_id}/download     pobranie ZIP
GET  /api/jobs/{job_id}/events        zdarzenia SSE
GET  /api/workpacks/ocr-health        dostępność workera OCR
GET  /health                          stan aplikacji i narzędzi CLI
```

## Rozwój i testy

Wymagane są Python 3.12 oraz Docker lub Podman.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
docker build --target test -t subtitle-agent:test .
```
