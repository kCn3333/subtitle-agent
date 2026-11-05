# Subtitle Agent

Subtitle Agent analizuje napisy filmu lub odcinka i przygotowuje ZIP do synchronizacji albo tłumaczenia. Biblioteka mediów jest montowana tylko do odczytu, a aplikacja nie wymaga klucza OpenAI.

Gotowy workpack do synchronizacji można przekazać do niezależnego modelu przez API zgodne z Chat Completions. W `/settings` ustaw adres API, nazwę modelu, opcjonalny klucz i timeout. Aplikacja sprawdza zwrócone czasy po ID, zachowuje tekst PL i udostępnia wynik SRT oraz czas żądania. [Instrukcja konfiguracji i testowania](docs/ai-api-sync.md).

## Co potrafi

- sprawdza parametry materiału i dostępne ścieżki napisów;
- wybiera wbudowaną referencję (preferuje język angielski, a przy jego braku inne języki); ranking preferuje PGS, uwzględniając kary za ścieżki częściowe, komentarze i SDH;
- przy kilku źródłach, także w innych językach, pozwala ręcznie wybrać wzorzec i ponownie przygotować paczkę;
- przy braku wbudowanej referencji proponuje zewnętrzne napisy i wymaga potwierdzenia użytkownika (język SRT rozpoznaje także z treści, bez oznaczenia w nazwie);
- wykrywa niezgodne polskie napisy i inne wersje materiału;
- eksportuje napisy tekstowe, PGS oraz DVD/VobSub;
- opcjonalnie wykonuje OCR napisów graficznych na CPU;
- tworzy ZIP z manifestem, raportem, sumami SHA-256 i potrzebnymi napisami.

Tryby w GUI:

- **Sprawdź napisy** — raport bez ZIP-a;
- **Przygotuj do synchronizacji** — wymaga zgodnej referencji źródłowej i kandydata PL;
- **Przygotuj do tłumaczenia** — wymaga referencji źródłowej, ale nie napisów PL.

## Uruchomienie w Portainerze

1. Otwórz **Stacks → Add stack → Web editor**.
2. Wklej zawartość `compose.example.yml`.
3. Zmień hostowe ścieżki `/media/movies` i `/media/shows`.
4. Ustaw zmienne `AUTH_USERNAME`, `AUTH_PASSWORD`, `AUTH_PUBLIC_URL` i `AUTH_TRUSTED_PROXIES` zgodnie z sekcją logowania poniżej.
5. Pozostaw mounty mediów jako `:ro`, skonfiguruj reverse proxy z HTTPS i wdroż stack.

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
| `AUTH_USERNAME` / `AUTH_PASSWORD` | wymagane | Dane logowania z Compose; hasło minimum 16 znaków |
| `AUTH_PUBLIC_URL` | wymagany | Publiczny adres HTTPS bez ścieżki |
| `AUTH_TRUSTED_PROXIES` | puste | Zaufane adresy/CIDR reverse proxy |
| `AUTH_LOCKOUT_SECONDS` | `3600` | Blokada IP po trzeciej błędnej próbie |
| `AUTH_SESSION_SECONDS` | `28800` | Maksymalny czas sesji |
| `DATA_ROOT` | `/data` | Baza i pliki robocze |
| `SUBTITLE_AGENT_MEDIA_ROOTS` | `/media/movies:/media/shows` | Dozwolone katalogi mediów |
| `MAX_CONCURRENT_JOBS` | `1` | Liczba równoległych operacji przygotowania i przebudowy |
| `MAX_QUEUED_JOBS` | `5` | Limit oczekujących operacji przygotowania i przebudowy |
| `MAX_QUEUED_AI_OPERATIONS` | `5` | Limit oczekujących operacji AI; wykonywana jest jedna naraz |
| `OCR_WORKER_URL` | brak | Np. `http://subtitle-ocr-worker:8090` |
| `OCR_TIMEOUT_SECONDS` | `900` | Limit czasu OCR |
| `WORKPACK_MAX_ARCHIVE_BYTES` | `104857600` | Maksymalny rozmiar ZIP |
| `WORKPACK_CLEANUP_INTERVAL_HOURS` | `6` | Okresowe porządkowanie danych (także przy starcie i zakończeniu operacji) |

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
reference/selected/    wybrana referencja źródłowa
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

W `DATA_ROOT` (np. katalog hosta `/dane` zamontowany jako `/data`) baza, raporty, zdarzenia, pliki robocze i archiwalne obejmują ostatnie 30 tytułów. Po przekroczeniu limitu usuwane są dane starszych tytułów i nieużywane pliki archiwalne. Aktywne zadania i żądania AI kończą pracę przed usunięciem swoich danych. Porządkowanie działa przy starcie, zakończeniu zadania oraz okresowo. Dotychczasowy limit 72 godzin nie obowiązuje; `WORKPACK_RETENTION_HOURS` pozostaje akceptowany dla zgodności ze starszą konfiguracją.

