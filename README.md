# Subtitle Agent

Subtitle Agent przygotowuje mały, samowystarczalny workpack ZIP z napisami i analizą techniczną. Paczkę pobiera się z prostego GUI i przekazuje do ChatGPT w celu synchronizacji, korekty językowej, tłumaczenia albo inspekcji ścieżek. Domyślny tryb `WORKPACK` nie korzysta z OpenAI API, nie wymaga klucza i nigdy nie zapisuje w bibliotece mediów.

## Przepływ

1. Podaj absolutną ścieżkę filmu pod jednym z dozwolonych mountów `/media:ro`.
2. Wybierz „Sprawdź napisy”, „Przygotuj do synchronizacji” albo „Przygotuj do tłumaczenia”.
3. Aplikacja uruchamia ffprobe, klasyfikuje napisy i wykonuje tylko operacje potrzebne dla trybu `INSPECT`, `PREPARE_SYNC` albo `PREPARE_TRANSLATION`.
4. Pobierz ZIP i prześlij najlepiej całe archiwum do ChatGPT. `REQUEST.md` zawiera gotowe polecenie, a `manifest.json` jest źródłem danych technicznych.

GUI pokazuje logi na żywo przez SSE, ranking angielskich ścieżek, kandydatów PL, ostrzeżenia, rozmiar i SHA-256. Historia zadania i metadane pobierania pozostają w SQLite po restarcie.

## Zawartość ZIP

```text
manifest.json                 wersjonowany subtitle-workpack-v2
REQUEST.md                    instrukcja po polsku dla wybranego zadania
checksums.sha256              sumy wszystkich pozostałych wpisów
reference/selected/           wymagana angielska referencja
polish/                       wyłącznie kandydaci paczki synchronizacyjnej
analysis/                     raport v2 i pliki techniczne wymagane przez dany tryb
```

SubRip, ASS, SSA, WebVTT i mov_text otrzymują tekstową kopię SRT. PGS jest eksportowany jako `.sup` wraz z timeline pakietów, a DVD/VobSub jako kompletna para `.idx` + `.sub`. Jeżeli skonfigurowano worker CPU, graficzna referencja jest dodatkowo rozpoznawana do `selected.eng.ocr.srt`. Bez workera lub po błędzie OCR translacja kończy się `NEEDS_OCR` i zachowuje oryginalne pliki graficzne w paczce. Hipotezy synchronizacji są tylko diagnostyczne i słaby wynik nie jest przedstawiany jako gotowa synchronizacja.

Ranking preferuje angielski, pełne dialogi, format tekstowy i tytuł `Full Dialogue`. Mocno obniża ocenę commentary, forced, SDH/CC, hearing impaired, signs/songs/foreign parts oraz `Japanese Parts Only`. Gdy dwie najlepsze ścieżki dzieli mniej niż skonfigurowany margines, GUI oznacza wybór jako niejednoznaczny i pozwala przebudować paczkę z innym wykrytym strumieniem bez ponownego ffprobe.

### Tożsamość filmu i odcinka

Każdy plik otrzymuje ustrukturyzowaną tożsamość `MOVIE`, `EPISODE` albo `UNKNOWN`. Parser rozpoznaje `S01E24`, `s1e24`, `1x24`, `S01E23E24` i `S01E23-E24`. Dwa jawne identyfikatory odcinka muszą mieć dokładnie zgodny sezon, początek i koniec zakresu; wspólny tytuł serialu nigdy nie znosi konfliktu. Napisy bez identyfikatora odcinka mogą pojawić się w raporcie jako niejednoznaczne, ale nie są automatycznie dodawane do ZIP. Dla filmów porównywany jest znormalizowany tytuł oraz rok, gdy występuje po obu stronach. Raport zapisuje `matchConfidence`, `matchReasons` i informację, czy dopasowanie może być użyte automatycznie.

### Pipeline'y workpacków i raport inspekcji v2

