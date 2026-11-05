# Subtitle Agent

Subtitle Agent to aplikacja webowa do analizy napisów, przygotowania materiału referencyjnego oraz synchronizacji i tłumaczenia na język polski. Obsługuje napisy tekstowe i graficzne PGS/VobSub, przetwarzane przez osobny worker OCR.

Aplikacja tworzy pakiety ZIP z napisami i metadanymi oraz pliki wynikowe SRT. Biblioteka mediów jest montowana wyłącznie do odczytu.

## Funkcje

| Tryb | Wymagane dane | Wynik |
|---|---|---|
| Analiza napisów | Plik filmu lub odcinka | Raport ścieżek, referencji i kandydatów PL |
| Przygotowanie do synchronizacji | Referencja źródłowa i pasujące napisy PL | Pakiet ZIP; po przetworzeniu przez AI — SRT z poprawionymi czasami |
| Przygotowanie do tłumaczenia | Referencja źródłowa | Pakiet ZIP; po przetworzeniu przez AI — polski SRT z czasami referencji |

Referencje osadzone są wybierane z preferencją języka angielskiego i PGS. Wybór niejednoznaczny oraz użycie napisów zewnętrznych wymagają zatwierdzenia. Dostępna jest ręczna zmiana referencji i przebudowa pakietu.

Synchronizacja i tłumaczenie korzystają z API zgodnego z Chat Completions. Walidacja sprawdza kompletność segmentów, strukturę SRT i zakresy czasowe; trafność synchronizacji oraz jakość OCR i tłumaczenia wymagają oceny użytkownika.

## Wdrożenie

[compose.example.yml](compose.example.yml) definiuje aplikację i worker OCR, trwały wolumen `/data` oraz bibliotekę mediów montowaną z `:ro`. Podstawowe parametry dostępu:

| Zmienna | Znaczenie |
|---|---|
| `AUTH_USERNAME` | Login do aplikacji |
| `AUTH_PASSWORD` | Hasło o długości 16–1024 znaków |
| `AUTH_PUBLIC_URL` | Publiczny adres HTTPS, bez ścieżki |
| `AUTH_TRUSTED_PROXIES` | Zaufane adresy/CIDR proxy; za proxy wymagane do poprawnego ustalania IP klienta |

Po uzupełnieniu konfiguracji:

```bash
docker compose -f compose.example.yml up -d
```

Port HTTP aplikacji to `8080`; przykładowy Compose wiąże go z `127.0.0.1`. Dostęp publiczny wymaga reverse proxy lub Cloudflare Tunnel z HTTPS. Worker OCR pozostaje dostępny w sieci wewnętrznej.

Pełna konfiguracja: [.env.example](.env.example). Topologie proxy, ustawienia dostępu i trwałość danych: [instrukcja wdrożenia](docs/deployment.md).

## Wykonywanie zadań

Przygotowanie materiału i operacje AI korzystają z niezależnych kolejek. Oczekiwanie na model nie blokuje przygotowania kolejnego filmu.

| Kolejka | Zakres | Domyślny limit |
|---|---|---|
| Przygotowanie | Analiza, ekstrakcja, OCR, pakowanie i przebudowy | 1 operacja wykonywana, 5 oczekujących |
| AI | Synchronizacja, tłumaczenie i test połączenia | 1 operacja wykonywana, 5 oczekujących |

Powtórne zgłoszenie tej samej aktywnej operacji zadania zwraca istniejące zlecenie. Rezerwacja zadania chroni przed równoczesną zmianą referencji i przetwarzaniem AI. Po restarcie niedokończone operacje są oznaczane jako przerwane; żądania AI nie są automatycznie ponawiane.

Archiwum przechowuje wyniki ostatnich 30 tytułów. Starsze dane są usuwane, z wyjątkiem plików używanych przez oczekujące lub aktywne operacje.

## Dostęp i architektura

Strony, API i wyniki są chronione logowaniem. Trzy błędne próby blokują IP na godzinę. Sesja trwa domyślnie 8 godzin lub 30 dni po wybraniu opcji zapamiętania. Żądania zmieniające dane wymagają tokenu CSRF.

Backend wykorzystuje Python 3.12, FastAPI, SQLite, FFmpeg i mkvtoolnix. Worker OCR korzysta z Tesseracta i seconv. Aplikacja działa jako jeden proces; kolejki nie są współdzielone między instancjami.

## Dokumentacja

- [Wdrożenie i konfiguracja](docs/deployment.md)
- [Konfiguracja AI, kontrakt API i diagnostyka](docs/ai-api-sync.md)
- [Próbka VobSub używana w testach](tests/fixtures/README.md)

## Testy

Testy backendu są uruchamiane w obrazie Python 3.12. Testy interfejsu wymagają Node.js.

```bash
podman build --target test -t subtitle-agent:test .
node --test tests/*_ui.test.cjs
```