Przy pierwszym starcie archiwum przejmuje dostępne workpacki i poprawne wyniki synchronizacji. Wcześniej usuniętych artefaktów nie odtwarza. Pliki archiwum znajdują się w `archive/`, z nazwami według SHA-256; źródłowa biblioteka mediów pozostaje tylko do odczytu. Font Awesome Free jest dostarczany lokalnie w `app/static/fontawesome/` razem z licencją.

### Komunikaty i łączność AI

Każda operacja zaczyna się w konsoli nazwą czynności i czytelnym tytułem filmu lub odcinka. Czas żądań, tokeny i koszt API są pokazywane w konsolach; wyróżnione podsumowanie obejmuje również niepoprawne odpowiedzi modelu. Panele wyników pokazują dostępność SRT bez kosztów i tokenów.

Wskaźnik API obok OCR odświeża się co 30 sekund na podstawie zapisanych ustawień. Sprawdza odczytowy endpoint `/models`, a jeśli jest niedostępny — odpowiedź na `HEAD /chat/completions`, bez uruchamiania modelu. Zielony wskaźnik potwierdza odpowiedź serwera, nie możliwość wykonania synchronizacji. Przycisk testu połączenia sprawdza skonfigurowany model rzeczywistym żądaniem.

Kolumna „Koszt AI” w archiwum sumuje tokeny i koszty USD wszystkich zapisanych żądań synchronizacji i tłumaczenia dla danego tytułu, również błędnych odpowiedzi i ponownych prób. Testy połączenia nie są przypisywane do filmu. Metryki są przechowywane niezależnie od konsoli i usuwane razem z tytułem po przekroczeniu limitu 30. Brak metryk oznacza „—”, a częściowa suma jest oznaczona „≥”. Przy aktualizacji aplikacja przejmuje dostępne metryki ze starej konsoli lub ostatniego wyniku; wcześniej usuniętych zapisów nie da się odtworzyć.

Stan pracy AI jest dostępny pod `/api/tasks/{job_id}/ai-status`. Strona główna odczytuje go co 2 sekundy, odtwarzając animowany wskaźnik w konsoli, fazę i czas oczekiwania również po powrocie z ustawień lub odświeżeniu strony. Podczas oczekiwania w kolejce i aktywnej operacji przycisk przekazania, wybór PL i zmiana referencji są zablokowane. Po zakończeniu GUI pokazuje wynik lub błąd i podsumowanie; przy utracie łączności blokuje wysyłanie do czasu potwierdzenia stanu serwera. Wskaźnik oznacza trwające żądanie, a nie procentowy postęp generowania modelu.

Ustawienie reasoning oferuje domyślne zachowanie (pominięcie parametru), `none`, `minimal`, `low`, `medium`, `high`, `xhigh` i `max`. Nie każdy model obsługuje wszystkie poziomy; Gemini 3.8 Flash obsługuje `low`, `medium`, `high` i wymaga reasoning. Dla OpenRouter aplikacja wysyła `reasoning.effort`, a do innych endpointów `reasoning_effort`. Opcjonalny limit wyjścia wysyłany jest jako `max_tokens`; puste pole pomija parametr. Budżet obejmuje reasoning i finalny wynik, nie może przekroczyć limitu modelu lub dostawcy. Te ustawienia obowiązują w teście połączenia, synchronizacji i tłumaczeniu.

Wyniki AI są pobierane jako `<nazwa pliku filmu bez rozszerzenia>.AI-Synced-v001.pl.srt` (synchronizacja) lub `.AI-Translated-v001.pl.srt` (tłumaczenie). Prefiks zachowuje nazwę materiału dla dopasowania napisów w Jellyfin. Każdy nowy wynik o innym SHA otrzymuje kolejną wersję w obrębie tytułu i trybu; identyczny wynik zachowuje dotychczasową nazwę. Numeracja obejmuje kolejne zadania i restarty. Pobieranie z archiwum używa tej samej nazwy; istniejące wpisy SRT otrzymują ją przy starcie aplikacji, bez zmiany treści plików.

Referencją synchronizacji lub tłumaczenia mogą być także napisy francuskie i inne wykryte języki. Wybierz ścieżkę w polu „Wybierz wykrytą referencję” i użyj „Zbuduj ponownie z tą referencją”, a następnie „Przekaż do AI”. Napisy zewnętrzne wymagają potwierdzenia. Worker OCR zawiera dane eng, fra, deu, spa, ita, por i pol; brak danych wybranego języka daje czytelny błąd zamiast OCR po angielsku. Zmiana języków OCR wymaga przebudowania obrazu workera.

