# Subtitle Agent

Lekka usługa do bezpiecznej analizy mediów i napisów. Etap 5 dodaje jawnie włączane, wersjonowane publikowanie zatwierdzonego preview obok filmu. Analiza nadal korzysta wyłącznie z mountów `/media:ro`; wydzielony publisher jako jedyny może zapisać nowy plik przez osobny widok `/publish:rw`.

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
| `ALIGNMENT_MIN_SCALE` / `ALIGNMENT_MAX_SCALE` | `0.94` / `1.06` | Bezpieczny zakres współczynnika affine |
| `ALIGNMENT_MAX_SEGMENTS` | `3` | Maksymalna liczba odcinków piecewise |
| `ALIGNMENT_MIN_POINTS_PER_SEGMENT` | `4` | Minimalna liczba punktów w odcinku |
| `ALIGNMENT_END_TOLERANCE_MS` | `1000` | Tolerancja końca filmu w milisekundach |
| `OPENAI_SEMANTIC_ALIGNMENT_ENABLED` | `false` | Zezwala na semantyczne dopasowanie; sam klucz nie włącza funkcji |
| `OPENAI_MODEL` | `gpt-5.6-terra` | Model ustalony przez administratora, nie klienta HTTP |
| `OPENAI_REASONING_EFFORT` | `low` | Poziom reasoning (`none`–`xhigh`) |
| `OPENAI_TIMEOUT_SECONDS` | `90` | Limit pojedynczego żądania |
| `OPENAI_MAX_RETRIES` | `3` | Retry tylko dla timeout/connection/429/wybranych 5xx |
| `OPENAI_MAX_REQUESTS_PER_JOB` | `24` | Łączny limit prób w zadaniu |
| `OPENAI_MAX_INPUT_TOKENS_PER_JOB` | `120000` | Budżet tokenów wejścia |
| `OPENAI_MAX_OUTPUT_TOKENS_PER_JOB` | `12000` | Budżet tokenów wyjścia |
| `OPENAI_MAX_CONCURRENT_REQUESTS` | `2` | Wspólny limit równoległych żądań procesu |
| `OPENAI_SEMANTIC_WINDOW_SIZE` / `OPENAI_SEMANTIC_WINDOW_OVERLAP` | `18` / `4` | Rozmiar i nakładanie kontrolowanych okien |
| `OPENAI_MIN_CONFIDENCE` | `0.72` | Minimalne confidence kandydata |
| `OPENAI_API_KEY_FILE` | brak | Plik sekretu; ma pierwszeństwo przed `OPENAI_API_KEY` |
| `OPENAI_API_KEY` | brak | Sekret z otoczenia, używany tylko bez pliku sekretu |
| `SUBTITLE_AGENT_PUBLISH_ENABLED` | `false` | Główny przełącznik publikacji |
| `SUBTITLE_AGENT_PUBLISH_MODE` | `PREVIEW_ONLY` | `PREVIEW_ONLY`, `MANUAL` albo `AUTO_HIGH` |
| `SUBTITLE_AGENT_PUBLISH_MAPPINGS_JSON` | mapowanie movies/shows | Jednoznaczny obiekt JSON z rootów `/media` do `/publish` |
| `SUBTITLE_AGENT_AUTO_PUBLISH_MIN_QUALITY` | `HIGH` | Udokumentowany minimalny poziom automatyczny; `AUTO_HIGH` zawsze wymaga HIGH |
| `SUBTITLE_AGENT_AUTO_PUBLISH_REQUIRE_SEMANTIC` | `true` | Wymaga zaakceptowanych kotwic semantycznych bez fallbacku |
| `SUBTITLE_AGENT_PUBLISH_MAX_VERSION` | `999` | Najwyższa wersja `vNNN` |
| `SUBTITLE_AGENT_PUBLISH_FILE_MODE` | `0644` | Tryb wyłącznie nowo utworzonego pliku |

`.env.example` zawiera wyłącznie pusty placeholder. Brak klucza albo wyłączona funkcja nie blokuje startu ani etapów 2–3. `GET /api/jobs/semantic/config` pokazuje tylko stan, model i niesekretne limity; nie wykonuje żądania API.

### OpenAI, prywatność i tryby

