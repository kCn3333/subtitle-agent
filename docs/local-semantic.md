# Lokalna synchronizacja EN–PL

Nowa funkcja tworzy podgląd PL SRT i raport JSON w katalogu roboczym. Tekst PL pozostaje niezmieniony. Wynik jest eksperymentalny (`REVIEW_REQUIRED`), bez automatycznej publikacji. Dotychczasowy OCR, workpacki, ręczny wybór EN i potwierdzanie zewnętrznego EN pozostają dostępne.

## Model i kontrakt

Sprawdzono 2026-10-03:

- [Model card](https://huggingface.co/sentence-transformers/paraphrase-multilingual-mpnet-base-v2): Apache-2.0, 768 wymiarów, limit 128 tokenów.
- [Rewizja modelu](https://huggingface.co/sentence-transformers/paraphrase-multilingual-mpnet-base-v2/tree/4328cf26390c98c5e3c738b4460a05b95f4911f5): `4328cf26390c98c5e3c738b4460a05b95f4911f5`, potwierdzona przez API Hugging Face.
- [Sentence Transformers 5.1.1](https://github.com/huggingface/sentence-transformers/blob/v5.1.1/pyproject.toml) obsługuje Transformers `>=4.41,<5`; dlatego przypięto 4.55.4. Aktualna dokumentacja instalacji dotyczy także nowszych wersji; nie mieszamy jej wymagań z tym lockiem.
- [PyTorch 2.8.0](https://pytorch.org/get-started/previous-versions/): osobne wheele CPU i CUDA 12.8. Wspólny lock Python znajduje się w `requirements-semantic.txt`.
- [Dokumentacja wydajności](https://sbert.net/docs/sentence_transformer/usage/efficiency.html) i [modeli](https://sbert.net/docs/sentence_transformer/pretrained_models.html). Eksperyment korzysta z backendu Torch.

Worker ma jeden proces Uvicorna i jedno aktywne żądanie. Kolejka HTTP ma pojemność zero: zajęty worker odpowiada 429. Aplikacja ponawia ograniczoną liczbę prób. Obliczenia i ładowanie modelu wykonują się poza pętlą obsługującą healthchecki. Timeout nie zwalnia slotu, dopóki rzeczywiste obliczenie się nie zakończy.

`GET /health` sprawdza proces. `GET /ready` sprawdza załadowany model. `POST /embeddings` przyjmuje `{"segments":[{"id":"en:0:1","text":"example"}]}` i zwraca `embeddings` z ID/wektorem oraz `metadata`: model, revision, device, dtype, wymiar, limit tokenów, inferencję, wersje bibliotek i zasoby. `/ready` i `/embeddings` wymagają Bearer tokenu. Worker nie dostaje czasów, filmu, audio, mediów ani bazy aplikacji.

Limity: 1 MiB rzeczywistego body, 128 segmentów/żądanie, 8000 znaków/segment, 160 znaków/ID, 10 s na body. Długie teksty są dzielone tokenizerem; długość każdej części jest ponownie sprawdzana przed `encode`. Wynik długiej kwestii jest średnią znormalizowanych embeddingów części, następnie normalizowaną. Nie ma cichego truncation. CPU: FP32. CUDA: FP32 domyślnie; FP16 wymaga osobnego pomiaru jakości. TF32 jest wyłączone.

## CPU: jeden stack z aplikacją

Nie zmieniaj istniejących mountów mediów `:ro`. Przed użyciem ustaw rzeczywiste ścieżki w podstawowym Compose. Użyj tej samej nazwy projektu co dla istniejącego stacka, aby zachować wolumen aplikacji. Nie wdrażano tych zmian do produkcji w ramach implementacji.

Utwórz poza repo plik zawierający losowy token i ustaw `SEMANTIC_TOKEN_FILE` na jego bezwzględną ścieżkę. Sekret jest montowany do obu usług jako `/run/secrets/semantic_token`. W Portainerze wskaż plik dostępny dla procesu wdrażającego stack albo odpowiednik secret zgodny z używanym trybem. Nie wpisuj tokenu w repo, GUI ani URL.

```bash
docker compose -f compose.example.yml -f compose.semantic-cpu.yml --profile local-ai config --quiet
docker compose -f compose.example.yml -f compose.semantic-cpu.yml --profile local-ai build subtitle-agent subtitle-semantic-worker
```

Jednorazowe pobranie wag (internet tylko podczas inicjalizacji):

```bash
docker compose -f compose.example.yml -f compose.semantic-cpu.yml --profile local-ai run --rm --no-deps \
  -e HF_HUB_OFFLINE=0 subtitle-semantic-worker python -m semantic_worker.download
```

Jeśli transport Xet nie działa, można dodać `-e HF_HUB_DISABLE_XET=1`. Cache pozostaje w nazwanym wolumenie `semantic_model_cache`. Czas pobrania jest wypisywany osobno. Nie wliczaj go do benchmarku inferencji. Po pobraniu:

```bash
docker compose -f compose.example.yml -f compose.semantic-cpu.yml --profile local-ai up -d
```

Dla Portainera Web editor przygotuj jeden scalony YAML przez `docker compose -f compose.example.yml -f compose.semantic-cpu.yml --profile local-ai config`. W scalonej definicji workera usuń `profiles: [local-ai]`, aby jego uruchomienie nie zależało od obsługi profili przez daną wersję Portainera. Obrazy `subtitle-agent:local` i `subtitle-semantic:cpu` muszą być wcześniej zbudowane na obsługiwanym przez Portainera hoście Docker; w Web editor nie zakładaj dostępności kontekstu build `.`. Usuń sekcje `build` ze scalonego YAML po zbudowaniu obrazów i zachowaj sekrety oraz mounty. [Portainer pozwala także wdrażać z repozytorium z dodatkowymi plikami Compose](https://docs.portainer.io/sts/user/docker/stacks/add), lecz nie testowano wdrożenia w konkretnej instancji użytkownika.

W GUI otwórz **Ustawienia lokalnego AI** (`/settings`), wybierz **CPU w stacku aplikacji**, zapisz i sprawdź worker. Domyślny URL CPU to `http://subtitle-semantic-worker:8091`. Port workera nie jest wystawiony na hosta. Brak gotowości AI nie blokuje startu aplikacji. Następnie wybierz **Przygotuj do synchronizacji**, referencję EN i PL, a po przygotowaniu naciśnij **Synchronizuj lokalnie**. SRT i raport pobiera się z wyniku zadania.

## GPU: worker na drugiej maszynie w LAN

Nie zakładamy systemu, adresu, sterownika ani działającego NVIDIA Container Toolkit. Przed uruchomieniem ustal te dane przez odczyt na maszynie GPU. Wymagane są runtime z obsługą deklaracji GPU w Compose, zgodny sterownik dla wybranego Torch/CUDA i dostęp sieciowy między aplikacją a workerem. Konfiguracja CUDA bez dostępnego GPU zwraca błąd gotowości; nie uruchamia modelu na CPU.

Na maszynie GPU użyj `compose.semantic-gpu.yml`. Wymagane parametry:

- `AI_LAN_BIND_ADDRESS`: rzeczywisty adres interfejsu zaufanej sieci;
- `AI_LAN_PORT`: wybrany port;
- `SEMANTIC_TOKEN_FILE`: lokalny plik zawierający ten sam token co w aplikacji.

```bash
docker compose -f compose.semantic-gpu.yml config --quiet
docker compose -f compose.semantic-gpu.yml build
docker compose -f compose.semantic-gpu.yml run --rm --no-deps \
  -e HF_HUB_OFFLINE=0 subtitle-semantic-worker python -m semantic_worker.download
docker compose -f compose.semantic-gpu.yml up -d
```

Na serwerze aplikacji możesz użyć podstawowego Compose z `compose.semantic-lan.yml`, ustawiając `LOCAL_WORKER_URL` i `SEMANTIC_TOKEN_FILE`, albo pozostawić stack CPU i przełączyć wariant w `/settings`. Wybierz **GPU na innej maszynie w LAN**, podaj URL, sprawdź i zapisz. Ten wariant wymaga odpowiedzi `device=cuda` i tej samej rewizji modelu. Nie przenosi mediów ani bazy. Fallback CPU jest domyślnie wyłączony i ma osobny checkbox; raport wskazuje faktycznie użyte urządzenie.

Wystawiaj worker wyłącznie w zaufanym LAN. Plain HTTP przenosi tekst i token w obrębie tej sieci. Nie dodano publicznego proxy ani zmian firewalla/sterowników. Obsługa TLS może użyć już istniejącego, zaufanego endpointu HTTPS.

## Ustawienia i limity

Wybór wariantu/URL zapisuje się w SQLite i obowiązuje po restarcie. Zadanie zapamiętuje swój wybór przy dodaniu do kolejki; zmiana ustawień nie przełącza aktywnego zadania. Token pozostaje sekretem wdrożenia.

| Parametr | Domyślna | Znaczenie |
|---|---:|---|
| `AI_THREADS` | 2 | Torch intra-op, OMP/MKL/OpenBLAS; inter-op 1, tokenizer bez dodatkowej równoległości |
| `AI_BATCH_SIZE` | 16 | Partie obliczeń wewnątrz workera |
| `LOCAL_WORKER_BATCH_SIZE` | 32 | Segmenty/grupy w żądaniu HTTP, max 128 |
| `LOCAL_WORKER_TIMEOUT_SECONDS` | 120 | Timeout HTTP aplikacji |
| `AI_REQUEST_TIMEOUT_SECONDS` | 120 | Deadline odpowiedzi workera |
| `LOCAL_WORKER_RETRIES` | 1 | Ograniczone retry, max 3 |
| `LOCAL_WORKER_MAX_CUES` | 12000 | Limit segmentów na język |
| `LOCAL_CPU_WORKER_URL` | nazwa usługi CPU | Adres CPU dla ustawień i jawnego fallbacku |
| `LOCAL_WORKER_TOKEN_FILE` | brak | Sekret serwer–worker |

Kolejka aplikacji ma 64 miejsca; używa istniejącego `MAX_CONCURRENT_JOBS` (zalecany start: 1). Powtórne uruchomienie tego samego zadania jest blokowane, a bilety kolejki zabezpieczają anulowanie/ponowne dodanie. `WAITING_AI` jest trwałym stanem z przyczyną i przyciskami wznowienia/anulowania; nie zajmuje aktywnego workera. Restart aktywnego zadania oznacza `INTERRUPTED`, usuwa niezatwierdzony podgląd i wymaga jawnego wznowienia. Retencja katalogów roboczych nadal obowiązuje; po wygaśnięciu źródeł potrzebne jest nowe przygotowanie.

Nie ustalono RAM ani obciążenia docelowego i5-8400. Dlatego nie dodano arbitralnego limitu pamięci ani CPU do workera AI. Przed ustaleniem `mem_limit`/`cpus` wykonaj pomiar na docelowym sprzęcie; pozostaw zapas nad szczytem. Dwa wątki są ostrożnym punktem startowym, a nie gwarancją zużycia dwóch rdzeni przez cały proces.

## Dopasowanie i kontrola jakości

Referencja ma `sourceId`, oryginalny rodzaj, ścieżkę tekstowego SRT, SHA-256, pochodzenie i oceny OCR. Wybór nowego EN wymaga rzeczywistego przygotowania nowego źródła; ID i hash są sprawdzane przed dopasowaniem. Grafika po poprawnym OCR jest wejściem tekstowym, z zachowanym pochodzeniem. Wadliwa struktura OCR blokuje automat; podejrzany tekst jest ostrzeżeniem do przeglądu.

Embeddingi obejmują pojedyncze kwestie i grupy 1–3 sąsiadów (bez łączenia przez przerwy >4 s). Kandydaci są wyszukiwani globalnie blokami 64 × liczba grup PL. Sparse DP wybiera maksymalnie ważoną monotoniczną sekwencję z pominięciami, relacjami 1:1, 1:N, N:1 i bez ponownego użycia segmentów. Pamięć DP jest O(K+N), czas O(K log N); retrieval nadal ma kwadratowy koszt obliczeń, ale nie przechowuje pełnej macierzy filmu.

Przewaga nad alternatywami, sąsiedni kontekst oraz kary za krótkie/powtarzalne kwestie ograniczają fałszywe dopasowania. Cosine similarity nie jest prawdopodobieństwem. Kotwica grupowa używa pierwszej granicy i jawnej niepewności obejmującej grupę; ma słabą wagę i nie jest mocnym punktem kontrolnym.

Modele czasu: tożsamość, ważony offset, odporny drift, a przy utrzymującym się skoku reszt — model odcinkowy. Kotwice strukturalne nie dominują nad treścią. Granice montażu nie są równymi częściami listy. Luki między odcinkami pozostają nieinterpolowane i oznaczone do przeglądu. Każdy czwarty odpowiedni punkt 1:1 jest kontrolą poza fitowaniem; to wewnętrzna kontrola czasowa, nie niezależna prawda benchmarkowa.

Raport zawiera pokrycie czasu materiału i obsadzonych przedziałów dialogów, największą lukę, początek/środek/koniec, niepewne fragmenty oraz walidację po clamp. Rozróżnia istniejące i wprowadzone nakładanie, odwrócone przedziały, kolejność, zakres, liczbę segmentów i tekst. Błędy nie usuwają kwestii. Podgląd może zawierać błędne przedziały wskazane w raporcie i wymagać ręcznej korekty.

## Benchmark skuteczności i wydajności

Runner jest lokalnym modułem, bez dodatkowej usługi. Przykładowe, autorskie dane i SHA-256 są w `benchmarks/synthetic/manifest.json`; generator pozwala je odtworzyć. `benchmarks/real-template.json` zawiera pusty manifest i szablon anotacji. Rzeczywiste pary muszą mieć sprawdzony tytuł/odcinek, wydanie i hashe. Anotacje pochodzą z ręcznie sprawdzonego PL albo 20–30 pewnych punktów rozłożonych po materiale, plus okolice zmian. `scope=sampled` nie oznacza skuteczności dla wszystkich segmentów. Niepewne granice są wykluczane i liczone oddzielnie.

Algorytm dostaje tylko wejściowe SRT i długość materiału. Runner odczytuje ground truth dopiero po dopasowaniu. Zbiór do strojenia jest oznaczony `tuning`, końcowy `evaluation`. Progi wyszukiwania są wersjonowane, lecz nie skalibrowane do autoakceptacji; autoakceptacja pozostaje wyłączona. Zamrożenie przyszłych progów wymaga osobnego zestawu do strojenia i późniejszej oceny bez zmian na zbiorze końcowym.

W środowisku Python 3.12 z zależnościami aplikacji:

```bash
python -m benchmarks.generate /tmp/subtitle-synthetic
python -m benchmarks.run /tmp/subtitle-synthetic/manifest.json --output /tmp/results-cpu \
  --url http://adres-cpu:port --token-file /sciezka/poza/repo/token \
  --environment /sciezka/environment.json --batch-size 32 --repetitions 3
python -m benchmarks.run /tmp/subtitle-synthetic/manifest.json --output /tmp/results-gpu \
  --url http://adres-gpu:port --token-file /sciezka/poza/repo/token \
  --environment /sciezka/environment.json --batch-size 32 --repetitions 3
```

CPU w stacku nie ma portu hosta: runner można uruchomić w kontenerze obrazu testowego, w sieci Compose, z URL `http://subtitle-semantic-worker:8091` i mountem wyłącznie manifestu/danych oraz sekretu. Przypisz `--commit` do SHA checkouta, jeśli runner nie widzi `.git`.

Wyniki: `results.json`, `results.csv`, `report.md`, osobne podglądy i raporty przypadków. Metryki przed/po obejmują start/koniec, medianę/p95/maksimum, odsetki <=250/500/1000 ms i n, precision/recall dokładnych relacji grupowych, pokrycie, przegląd, false accepts i invariants. Input, baseline strukturalny i lokalny model są raportowane oddzielnie. Przypadek niewłaściwego filmu pokazuje, dlaczego sama geometria czasów nie potwierdza treści. [ffsubsync](https://github.com/smacke/ffsubsync) nie został dodany jako opcjonalny baseline.

Jedna rozgrzewka i trzy powtórzenia bez cache embeddingów. Czas warm jest raportowany jako mediana i zakres, bez p95 z trzech uruchomień. Czas zadania obejmuje HTTP/sieć, inferencję (CUDA synchronizowana na granicach), matching, fit, walidację, odczyt SRT i zapis SRT. W zadaniu GUI dodatkowo zapisuje się kolejkę i wcześniejsze przygotowanie/OCR; runner gotowych SRT ma OCR `null` i kolejkę 0. OCR nie jest ukrywany w porównaniu CPU/GPU.

Wypełnij `benchmarks/environment-template.json` rzeczywistym sprzętem, systemem, runtime, limitami, sterownikiem i innym obciążeniem. Rejestruj równolegle `docker stats` oraz na GPU `nvidia-smi --query-gpu=timestamp,name,driver_version,memory.used,utilization.gpu --format=csv -l 1`. Worker podaje procesowy RSS, CPU seconds, cgroup v2 `memory.peak`/`cpu.stat` i pamięć Torch GPU oraz stan free/total całego GPU. `memory.peak` i RSS są maksimum od startu procesu, nie izolowanym maksimum każdego przypadku. Dodatkowe obciążenie GPU wymaga logów urządzenia. Odczyty niedostępne są `null`, nigdy zastępowane szacunkami.

Z osobnego startu procesu z pobranym cache zmierz czas do `/ready`; `worker.loadMs` jest czasem samego ładowania modelu, nie całego startu procesu. `download.py` mierzy tylko inicjalizację wag. Nie wykonuj pobrania w pomiarze. Porównanie kontrolowane używa FP32, tych samych hashy, modelu, progów, partii i wątków. Optymalizacja partii lub FP16 wymaga osobnej kategorii i ponownej oceny jakości.

## Wyłączenie i walidacja

W `/settings` wybierz **Wyłączony**. OCR i workpacki nadal działają. Możesz zatrzymać tylko worker AI (`docker compose ... stop subtitle-semantic-worker`) i wrócić do podstawowego pliku Compose. Nie usuwaj wolumenów; nie używaj `down -v`. Przy powrocie uwzględnij nazwę istniejącego projektu/stacka.

Wykonano testy w Python 3.12 przez `podman build --target test -t subtitle-agent:test .`, testy GUI `node --test tests/*.test.cjs`, `node --check`, `git diff --check` oraz Compose 2.39.4 `config --quiet` dla CPU, GPU i LAN. Obraz CPU zbudowano. Szczegóły ograniczeń i wyników znajdują się w [raporcie walidacji](local-semantic-validation.md). Walidacja YAML nie oznacza uruchomienia CUDA ani pomiaru skuteczności rzeczywistego modelu.