## Logowanie i dostęp z Internetu

Logowanie jest obowiązkowe dla stron, ustawień, API, SSE i pobierania plików. W Compose ustaw `AUTH_USERNAME`, `AUTH_PASSWORD` (unikalne hasło, minimum 16 znaków) i `AUTH_PUBLIC_URL`, np. `https://subtitles.example.invalid`. Brak danych logowania zatrzymuje start aplikacji. W Portainerze wpisz je jako zmienne środowiskowe stacka; przy lokalnym Compose możesz skopiować `.env.example` do `.env`. Nie zapisuj prawdziwych haseł w repozytorium.

Przed publicznym uruchomieniem skonfiguruj reverse proxy z HTTPS i zachowaniem nagłówka `Host`. `AUTH_PUBLIC_URL` musi dokładnie odpowiadać adresowi w przeglądarce, bez ścieżki. Domyślnie port hosta jest dostępny tylko pod `127.0.0.1:8080`; nie udostępniaj portów aplikacji ani workera OCR z pominięciem proxy. Gdy proxy działa w osobnym kontenerze, połącz je z aplikacją prywatną siecią Docker i usuń publikowanie portu hosta.

`AUTH_TRUSTED_PROXIES` to rozdzielona przecinkami lista rzeczywistych adresów lub wąskich podsieci proxy widzianych przez kontener aplikacji. Pozostawienie pustej wartości oznacza używanie adresu bezpośredniego połączenia — za proxy wszyscy użytkownicy mogą wtedy współdzielić jedną blokadę. Dla połączeń od zaufanego proxy wymagany jest `X-Forwarded-For`. Aplikacja odczytuje łańcuch od prawej do pierwszego niezaufanego adresu, ignorując dopisane po lewej adresy klienta. Proxy brzegowe musi nadpisywać nagłówek wartością ustaloną z połączenia. Nie ufaj całej sieci Internet ani nagłówkom od niezaufanych nadawców. Obraz uruchamia Uvicorn z `--no-proxy-headers`, aby zachować rzeczywisty adres połączenia; nie usuwaj tej opcji.

Przykładowa konfiguracja Caddy działającego na hoście (podmień domenę):

```caddyfile
subtitles.example.invalid {
    reverse_proxy 127.0.0.1:8080 {
        header_up X-Forwarded-For {remote_host}
    }
}
```

Jeśli przed Caddy działa dodatkowy pośrednik, np. Cloudflare, skonfiguruj osobno jego weryfikację i ustalanie prawdziwego IP w proxy. Nie przekazuj bez weryfikacji `CF-Connecting-IP` od dowolnego klienta. Sprawdź przed publikacją, czy blokada dotyczy właściwego IP, a inny klient nadal może się zalogować.

Cloudflare Tunnel może łączyć się bezpośrednio z aplikacją, bez Caddy/Nginx. Gdy `cloudflared` działa na innym hoście, jako usługę tunelu ustaw `http://<adres-LAN-hosta-aplikacji>:8080`. W Compose zamień przypisanie portu do `127.0.0.1` na `<adres-LAN-hosta-aplikacji>:8080:8080` i ogranicz dostęp do tego portu do hosta tunelu regułami firewalla uwzględniającymi Docker. Nie otwieraj portu na Internet.

`AUTH_PUBLIC_URL` pozostaje publicznym adresem HTTPS, a `AUTH_COOKIE_SECURE=true`. Zachowaj publiczną domenę w nagłówku `Host` (w tunelu można ustawić HTTP Host Header). Do `AUTH_TRUSTED_PROXIES` wpisz rzeczywisty adres źródłowy połączenia widziany przez aplikację. Przy standardowym NAT sieci bridge Docker będzie to zwykle adres LAN hosta tunelu z maską `/32`, a nie wewnętrzny adres jego kontenera ani publiczne zakresy Cloudflare. Potwierdź adres źródłowy i działanie blokady przed publikacją; pozostaw przekazywanie `X-Forwarded-For` przez tunel.

Po trzech błędnych logowaniach IP jest blokowane na **3600 sekund**; blokada obejmuje również dostęp do chronionych zasobów. Próby w trakcie blokady nie przedłużają jej. Nieudane próby są liczone w okresie bezczynności równym czasowi blokady; poprawne logowanie zeruje licznik. Liczniki i blokady są w SQLite, więc restart ich nie usuwa. `AUTH_LOCKOUT_SECONDS` pozwala zmienić czas. Jeśli trzeba odblokować adres wcześniej, administrator może zatrzymać aplikację i usunąć jego rekord z tabeli `auth_failures` w bazie `/data/subtitle-agent.db`, wykonując wcześniej kopię bazy.

