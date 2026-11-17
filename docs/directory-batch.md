# Przetwarzanie katalogów

Podaj bezwzględną ścieżkę katalogu widoczną w kontenerze. Aplikacja skanuje podkatalogi i przetwarza pliki kolejno; cały katalog zajmuje jedno miejsce w kolejce i archiwum. Pomija dowiązania, ukryte pliki i katalogi oraz dodatki, zwiastuny i próbki.

## Dopasowanie i decyzje

Napisy są szukane obok wideo, najpierw według pełnej nazwy bazowej, np. `Serial.S01E02.WEB.mkv` → `Serial.S01E02.WEB.pl.srt`, następnie według tożsamości materiału i numeru odcinka. Przy różnych wydaniach tego samego odcinka niejednoznaczne napisy nie są automatycznie przypisywane.

Wymagany wybór lub potwierdzenie zewnętrznej referencji zatrzymuje przygotowanie przed ekstrakcją/OCR. W panelu statusu potwierdź wybór albo użyj **Pomiń bieżący plik**. Analiza zbiera raporty bez tego potwierdzenia i bez końcowego ZIP-a.

Błąd pliku nie zatrzymuje pozostałych. **Ponów błędne pliki** zachowuje gotowe wyniki, a **Wznów katalog** kontynuuje przerwaną partię po restarcie. Wstrzymane i przerwane katalogi są chronione przed retencją.

## Wyniki i limity

Przygotowanie daje jeden ZIP z manifestem, raportem i katalogami `items/` dla poszczególnych materiałów, bez wideo i zagnieżdżonych ZIP-ów. Świadome pominięcie nie oznacza błędu; braki i błędy są oznaczane w raporcie. Przekroczenie limitu paczki blokuje jej publikację.

| Parametr | Domyślnie |
| --- | --- |
| `DIRECTORY_MAX_ITEMS` | 500 plików wideo |
| `DIRECTORY_MAX_ARCHIVE_BYTES` | 1 GiB |
| `DIRECTORY_MAX_FILES` | 10000 plików w ZIP-ie |

Limity `WORKPACK_MAX_*` nadal obowiązują dla każdego pojedynczego materiału.

**Przekaż do AI** wysyła gotowe materiały kolejno, osobnym żądaniem dla każdego pliku. Wynik to wspólny ZIP z SRT i manifestem; przy błędzie dostępne są wyniki częściowe. Ponowienie wykorzystuje poprawne wyniki przy zgodnych wejściach i ustawieniach modelu. Po restarcie wymaga ręcznego uruchomienia. Podczas oczekiwania i pracy AI przebudowa partii jest zablokowana.