Funkcja korzysta z płatnego zewnętrznego API. Każde żądanie Responses API jest niezależne, używa Structured Outputs i `store=false`. Wysyłane są tylko losowy identyfikator partii, techniczne ID i kolejność cue, znormalizowany tekst oraz względna pozycja. Nie są wysyłane ścieżki, multimedia, raport ffprobe, logi, konfiguracja serwera ani dane innych zadań. Prompt `semantic-anchor-v1` traktuje tekst napisów jako niezaufane dane i zabrania wykonywania zawartych w nim instrukcji. Pełne prompty i surowe odpowiedzi nie trafiają do logów ani SQLite.

Tryb `STRUCTURAL_ONLY` nigdy nie wywołuje API. `SEMANTIC_PREFERRED` próbuje semantyki, ale jawnie zapisuje fallback do kotwic lokalnych po kontrolowanym błędzie. `SEMANTIC_REQUIRED` bez poprawnych kotwic kończy zadanie jako `AI_UNAVAILABLE` albo `AI_BUDGET_EXCEEDED` i nie tworzy wyniku tylko strukturalnego. Telemetria w raporcie i GUI obejmuje żądania, retry, tokeny wejściowe/cache/wyjściowe/razem, czas API, przyjęte i odrzucone kotwice oraz fallback — bez szacowania ceny.

Provider wykonuje dwa przebiegi ograniczonych okien (początek, środek, koniec i długie przerwy), waliduje lokalnie identyfikatory, liczność, ciągłość, ponowne użycie, monotoniczność, skoki względem modelu strukturalnego i konflikty nakładających się odpowiedzi. Grupa daje jedną reprezentatywną kotwicę wyliczoną z lokalnych czasów. Zweryfikowane kotwice semantyczne mają pierwszeństwo, a deterministyczny silnik nadal sam wybiera model i przelicza timestampy.

W Portainerze utwórz sekret `openai_api_key` i zamontuj go jako `/run/secrets/openai_api_key`, zgodnie z `compose.example.yml`. W środowisku bez obsługi sekretów można ustawić `OPENAI_API_KEY` w chronionych zmiennych stosu (nigdy w pliku repozytorium), usuwając `OPENAI_API_KEY_FILE`. Pusty lub nieczytelny plik sekretu celowo zatrzymuje start.

### Bezpieczne publikowanie i Portainer

Domyślny `PREVIEW_ONLY` niczego nie zapisuje w bibliotece i działa bez `/publish`. Najpierw wdroż aplikację w tym trybie, sprawdź analizę oraz OpenAI, a następnie na hoście ustal prawa:

```bash
stat -c '%u:%g %A %n' /media/movies /media/shows
findmnt -T /media/movies
findmnt -T /media/shows
```

Kontener pozostaje UID/GID `10001:10001`. Nie wykonuje `chown`, `chmod` katalogów ani zmian ACL. Jeżeli zapis zapewnia grupa katalogu, dodaj w Portainerze `group_add` z faktycznym GID odczytanym na hoście — dokumentacja celowo nie zgaduje tej wartości.

Po sprawdzeniu uprawnień odkomentuj opcjonalne i wrażliwe mounty w `compose.example.yml`:

```yaml
- /media/movies:/publish/movies:rw
- /media/shows:/publish/shows:rw
```

Widoki `/media/...:ro` muszą pozostać bez zmian. Uruchom diagnostykę `GET /api/jobs/publishing/config`, włącz `MANUAL`, opublikuj jeden testowy plik i sprawdź go w Jellyfinie. Dopiero później rozważ `AUTO_HIGH`. Jellyfin może wykryć plik przez monitoring systemu, ale czasami potrzebny jest ręczny skan biblioteki; aplikacja nie wywołuje Jellyfin API.

Publisher wybiera najdłuższy pasujący media root, zachowuje względny katalog filmu i używa dokładnego rdzenia nazwy wideo: `<media_stem>.AI-Sync-v001.pl.srt`. Sprawdza, czy widoki RO/RW mają te same `st_dev` i `st_ino`, nie przechodzi przez symlink poza root i nie tworzy katalogów. Pierwszy wolny numer jest rezerwowany po blokadzie per film; symlink także jest kolizją.

