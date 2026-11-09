# Wdrożenie i konfiguracja

Wdrożenie obejmuje aplikację i worker OCR z [compose.example.yml](../compose.example.yml). Zmienne środowiskowe opisuje [.env.example](../.env.example). Domyślny tryb `WORKPACK` udostępnia interfejs opisany w [README](../README.md); `ADVANCED` włącza historyczne endpointy.

Ustawienia dostępne bez restartu są opisane w [konfiguracji aplikacji](app-settings.md). Wartości zapisane w GUI mają pierwszeństwo przed ich odpowiednikami w Compose/env.

## Sieć i proxy

| Usługa | Port kontenera | Dostęp |
|---|---|---|
| Aplikacja | `8080` | Przez reverse proxy lub Cloudflare Tunnel |
| OCR | `8090` | Wyłącznie sieć wewnętrzna; bez portu hostowego |

`AUTH_PUBLIC_URL` musi odpowiadać publicznemu adresowi aplikacji, bez ścieżki. Dla dostępu przez HTTPS obowiązuje `AUTH_COOKIE_SECURE=true`. Wartość `false` jest przeznaczona do lokalnego HTTP.

Proxy musi zachować publiczną domenę w nagłówku `Host`. `AUTH_TRUSTED_PROXIES` przyjmuje rozdzielone przecinkami adresy lub CIDR zaufanych pośredników. Dla takiego połączenia wymagany jest `X-Forwarded-For`; aplikacja analizuje łańcuch od prawej do pierwszego niezaufanego adresu. Proxy brzegowe powinno ustalać adres klienta z połączenia, zamiast ufać wartości dostarczonej przez klienta.

Pusta lista zaufanych proxy oznacza używanie IP bezpośredniego połączenia. Za proxy może to powodować wspólną blokadę IP dla wszystkich użytkowników. Obraz uruchamia Uvicorn z `--no-proxy-headers`, aby aplikacja mogła zweryfikować rzeczywiste źródło połączenia.

### Tunel na osobnym hoście

| Element | Konfiguracja |
|---|---|
| Publikacja portu aplikacji | `ADRES_LAN_APLIKACJI:8080:8080` |
| Usługa docelowa tunelu | `http://ADRES_LAN_APLIKACJI:8080` |
| Publiczny adres | `AUTH_PUBLIC_URL=https://napisy.example.com` |
| Zaufane źródło | Adres widziany przez aplikację, zwykle adres LAN hosta tunelu z `/32` przy NAT Docker |

Wewnętrzny adres kontenera `cloudflared` nie musi być adresem widocznym na drugim hoście. Adres źródłowy wymaga potwierdzenia dla danej topologii. Dostęp do opublikowanego portu należy ograniczyć do hosta proxy/tunelu regułami uwzględniającymi Docker.

## Uwierzytelnianie

`AUTH_USERNAME`, `AUTH_PASSWORD` i `AUTH_PUBLIC_URL` są wymagane do startu aplikacji. Hasło musi mieć 16–1024 znaki.

| Parametr | Domyślne działanie |
|---|---|
| `AUTH_LOCKOUT_SECONDS=3600` | Godzinna blokada IP po trzech błędnych logowaniach |
| `AUTH_SESSION_SECONDS=28800` | Sesja ważna przez 8 godzin |
| Zapamiętanie przy logowaniu | Sesja ważna przez 30 dni od logowania, bez przedłużania przy aktywności |

Poprawne logowanie zeruje licznik błędów. Próby podczas blokady nie przedłużają jej; restart nie usuwa blokady. Wylogowanie unieważnia sesję na serwerze. Zmiana danych logowania w konfiguracji i ponowne uruchomienie aplikacji unieważniają wcześniejsze sesje.

Cookie sesji ma `HttpOnly` i `SameSite=Strict`, a domyślnie także `Secure`. Publiczny endpoint `/health` zwraca tylko `{"status":"ok"}`; OpenAPI jest wyłączone.

## Zasoby i dane

- Biblioteka mediów: mounty `:ro`; ścieżki podawane w aplikacji odnoszą się do kontenera.
- `/data`: SQLite, pliki robocze i archiwum. Klucz dostawcy AI jest przechowywany bez szyfrowania aplikacyjnego; wolumen i jego kopie wymagają ograniczenia dostępu.
- Worker OCR: CPU, domyślnie limit 2 CPU i 1 GiB RAM w Compose, bez mountów biblioteki i `/data`.
- OCR obsługuje PGS oraz VobSub, z językami `eng`, `fra`, `deu`, `spa`, `ita`, `por`, `pol`. Błąd lub brak obsługi języka kończy przygotowanie statusem `NEEDS_OCR`.
- `INCLUDE_GRAPHIC_REFERENCE=true` dołącza źródłowe pliki graficzne do ZIP synchronizacji. Nie zmienia paczek tłumaczenia.

| Parametr | Domyślna wartość | Zakres |
|---|---:|---|
| `MAX_CONCURRENT_JOBS` | `1` | Równoległe operacje przygotowania i przebudowy |
| `MAX_QUEUED_JOBS` | `5` | Oczekujące operacje przygotowania i przebudowy |
| `MAX_QUEUED_AI_OPERATIONS` | `5` | Oczekujące operacje AI; jedna jest wykonywana |
| `WORKPACK_CLEANUP_INTERVAL_HOURS` | `6` | Okresowe porządkowanie, oprócz startu i zakończenia operacji |

Retencja obejmuje 30 tytułów według ścieżki materiału. `WORKPACK_RETENTION_HOURS` pozostaje parametrem zgodnościowym i nie zmienia tego limitu. Dane oczekujących i aktywnych operacji są chronione do ich zakończenia. Kolejki wymagają jednej instancji procesu aplikacji.
