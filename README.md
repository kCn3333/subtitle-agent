# Subtitle Agent

Subtitle Agent przygotowuje mały, samowystarczalny workpack ZIP z napisami i analizą techniczną. Paczkę pobiera się z prostego GUI i przekazuje do ChatGPT w celu synchronizacji, korekty językowej, tłumaczenia albo inspekcji ścieżek. Domyślny tryb `WORKPACK` nie korzysta z OpenAI API, nie wymaga klucza i nigdy nie zapisuje w bibliotece mediów.

## Przepływ

1. Podaj absolutną ścieżkę filmu pod jednym z dozwolonych mountów `/media:ro`.
2. Wybierz `SYNC_ONLY`, `LANGUAGE_REVIEW`, `SYNC_AND_LANGUAGE_REVIEW`, `TRANSLATE_TO_POLISH` albo `INSPECT_SUBTITLES`.
3. Aplikacja uruchamia ffprobe, klasyfikuje napisy, wyodrębnia angielską referencję i kopiuje polskie pliki byte-for-byte do `/data/work/jobs/<uuid>`.
4. Pobierz ZIP i prześlij najlepiej całe archiwum do ChatGPT. `REQUEST.md` zawiera gotowe polecenie, a `manifest.json` jest źródłem danych technicznych.

GUI pokazuje logi na żywo przez SSE, ranking angielskich ścieżek, kandydatów PL, ostrzeżenia, rozmiar i SHA-256. Historia zadania i metadane pobierania pozostają w SQLite po restarcie.

## Zawartość ZIP

```text
manifest.json                 wersjonowany subtitle-workpack-v1
REQUEST.md                    instrukcja po polsku dla wybranego zadania
README.txt                    krótki przewodnik po paczce
checksums.sha256              sumy wszystkich pozostałych wpisów
reference/selected/           angielska referencja i tekstowa kopia UTF-8 SRT
polish/                       kandydaci skopiowani bez zmian
analysis/                     media, strumienie, rankingi, timeline i hipotezy
```

SubRip, ASS, SSA, WebVTT i mov_text otrzymują kopię SRT; zachowywany jest też oryginalny format strumienia. PGS jest eksportowany jako `.sup` wraz z timeline pakietów. Nie jest wykonywany OCR. DVD/VobSub wymaga kompletnej pary `.idx` + `.sub`. Hipotezy synchronizacji są tylko diagnostyczne i słaby wynik nie jest przedstawiany jako gotowa synchronizacja.

Ranking preferuje angielski, pełne dialogi, format tekstowy i tytuł `Full Dialogue`. Mocno obniża ocenę commentary, forced, SDH/CC, hearing impaired, signs/songs/foreign parts oraz `Japanese Parts Only`. Gdy dwie najlepsze ścieżki dzieli mniej niż skonfigurowany margines, GUI oznacza wybór jako niejednoznaczny i pozwala przebudować paczkę z innym wykrytym strumieniem bez ponownego ffprobe.

## Uruchomienie

