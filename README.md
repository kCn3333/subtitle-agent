# Subtitle Agent

Subtitle Agent analizuje napisy filmu lub odcinka i przygotowuje ZIP do synchronizacji albo tłumaczenia. Biblioteka mediów jest montowana tylko do odczytu, a aplikacja nie wymaga klucza OpenAI.

Gotowy workpack do synchronizacji można przekazać do niezależnego modelu przez API zgodne z Chat Completions. W `/settings` ustaw adres API, nazwę modelu, opcjonalny klucz i timeout. Aplikacja sprawdza zwrócone czasy po ID, zachowuje tekst PL i udostępnia wynik SRT oraz czas żądania. [Instrukcja konfiguracji i testowania](docs/ai-api-sync.md).

## Co potrafi

- sprawdza parametry materiału i dostępne ścieżki napisów;
- wybiera wbudowaną angielską referencję; ranking preferuje PGS, uwzględniając kary za ścieżki częściowe, komentarze i SDH;
- przy kilku źródłach EN pozwala ręcznie wybrać wzorzec i ponownie przygotować paczkę;
- przy braku wbudowanej referencji proponuje zewnętrzne napisy EN i wymaga potwierdzenia użytkownika (język SRT rozpoznaje także z treści, bez oznaczenia w nazwie);
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
| `WORKPACK_CLEANUP_INTERVAL_HOURS` | `6` | Okresowe porządkowanie danych (także przy starcie i tworzeniu zadania) |

Pełny zestaw bezpiecznych wartości znajduje się w `.env.example` i `compose.example.yml`.

## OCR

OCR działa na CPU przy użyciu Tesseracta i przypiętego `seconv v5.2.0-rc2`. Obsługiwane są referencje PGS (`.sup`) i DVD/VobSub (`.idx` + `.sub`).

Podczas OCR konsola pokazuje jeden aktualizowany wiersz z informacją o pracy i czasem oczekiwania. Worker nie udostępnia procentu postępu. Utrata połączenia z aplikacją jest oznaczana osobno.

Wynik zawiera osobne oceny:

- `structuralQuality` — poprawność SRT i zgodność timestampów;
- `textQuality` — podejrzane błędy rozpoznanego tekstu.

Walidacja PGS uwzględnia zdarzenia pokazania i ukrycia bitmapy: stosunek liczby segmentów OCR do zdarzeń równy 0,5 sam w sobie nie obniża oceny. Porównuje pierwszy timestamp oraz koniec ostatniego segmentu OCR z ostatnim zdarzeniem PGS. Raport zachowuje surowy i oceniany stosunek liczników; dla indeksu VobSub nadal porównywane są początki segmentów.

Przed zapisaniem SRT normalizowane są jednoznaczne formy `|` → `I`, `-|` → `-I` i skróty takie jak `|'ve` → `I've`. Numery i timestampy pozostają bez zmian. Raport zawiera liczbę poprawek (`normalization.pipeToICount`) i pozostawionych niejednoznacznych znaków (`unresolvedPipeCount`).

W `PREPARE_SYNC` referencja graficzna jest automatycznie wysyłana do skonfigurowanego workera. Wynik `reference/selected/selected.eng.ocr.srt` służy do analizy synchronizacji; ZIP zawiera też `analysis/ocr-quality-report.json`. Źródłowy strumień jest opisany w `reference.ocrSource` manifestu. Techniczny timeline pakietów służy wyłącznie ocenie OCR.

Domyślnie ZIP synchronizacji nie zawiera plików graficznych. `INCLUDE_GRAPHIC_REFERENCE=true` dołącza oryginalny `.sup` lub parę `.idx`/`.sub`. Oryginały pozostają w katalogu roboczym do czasu jego usunięcia przez retencję. Ta opcja nie zmienia paczek do tłumaczenia.

