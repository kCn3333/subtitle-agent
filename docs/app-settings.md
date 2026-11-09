# Ustawienia aplikacji bez restartu

Zakładka **Ustawienia** (`/settings`) zawiera panel **Ustawienia aplikacji**.
Konfiguracja dostawcy i konsola znajdują się w osobnej zakładce **AI** (`/ai`).
Każdy formularz ma własny przycisk zapisu. Rozmiary w GUI są podane w MiB.

| Grupa | Ustawienia Compose/env dostępne w GUI |
|---|---|
| Przetwarzanie | `FFPROBE_TIMEOUT_SECONDS`, `FFMPEG_TIMEOUT_SECONDS`, `WORKPACK_REFERENCE_SCORE_MARGIN`, `WORKPACK_MAX_POLISH_CANDIDATES` |
| Paczka filmu lub odcinka | `WORKPACK_MAX_ARCHIVE_BYTES`, `WORKPACK_MAX_FILES` |
| Katalogi i sezony | `DIRECTORY_MAX_ITEMS`, `DIRECTORY_MAX_ARCHIVE_BYTES`, `DIRECTORY_MAX_FILES` |
| OCR | `OCR_WORKER_URL`, `OCR_TIMEOUT_SECONDS`, `OCR_MAX_OUTPUT_BYTES`, `INCLUDE_GRAPHIC_REFERENCE` |

Zmiany są zapisywane w tabeli `app_settings` bazy `/data/subtitle-agent.db`,
bez modyfikacji plików Compose ani `.env`. Niezapisana konfiguracja pochodzi ze
środowiska. Po zapisie wartości z GUI mają pierwszeństwo także po restarcie kontenera;
trzeba zachować wolumen `/data`. Błędny zapis nie zmienia dotychczasowej konfiguracji.

Każde rozpoczynane zadanie otrzymuje kopię konfiguracji. Zadania już wykonywane
zachowują swoje ustawienia do końca; dotyczy to wszystkich odcinków i końcowego ZIP-a
jednej partii. Zadanie oczekujące w kolejce odczytuje nowe wartości przy rozpoczęciu.
Zapis nie ponawia istniejących zadań ani nie przebudowuje gotowych paczek.

Adres OCR musi wskazywać usługę HTTP dostępną z kontenera. Zmiana adresu nie uruchamia
workera ani nie zmienia jego własnych limitów CPU, pamięci, czasu i rozmiaru odpowiedzi.
`OCR_MAX_OUTPUT_BYTES` w GUI określa limit przyjmowany przez aplikację.

Parametry startowe i bezpieczeństwa pozostają w Compose/env: adres i port nasłuchu,
uwierzytelnianie, proxy, katalogi i wolumeny, tryb aplikacji, liczba workerów, pojemność
kolejek oraz konfiguracja historycznego trybu ADVANCED. Panel nie udostępnia kluczy,
haseł ani parametrów automatycznej publikacji.

`GET /api/settings/app` zwraca wyłącznie wymienione pola, z nazwami `snake_case`
i rozmiarami w bajtach. `PUT /api/settings/app` przyjmuje kompletny zestaw tych pól;
nieznane pola i nieprawidłowe wartości zwracają `422` bez częściowego zapisu.
Oba endpointy wymagają sesji, a zapis również poprawnego tokenu CSRF.
