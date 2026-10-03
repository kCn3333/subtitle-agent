# Walidacja lokalnego dopasowania — 2026-10-03

Kod aplikacji i integracji: `d5654635eb6e679496a4324e9f29b5ee52a848c6`, gałąź `feat/local-semantic-sync`, baza `6287d3d6f663e28d1a2c151d3c19d1f3bf942472`. Czwarty commit dodaje runner, dane, raporty i dokumentację. Nie wykonano push, merge ani wdrożenia produkcyjnego. Nie zmieniono hosta, sterowników, firewalla, działających usług ani mountów mediów.

## Wykonane sprawdzenia

| Sprawdzenie | Wynik |
|---|---|
| Python 3.12: `podman build --target test -t subtitle-agent:test .` | PASS: 240 testów, jedno ostrzeżenie deprecacji Starlette/AnyIO |
| GUI: `node --test tests/*.test.cjs` | PASS: oba pliki testów |
| `node --check app/static/app.js`, `node --check app/static/settings.js` | PASS |
| `git diff --check` | PASS |
| CPU overlay + baza, Compose 2.39.4 `config --quiet` | PASS: model konfiguracji, bez wdrożenia |
| CUDA Compose, `config --quiet` | PASS: model konfiguracji, bez uruchomienia CUDA |
| LAN overlay + baza, `config --quiet` | PASS |
| `podman build -f Dockerfile.semantic --target cpu -t subtitle-semantic:cpu .` | PASS: rzeczywisty build CPU, bez pakietów NVIDIA w locku CPU |
| `/health` przy niepełnym cache modelu, rzeczywisty obraz CPU | PASS; `/ready` zgłasza brak gotowości |
| Konfiguracja `AI_DEVICE=cuda` w obrazie CPU bez dostępnego CUDA | PASS: jawny błąd CUDA, bez fallbacku |
| Pełna inferencja modelu CPU | NOT_RUN: pobranie wag nie zakończyło się sukcesem |
| Obraz CUDA: build / start na RTX 4060 | NOT_RUN: brak udostępnionego środowiska GPU |
| Pomiary na i5-8400 i RTX 4060 | NOT_RUN: brak autoryzowanego dostępu do tych maszyn |
| Skuteczność na rzeczywistych materiałach użytkownika | NOT_RUN: nie dostarczono sprawdzonych EN–PL i ręcznej prawdy odniesienia |

Próbowano jawnej inicjalizacji wag w osobnym wolumenie testowym, poza benchmarkiem. Transport Xet zakończył się błędem połączenia z `cas-server.xethub.hf.co`; ponowienie z wyłączonym Xet również nie mogło połączyć się z CDN `us.aws.cdn.hf.co` (`Connection refused`). Nie uznano częściowego cache za poprawnie pobrany model. Nie wpisano czasu tej nieudanej operacji do tabeli wydajności inferencji.

Bieżący host został odczytany jako i5-10400F, 12 logicznych CPU, około 46 GiB RAM. To inny sprzęt niż docelowy i5-8400; nie przenosimy wyników między nimi. Nie wykonano pomiaru inferencji na tym hoście. Build i smoke testy nie są pomiarem wydajności ani skuteczności modelu.

Regresje obejmują OCR jako przygotowaną referencję tekstową, zmianę source ID/hashu, strukturę OCR, niedostępność i ograniczone retry, błędny JSON/ID/wymiar/NaN/Inf, timeout bez zwalniania aktywnego slotu, health podczas obliczeń, trwałe oczekiwanie, anulowanie, restart i duplikację biletów kolejki. Kontrolowane wektory sprawdzają globalne wyszukiwanie przy dużym offsetcie, odrzucenie obcego tekstu, monotoniczne relacje grupowe, wagi, pokrycie tylko początku, odwrócony przedział po clamp, zachowanie PL i niezależne od fitowania kontrole. Testy API sprawdzają zapis CPU/GPU, odtworzenie ustawień i nieujawnianie tokenu.

## Uruchomiony benchmark kontrolny

Oceniono osiem autorskich przypadków syntetycznych. Uruchomiono wejście bez korekty i baseline strukturalny. Runner zapisał 24 rekordy: 16 ocenionych wejść/baseline’ów oraz osiem jawnych `NOT_RUN` lokalnego modelu (URL nie był skonfigurowany, ponieważ brak pełnego cache). Nie użyto OpenAI ani płatnego API. Anotacje i hashe są niezależne od wyników algorytmu.

| Przypadek | Zbiór | Baseline: mediana / p95 / max błędu startu, ms | Precision / recall relacji |
|---|---|---|---|
| offset | tuning | 0 / 0 / 0 | 1 / 0.8 |
| drift | tuning | 0 / 0 / 0 | 1 / 0.8 |
| cut | evaluation | 0 / 0 / 12000 | 1 / 0.8 |
| różne grupy EN | evaluation | 0 / 2500 / 8000 | 0 / 0 |
| różne grupy PL | evaluation | 1154 / 2500 / 2500 | 0 / 0 |
| SDH | evaluation | 0 / 0 / 0 | 1 / 0.8 |
| błędy OCR | evaluation | 0 / 0 / 0 | 1 / 0.8 |
| niewłaściwy film | evaluation | N/A: brak odpowiadających wypowiedzi | 0 / N/A |

W negatywnym przypadku nie nadano arbitralnych „prawidłowych” czasów dla nieodpowiadającego filmu. Baseline tworzy 24 fałszywe relacje mimo podobnego układu czasów. Wszystkie baseline’y są hipotezami, więc liczba automatycznie zaakceptowanych zadań wynosi zero; nie jest to dowód wysokiej skuteczności. Podzbiór autoakceptacji jest pusty. Nowy lokalny algorytm również nie autoakceptuje wyników przed kalibracją na rzeczywistych danych.

[Pełny raport przed/po](../benchmarks/results/not-run/report.md), [JSON](../benchmarks/results/not-run/results.json), [CSV](../benchmarks/results/not-run/results.csv) zawierają liczebności, błędy startu/końca, progi 250/500/1000 ms, relacje, przegląd i walidację tekstu/liczby/przedziałów. Nie zawierają oszacowanych czasów CPU/GPU. Te wyniki nie mierzą skuteczności Sentence Transformers.

## Brakujące dane do docelowych pomiarów

1. Autoryzowany dostęp do i5-8400: RAM, obciążenie, Docker/Portainer, nazwa istniejącego projektu/stacka i możliwość osobnego testu.
2. Maszyna RTX 4060: system, sterownik, runtime/NVIDIA Container Toolkit, odczytane zasoby i dostęp. URL/port/interfejs LAN należy podać w ustawieniach i Compose; nie zostały odgadnięte.
3. Możliwość dokończenia jednorazowego pobrania przypiętych wag albo poprawny cache tej rewizji.
4. Ręcznie sprawdzone EN–PL o potwierdzonym wydaniu/hashach i anotacje: pełna referencja lub co najmniej 20–30 rozłożonych punktów dla każdego rzeczywistego przypadku.

Gotowe konfiguracje i polecenia znajdują się w [instrukcji](local-semantic.md). Po uzyskaniu dostępu wykonaj identyczne wejścia, jedną rozgrzewkę i trzy powtórzenia CPU/FP32 oraz GPU/FP32, zapisując sieć, zasoby, obciążenie i jakość. GPU/FP16 oraz zoptymalizowane partie są osobnymi eksperymentami.
