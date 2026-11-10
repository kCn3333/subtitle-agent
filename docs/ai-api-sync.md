# AI: konfiguracja, API i diagnostyka

## Konfiguracja

W zakładce **AI**, w panelu **Ustawienia AI**, podaj adres osiągalny z kontenera aplikacji, model oraz klucz, jeśli dostawca go wymaga. `localhost` oznacza kontener aplikacji. Wybierz zapisany model z listy lub wpisz nową konfigurację.

- **Test połączenia** sprawdza formularz bez zmiany aktywnej konfiguracji i bez zapisu na liście.
- **Zapisz** utrwala i aktywuje bieżące ustawienia. Jeśli wybrano model z listy, aktualizuje jego konfigurację. Bez wybranej pozycji zapisuje ustawienia poza listą modeli.
- **Dodaj** obok nazwy dodaje model do listy i aktywuje go. Nie zastępuje poprzednio wybranej pozycji o innej nazwie; ten sam adres API i identyfikator modelu aktualizują istniejącą pozycję.
- Wybór pozycji listy przywraca jej adres, parametry i klucz oraz aktywuje ją dla kolejnych zadań.
- **Usuń model z listy** usuwa pozycję, zachowując aktywną konfigurację i trwające zadania.

Domyślny limit to 10 modeli. Można go zmienić bez restartu w **Ustawienia → Lista modeli AI** (`AI_MAX_SAVED_MODELS`, zakres 1–100). Zmniejszenie poniżej liczby zapisanych pozycji jest odrzucane — najpierw usuń wybrane modele. Obecna konfiguracja z wcześniejszej wersji jest jednorazowo przenoszona na listę, jeśli ma adres API i nazwę modelu.

Zapisane modele i wybrana zapisana konfiguracja pozostają po restarcie w SQLite. Ustawienie tymczasowe nie zastępuje ostatniej trwale zapisanej konfiguracji. Klucze nie są zwracane w odczycie API; lista pokazuje tylko informację o ich obecności. Zmiana adresu API wymaga podania odpowiedniego klucza i nie przenosi klucza poprzedniego serwera.

| Pole | Działanie |
|---|---|
| Adres API | Bazowy URL, np. `http://serwer-modelu:8000/v1`, lub pełny URL `/chat/completions` |
| Model | Identyfikator wymagany przez dostawcę |
| Klucz API | Przesyłany jako Bearer; puste pole zachowuje klucz wskazanej konfiguracji przy tym samym adresie API; osobny przycisk usuwa klucz |
| Timeout | 1–3600 s; limit żądania do modelu, bez czasu oczekiwania w kolejce |
| Reasoning effort | Puste pole pomija parametr; `none` wysyła żądanie wyłączenia reasoning. Obsługa zależy od modelu |
| Limit tokenów wyjścia | Puste pole pomija parametr; ustawiona wartość jest wysyłana jako `max_tokens` |
| Format odpowiedzi | JSON wysyła `response_format: {"type":"json_object"}`; wymaga obsługi przez dostawcę |

Dla adresu `https://openrouter.ai/api/v1` aplikacja wysyła `reasoning.effort`; dla pozostałych — `reasoning_effort`. Limit wyjścia musi mieścić się w ograniczeniach modelu i dostawcy.

API modelu musi obsługiwać Chat Completions: `model`, `messages`, `stream:false` oraz JSON w `choices[0].message.content`. Cały zestaw napisów trafia do jednego żądania; kontekst musi pomieścić instrukcję, wejście i wynik. Aplikacja nie dzieli automatycznie filmu i nie uruchamia modelu lokalnie.

Wskaźnik API potwierdza łączność z serwerem. Test połączenia wykonuje rzeczywiste żądanie do modelu i może kosztować; nie sprawdza jakości synchronizacji ani wystarczającego kontekstu dla filmu.

Żądania zapisu, aktywacji i testu formularza przyjmują `settings` z polami konfiguracji.
Opcjonalne `key_source_id` wskazuje ID zapisanego modelu lub `active`; pominięty klucz
jest zachowywany tylko z tego źródła i tylko przy identycznym adresie API.
Bez źródła pominięty klucz oznacza brak klucza, a `api_key: ""` usuwa go jawnie.
`model_id` służy do aktualizacji konkretnej pozycji przy zapisie modelu.