Bez zaznaczenia dodatkowej opcji sesje wygasają po `AUTH_SESSION_SECONDS` (domyślnie 8 godzin). Zaznaczenie **Zapamiętaj mnie na 30 dni** podczas logowania ustawia ważność sesji i cookie na 30 dni od tego logowania; termin nie przedłuża się przy korzystaniu z aplikacji. Przycisk **Wyloguj** unieważnia sesję po stronie serwera, również zapamiętaną na 30 dni. Zmiana użytkownika lub hasła w Compose i restart unieważniają poprzednie sesje. Cookie sesji ma `HttpOnly`, `Secure` i `SameSite=Strict`; żądania zmieniające dane wymagają tokenu CSRF. Klienci API również muszą się zalogować i przekazywać token CSRF ze strony głównej. `/health` ujawnia publicznie tylko `{"status":"ok"}`; dokumentacja OpenAPI jest wyłączona. Testy lokalne przez HTTP wymagają jawnego `AUTH_COOKIE_SECURE=false` oraz zgodnego `AUTH_PUBLIC_URL=http://...`; dla dostępu z Internetu pozostaw `true` i HTTPS.

Ochrona logowania nie zastępuje aktualizacji obrazu, zabezpieczenia hosta, kopii `/data` ani limitów ruchu w reverse proxy. `/data` zawiera ustawienia i klucze API — ogranicz dostęp do wolumenu i kopii zapasowych. Uruchamiaj jedną instancję aplikacji; kolejka zadań pozostaje lokalna dla procesu.


## Kolejki operacji

W trybie WORKPACK analiza, ekstrakcja, OCR i pakowanie, także po zmianie referencji, korzystają ze wspólnej kolejki przygotowania. Domyślnie pracuje jedna operacja, a pięć kolejnych może oczekiwać. Ustawienia to `MAX_CONCURRENT_JOBS` i `MAX_QUEUED_JOBS`. Ponowne zgłoszenie tego samego materiału i trybu podczas przygotowania zwraca istniejące zadanie; powtórna identyczna przebudowa również nie dodaje nowej pracy.

Synchronizacja AI, tłumaczenie AI i test połączenia z modelem korzystają z osobnej kolejki: jedna operacja działa, do pięciu oczekuje (`MAX_QUEUED_AI_OPERATIONS`). Przygotowanie kolejnego filmu może trwać podczas oczekiwania na odpowiedź modelu. Model, adres API, klucz i parametry AI są pobierane w momencie przyjęcia zlecenia; późniejsza zmiana ustawień nie zmienia oczekującej operacji. Klucz nie jest zapisywany w rekordzie kolejki ani zwracany przez endpoint statusu.

Wspólna rezerwacja zadania uniemożliwia zmianę jego referencji podczas oczekiwania i wykonywania AI. Powtórne wysłanie tej samej operacji AI zwraca jej istniejący identyfikator; inne parametry dla zajętego zadania dają HTTP `409`. Pełna kolejka daje HTTP `429` z kodem `QUEUE_FULL` i nagłówkiem `Retry-After`. Pliki zadań oczekujących na AI i przebudowę są chronione przed porządkowaniem do zakończenia operacji.

`POST /api/tasks/{job_id}/ai-sync`, `POST /api/tasks/{job_id}/ai-translate` i `POST /api/settings/ai/test` zwracają teraz HTTP `202` z `operation_id`. Nie utrzymują połączenia HTTP przez czas kolejki ani pracy modelu. Stan `queued`, `running`, `completed` lub `failed` odczytuje się przez `GET /api/ai-operations/{operation_id}`; dla zadań nadal działa `GET /api/tasks/{job_id}/ai-status`. Zakończona operacja zawiera `result` albo `error` oraz `http_status`. Pliki wynikowe pobiera się przez dotychczasowe endpointy. GUI obsługuje ten przebieg automatycznie i pokazuje oczekiwanie oraz pozycję w kolejce AI.

Stan AI jest zapisywany w SQLite. Po restarcie oczekujące i wykonywane operacje są oznaczane jako przerwane; aplikacja nie ponawia automatycznie płatnych żądań. Operacje przygotowania również są oznaczane jako przerwane. W razie potrzeby użytkownik zleca je ponownie. Kolejki działają w jednej instancji procesu aplikacji, zgodnie z konfiguracją obrazu.
