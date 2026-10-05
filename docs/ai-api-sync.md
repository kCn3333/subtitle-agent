# Synchronizacja i tłumaczenie przez skonfigurowane API

Aplikacja przygotowuje referencję źródłową i istniejące napisy PL, korzystając z dotychczasowej ekstrakcji i OCR. Wysyła jedno żądanie do niezależnego modelu, sprawdza odpowiedź i składa SRT z oryginalnym tekstem PL. Nie instaluje modeli, nie pobiera wag i nie zarządza CPU/GPU. Nie wymaga dodatkowej usługi w Compose.

## Ustawienia i pierwszy test

1. Wdróż obraz aplikacji zawierający tę zmianę, zachowując obecny stack, wolumen danych i mounty mediów. Konfiguracja OCR pozostaje dotychczasowa.
2. Otwórz **Ustawienia AI** (`/settings`). Podaj adres bazowy API, np. `http://serwer-modelu:8000/v1`, nazwę modelu, opcjonalny klucz i timeout. Możesz również podać pełny URL kończący się `/chat/completions`. Adres dotyczy połączenia z kontenera aplikacji; `localhost` oznacza ten kontener.
3. Kliknij **Zapisz i testuj połączenie**. Test wykonuje małe żądanie Chat Completions do wybranego modelu i oczekuje `{"ok":true}`. Sprawdza uwierzytelnienie, model, odpowiedź i prostą instrukcję JSON; nie potwierdza jakości synchronizacji ani pojemności kontekstu dla całego filmu.
4. Wróć na ekran główny, wybierz **Przygotuj do synchronizacji** i uruchom przygotowanie filmu/odcinka. Wybór referencji i potwierdzenie zewnętrznych napisów działają jak dotychczas. PGS/VobSub najpierw przechodzi przez skonfigurowany OCR.
5. Po przygotowaniu wybierz polski plik SRT i kliknij **Przekaż do AI**. Zmiana referencji wymaga najpierw ponownego zbudowania workpacka. Aplikacja wyśle tekstowe segmenty referencji i PL oraz czas trwania filmu do skonfigurowanego endpointu.
6. Pobierz wynik SRT. Ekran pokazuje czas żądania oraz tokeny, jeśli API je zwraca. Sprawdź synchronizację w kilku miejscach filmu. Walidacja sprawdza strukturę i przedziały, a nie trafność dopasowania dialogów.

Przycisk „Przekaż do AI” znajduje się po prawej stronie panelu synchronizacji lub tłumaczenia, pod kartą statusu. W synchronizacji można wybrać PL. Po kliknięciu konsola pokazuje trwające żądanie i licznik czasu oczekiwania; API nie dostarcza procentowego postępu.

Ustawienia zapisują się w SQLite w `/data` i pozostają po restarcie. Klucz jest przechowywany w bazie bez szyfrowania aplikacyjnego; nie trafia do odpowiedzi API ustawień, raportu ani SRT. Puste pole w formularzu zachowuje zapisany klucz; checkbox pozwala go usunąć. Nowe API nie dodaje mechanizmu logowania do aplikacji; dostęp do ustawień podlega temu samemu zabezpieczeniu dostępu co reszta panelu.

Opcjonalne **Reasoning effort** domyślnie ma wartość **domyślne modelu**: pole `reasoning_effort` jest pomijane w żądaniu do modelu. **Wyłączone** dodaje `"reasoning_effort":"none"`. Ustawienie obowiązuje zarówno podczas testu połączenia, synchronizacji i tłumaczenia. W API ustawień `null` oznacza pominięcie, a `"none"` wyłączenie. Istniejące konfiguracje bez tego pola zachowują domyślne zachowanie. Opcję wyłączenia wybieraj dla modelu i dostawcy obsługującego tę wartość.

## Kontrakt

