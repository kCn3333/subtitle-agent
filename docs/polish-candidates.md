# Kandydaci polskich napisów

Polskie materiały w `manifest.polish_candidates` mają wspólny kontrakt eksportu: `archiveName`,
`originalName`, `sha256`, `cueCount`, `identityMatch`, `eligibleByDefault`, `reasonCode`,
`compatibilityWarnings` oraz `exportPurpose`. W PREPARE_SYNC `SYNCHRONIZATION` oznacza kandydata
zakwalifikowanego do wyboru, a `MANUAL_REVIEW` materiał diagnostyczny z zablokowanym
użyciem automatycznym. Obecność w ZIP-ie nie potwierdza zgodności treści ani kompletności
(`contentCompatibility=UNDETERMINED`, `completeness=UNVERIFIED`). Duże przekroczenie czasu
może wymagać offsetu lub korekty tempa; samo nie dowodzi innej produkcji. Raport zachowuje
surowy zakres i, przy wiarygodnym modelu, pokazuje `proposedCorrectedRange` bez zmiany źródła.
Paczka diagnostyczna pozostaje do pobrania przy `WORKPACK_INCOMPLETE`; w katalogu jej pozycja
jest zakończonym eksportem wymagającym przeglądu, a nie błędem wykonania.

To rozszerzenie schematu `subtitle-workpack-v2`: dotychczasowe pola i ścieżki pozostają
aktualne. Starsze paczki bez `exportPurpose` są obsługiwane przez odbiorców; brak
`eligibleByDefault` zachowuje dotychczasową kwalifikację. W nowych paczkach wartość
`eligibleByDefault=false` zawsze blokuje wybór w GUI AI oraz żądanie do API AI,
również gdy obok jest kandydat zakwalifikowany. Ocena modelu nie odblokowuje wyboru.