Zapis odbywa się do losowego ukrytego pliku przez `O_CREAT|O_EXCL`, następnie `flush`, `fsync`, ustawienie trybu i hard link do nieistniejącej nazwy, po czym `fsync` katalogu. Nie używa `replace` ani nadpisującego `rename`. Gdy system plików nie wspiera bezpiecznego hard linku, wynik to `PUBLISH_UNSUPPORTED_FILESYSTEM`, a preview zostaje w `/data`.

W `MANUAL` dopuszczone są tylko HIGH/MEDIUM bez krytycznych ostrzeżeń. `AUTO_HIGH` wymaga HIGH, zgodnych hashy i tożsamości źródeł, pełnej walidacji oraz — domyślnie — kotwic semantycznych bez fallbacku. Powtórzenie zakończonej publikacji jest idempotentne; brak lub zmiana opublikowanego pliku daje `PUBLISH_CONFLICT`, nigdy nadpisanie.

## API

- `GET /` — GUI.
- `GET /health` — gotowość aplikacji i narzędzi.
- `POST /api/jobs` — tworzy trwałe zadanie analizy (`{"mediaPath":"/media/shows/example.mkv"}`).
- `GET /api/jobs` — lista ostatnich zadań.
- `GET /api/jobs/{job_id}` — stan i pełny raport zadania.
- `GET /api/jobs/{job_id}/events` — historia i zdarzenia SSE; `Last-Event-ID` umożliwia wznowienie, heartbeat pojawia się po 15 s ciszy.
- `POST /api/jobs/{job_id}/alignment` — synchronizuje wyłącznie źródła zapisane w raporcie zadania.
- `GET /api/jobs/semantic/config` — bezpieczna diagnostyka aktywacji, modelu i limitów bez sekretu i bez płatnego requestu.
- `GET /api/jobs/publishing/config` — niesekretna diagnostyka trybu, mapowań i praw procesu; nic nie zapisuje w katalogu filmu.
- `GET /api/jobs/{job_id}/publication/preview` — przewidywana nazwa i powód blokady, wyliczane po stronie serwera.
- `POST /api/jobs/{job_id}/publication` — idempotentna publikacja przyjmująca tylko potwierdzenie i oczekiwany SHA-256 preview.
- `GET /api/jobs/{job_id}/preview` — pobiera roboczy podgląd SRT bez przyjmowania ścieżki.

Zdarzenia mają rosnącą sekwencję, czas ze strefą, etap, poziom, wiadomość i postęp 0–100. SQLite pracuje w WAL; maksymalnie 500 zdarzeń pozostaje na zadanie. Zadania niedokończone podczas restartu stają się `INTERRUPTED`. Raport obejmuje dane techniczne, ścieżki napisów, analizę zewnętrznych SRT, rankingi z powodami punktacji, wybory oraz ostrzeżenia.

## GHCR i CI

GitHub Actions buduje `linux/amd64`, testuje `/health`, `ffmpeg` i `ffprobe`, a poza pull requestem publikuje `latest`, `sha-*` i tagi semver przy użyciu `GITHUB_TOKEN` — bez PAT i sekretów aplikacji. Publiczny obraz można pobierać anonimowo. Po pierwszej publikacji może być konieczne jednorazowe ustawienie pakietu jako **Public** w GitHub Packages; publiczne repozytorium nie gwarantuje automatycznie publicznego pakietu GHCR.

## Bezpieczeństwo i ograniczenia etapu 5

Kontener działa jako UID/GID 10001, bez capabilities i z `no-new-privileges`. Publisher nigdy nie usuwa ani nie nadpisuje SRT, filmu, NFO lub katalogu. Audyt SQLite zapisuje czas, tryb, wynik, jakość, ścieżki RO/RW, wersję, hashe, rozmiar, tożsamość źródła i niesekretny błąd — bez treści napisów i klucza. Nadal brak OCR oraz Jellyfin API. Standardowe testy nie wykonują płatnych żądań ani zapisu do rzeczywistego `/media`.

## Kolejne etapy

Kolejny etap może dodać OCR albo opcjonalne, uwierzytelnione odświeżenie Jellyfina. Żadna z tych funkcji nie należy do etapu 5.
