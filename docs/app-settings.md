# Ustawienia aplikacji bez restartu

Zakładka **Ustawienia** (`/settings`) zawiera panel **Ustawienia aplikacji**.
Konfiguracja dostawcy i konsola znajdują się w osobnej zakładce **AI** (`/ai`).
Każdy formularz ma własny przycisk zapisu. Rozmiary w GUI są podane w MiB.

| Grupa | Ustawienia Compose/env dostępne w GUI |
|---|---|
| Przetwarzanie | `FFPROBE_TIMEOUT_SECONDS`, `FFMPEG_TIMEOUT_SECONDS`, `WORKPACK_REFERENCE_SCORE_MARGIN`, `WORKPACK_MAX_POLISH_CANDIDATES` |
| Paczka filmu lub odcinka | `WORKPACK_MAX_ARCHIVE_BYTES`, `WORKPACK_MAX_FILES` |
| Katalogi i sezony | `DIRECTORY_MAX_ITEMS`, `DIRECTORY_MAX_ARCHIVE_BYTES`, `DIRECTORY_MAX_FILES` |
| Lista modeli AI | `AI_MAX_SAVED_MODELS` (domyślnie 10, zakres 1–100) |
| Archiwum | `ARCHIVE_MAX_TITLES` (domyślnie 30, zakres 1–10000) |
| OCR | `OCR_WORKER_URL`, `OCR_TIMEOUT_SECONDS`, `OCR_MAX_OUTPUT_BYTES`, `INCLUDE_GRAPHIC_REFERENCE` |

Zmiany są zapisywane w tabeli `app_settings` bazy `/data/subtitle-agent.db`,
bez modyfikacji plików Compose ani `.env`. Niezapisana konfiguracja pochodzi ze
środowiska. Po zapisie wartości z GUI mają pierwszeństwo także po restarcie kontenera;
trzeba zachować wolumen `/data`. Błędny zapis nie zmienia dotychczasowej konfiguracji.

Każde rozpoczynane zadanie otrzymuje kopię konfiguracji. Zadania już wykonywane
zachowują swoje ustawienia do końca; dotyczy to wszystkich odcinków i końcowego ZIP-a
jednej partii. Zadanie oczekujące w kolejce odczytuje nowe wartości przy rozpoczęciu.
Zapis nie ponawia istniejących zadań ani nie przebudowuje gotowych paczek.

Limit listy modeli AI obowiązuje od razu i nie usuwa żadnych pozycji. Zmniejszenie go poniżej liczby zapisanych modeli zwraca błąd bez zmiany ustawień.

Limit archiwum jest wspólną polityką i obowiązuje bez restartu, niezależnie od kopii
konfiguracji aktywnego zadania. Lista pokazuje ostatnie N różnych ścieżek filmów lub
katalogów, a wszystkie wersje paczek i napisów danego tytułu zajmują jedną pozycję.
Odcinki należące do zadania katalogowego nie zajmują dodatkowych pozycji.
Przy najbliższym porządkowaniu — po zadaniu lub w cyklu konserwacji — starsze,
niechronione zadania i ich pliki są usuwane. Aktywne i oczekujące operacje AI oraz
wstrzymane/przerwane katalogi wraz z materiałami są chronione; przechowywane dane mogą
przez to tymczasowo przekraczać limit. Zwiększenie limitu nie odtwarza usuniętych plików.
Retencja godzinowa nie jest stosowana; dawne `WORKPACK_RETENTION_HOURS` jest ignorowane.

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

## Powiadomienia przeglądarkowe

Przełącznik **Powiadomienia w przeglądarce** w panelu ustawień jest domyślnie włączony.
Zapamiętane wyłączenie pozostaje zachowane. Wymaga HTTPS (localhost jest wyjątkiem),
obsługiwanej przeglądarki oraz zgody użytkownika. Przy pierwszym uruchomieniu zadania
lub ręcznym włączeniu przełącznika przeglądarka może poprosić o zgodę. Samo otwarcie
strony nie wyświetla prośby. Po odmowie zgodę można zmienić w ustawieniach witryny
w przeglądarce. Wyświetlanie może blokować także system, np. tryb „Nie przeszkadzać”.
Preferencja jest lokalna dla przeglądarki i adresu aplikacji; nie trafia do SQLite ani Compose.

Pozostaw otwartą kartę aplikacji. Może działać w tle albo pokazywać AI, Ustawienia lub
Archiwum. Monitorowane są zadania rozpoczęte przy włączonej funkcji oraz przywrócone
aktywne zadanie. Aplikacja informuje o zakończeniu analizy, workpacka i operacji AI,
błędzie, przerwaniu i wymaganym wyborze referencji. Katalog daje jedno powiadomienie
końcowe dla całej kolejki; oczekiwanie na decyzję może wystąpić dla kolejnych odcinków.
Kliknięcie otwiera odpowiednie zadanie. Odświeżanie strony nie powtarza już pokazanych
komunikatów.

Nie ma Web Push. Service worker obsługuje wyłącznie wyświetlenie i kliknięcie
powiadomienia, także w obsługiwanych przeglądarkach mobilnych; nie odpytuje zadań po
zamknięciu karty. Zamknięcie, uśpienie lub zamrożenie karty i ograniczenia pracy w tle
mogą uniemożliwić albo opóźnić komunikat.