Opcjonalny **Format odpowiedzi → JSON** wysyła `response_format: {"type":"json_object"}` podczas testu, synchronizacji i tłumaczenia. Domyślna opcja **domyślne API** pomija pole, także dla konfiguracji zapisanych przed tą zmianą. Wybierz JSON tylko dla endpointu obsługującego ten parametr; [Ollama obsługuje `response_format` przez API zgodne z OpenAI](https://docs.ollama.com/capabilities/structured-outputs). Tryb JSON ogranicza format odpowiedzi, ale nie gwarantuje poprawnego schematu, kompletu ID ani trafności czasów — dotychczasowa walidacja nadal obowiązuje.

Dla dużych workpacków sprawdź też efektywny kontekst niezależnego serwera modelu. Liczba `prompt_tokens` sama nie potwierdza ucięcia danych. [W Ollama kontekst modelu dla Chat Completions ustawia się po stronie serwera/Modelfile, a nie standardowym polem żądania OpenAI](https://docs.ollama.com/api/openai-compatibility#setting-the-local-context-size). Kontekst musi pomieścić oba zestawy napisów, instrukcję i cały wynik; jego zwiększenie wymaga odpowiedniej pamięci. Aplikacja nie zmienia ustawień serwera.

Adapter używa [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat): `POST /chat/completions`, pola `model`, `messages` i `stream:false`. Klucz, jeśli ustawiony, jest przesyłany jako `Authorization: Bearer …`. Aplikacja nie korzysta z endpointu listowania modeli ani z wymagających osobnego wsparcia opcji structured outputs. Usługa z innym API wymaga osobnego adaptera.

Wiadomość systemowa opisuje synchronizację oraz wymaga odpowiedzi JSON. Wiadomość użytkownika zawiera:

```json
{
  "duration_ms": 7200000,
  "english": [{"id": "en:1", "text": "Hello.", "start_ms": 1000, "end_ms": 2000}],
  "polish": [{"id": "pl:1", "text": "Cześć.", "start_ms": 4000, "end_ms": 5000}]
}
```

ID wynika z pozycji segmentu w przygotowanym pliku, nie z numeru wpisanego w SRT. Jest stabilne dla tego samego wejścia, nawet jeśli SRT zawiera powtarzające się numery. Model zwraca w `choices[0].message.content` tekst JSON:

```json
{"segments": [{"id": "pl:1", "start_ms": 1000, "end_ms": 2000}]}
```

Wszystkie polskie ID muszą wystąpić dokładnie raz. Nieznane ID, brakujące/duplikowane ID, inne pola, niecałkowite milisekundy, odwrócone lub puste przedziały i czasy poza `0..duration_ms` powodują czytelny błąd. Odpowiedź z `finish_reason=length` lub innym niepełnym zakończeniem jest odrzucana. Dopuszczalny jest jeden blok Markdown zawierający wyłącznie JSON. Nie naprawiamy i nie uzupełniamy niepełnych odpowiedzi.

Aplikacja ustawia wynik w oryginalnej kolejności PL, dołącza oryginalny tekst (w tym podziały wierszy i tagi) i zapisuje UTF-8 SRT. Nie publikuje go automatycznie w bibliotece mediów. Niepoprawny segment wejściowy blokuje żądanie, zamiast być pomijany. Przed wysłaniem i zapisaniem wyniku sprawdzane są hashe przygotowanych źródeł. Zmiana referencji unieważnia stary wynik.

## Czas i błędy

W **Ustawieniach AI** dostępna jest przewijalna **Konsola AI**, odświeżana co 2 sekundy. Pokazuje komunikaty testu i synchronizacji, ID zadania, status HTTP, czas odpowiedzi, pełną odpowiedź API (w tym `message.content`, `reasoning_content`, `finish_reason` i `usage`, jeśli występują) oraz błąd walidacji. Odpowiedź jest zapisywana także wtedy, gdy model zwraca zwykły tekst zamiast JSON. Nie oznacza to dostępu do wewnętrznych logów serwera ani do postępu generowania: odpowiedź pojawia się po jej odebraniu.

Historia jest zapisywana w SQLite, obejmuje ostatnie 100 wpisów i pozostaje po restarcie aplikacji. Każdy wpis ma limit 131072 znaków; skrócenie jest jawnie oznaczone. Można wyczyścić historię przyciskiem. Konsola może zawierać treść dialogów i odpowiedzi modelu. Nie zapisujemy nagłówków uwierzytelnienia ani wejściowego promptu; wystąpienia skonfigurowanego klucza API w odpowiedzi są maskowane. Starszych błędów sprzed tej poprawki nie można odtworzyć — potrzebna jest nowa odpowiedź modelu.

Czas to okres od rozpoczęcia wysyłania pojedynczego żądania do odebrania całej odpowiedzi HTTP. Obejmuje sieć i pracę serwera/modelu; nie obejmuje przygotowania, OCR, walidacji ani zapisu SRT. Opcjonalnie pokazujemy `prompt_tokens`, `completion_tokens` i `total_tokens` z `usage`. Nie szacujemy brakujących tokenów, nie ponawiamy automatycznie żądania i nie prowadzimy benchmarków.

Wszystkie napisy trafiają do jednego żądania. Model musi mieć wystarczający kontekst i limit wyjścia dla kompletnego wyniku. Przekroczenie tych limitów daje błąd API albo odrzucenie niepełnej odpowiedzi. Nie dodano automatycznego dzielenia filmu. Timeout w ustawieniach ogranicza całe oczekiwanie (1–3600 s); jeśli przed aplikacją działa proxy, jego limit musi pozwalać na takie oczekiwanie. Przerwanie oczekiwania nie gwarantuje zatrzymania pracy po stronie niezależnego serwera.

Błędy HTTP pokazują status bez treści odpowiedzi dostawcy. Odpowiedź ma limit 8 MiB. Redirecty nie są śledzone. Niepoprawna odpowiedź nie tworzy częściowego SRT; nowa próba usuwa poprzedni wynik zadania. Wynik zapisuje się w katalogu zadania i jest kopiowany do archiwum ostatnich 30 tytułów.

Poprawny test połączenia potwierdza tylko małe żądanie testowe. Jeśli pełna synchronizacja nie zwróci JSON, komunikat rozróżnia pustą treść, tekst zamiast JSON, niepoprawny JSON oraz błędny format odpowiedzi HTTP. Podaje też czas żądania i dla błędnego JSON pozycję błędu oraz długość treści, bez ujawniania odpowiedzi modelu. Przyczynę sprawdź w odpowiedzi serwera modelu: `choices[0].message.content` i `finish_reason`. Bez tego nie można odróżnić ograniczeń modelu od niezgodnego formatu generowanej odpowiedzi.

API aplikacji:

```text
GET/PUT /api/settings/ai
POST    /api/settings/ai/test
POST    /api/tasks/{job_id}/ai-sync
POST    /api/tasks/{job_id}/ai-translate
GET     /api/tasks/{job_id}/ai-translate
GET     /api/tasks/{job_id}/ai-translate/download
GET     /api/tasks/{job_id}/ai-sync
GET     /api/tasks/{job_id}/ai-sync/download
```

POST synchronizacji wymaga `polish_file` (przygotowany `archiveName`) oraz `reference_source_id` (np. `embedded:4`). Równoległe żądanie dla tego samego zadania zwraca 409; podczas żądania nie można przebudować jego referencji. Po restarcie nie ponawiamy żądania automatycznie.


## Tłumaczenie na PL

Wybierz **Przygotuj do tłumaczenia**, poczekaj na gotowy workpack i kliknij **Przekaż do AI** w panelu **Tłumaczenie przez AI**. Nie są potrzebne istniejące PL. Referencja graficzna wymaga zakończonego OCR; zewnętrzna referencja źródłowa nadal wymaga potwierdzenia. Aplikacja wysyła jedno żądanie z segmentami referencji. Model zwraca `{"segments":[{"id":"ref:1","text":"Polski tekst."}]}` (starsze referencje EN używają ID `en:1`) — tekst dla każdego ID referencji dokładnie raz, bez nowych czasów. Aplikacja zachowuje kolejność i czasy referencji, sprawdza kompletność ID i poprawność tekstu SRT. Walidacja nie ocenia jakości językowej tłumaczenia.

POST `/api/tasks/{job_id}/ai-translate` wymaga tylko `reference_source_id`. Poprawny wynik zapisuje się jako `ai-translation.pl.srt`; metryki i hashe znajdują się w `ai-translation.json`. Wynik można pobrać z panelu oraz z Archiwum, gdzie ma oznaczenie tłumaczenia. Zmiana wejścia unieważnia wynik w panelu; wcześniej zarchiwizowana wersja pozostaje dostępna w ramach retencji 30 tytułów.

## Koszt API i OpenRouter

Dla OpenRouter ustaw **Adres API** na `https://openrouter.ai/api/v1`, nazwę modelu w formacie dostawcy oraz własny klucz API. Obsługa JSON i `reasoning_effort` zależy od wybranego modelu/dostawcy; domyślne opcje pomijają te pola.

Aplikacja zachowuje numeryczne, nieujemne i skończone `usage.cost`, również zero. Obok czasu i tokenów konsola pokazuje koszt zgłoszony przez API, a na końcu podsumowanie kosztu całej operacji. Obecnie synchronizacja, tłumaczenie i test to osobne operacje po jednym żądaniu; wcześniejsze próby i test połączenia nie są doliczane do nowej próby. Koszt jest widoczny również przy odrzuceniu płatnej odpowiedzi (np. niepełnym JSON lub brakujących ID). Nie jest estymowany z cennika ani tokenów. Jeśli API nie podaje kosztu, podsumowanie wskazuje brak danych, a nie bezpłatną operację.

OpenRouter podaje koszt obciążenia konta w USD. Dla innych endpointów, które zwracają `usage.cost`, aplikacja pokazuje wartość w jednostkach API bez zgadywania waluty. Nie wysyłamy dodatkowych pól ani żądań do pobrania kosztu: [OpenRouter Usage Accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting) zwraca go w standardowej odpowiedzi. Historia w ustawieniach obejmuje testy, synchronizację i tłumaczenie; podsumowanie kosztu jest zapisywane także w diagnostyce nieudanego żądania, jeśli koszt został odebrany.

## Referencja w innym języku

Możesz użyć francuskich lub innych wykrytych napisów zamiast angielskich, zarówno do synchronizacji istniejących PL, jak i tłumaczenia na PL. Wybierz je w polu **Wybierz wykrytą referencję**, kliknij **Zbuduj ponownie z tą referencją**, a po przygotowaniu **Przekaż do AI**. Napisy osadzone wybierane są automatycznie z preferencją EN; zewnętrzne wymagają potwierdzenia. Język wynika z metadanych ścieżki lub oznaczenia w nazwie, np. `Film.fr.srt`.

Dla graficznej referencji OCR używa jej języka. Obraz workera zawiera dane angielskie, francuskie, niemieckie, hiszpańskie, włoskie, portugalskie i polskie. Starszy obraz workera wymaga aktualizacji. Brak danych języka blokuje OCR z czytelnym błędem.
