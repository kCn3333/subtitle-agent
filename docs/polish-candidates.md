# Kwalifikacja polskich napisów

Obecność napisów w ZIP-ie nie potwierdza zgodności treści ani kompletności. W synchronizacji `exportPurpose=SYNCHRONIZATION` oznacza kandydata do wyboru, a `MANUAL_REVIEW` — materiał wyłącznie do ręcznej oceny. `eligibleByDefault=false` blokuje użycie w interfejsie i API AI; ocena modelu nie znosi blokady.

Manifest `subtitle-workpack-v2` opisuje kandydata polami `archiveName`, `originalName`, `sha256`, `cueCount`, `identityMatch`, `eligibleByDefault`, `reasonCode`, `compatibilityWarnings` i `exportPurpose`. Domyślne oceny to `contentCompatibility=UNDETERMINED` i `completeness=UNVERIFIED`. Starsze paczki bez nowych pól zachowują wcześniejsze zasady kwalifikacji.

Duże przekroczenie czasu może wynikać z offsetu lub tempa i samo nie dowodzi innej produkcji. `proposedCorrectedRange` pokazuje wiarygodną hipotezę bez zmiany źródła.

Paczka diagnostyczna pozostaje dostępna przy `WORKPACK_INCOMPLETE`. W katalogu oznacza zakończony eksport wymagający przeglądu, a nie błąd wykonania.