## Wyniki

| Operacja | Model zwraca | Aplikacja zachowuje |
|---|---|---|
| Synchronizacja | Czasy dla każdego ID napisów PL | Tekst PL, tagi i kolejność |
| Tłumaczenie | Polski tekst dla każdego ID referencji | Czasy i kolejność referencji |

Zmiana referencji wymaga przebudowy workpacka. Referencja graficzna musi przejść OCR; zewnętrzna wymaga zatwierdzenia. Do synchronizacji potrzebne są istniejące napisy PL.

Pliki do pobrania: `<film>.AI-Synced-v001.pl.srt` lub `<film>.AI-Translated-v001.pl.srt`. Nowa treść otrzymuje kolejną wersję; identyczna zachowuje nazwę. Wyniki są dostępne również w archiwum. Aplikacja nie zapisuje ich automatycznie w bibliotece mediów.

## API aplikacji

Logowanie wymaga cookie i tokenu z `meta[name="login-csrf-token"]` strony `/login`. `POST /api/auth/login` przyjmuje JSON z `username`, `password` i opcjonalnym booleanem `remember`. Wymaga nagłówków `Origin` zgodnego z `AUTH_PUBLIC_URL` oraz `X-CSRF-Token`. Po zalogowaniu żądania zmieniające dane wymagają cookie sesji i `X-CSRF-Token` z `meta[name="csrf-token"]` strony głównej.

| Metoda i ścieżka | Działanie |
|---|---|
| `GET /api/settings/ai/models` | Lista zapisanych konfiguracji, aktywna pozycja i limit; bez kluczy |
| `POST /api/settings/ai/models` | Zapis/aktualizacja modelu i aktywacja |
| `POST /api/settings/ai/models/{id}/activate` | Wybór zapisanej konfiguracji |
| `DELETE /api/settings/ai/models/{id}` | Usunięcie pozycji z listy |
| `POST /api/settings/ai/save` | Trwały zapis bieżącej konfiguracji poza listą |
| `POST /api/settings/ai/activate` | Tymczasowa konfiguracja bez zapisu |
| `GET/PUT /api/settings/ai` | Odczyt/zapis konfiguracji; klucz nie jest zwracany |
| `POST /api/settings/ai/test` | Test bieżącej konfiguracji lub konfiguracji przekazanej w żądaniu, bez zapisywania |
| `POST /api/tasks/{job_id}/ai-sync` | Synchronizacja; pola `reference_source_id` i `polish_file` |
| `POST /api/tasks/{job_id}/ai-translate` | Tłumaczenie; pole `reference_source_id` |
| `GET /api/tasks/{job_id}/ai-inputs` | Gotowe materiały katalogu i kandydaci PL dla każdego pliku |
| `GET /api/ai-operations/{operation_id}` | Status dowolnej operacji AI |
| `GET /api/tasks/{job_id}/ai-status` | Ostatni status AI danego zadania |
| `GET /api/tasks/{job_id}/ai-sync` | Metadane wyniku synchronizacji |
| `GET /api/tasks/{job_id}/ai-translate` | Metadane wyniku tłumaczenia |
| `GET /api/tasks/{job_id}/ai-sync/download` | Pobranie SRT lub ZIP-a wyników katalogu |
| `GET /api/tasks/{job_id}/ai-translate/download` | Pobranie SRT lub ZIP-a tłumaczeń katalogu |
| `GET/DELETE /api/settings/ai/console` | Odczyt/usunięcie wspólnej historii diagnostycznej |

`reference_source_id` to np. `embedded:4` lub `external:Film.en.srt`; `polish_file` musi być wartością `archiveName` przygotowanego kandydata.

Dla zadania katalogowego te same endpointy POST przyjmują `items`: listę obiektów z `job_id`, `reference_source_id` i — dla synchronizacji — `polish_file`. Lista musi obejmować wszystkie pozycje oznaczone jako gotowe przez `ai-inputs`, bez duplikatów. Partia zajmuje jedno miejsce w kolejce, a żądania do modelu wykonywane są kolejno, oddzielnie dla każdego pliku. Status zawiera `batch=true` i listę `items` z postępem. Wynik to ZIP z SRT i manifestem. Gdy część plików zakończy się błędem, status to `failed`, ale `result` nadal zawiera dostępną paczkę częściową. Ponowienie wykorzystuje gotowe wyniki zgodne z wybranymi wejściami i ustawieniami modelu; zmiana samego timeoutu nie wymusza ponownego przetwarzania.