Wymagania: Python 3.12, ffmpeg/ffprobe oraz opcjonalnie Docker.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
DATA_ROOT=./data SUBTITLE_AGENT_APP_MODE=WORKPACK uvicorn app.main:app --host 0.0.0.0 --port 8080
pytest -q
docker build --platform linux/amd64 -t subtitle-agent:local .
```

Obraz produkcyjny: `ghcr.io/kcn3333/subtitle-agent:latest`. Kontener działa jako `10001:10001`.

### Portainer

Wybierz **Stacks → Add stack → Web editor**, wklej `compose.example.yml`, zmień tylko hostowe ścieżki `/media/movies` i `/media/shows`, a następnie wdroż. Oba mounty muszą pozostać `:ro`; `/data` jest jedynym zapisywalnym wolumenem. Domyślny stack nie zawiera `/publish`, sekretu OpenAI, mountu RW ani rozszerzonych capabilities.

## Konfiguracja WORKPACK

| Zmienna | Domyślna | Znaczenie |
|---|---:|---|
| `SUBTITLE_AGENT_APP_MODE` | `WORKPACK` | `WORKPACK` albo historyczny `ADVANCED` |
| `SUBTITLE_AGENT_MEDIA_ROOTS` | `/media/movies:/media/shows` | Dozwolone korzenie RO |
| `DATA_ROOT` | `/data` | SQLite i katalogi zadań |
| `MAX_CONCURRENT_JOBS` | `1` | Równoległe zadania |
| `FFPROBE_TIMEOUT_SECONDS` | `30` | Limit ffprobe |
| `FFMPEG_TIMEOUT_SECONDS` | `600` | Limit ekstrakcji |
| `WORKPACK_REFERENCE_SCORE_MARGIN` | `10` | Margines niejednoznaczności |
| `WORKPACK_MAX_REFERENCE_ALTERNATIVES` | `2` | Maksymalna liczba alternatyw |
| `WORKPACK_INCLUDE_REFERENCE_ALTERNATIVES` | `true` | Dołączanie alternatyw przy remisie |
| `WORKPACK_MAX_POLISH_CANDIDATES` | `10` | Limit plików PL |
| `WORKPACK_MAX_ARCHIVE_BYTES` | `104857600` | Limit ZIP 100 MiB |
| `WORKPACK_MAX_FILES` | `100` | Limit wpisów ZIP |

Pozostałe standardowe zmienne to `APP_NAME`, `APP_HOST`, `APP_PORT` i `LOG_LEVEL`. `.env.example` zawiera wyłącznie niesekretne wartości.

## API

- `POST /api/workpacks` — tworzy `PREPARE_WORKPACK` (`mediaPath`, `taskType`).
- `GET /api/workpacks/{job_id}` — raport, ranking i metadane ZIP.
- `POST /api/workpacks/{job_id}/reference` — wybiera wyłącznie strumień zapisany w analizie.
- `GET /api/workpacks/{job_id}/download` — bezpieczne pobieranie po UUID, z nagłówkiem `X-Workpack-SHA256`.
- `GET /api/workpacks/config` — niesekretna diagnostyka limitów.
- `GET /api/jobs/{job_id}/events` — historia i zdarzenia SSE z heartbeat co 15 sekund.
- `GET /health` — stan ffmpeg i ffprobe.

W `WORKPACK` endpointy semantyczne, synchronizujące i publikujące etapów 1–5 zwracają `ADVANCED_MODE_DISABLED`. Tryb `ADVANCED` zachowuje ich kod jako opcjonalną funkcję historyczną, lecz wymaga świadomej konfiguracji; nie jest potrzebny do podstawowego zastosowania.

## Prywatność i bezpieczeństwo

Archiwum nie zawiera filmu, audio, sekretów, pełnych ścieżek hosta ani surowego raportu ffprobe. Nazwy wpisów są względne i sanityzowane; symlinki, urządzenia i traversal `../` nie są pakowane. Pliki PL zachowują bajty i kodowanie źródła. Wszystkie artefakty powstają wyłącznie w katalogu UUID pod `/data`; `/media` nie jest modyfikowane. Tryb WORKPACK nie wykonuje żadnego requestu OpenAI i nie generuje kosztów API.

## Ograniczenia

Brak OCR oznacza, że treść PGS/VobSub nie jest dostępna jako tekst. Jakość hipotez deterministycznych zależy od liczby i zgodności segmentów. Limity archiwum mogą skutkować statusem `WORKPACK_INCOMPLETE` z jawną listą ostrzeżeń.

GitHub Actions buduje i testuje obraz `linux/amd64`. Publiczne repozytorium nie gwarantuje publicznego GHCR; po pierwszej publikacji właściciel może jednorazowo ustawić pakiet jako **Public** w GitHub Packages.
