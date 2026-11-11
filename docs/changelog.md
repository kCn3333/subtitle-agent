# Changelog

Historia opublikowanych wydań Subtitle Agent. Wersje mają format `MAJOR.MINOR.PATCH`; tag Git i numer w interfejsie odpowiadają wartości `__version__` w [app/__init__.py](../app/__init__.py).

## Niewydane

- W nagłówku pozostaje tylko wskaźnik OCR, wyrównany do prawego rogu; usunięto stały napis ONLINE i wskaźnik API wraz z jego automatycznym odpytywaniem.
- Nawigacja ma postać zakładek wyrównanych do prawej, z wyróżnieniem aktywnej strony bez błękitnego podkreślenia i większym odstępem od belki tytułowej.
- Na ekranach mobilnych menu jest rozwijane przyciskiem hamburgera; obsługuje zamykanie klawiszem Escape i kliknięciem poza menu.

## 1.0.0 — 2026-11-10

Pierwsze oznaczone wydanie projektu, obejmujące dotychczasowy zakres aplikacji. Wcześniejszy rozwój jest dostępny w historii commitów.

### Analiza i przygotowanie materiałów

- Analiza filmów, odcinków i katalogów: ścieżki osadzone, napisy zewnętrzne, referencje i kandydaci PL.
- Dobór referencji w wielu językach z preferencją angielskiego; potwierdzanie zewnętrznego wzorca i niejednoznacznych wyborów.
- Obsługa napisów tekstowych SRT, ASS, SSA i VTT oraz referencji graficznych PGS/VobSub przez osobny worker OCR.
- Workpacki ZIP z materiałami źródłowymi, manifestem i raportem. Walidacja obecności i sum SHA-256 deklarowanych artefaktów, także zagnieżdżonych referencji.
- Diagnostyka przesunięcia i dryfu z kandydatami standardowych FPS. Słabe modele nie są prezentowane jako gotowa korekta; flaga `forced` pozostaje widoczna.
- Niepewne napisy PL mogą zostać dołączone do diagnostyki z blokadą automatycznego użycia.

### Katalogi i AI

- Trwała kolejka plików katalogu, dopasowanie napisów do odcinków, wstrzymanie przy wymaganej decyzji oraz pomijanie, wznawianie i ponawianie błędnych pozycji.
- Jeden końcowy workpack dla katalogu ze wspólnym manifestem i skróconym raportem.
- Synchronizacja i tłumaczenie przez API zgodne z Chat Completions. Każdy odcinek trafia do AI w osobnym żądaniu, kolejno; wyniki można pobrać jako wspólny ZIP.
- Osobny pomarańczowy postęp kolejki AI, lista odcinków, historia odpowiedzi oraz czas, tokeny i koszt, jeśli zwraca je dostawca.
- Lista zapisanych konfiguracji modeli: dodawanie, wybór, aktualizacja, usuwanie i testowanie bez zmiany aktywnego modelu. Domyślny limit 10 pozycji jest konfigurowalny.
- Walidacja wyników SRT: kompletność segmentów, format i wymagane zachowanie tekstu lub czasów materiału wejściowego.

### Interfejs i utrzymanie

- Oddzielne panele AI i ustawień aplikacji. Zapis wybranych ustawień bez restartu, w tym limitu pozycji archiwum i listy modeli.
- Archiwum domyślnie 30 tytułów lub katalogów; ochrona danych aktywnych i wstrzymanych operacji przed usunięciem.
- Kompaktowe listy postępu, automatyczne przewijanie do bieżącego odcinka i reset widoku przy nowym zadaniu.
- Logo robota, favicony, wersja wydania i odnośnik do projektu na GitHubie w nagłówku oraz na ekranie logowania.
- Logowanie, sesje, ochrona CSRF i blokady prób logowania. Biblioteka mediów pozostaje montowana tylko do odczytu.
- Obrazy aplikacji i workera OCR publikowane w GHCR przez GitHub Actions.

### Zakres i ograniczenia

- Aplikacja działa jako jeden proces; kolejki nie są współdzielone między instancjami.
- Żądania AI nie są automatycznie ponawiane po restarcie.
- Jakość synchronizacji, tłumaczenia i OCR oraz kompletność dialogów wymagają oceny użytkownika.

### Wdrożenie wydania

Po zakończeniu workflow `Container` tag `v1.0.0` udostępnia obrazy:

```text
ghcr.io/kcn3333/subtitle-agent:1.0.0
ghcr.io/kcn3333/subtitle-agent-ocr:1.0.0
```

Można przypiąć oba obrazy do tej samej wersji w Compose. Zachowaj dotychczasowy wolumen `/data` i mounty mediów tylko do odczytu. Przed aktualizacją wykonaj kopię danych. Konfiguracja i baza pozostają w istniejącym wolumenie; nie usuwaj go przy odtwarzaniu kontenerów.

## Przygotowanie kolejnego wydania

1. Zmień `__version__` w `app/__init__.py` i dodaj sekcję zmian z datą w tym pliku.
2. Uruchom testy kontenerowe i testy UI opisane w [README](../README.md#dokumentacja-i-rozwój), następnie zatwierdź i wypchnij zmiany.
3. Utwórz tag `vMAJOR.MINOR.PATCH` wskazujący zweryfikowany commit. Workflow sprawdza zgodność tagu z wersją w kodzie i buduje oba obrazy.
4. Sprawdź wynik workflow i opublikuj GitHub Release z opisem z odpowiedniej sekcji changeloga. Nie przenoś istniejących tagów wydań.

[Wydania na GitHubie](https://github.com/kCn3333/subtitle-agent/releases) · [README](../README.md)