Zlecenie AI zwraca HTTP `202` z `operation_id`. Odczytuj status do `completed` lub `failed`: wynik jest w `result`, błąd w `error`, kod zakończenia w `http_status`. `queued` zawiera `queue_position`; `running` — bieżącą fazę. Odświeżenie strony ani rozłączenie klienta nie zleca ponownie pracy.

Jedna operacja AI działa naraz; domyślnie pięć oczekuje. Identyczne ponowne zgłoszenie aktywnej operacji zadania zwraca ten sam identyfikator. Inne parametry zajętego zadania dają `409`, pełna kolejka — `429`. Parametry modelu i klucz są ustalane przy przyjęciu zlecenia. Po restarcie niedokończona operacja ma status przerwania; ponowienie wymaga nowego zlecenia.

## Format danych modelu

Synchronizacja wysyła czas filmu, język referencji, segmenty referencji i PL. Przykład dla referencji angielskiej:

```json
{
  "duration_ms": 7200000,
  "reference_language": "eng",
  "english": [{"id":"en:1","text":"Hello.","start_ms":1000,"end_ms":2000}],
  "polish": [{"id":"pl:1","text":"Cześć.","start_ms":4000,"end_ms":5000}]
}
```

Odpowiedź synchronizacji:

```json
{"segments":[{"id":"pl:1","start_ms":1000,"end_ms":2000}]}
```

Dla innych języków pole `english` zastępuje `reference`, z ID `ref:1` itd. Tłumaczenie pomija `polish`; odpowiedź zawiera tekst i ID referencji:

```json
{"segments":[{"id":"ref:1","text":"Cześć."}]}
```

ID wynika z pozycji segmentu, nie z numeru wpisanego w SRT. Każde ID musi wystąpić dokładnie raz. Niekompletne odpowiedzi, obce pola, nieprawidłowe czasy i tekst naruszający format SRT są odrzucane. Walidacja nie ocenia dopasowania dialogów ani jakości tłumaczenia.

## Diagnostyka

Konsola AI przechowuje ostatnie 100 wpisów, do 131072 znaków każdy. Pokazuje odpowiedź dostawcy, błędy, czas, tokeny i zgłoszony koszt. Może zawierać dialogi; klucz API jest maskowany. Wyczyszczenie tej konsoli usuwa historię wszystkim użytkownikom. Główne **Wyczyść** usuwa tylko lokalny widok konsoli zadania.

| Objaw | Sprawdź |
|---|---|
| `401`, np. `API key expired` | Ważność klucza; wpisz nowy i zapisz ustawienia |
| Timeout modelu | Dostępność dostawcy i ustawiony timeout; przerwanie żądania nie gwarantuje zatrzymania modelu |
| Niepełny JSON, brak ID, `finish_reason=length` | Odpowiedź w konsoli, kontekst modelu i limit wyjścia |
| `409` | Operacja zadania już oczekuje/działa albo zmieniły się dane wejściowe |
| `429` z `QUEUE_FULL` | Brak miejsca w kolejce; ponów po zwolnieniu miejsca |
| `NEEDS_OCR` | Dostępność workera, jego logi i obsługę języka referencji |

Odpowiedź modelu ma limit 8 MiB; przekierowania HTTP nie są śledzone. Nowa próba usuwa poprzedni wynik roboczy zadania; zarchiwizowane wersje pozostają do czasu retencji.

Czas żądania obejmuje sieć i pracę modelu, bez kolejki, OCR i zapisu SRT. Koszt pochodzi z `usage.cost`, również dla odpowiedzi odrzuconej podczas walidacji. Dla OpenRouter oznaczany jest jako USD; dla innych API waluta nie jest zgadywana. Brak danych kosztowych nie oznacza darmowej operacji. Archiwum sumuje koszty USD i tokeny dla tytułu, także z nieudanych prób; testy połączenia są poza tą sumą.
