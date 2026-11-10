# Przetwarzanie katalogu

Podaj w zwykłym formularzu absolutną ścieżkę do katalogu sezonu, serialu lub filmów, widoczną wewnątrz kontenera. Aplikacja skanuje podkatalogi, zapisuje listę w SQLite i wykonuje pliki kolejno w ramach jednego zadania. Liczba plików nie jest ograniczona liczbą miejsc `MAX_QUEUED_JOBS` — katalog zajmuje jedno miejsce.

Skanowanie pomija dowiązania, ukryte katalogi/pliki oraz standardowe dodatki, zwiastuny i próbki. Obsługuje te same rozszerzenia wideo co pojedyncze zadanie. Odcinki są sortowane według rozpoznanego sezonu i numeru; `S01E01-E02` jest jednym materiałem. Nie pobiera metadanych z Jellyfina ani nie zmienia nazw plików.

## Dopasowanie napisów

Napisy są wyszukiwane obok danego pliku wideo. Najpierw sprawdzana jest pełna nazwa bazowa wideo z dopiskami języka, flag i opisu ścieżki, np. `Show.S01E02.WEB.mkv` → `Show.S01E02.WEB.pl.srt`. Najdłuższa zgodna nazwa ma pierwszeństwo. Gdy brak zgodnej nazwy bazowej, używany jest dotychczasowy parser tożsamości filmu/serialu i identyfikatorów `SxxEyy` lub `1x02`.

Sam identyfikator odcinka nie wystarcza do automatycznego przypisania napisów, gdy w katalogu są różne wydania tego odcinka. Takie napisy mają niejednoznaczną tożsamość i nie są automatycznie dołączane jako polska baza. Obsługiwane są dotychczasowe aliasy języków oraz dopiski `default`, `forced`/`foreign`, `sdh`, `cc` i `hi` przy rozpoznanym języku.

## Kolejka i decyzje

Panel statusu pokazuje listę materiałów, stan każdego z nich, postęp bieżącego pliku i całego katalogu. Dla przygotowania synchronizacji/tłumaczenia niejednoznaczna referencja, brak referencji albo referencja zewnętrzna wymagają decyzji. Kolejka zatrzymuje się przed ekstrakcją/OCR, więc następny plik nie jest rozpoczynany. Wybierz referencję w tym samym panelu i potwierdź albo kliknij „Pomiń bieżący plik”. Decyzja dotyczy wyłącznie tego materiału.

Tryb „Sprawdź napisy” zbiera raporty bez konieczności wyboru referencji i bez końcowego ZIP-a, tak jak dla pojedynczego pliku. Błąd przetwarzania jednego materiału nie zatrzymuje pozostałych; jest jawnie oznaczony w wyniku. „Ponów błędne pliki” uruchamia ponownie tylko nieudane pozycje, zachowując gotowe wyniki. Po restarcie zadanie pozostaje przerwane, z zachowaną listą i gotowymi pozycjami. Przycisk „Wznów katalog” uruchamia niedokończoną pozycję. Aplikacja nie uruchamia automatycznie płatnych operacji AI.

## Wynik

Przygotowanie synchronizacji/tłumaczenia kończy się jednym ZIP-em: główny manifest, REQUEST.md, raport zbiorczy i katalogi `items/001-…`, `items/002-…` z materiałami poszczególnych plików. W paczce nie ma zagnieżdżonych ZIP-ów ani wideo. Ścieżki w manifestach odnoszą się do głównego katalogu paczki. Wszystkie zadeklarowane artefakty są sprawdzane przez walidator ZIP-a.

Raport pokazuje wspólne parametry wideo/referencji, grupuje różnice i powtarzające się ostrzeżenia. Szczegóły pozostają w materiałach każdego pliku. Braki i błędy oznaczają wynik niekompletny. Świadome pominięcia przez użytkownika są zapisane w manifeście, ale nie oznaczają błędu: pozostałe poprawnie przygotowane materiały kończą się statusem WORKPACK_READY i postępem 100%. Przekroczenie limitów końcowej paczki blokuje jej publikację zamiast usuwać z niej pliki.

Domyślne limity: `DIRECTORY_MAX_ITEMS=500`, `DIRECTORY_MAX_ARCHIVE_BYTES=1073741824` (1 GiB), `DIRECTORY_MAX_FILES=10000`. Limity `WORKPACK_MAX_*` nadal obowiązują materiały każdego pojedynczego pliku. Zadania wewnętrzne nie zajmują oddzielnych pozycji w archiwum o konfigurowalnej liczbie pozycji (domyślnie 30). Wstrzymane/przerwane katalogi i ich materiały są chronione przed retencją; gotowy katalog zajmuje jedną pozycję archiwum.

## Przekazanie katalogu do AI

Po zbudowaniu workpacka dostępny jest przycisk „Przekaż do AI”. Obejmuje gotowe materiały; pliki świadomie pominięte lub niegotowe nie są wysyłane. Gdy istnieje kilka przygotowanych plików PL dla odcinka, interfejs umożliwia ich wybór. Cała partia zajmuje jedno miejsce w kolejce AI, a każdy odcinek jest przetwarzany osobnym żądaniem, z własną referencją i napisami PL.

Lista pokazuje bieżący odcinek AI i zakończone pozycje. Wyniki można pobrać jako jeden ZIP z plikami SRT i manifestem oraz z archiwum katalogu. Koszty i tokeny są przypisane do katalogu. Błąd jednego odcinka nie usuwa gotowych wyników; częściowa paczka pozostaje dostępna do pobrania.

Ponowne przekazanie tych samych materiałów wykorzystuje poprawne zapisane wyniki przy niezmienionych wejściach i ustawieniach modelu, wykonując pozostałe pozycje. Zmiana pliku PL lub ustawień modelu wymaga nowego przetworzenia odpowiednich materiałów. Po restarcie aplikacja nie ponawia żądań automatycznie — użytkownik musi ponownie kliknąć przycisk. W czasie oczekiwania i pracy AI przebudowa partii jest zablokowana.
