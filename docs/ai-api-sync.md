# Obsługa i konfiguracja AI

## Przygotowanie materiałów

Podaj ścieżkę filmu lub katalogu i wybierz analizę, przygotowanie synchronizacji albo tłumaczenia. Synchronizacja wymaga istniejących napisów PL. Referencje osadzone są wybierane automatycznie, gdy wybór jest jednoznaczny, z preferencją angielskiego; zewnętrzny wzorzec wymaga potwierdzenia. Zmiana referencji wymaga przebudowy ZIP-a. PGS/VobSub przechodzą OCR.

Paczka ZIP zawiera materiały źródłowe, manifest i raport. Obecność plików i ich sumy SHA-256 są weryfikowane. Niepewne napisy PL mogą być dostępne wyłącznie do ręcznej oceny — [zasady kwalifikacji](polish-candidates.md).

## Model

W zakładce **AI** podaj adres API osiągalny z kontenera, identyfikator modelu i wymagany klucz. `localhost` oznacza kontener aplikacji. Adres może być bazowym URL, np. `http://serwer-modelu:8000/v1`, albo pełnym URL `/chat/completions`.

- **Test połączenia** wysyła rzeczywiste, potencjalnie płatne żądanie bez zapisu ustawień.
- **Dodaj** zapisuje model na liście i aktywuje go. Wybór pozycji przywraca jej konfigurację.
- **Zapisz** utrwala bieżące ustawienia; **Usuń model z listy** zachowuje aktywną konfigurację.
- Zmiana adresu API wymaga odpowiedniego klucza; klucz poprzedniego serwera nie jest przenoszony.

| Parametr | Znaczenie |
| --- | --- |
| Timeout | 1–3600 s, bez czasu oczekiwania w kolejce |
| Reasoning effort | Puste pomija parametr; obsługa wartości zależy od modelu |
| Limit tokenów wyjścia | Wysyłany jako `max_tokens`; musi pomieścić cały wynik |
| Format JSON | Wysyła `response_format: {"type":"json_object"}`; wymaga obsługi przez dostawcę |

Dostawca musi obsługiwać Chat Completions i zwracać JSON w `choices[0].message.content`. Dla OpenRouter wysyłane jest `reasoning.effort`, dla pozostałych API — `reasoning_effort`. Cały zestaw napisów filmu lub odcinka trafia do jednego żądania; kontekst modelu musi pomieścić instrukcję, wejście i wynik. Aplikacja nie uruchamia modelu ani nie dzieli automatycznie długiego filmu.

## Wyniki i ograniczenia

Synchronizacja zmienia czasy, zachowując tekst PL, tagi i kolejność. Tłumaczenie zmienia tekst, zachowując czasy i kolejność referencji. Wyniki są wersjonowane jako `AI-Synced-v001.pl.srt` lub `AI-Translated-v001.pl.srt`, dostępne do pobrania i w archiwum. Aplikacja nie zapisuje ich w bibliotece mediów.

Walidacja sprawdza kompletność ID segmentów, format SRT i poprawność czasów. Dopasowanie dialogów, kompletność referencji, jakość OCR i tłumaczenia wymagają oceny użytkownika. Flaga `forced` może oznaczać niepełne napisy; sama liczba segmentów nie potwierdza zgodności, a słaba hipoteza czasowa nie jest gotową korektą.

AI ma oddzielną kolejkę: jedna operacja działa, domyślnie pięć oczekuje. Restart przerywa niedokończone operacje; ponowienie jest ręczne. Dla całych katalogów zobacz [przetwarzanie katalogów](directory-batch.md).

## Diagnostyka

Konsola AI pokazuje odpowiedź dostawcy, czas, tokeny i koszt, jeśli został zwrócony. Może zawierać dialogi; wyczyszczenie historii dotyczy wszystkich użytkowników. Brak kosztu nie oznacza darmowej operacji. Archiwum sumuje tokeny i koszty USD, także nieudanych prób; testy połączenia są poza sumą.

| Objaw | Co sprawdzić |
| --- | --- |
| `401` | Klucz API i jego ważność |
| Timeout | Dostępność dostawcy i limit czasu; przerwanie żądania nie gwarantuje zatrzymania modelu |
| Niepełny JSON, brak ID, `finish_reason=length` | Odpowiedź w konsoli, kontekst modelu i limit tokenów wyjścia |
| `409` | Trwająca operacja lub zmienione dane wejściowe |
| `429` z `QUEUE_FULL` | Zapełniona kolejka; ponów po zwolnieniu miejsca |
| `NEEDS_OCR` | Dostępność, logi i obsługiwane języki workera OCR |

## Integracja przez API

API wymaga sesji. Logowanie przez `POST /api/auth/login` wymaga `Origin` zgodnego z `AUTH_PUBLIC_URL` i `X-CSRF-Token` z meta `login-csrf-token` strony `/login`. Zmiany po zalogowaniu wymagają tokenu z meta `csrf-token` strony głównej.

`POST /api/tasks/{job_id}/ai-sync` przyjmuje `reference_source_id` i `polish_file` (nazwę `archiveName` kandydata); `/ai-translate` tylko referencję. Dla katalogu wyślij `items` obejmujące wszystkie gotowe pozycje z `GET /api/tasks/{job_id}/ai-inputs`.

Zlecenie zwraca `202` z `operation_id`. Odczytuj `GET /api/ai-operations/{operation_id}` do `completed` lub `failed`; `result` zawiera wynik, `error` — błąd. Przy błędzie katalogu `result` może nadal zawierać paczkę częściową. Pobieranie: `GET /api/tasks/{job_id}/ai-sync/download` lub `/ai-translate/download`.
