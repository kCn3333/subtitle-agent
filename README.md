# <img src="app/static/logo.svg" width="48" height="48" alt=""> Subtitle Agent

[![Python 3.12](https://img.shields.io/badge/Python-3.12-74c7ec?style=flat-square&logo=python&logoColor=74c7ec&labelColor=131b26)](Dockerfile)
[![FastAPI](https://img.shields.io/badge/FastAPI-backend-3ddc84?style=flat-square&logo=fastapi&logoColor=3ddc84&labelColor=131b26)](requirements.txt)
[![SQLite](https://img.shields.io/badge/SQLite-storage-74c7ec?style=flat-square&logo=sqlite&logoColor=74c7ec&labelColor=131b26)](app/services/job_manager.py)
[![FFmpeg](https://img.shields.io/badge/FFmpeg-media-3ddc84?style=flat-square&logo=ffmpeg&logoColor=3ddc84&labelColor=131b26)](Dockerfile)
[![Tesseract OCR](https://img.shields.io/badge/Tesseract-OCR-f59a45?style=flat-square&labelColor=131b26)](Dockerfile.ocr)
[![JavaScript](https://img.shields.io/badge/JavaScript-UI-f59a45?style=flat-square&logo=javascript&logoColor=f59a45&labelColor=131b26)](app/static/)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-74c7ec?style=flat-square&logo=docker&logoColor=74c7ec&labelColor=131b26)](compose.example.yml)

Aplikacja webowa do analizy napisów, synchronizacji i tłumaczenia na język polski. Przyjmuje ścieżkę do filmu, odcinka lub katalogu. Przygotowuje workpacki ZIP z materiałami źródłowymi i metadanymi oraz pliki wynikowe SRT z API modelu.

**Biblioteka mediów jest montowana tylko do odczytu.** Wyniki trafiają do osobnego wolumenu danych i są dostępne do pobrania.

[Uruchomienie](#uruchomienie) · [Przetwarzanie katalogów](docs/directory-batch.md) · [Konfiguracja AI](docs/ai-api-sync.md) · [Wdrożenie](docs/deployment.md)

## Możliwości

| Operacja | Dane wejściowe | Wynik |
|---|---|---|
| Analiza | Film, odcinek lub katalog | Raport ścieżek, referencji, kandydatów PL i zgodności czasowej |
| Przygotowanie synchronizacji | Referencja i pasujące napisy PL | Workpack ZIP i synchronizacja przez AI |
| Przygotowanie tłumaczenia | Referencja źródłowa | Workpack ZIP i tłumaczenie przez AI |

- **Referencje:** osadzone lub zewnętrzne, z preferencją języka angielskiego i PGS. Niejednoznaczny wybór oraz użycie zewnętrznego wzorca wymagają potwierdzenia.
- **Formaty:** tekstowe SRT, ASS, SSA i VTT; referencje graficzne PGS/VobSub rozpoznawane przez osobny worker OCR z Tesseractem i seconv.
- **AI:** API zgodne z Chat Completions, z konfiguracją adresu, modelu i klucza w interfejsie. Synchronizacja zachowuje polski tekst; tłumaczenie zachowuje czasy referencji.
- **Wyniki:** wersjonowane pliki SRT, manifesty, sumy SHA-256 i archiwum pobrań. Konsola pokazuje przebieg pracy, czas, tokeny i koszt, jeśli dostawca go zwraca.

## Przetwarzanie katalogów

Po podaniu katalogu aplikacja skanuje podkatalogi, rozpoznaje filmy i odcinki oraz tworzy trwałą kolejkę. Napisy przypisuje najpierw według pełnej nazwy bazowej wideo, następnie według tożsamości materiału i numeru odcinka. Różne wydania tego samego odcinka nie otrzymują automatycznie niejednoznacznych napisów.

Panel statusu pokazuje listę plików i postęp bieżącej pozycji. Wymagany wybór referencji wstrzymuje całą partię przed ekstrakcją/OCR. Dostępne są potwierdzenie, pominięcie pliku, wznowienie po restarcie i ponowienie tylko błędnych pozycji.

Przygotowanie kończy się **jednym ZIP-em dla katalogu**: osobne materiały każdego pliku, wspólny manifest i skrócony raport parametrów wspólnych oraz różnic. Analiza katalogu zwraca raport bez ZIP-a. Przycisk „Przekaż do AI” przetwarza gotowe pozycje kolejno, osobnymi żądaniami, i udostępnia jeden ZIP z wynikami SRT. Pominięcie pliku przez użytkownika nie oznacza błędu zadania.

Szczegóły dopasowania, limitów i struktury paczki: [przetwarzanie katalogów](docs/directory-batch.md).

## Uruchomienie

Wymagany jest Docker z Compose. Zestaw obejmuje aplikację i worker OCR; obrazy są pobierane z GHCR.

```bash
cp .env.example .env
```

Uzupełnij `.env` i dostosuj ścieżki biblioteki w [compose.example.yml](compose.example.yml):

| Ustawienie | Wartość |
|---|---|
| `AUTH_USERNAME` | Login do aplikacji |
| `AUTH_PASSWORD` | Własne hasło o długości 16–1024 znaków |
| `AUTH_PUBLIC_URL` | Publiczny adres HTTPS aplikacji, bez ścieżki |
| `AUTH_TRUSTED_PROXIES` | Adresy/CIDR zaufanych pośredników, jeśli aplikacja działa za proxy |
| Mounty mediów | Katalogi hosta dostępne w kontenerze jako `/media/movies` i `/media/shows`, z `:ro` |

```bash
docker compose -f compose.example.yml up -d
```

Domyślny adres nasłuchu to `127.0.0.1:8080`. Dostęp przez HTTPS zapewnia reverse proxy lub Cloudflare Tunnel. Worker OCR jest dostępny tylko w sieci wewnętrznej stosu. Wolumen `/data` przechowuje bazę SQLite, ustawienia, pliki robocze i archiwum.

Zakładka **AI** zawiera listę zapisanych modeli, ustawienia i konsolę. Model można zapisać, wybrać, usunąć z listy albo przetestować bez zapisu. Przycisk „+” dodaje model do listy, a „Zapisz” utrwala bieżące ustawienia. Domyślny limit 10 modeli zmienia się w Ustawieniach. W osobnej zakładce **Ustawienia** można zmieniać timeouty, OCR i limity paczek bez restartu. Zapis działa dla kolejnych zadań i pozostaje po restarcie. [Zakres ustawień aplikacji](docs/app-settings.md).

Pełna lista zmiennych: [.env.example](.env.example). Ustawienia proxy, dostępu i wolumenów: [instrukcja wdrożenia](docs/deployment.md).

## Kolejki i przechowywanie

| Kolejka | Zakres | Domyślnie |
|---|---|---|
| Przygotowanie | Analiza, ekstrakcja, OCR, pakowanie i przebudowy | 1 wykonywana operacja + 5 oczekujących |
| AI | Synchronizacja, tłumaczenie i test połączenia | 1 wykonywana operacja + 5 oczekujących |

Katalog zajmuje jedno miejsce w kolejce przygotowania. Oczekiwanie na odpowiedź AI nie blokuje przygotowania kolejnych materiałów. Aplikacja działa jako jeden proces; kolejki nie są współdzielone między instancjami.

Archiwum obejmuje domyślnie ostatnie 30 tytułów lub katalogów. Liczbę pozycji można zmienić bez restartu w **Ustawienia → Archiwum i retencja**. Pliki partii nie zajmują osobnych pozycji archiwum. Dane aktywnych operacji oraz wstrzymanych i przerwanych partii są chronione przed usunięciem. Żądania AI nie są automatycznie ponawiane po restarcie.

## Walidacja wyników

- **Diagnostyka PL:** niepewne materiały mogą trafić do ZIP-a z blokadą automatycznego użycia. [Kontrakt kandydatów](docs/polish-candidates.md).
- **ZIP:** obecność i SHA-256 deklarowanych artefaktów, również zagnieżdżonych w manifestach. Przekroczenie limitów końcowej paczki katalogowej blokuje jej publikację.
- **SRT z AI:** kompletność identyfikatorów segmentów, format, zakresy czasowe i zachowanie wymaganych danych wejściowych.
- **Diagnostyka czasowa:** hipotezy wymagają potwierdzenia dialogami. Numery i liczba segmentów nie dowodzą zgodności; słaby model jest oznaczany jako „brak wiarygodnej hipotezy”.
- **Kompletność referencji:** flaga `forced` pozostaje widoczna i wymaga oceny. Raport rozróżnia możliwe podpisy autorów, plansze i kwestie bez potwierdzenia.

Trafność synchronizacji, kompletność dialogów oraz jakość OCR i tłumaczenia wymagają oceny użytkownika.

## Dokumentacja i rozwój

| Dokument | Zakres |
|---|---|
| [Wdrożenie](docs/deployment.md) | Dostęp, proxy, uwierzytelnianie, dane i zasoby |
| [AI i API](docs/ai-api-sync.md) | Ustawienia dostawcy, kontrakt odpowiedzi i diagnostyka |
| [Katalogi](docs/directory-batch.md) | Dopasowanie plików, kolejka, decyzje i wspólny workpack |

Testy backendu uruchamiane są w kontenerze Python 3.12; testy interfejsu wymagają Node.js:

```bash
podman build --target test -t subtitle-agent:test .
node --test tests/*_ui.test.cjs
```

Licencja: [MIT](LICENSE).