Przygotowanie materiałów ma trzy niezależne profile. `INSPECT` tworzy raport techniczny bez ZIP-a i kończy się `INSPECTION_READY`; jego tymczasowy katalog roboczy jest usuwany po zapisaniu raportu w SQLite. `PREPARE_SYNC` wymaga angielskiej referencji oraz co najmniej jednego jednoznacznie dopasowanego kandydata PL. `PREPARE_TRANSLATION` nie wymaga napisów PL. Referencję tekstową pakuje bezpośrednio, a graficzną VobSub/PGS może rozpoznać opcjonalny worker CPU. Bez workera lub po kontrolowanym błędzie nadal powstaje pakiet `NEEDS_OCR` z oryginalną referencją. Status `WORKPACK_INCOMPLETE` wynika wyłącznie z niespełnionych wymagań wybranego profilu; ostrzeżenia diagnostyczne pozostają informacyjne.

Raport v2 zawiera tożsamość medium, parametry techniczne z dokładnym ułamkiem FPS, wszystkie ścieżki osadzone, rankingi z uzasadnieniami, zaakceptowanych i odrzuconych kandydatów PL, statystyki oraz błędy struktury SRT. Dla synchronizacji zapisuje wyłącznie hipotezę modelu wraz z liczbą kotwic, rozrzutem, pokryciem i pewnością. Pole `sufficientAnchors=false` oznacza, że wyniku nie wolno traktować jako gotowej synchronizacji.

Nowe GUI korzysta z `POST /api/tasks` z polami `mediaPath` i `mode`. Odczyt oraz pobieranie są dostępne przez `/api/tasks/{job_id}` i `/api/tasks/{job_id}/download`. Dotychczasowe `/api/workpacks` pozostaje tymczasowo zgodne dla starszych klientów.

## Uruchomienie

Wymagania deweloperskie: Python 3.12, ffmpeg/ffprobe, MKVToolNix (`mkvextract`) oraz Docker lub Podman do zbudowania opcjonalnego workera OCR.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
DATA_ROOT=./data SUBTITLE_AGENT_APP_MODE=WORKPACK uvicorn app.main:app --host 0.0.0.0 --port 8080
pytest -q
docker build --platform linux/amd64 -t subtitle-agent:local .
docker build --platform linux/amd64 -f Dockerfile.ocr -t subtitle-agent-ocr:local .
```

Obrazy produkcyjne: `ghcr.io/kcn3333/subtitle-agent:latest` oraz opcjonalny CPU-only `ghcr.io/kcn3333/subtitle-agent-ocr:latest`. Kontenery działają odpowiednio jako `10001:10001` i `10002:10002`.

### Portainer

Wybierz **Stacks → Add stack → Web editor**, wklej `compose.example.yml`, zmień tylko hostowe ścieżki `/media/movies` i `/media/shows`, a następnie wdroż. Oba mounty muszą pozostać `:ro`; `/data` jest jedynym zapisywalnym wolumenem. Worker OCR nie ma mountu do mediów ani portu hostowego, komunikuje się tylko w prywatnej sieci Compose i w spoczynku nie uruchamia Tesseracta. Domyślny stack nie zawiera `/publish`, sekretu OpenAI, mountu RW ani rozszerzonych capabilities.

## Konfiguracja WORKPACK

| Zmienna | Domyślna | Znaczenie |
|---|---:|---|
| `SUBTITLE_AGENT_APP_MODE` | `WORKPACK` | `WORKPACK` albo historyczny `ADVANCED` |
| `SUBTITLE_AGENT_MEDIA_ROOTS` | `/media/movies:/media/shows` | Dozwolone korzenie RO |
| `DATA_ROOT` | `/data` | SQLite i katalogi zadań |
| `MAX_CONCURRENT_JOBS` | `1` | Równoległe zadania |
| `FFPROBE_TIMEOUT_SECONDS` | `30` | Limit ffprobe |
| `FFMPEG_TIMEOUT_SECONDS` | `600` | Limit ekstrakcji |
| `OCR_WORKER_URL` | brak | Wewnętrzny URL workera, np. `http://subtitle-ocr-worker:8090` |
| `OCR_TIMEOUT_SECONDS` | `900` | Limit jednego zadania OCR CPU |
| `OCR_MAX_OUTPUT_BYTES` | `20971520` | Maksymalny rozmiar wyniku SRT |
| `WORKPACK_REFERENCE_SCORE_MARGIN` | `10` | Margines niejednoznaczności |
| `WORKPACK_MAX_REFERENCE_ALTERNATIVES` | `2` | Maksymalna liczba alternatyw |
| `WORKPACK_INCLUDE_REFERENCE_ALTERNATIVES` | `true` | Dołączanie alternatyw przy remisie |
| `WORKPACK_MAX_POLISH_CANDIDATES` | `10` | Limit plików PL |
| `WORKPACK_MAX_ARCHIVE_BYTES` | `104857600` | Limit ZIP 100 MiB |
| `WORKPACK_MAX_FILES` | `100` | Limit wpisów ZIP |
| `WORKPACK_RETENTION_HOURS` | `72` | Czas dostępności artefaktów ZIP; raport pozostaje w SQLite |
| `WORKPACK_CLEANUP_INTERVAL_HOURS` | `6` | Okres bezpiecznego cleanupu artefaktów |

