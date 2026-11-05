# Subtitle Agent

Analiza napisów, OCR PGS/VobSub, synchronizacja i tłumaczenie na polski przez API modelu AI. Wyniki: ZIP i SRT. Filmy pozostają tylko do odczytu.

## Uruchomienie

1. W Portainerze utwórz stack z [compose.example.yml](compose.example.yml).
2. Dopasuj mounty mediów; pozostaw `:ro`. Zachowaj wolumen `/data` przy aktualizacjach.
3. W **Environment variables** stacka ustaw poniższe zmienne.
4. Skonfiguruj HTTPS przez reverse proxy lub Cloudflare Tunnel i wdroż stack.

| Zmienna | Wartość |
|---|---|
| `AUTH_USERNAME` | Login |
| `AUTH_PASSWORD` | Unikalne hasło, 16–1024 znaki |
| `AUTH_PUBLIC_URL` | Publiczny adres HTTPS bez ścieżki, np. `https://napisy.example.com` |
| `AUTH_TRUSTED_PROXIES` | Adresy/CIDR zaufanych proxy, rozdzielone przecinkami |

Obrazy: `ghcr.io/kcn3333/subtitle-agent:latest` i `ghcr.io/kcn3333/subtitle-agent-ocr:latest`. Przy aktualizacji stacka wybierz ponowne pobranie obrazów.

Port aplikacji w kontenerze: `8080`. Przykładowy Compose publikuje go tylko na `127.0.0.1:8080`. OCR nie wymaga portu hostowego ani mountów mediów.

### Proxy i tunel na innym hoście

- Proxy musi zachować publiczną domenę w `Host` i przekazywać poprawny `X-Forwarded-For`.
- Dla tunelu na innym hoście publikuj port jako `ADRES_LAN_APLIKACJI:8080:8080`; w tunelu ustaw `http://ADRES_LAN_APLIKACJI:8080`.
- Do `AUTH_TRUSTED_PROXIES` wpisz adres źródłowy widziany przez aplikację. Przy NAT Docker zwykle jest to adres LAN hosta tunelu z `/32`, a nie adres kontenera. Potwierdź go przed wdrożeniem.
- Ogranicz dostęp do portu aplikacji do hosta proxy/tunelu regułami uwzględniającymi Docker. Nie publikuj portu OCR.
- Pozostaw `AUTH_COOKIE_SECURE=true` i opcję Uvicorn `--no-proxy-headers`. Pusta lista zaufanych proxy oznacza użycie IP bezpośredniego połączenia; za proxy użytkownicy mogą wtedy współdzielić blokadę.

## Użycie

Podaj ścieżkę filmu **wewnątrz kontenera**, np. `/media/movies/Film.mkv`, i wybierz tryb:

| Tryb | Wynik / wymagania |
|---|---|
| Sprawdź napisy | Raport analizy |
| Przygotuj do synchronizacji | ZIP; wymagana referencja i pasujące napisy PL |
| Przygotuj do tłumaczenia | ZIP; wymagana referencja, bez napisów PL |

Wbudowana referencja jest wybierana automatycznie z preferencją angielskiego i PGS. Niejednoznaczny wybór oraz zewnętrzną referencję trzeba zatwierdzić. Zmiana referencji wymaga **Zbuduj ponownie**.

Aby otrzymać SRT, skonfiguruj **Ustawienia AI**, uruchom **Przekaż do AI** i pobierz wynik. [Konfiguracja AI, API i diagnostyka](docs/ai-api-sync.md).

OCR działa na CPU dla PGS (`.sup`) i VobSub (`.idx` + `.sub`). Worker zawiera języki `eng`, `fra`, `deu`, `spa`, `ita`, `por`, `pol`. Błąd OCR daje status `NEEDS_OCR`. Tekst po OCR i wynik AI wymagają sprawdzenia.

## Limity i dane

| Obszar | Domyślne ustawienie |
|---|---|
| Przygotowanie i przebudowy | 1 wykonywana operacja (`MAX_CONCURRENT_JOBS`), 5 oczekujących (`MAX_QUEUED_JOBS`) |
| Synchronizacja, tłumaczenie i test AI | 1 wykonywana operacja, 5 oczekujących (`MAX_QUEUED_AI_OPERATIONS`) |
| Logowanie | Blokada IP po 3 błędach na 1 godzinę (`AUTH_LOCKOUT_SECONDS=3600`) |
| Sesja | 8 godzin (`AUTH_SESSION_SECONDS=28800`) lub 30 dni po zaznaczeniu opcji przy logowaniu |
| Archiwum | Ostatnie 30 tytułów; starsze dane są usuwane |

- Uruchamiaj jeden proces aplikacji. Kolejki nie są współdzielone między instancjami.
- Identyczne zgłoszenie aktywnego przygotowania, przebudowy lub AI dla danego zadania korzysta z istniejącego zlecenia. Pełna kolejka zwraca `429`; konflikt operacji tego samego zadania — `409`.
- Pliki oczekujących i aktywnych operacji są chronione przed retencją. Po restarcie niedokończone operacje są oznaczane jako przerwane; AI nie jest ponawiane automatycznie.
- `/data` zawiera bazę, pliki robocze i archiwum. Twórz kopie zapasowe; baza zawiera klucz API bez szyfrowania aplikacyjnego.
- Wylogowanie unieważnia sesję. Zmiana loginu lub hasła unieważnia wcześniejsze sesje po ponownym uruchomieniu aplikacji. Blokady IP pozostają po restarcie.
- `/health` zwraca tylko `{"status":"ok"}`. Strony, API, logi i pobieranie wymagają logowania.

Pozostałe zmienne: [.env.example](.env.example). `WORKPACK_RETENTION_HOURS` jest zgodnościowe; nie zastępuje limitu 30 tytułów. `SUBTITLE_AGENT_APP_MODE=WORKPACK` udostępnia opisany interfejs; `ADVANCED` włącza historyczne endpointy.

## Testy

Backend jest testowany w kontenerze; testy UI wymagają Node.js.

```bash
podman build --target test -t subtitle-agent:test .
node --test tests/*_ui.test.cjs
```

Backend: Python 3.12, FastAPI, SQLite, FFmpeg i mkvtoolnix. Worker OCR: Tesseract i seconv, wersja przypięta w [Dockerfile.ocr](Dockerfile.ocr).
