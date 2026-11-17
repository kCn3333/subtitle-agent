# <img src="app/static/logo.svg" width="48" height="48" alt=""> Subtitle Agent

[![CI](https://img.shields.io/github/actions/workflow/status/kCn3333/subtitle-agent/container.yml?branch=main&style=flat-square&label=CI&logo=githubactions&logoColor=white&labelColor=131b26)](https://github.com/kCn3333/subtitle-agent/actions/workflows/container.yml)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-74c7ec?style=flat-square&logo=python&logoColor=74c7ec&labelColor=131b26)](Dockerfile)
[![FastAPI](https://img.shields.io/badge/FastAPI-backend-3ddc84?style=flat-square&logo=fastapi&logoColor=3ddc84&labelColor=131b26)](requirements.txt)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-media-3ddc84?style=flat-square&logo=ffmpeg&logoColor=3ddc84&labelColor=131b26)](Dockerfile)
[![Tesseract OCR](https://img.shields.io/badge/Tesseract-OCR-f59a45?style=flat-square&labelColor=131b26)](Dockerfile.ocr)
[![JavaScript](https://img.shields.io/badge/JavaScript-UI-f59a45?style=flat-square&logo=javascript&logoColor=f59a45&labelColor=131b26)](app/static/)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-74c7ec?style=flat-square&logo=docker&logoColor=74c7ec&labelColor=131b26)](compose.example.yml)

Subtitle Agent analizuje napisy filmów i seriali, synchronizuje istniejące napisy PL oraz tłumaczy napisy źródłowe na polski przez API AI. Obsługuje pojedyncze pliki i całe katalogi.

## Funkcje

- Analiza ścieżek osadzonych i zewnętrznych napisów SRT, ASS, SSA i VTT.
- Odczyt napisów graficznych PGS/VobSub przez osobny worker OCR.
- Synchronizacja i tłumaczenie przez API zgodne z Chat Completions.
- Paczki ZIP z materiałami źródłowymi i raportem oraz wersjonowane wyniki SRT.
- Kolejka katalogów, wznawianie pracy, archiwum i powiadomienia przeglądarkowe.

## Uruchomienie

Wymagany jest Docker z Compose. Obrazy aplikacji i workera OCR są pobierane z GHCR.

1. Pobierz [compose.example.yml](compose.example.yml) i [.env.example](.env.example) do jednego katalogu. Skopiuj `.env.example` jako `.env` i uzupełnij dane logowania oraz adres aplikacji.
2. Dostosuj mounty biblioteki w Compose i skonfiguruj dostęp HTTPS przez reverse proxy lub tunel.
3. Uruchom:

   ```sh
   docker compose -f compose.example.yml up -d
   ```

4. Otwórz adres z `AUTH_PUBLIC_URL` i zaloguj się. W zakładce **AI** podaj adres API, model i klucz, jeśli jest wymagany. Do analizy i przygotowania ZIP-a konfiguracja AI nie jest potrzebna.

### Najważniejsze ustawienia

| Ustawienie | Znaczenie |
| --- | --- |
| `AUTH_USERNAME`, `AUTH_PASSWORD` | Dane logowania w `.env`; hasło musi mieć 16–1024 znaki. |
| `AUTH_PUBLIC_URL` | Adres HTTPS aplikacji bez ścieżki, np. `https://napisy.example.com`. |
| `AUTH_TRUSTED_PROXIES` | Zaufane adresy IP/CIDR proxy, rozdzielone przecinkami. Ustaw adres widziany przez aplikację. |
| `ports` | Domyślnie `127.0.0.1:8080:8080`. Przy proxy na innym hoście zmień adres nasłuchu i ogranicz dostęp do proxy. |
| Mounty mediów, `SUBTITLE_AGENT_MEDIA_ROOTS` | Podłącz katalogi hosta z `:ro`; lista dozwolonych ścieżek musi odpowiadać mountom w kontenerze. Takie ścieżki podajesz w interfejsie. |
| `subtitle_agent_data:/data` | Trwała baza, ustawienia, pliki robocze i wyniki. Zachowaj wolumen przy aktualizacji. |
| `OCR_WORKER_URL` | Adres workera w sieci Compose; domyślnie `http://subtitle-ocr-worker:8090`. Portu OCR nie publikuj na hoście. |
| `ARCHIVE_MAX_TITLES` | Liczba tytułów lub katalogów w archiwum, domyślnie `30`. Dostępna także w Ustawieniach. |
| `image` | Oba obrazy domyślnie używają `latest`; konkretną wersję ustaw dla aplikacji i OCR. |

**Biblioteka mediów jest montowana tylko do odczytu.**

[Obsługa i konfiguracja AI](docs/ai-api-sync.md) · [Wdrożenie](docs/deployment.md) · [Ustawienia](docs/app-settings.md) · [Katalogi](docs/directory-batch.md) · [Historia zmian](docs/changelog.md) · [Licencja MIT](LICENSE)