OCR może wymagać korekty językowej. Gdy worker jest niedostępny lub wynik jest niepoprawny, aplikacja zachowuje oryginalną referencję graficzną w katalogu roboczym i zwraca status `NEEDS_OCR` z czytelnym opisem przyczyny zamiast błędu całego zadania. W synchronizacji bez poprawnego OCR nie są generowane hipotezy z pakietów graficznych.

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
- **Analiza mediów:** `ffprobe`; ekstrakcja tekstu i PGS przez FFmpeg. Metadane mają limit `FFPROBE_TIMEOUT_SECONDS` (domyślnie 30 s), a odczyt pakietów napisów graficznych z całego filmu korzysta z większego z limitów `FFPROBE_TIMEOUT_SECONDS` i `FFMPEG_TIMEOUT_SECONDS` (domyślnie 600 s). Eksportowana jest tylko wybrana referencja; alternatywy pozostają w rankingu do ręcznego wyboru.
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
GET  /api/settings/ai/health          łączność z zapisanym API AI
GET  /health                          stan aplikacji i narzędzi CLI
```

## Rozwój i testy

Wymagane są Python 3.12 oraz Docker lub Podman.

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
node --test tests/*.test.cjs
docker build --target test -t subtitle-agent:test .
```

### Archiwum i retencja

Strona `/archive` pokazuje 30 ostatnich filmów/odcinków według daty utworzenia zadania. Tytuł identyfikuje ścieżka materiału; kolejne zadania tego samego pliku zajmują jeden wiersz. Workpacki są deduplikowane według SHA-256, a poprawne wyniki SRT pozostają dostępne również po ponownym uruchomieniu synchronizacji. Tabela rozróżnia synchronizację i tłumaczenie; paczka do tłumaczenia sama nie jest wygenerowanym SRT.

W `DATA_ROOT` (np. katalog hosta `/dane` zamontowany jako `/data`) baza, raporty, zdarzenia, pliki robocze i archiwalne obejmują ostatnie 30 tytułów. Po przekroczeniu limitu usuwane są dane starszych tytułów i nieużywane pliki archiwalne. Aktywne zadania i żądania AI kończą pracę przed usunięciem swoich danych. Porządkowanie działa przy starcie, tworzeniu i zakończeniu zadania oraz okresowo. Dotychczasowy limit 72 godzin nie obowiązuje; `WORKPACK_RETENTION_HOURS` pozostaje akceptowany dla zgodności ze starszą konfiguracją.

Przy pierwszym starcie archiwum przejmuje dostępne workpacki i poprawne wyniki synchronizacji. Wcześniej usuniętych artefaktów nie odtwarza. Pliki archiwum znajdują się w `archive/`, z nazwami według SHA-256; źródłowa biblioteka mediów pozostaje tylko do odczytu. Font Awesome Free jest dostarczany lokalnie w `app/static/fontawesome/` razem z licencją.

### Komunikaty i łączność AI

Każda operacja zaczyna się w konsoli nazwą czynności i czytelnym tytułem filmu lub odcinka. Czas żądań, tokeny i koszt API są pokazywane w konsolach; wyróżnione podsumowanie obejmuje również niepoprawne odpowiedzi modelu. Panele wyników pokazują dostępność SRT bez kosztów i tokenów.

Wskaźnik API obok OCR odświeża się co 30 sekund na podstawie zapisanych ustawień. Sprawdza odczytowy endpoint `/models`, a jeśli jest niedostępny — odpowiedź na `HEAD /chat/completions`, bez uruchamiania modelu. Zielony wskaźnik potwierdza odpowiedź serwera, nie możliwość wykonania synchronizacji. Przycisk testu połączenia sprawdza skonfigurowany model rzeczywistym żądaniem.

Kolumna „Koszt AI” w archiwum sumuje tokeny i koszty USD wszystkich zapisanych żądań synchronizacji i tłumaczenia dla danego tytułu, również błędnych odpowiedzi i ponownych prób. Testy połączenia nie są przypisywane do filmu. Metryki są przechowywane niezależnie od konsoli i usuwane razem z tytułem po przekroczeniu limitu 30. Brak metryk oznacza „—”, a częściowa suma jest oznaczona „≥”. Przy aktualizacji aplikacja przejmuje dostępne metryki ze starej konsoli lub ostatniego wyniku; wcześniej usuniętych zapisów nie da się odtworzyć.
