# Wdrożenie i utrzymanie

Podstawowe uruchomienie i ustawienia Compose opisuje [README](../README.md#uruchomienie). Aplikację uruchamiaj jako jeden proces i jedną instancję — kolejki nie są współdzielone.

## Dostęp

- `AUTH_PUBLIC_URL` musi odpowiadać adresowi otwieranemu w przeglądarce. Proxy musi zachować publiczny nagłówek `Host`.
- Przy HTTPS pozostaw `AUTH_COOKIE_SECURE=true`. Lokalne HTTP wymaga adresu `http://…` w `AUTH_PUBLIC_URL` i `AUTH_COOKIE_SECURE=false` w Compose.
- Proxy na tym samym hoście korzysta z `127.0.0.1:8080`. Przy proxy/tunelu na osobnym hoście opublikuj port na adresie LAN aplikacji i ogranicz dostęp do hosta proxy regułami uwzględniającymi Docker.
- `AUTH_TRUSTED_PROXIES` wskazuje źródło połączenia widziane przez aplikację, niekoniecznie IP kontenera proxy. Zaufane proxy musi przekazywać `X-Forwarded-For`, a proxy brzegowe ustalać IP klienta z połączenia. Aplikacja czyta łańcuch od prawej do pierwszego niezaufanego adresu.
- Pusta lista proxy oznacza używanie IP bezpośredniego połączenia; za proxy wszyscy użytkownicy mogą wtedy podlegać wspólnej blokadzie logowania.
- Worker OCR działa tylko w sieci Compose na porcie `8090`. Publiczny `/health` aplikacji zwraca wyłącznie status.

Po trzech błędnych logowaniach IP jest blokowane domyślnie na godzinę (`AUTH_LOCKOUT_SECONDS=3600`). Sesja trwa 8 godzin (`AUTH_SESSION_SECONDS=28800`), a z opcją zapamiętania — 30 dni. Restart nie usuwa blokady; zmiana danych logowania i restart unieważniają sesje.

## Dane i zasoby

- `/data` zawiera SQLite, ustawienia, materiały robocze i archiwum. Klucze AI są zapisywane bez szyfrowania aplikacyjnego; ogranicz dostęp do wolumenu i kopii.
- OCR używa CPU, domyślnie do 2 CPU i 1 GiB RAM. Obsługiwane języki: `eng`, `fra`, `deu`, `spa`, `ita`, `por`, `pol`.
- Przygotowanie ma domyślnie 1 aktywną operację i 5 oczekujących (`MAX_CONCURRENT_JOBS`, `MAX_QUEUED_JOBS`). Oddzielna kolejka AI ma 1 aktywną i 5 oczekujących operacji (`MAX_QUEUED_AI_OPERATIONS`).
- Archiwum zachowuje domyślnie 30 tytułów lub katalogów. Aktywne operacje i wstrzymane/przerwane katalogi są chronione przed usunięciem. Porządkowanie odbywa się po zadaniach i co 6 godzin (`WORKPACK_CLEANUP_INTERVAL_HOURS`).

## Aktualizacja

Wykonaj kopię `/data`. W Compose ustaw oba obrazy na tę samą wersję albo pozostaw `latest`, następnie:

```sh
docker compose -f compose.example.yml pull
docker compose -f compose.example.yml up -d
```

Zachowaj wolumen danych; `docker compose down -v` go usuwa. Po aktualizacji sprawdź logowanie, archiwum i dostępność OCR. Operacje AI przerwane restartem wymagają ręcznego ponowienia.