Pozostałe standardowe zmienne to `APP_NAME`, `APP_HOST`, `APP_PORT` i `LOG_LEVEL`. `.env.example` zawiera wyłącznie niesekretne wartości.

## API

- `POST /api/workpacks` — tworzy `PREPARE_WORKPACK` (`mediaPath`, `taskType`).
- `GET /api/workpacks/{job_id}` — raport, ranking i metadane ZIP.
- `POST /api/workpacks/{job_id}/reference` — wybiera wyłącznie strumień zapisany w analizie.
- `GET /api/workpacks/{job_id}/download` — bezpieczne pobieranie po UUID, z nagłówkiem `X-Workpack-SHA256`.
- `GET /api/workpacks/config` — niesekretna diagnostyka limitów.
- `GET /api/jobs/{job_id}/events` — historia i zdarzenia SSE z heartbeat co 15 sekund.
- `GET /health` — stan ffmpeg, ffprobe i mkvextract.

W `WORKPACK` endpointy semantyczne, synchronizujące i publikujące etapów 1–5 zwracają `ADVANCED_MODE_DISABLED`. Tryb `ADVANCED` zachowuje ich kod jako opcjonalną funkcję historyczną, lecz wymaga świadomej konfiguracji; nie jest potrzebny do podstawowego zastosowania.

## Prywatność i bezpieczeństwo

Archiwum nie zawiera filmu, audio, sekretów, pełnych ścieżek hosta ani surowego raportu ffprobe. Nazwy wpisów są względne i sanityzowane; symlinki, urządzenia i traversal `../` nie są pakowane. Pliki PL zachowują bajty i kodowanie źródła. Wszystkie artefakty powstają wyłącznie w katalogu UUID pod `/data`; `/media` nie jest modyfikowane. Tryb WORKPACK nie wykonuje żadnego requestu OpenAI i nie generuje kosztów API. Worker przyjmuje wyłącznie ZIP z nazwami `selected.eng.idx`, `selected.eng.sub` albo `selected.eng.sup`; działa bez dostępu do mediów, jako użytkownik nieuprzywilejowany i zapisuje pliki tylko w tymczasowym `tmpfs`.

## Ograniczenia

OCR opiera się na przypiętym tagu `seconv v5.2.0-rc2` (commit `b236dc5bb369e92b2b5b996dc246ac6d4c632f2c`, zweryfikowany SHA-256 artefaktu) i Tesseract 5 z angielskimi danymi językowymi. Jest przeznaczony do pracy na CPU; nietypowe fonty i słabe bitmapy nadal mogą wymagać korekty w ChatGPT. Każdy udany wynik otrzymuje niezależne oceny `structuralQuality` i `textQuality` (`GOOD`, `WARNING`, `POOR` albo `UNKNOWN`) oraz `analysis/ocr-quality-report.json`; poprawna struktura nie jest deklaracją poprawności językowej. Niepoprawny strukturalnie SRT powoduje bezpieczny powrót do `NEEDS_OCR`. Jakość hipotez deterministycznych zależy od liczby i zgodności segmentów. Limity archiwum mogą skutkować statusem `WORKPACK_INCOMPLETE` z jawną listą ostrzeżeń.

GitHub Actions buduje i testuje obraz `linux/amd64`. Publiczne repozytorium nie gwarantuje publicznego GHCR; po pierwszej publikacji właściciel może jednorazowo ustawić pakiet jako **Public** w GitHub Packages.
