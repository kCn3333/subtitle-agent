# Ustawienia aplikacji

Zakładka **Ustawienia** zapisuje zmiany bez restartu w SQLite na `/data`. Zapisane wartości mają pierwszeństwo przed Compose/env i obowiązują kolejne rozpoczynane zadania; trwające zadanie zachowuje swoją konfigurację. Zapis nie przebudowuje gotowych paczek.

| Grupa | Parametry |
| --- | --- |
| Przetwarzanie | `FFPROBE_TIMEOUT_SECONDS`, `FFMPEG_TIMEOUT_SECONDS`, `WORKPACK_REFERENCE_SCORE_MARGIN`, `WORKPACK_MAX_POLISH_CANDIDATES` |
| Paczka filmu/odcinka | `WORKPACK_MAX_ARCHIVE_BYTES`, `WORKPACK_MAX_FILES` |
| Katalog | `DIRECTORY_MAX_ITEMS`, `DIRECTORY_MAX_ARCHIVE_BYTES`, `DIRECTORY_MAX_FILES` |
| Modele AI | `AI_MAX_SAVED_MODELS`: domyślnie 10, zakres 1–100; zmniejszenie wymaga wcześniejszego usunięcia nadmiarowych modeli |
| Archiwum | `ARCHIVE_MAX_TITLES`: domyślnie 30, zakres 1–10000 |
| OCR | `OCR_WORKER_URL`, `OCR_TIMEOUT_SECONDS`, `OCR_MAX_OUTPUT_BYTES`, `INCLUDE_GRAPHIC_REFERENCE` |

Rozmiary w panelu są podane w MiB. Zmiana limitów OCR w aplikacji nie zmienia limitów samego workera. `INCLUDE_GRAPHIC_REFERENCE` dołącza źródłowe napisy graficzne do ZIP-a synchronizacji.

Limit archiwum obowiązuje od razu; usuwanie odbywa się przy najbliższym porządkowaniu. Wszystkie wersje jednego tytułu zajmują jedną pozycję, podobnie cały katalog. Aktywne i wstrzymane operacje są chronione, więc dane mogą czasowo przekraczać limit. Zwiększenie limitu nie przywraca usuniętych plików.

Adres nasłuchu, logowanie, proxy, mounty i pojemność kolejek zmieniaj w [Compose](../compose.example.yml). Konfigurację dostawcy opisuje [obsługa AI](ai-api-sync.md).

## Powiadomienia

**Powiadomienia w przeglądarce** są domyślnie włączone. Wymagają zgody przeglądarki, HTTPS (lub localhost) i otwartej karty aplikacji, która może działać w tle. Informują o zakończeniu, błędzie lub wymaganej decyzji; katalog otrzymuje jedno powiadomienie końcowe.

Preferencja jest lokalna dla przeglądarki i adresu aplikacji. Po odmowie zmień zgodę w ustawieniach witryny. Zamknięcie lub uśpienie karty oraz ograniczenia systemowe mogą zablokować powiadomienia; aplikacja nie używa Web Push.
