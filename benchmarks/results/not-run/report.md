# Subtitle benchmark

Commit: `d5654635eb6e679496a4324e9f29b5ee52a848c6`. Thresholds: `local-experiment-v1`.

Synthetic controls are not real subtitle quality validation. CPU/GPU comparisons require identical hashes and FP32.
Automatic acceptance is disabled; accepted subset has zero cases. NOT_RUN values are not measurements.

| Case | Split | Method | Status | Start median / p95 / max (ms) | Start n | <=250 / 500 / 1000 | End median (ms) | Precision / recall | Warm median [min,max] ms |
|---|---|---|---|---|---|---|---|---|---|
| offset | tuning | input | RUN | 8000.0 / 8000 / 8000 | 30 | 0.0 / 0.0 / 0.0 | 8000.0 | None / 0.0 | None [None,None] |
| offset | tuning | structural_baseline | RUN | 0.0 / 0 / 0 | 30 | 1.0 / 1.0 / 1.0 | 0.0 | 1.0 / 0.8 | None [None,None] |
| offset | tuning | local | NOT_RUN | None / None / None | None | None / None / None | None | None / None | None [None,None] |
| drift | tuning | input | RUN | 3847.5 / 9780 / 9841 | 30 | 0.06666666666666667 / 0.06666666666666667 / 0.16666666666666666 | 3803.5 | None / 0.0 | None [None,None] |
| drift | tuning | structural_baseline | RUN | 0.0 / 0 / 0 | 30 | 1.0 / 1.0 / 1.0 | 0.0 | 1.0 / 0.8 | None [None,None] |
| drift | tuning | local | NOT_RUN | None / None / None | None | None / None / None | None | None / None | None [None,None] |
| cut | evaluation | input | RUN | 14000.0 / 20000 / 20000 | 30 | 0.0 / 0.0 / 0.0 | 14000.0 | None / 0.0 | None [None,None] |
| cut | evaluation | structural_baseline | RUN | 0.0 / 0 / 12000 | 30 | 0.9666666666666667 / 0.9666666666666667 / 0.9666666666666667 | 0.0 | 1.0 / 0.8 | None [None,None] |
| cut | evaluation | local | NOT_RUN | None / None / None | None | None / None / None | None | None / None | None [None,None] |
| en_groups | evaluation | input | RUN | 8000.0 / 8000 / 8000 | 30 | 0.0 / 0.0 / 0.0 | 8000.0 | None / 0.0 | None [None,None] |
| en_groups | evaluation | structural_baseline | RUN | 0.0 / 2500 / 8000 | 30 | 0.5333333333333333 / 0.5333333333333333 / 0.5333333333333333 | 2500.0 | 0.0 / 0.0 | None [None,None] |
| en_groups | evaluation | local | NOT_RUN | None / None / None | None | None / None / None | None | None / None | None [None,None] |
| pl_groups | evaluation | input | RUN | 8000 / 8000 / 8000 | 15 | 0.0 / 0.0 / 0.0 | 8000 | None / 0.0 | None [None,None] |
| pl_groups | evaluation | structural_baseline | RUN | 1154 / 2500 / 2500 | 15 | 0.2 / 0.26666666666666666 / 0.4666666666666667 | 1175 | 0.0 / 0.0 | None [None,None] |
| pl_groups | evaluation | local | NOT_RUN | None / None / None | None | None / None / None | None | None / None | None [None,None] |
| sdh | evaluation | input | RUN | 8000.0 / 8000 / 8000 | 30 | 0.0 / 0.0 / 0.0 | 8000.0 | None / 0.0 | None [None,None] |
| sdh | evaluation | structural_baseline | RUN | 0.0 / 0 / 0 | 30 | 1.0 / 1.0 / 1.0 | 0.0 | 1.0 / 0.8 | None [None,None] |
| sdh | evaluation | local | NOT_RUN | None / None / None | None | None / None / None | None | None / None | None [None,None] |
| ocr | evaluation | input | RUN | 8000.0 / 8000 / 8000 | 30 | 0.0 / 0.0 / 0.0 | 8000.0 | None / 0.0 | None [None,None] |
| ocr | evaluation | structural_baseline | RUN | 0.0 / 0 / 0 | 30 | 1.0 / 1.0 / 1.0 | 0.0 | 1.0 / 0.8 | None [None,None] |
| ocr | evaluation | local | NOT_RUN | None / None / None | None | None / None / None | None | None / None | None [None,None] |
| wrong_film | evaluation | input | RUN | None / None / None | 0 | None / None / None | None | None / None | None [None,None] |
| wrong_film | evaluation | structural_baseline | RUN | None / None / None | 0 | None / None / None | None | 0.0 / None | None [None,None] |
| wrong_film | evaluation | local | NOT_RUN | None / None / None | None | None / None / None | None | None / None | None [None,None] |

## Before and after timing error

| Case / method | Endpoint | Before median / p95 / max | After median / p95 / max | n | Before <=250 / 500 / 1000 | After <=250 / 500 / 1000 |
|---|---|---|---|---|---|---|
| offset / input | Start | 8000.0 / 8000 / 8000 | 8000.0 / 8000 / 8000 | 30 | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% |
| offset / input | End | 8000.0 / 8000 / 8000 | 8000.0 / 8000 / 8000 | 30 | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% |
| offset / structural_baseline | Start | 8000.0 / 8000 / 8000 | 0.0 / 0 / 0 | 30 | 0.0% / 0.0% / 0.0% | 100.0% / 100.0% / 100.0% |
| offset / structural_baseline | End | 8000.0 / 8000 / 8000 | 0.0 / 0 / 0 | 30 | 0.0% / 0.0% / 0.0% | 100.0% / 100.0% / 100.0% |
| drift / input | Start | 3847.5 / 9780 / 9841 | 3847.5 / 9780 / 9841 | 30 | 6.7% / 6.7% / 16.7% | 6.7% / 6.7% / 16.7% |
| drift / input | End | 3803.5 / 9824 / 9885 | 3803.5 / 9824 / 9885 | 30 | 6.7% / 6.7% / 13.3% | 6.7% / 6.7% / 13.3% |
| drift / structural_baseline | Start | 3847.5 / 9780 / 9841 | 0.0 / 0 / 0 | 30 | 6.7% / 6.7% / 16.7% | 100.0% / 100.0% / 100.0% |
| drift / structural_baseline | End | 3803.5 / 9824 / 9885 | 0.0 / 1 / 1 | 30 | 6.7% / 6.7% / 13.3% | 100.0% / 100.0% / 100.0% |
| cut / input | Start | 14000.0 / 20000 / 20000 | 14000.0 / 20000 / 20000 | 30 | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% |
| cut / input | End | 14000.0 / 20000 / 20000 | 14000.0 / 20000 / 20000 | 30 | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% |
| cut / structural_baseline | Start | 14000.0 / 20000 / 20000 | 0.0 / 0 / 12000 | 30 | 0.0% / 0.0% / 0.0% | 96.7% / 96.7% / 96.7% |
| cut / structural_baseline | End | 14000.0 / 20000 / 20000 | 0.0 / 0 / 20000 | 30 | 0.0% / 0.0% / 0.0% | 96.7% / 96.7% / 96.7% |
| en_groups / input | Start | 8000.0 / 8000 / 8000 | 8000.0 / 8000 / 8000 | 30 | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% |
| en_groups / input | End | 8000.0 / 8000 / 8000 | 8000.0 / 8000 / 8000 | 30 | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% |
| en_groups / structural_baseline | Start | 8000.0 / 8000 / 8000 | 0.0 / 2500 / 8000 | 30 | 0.0% / 0.0% / 0.0% | 53.3% / 53.3% / 53.3% |
| en_groups / structural_baseline | End | 8000.0 / 8000 / 8000 | 2500.0 / 8000 / 8000 | 30 | 0.0% / 0.0% / 0.0% | 46.7% / 46.7% / 46.7% |
| pl_groups / input | Start | 8000 / 8000 / 8000 | 8000 / 8000 / 8000 | 15 | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% |
| pl_groups / input | End | 8000 / 8000 / 8000 | 8000 / 8000 / 8000 | 15 | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% |
| pl_groups / structural_baseline | Start | 8000 / 8000 / 8000 | 1154 / 2500 / 2500 | 15 | 0.0% / 0.0% / 0.0% | 20.0% / 26.7% / 46.7% |
| pl_groups / structural_baseline | End | 8000 / 8000 / 8000 | 1175 / 2521 / 2521 | 15 | 0.0% / 0.0% / 0.0% | 20.0% / 26.7% / 46.7% |
| sdh / input | Start | 8000.0 / 8000 / 8000 | 8000.0 / 8000 / 8000 | 30 | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% |
| sdh / input | End | 8000.0 / 8000 / 8000 | 8000.0 / 8000 / 8000 | 30 | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% |
| sdh / structural_baseline | Start | 8000.0 / 8000 / 8000 | 0.0 / 0 / 0 | 30 | 0.0% / 0.0% / 0.0% | 100.0% / 100.0% / 100.0% |
| sdh / structural_baseline | End | 8000.0 / 8000 / 8000 | 0.0 / 0 / 0 | 30 | 0.0% / 0.0% / 0.0% | 100.0% / 100.0% / 100.0% |
| ocr / input | Start | 8000.0 / 8000 / 8000 | 8000.0 / 8000 / 8000 | 30 | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% |
| ocr / input | End | 8000.0 / 8000 / 8000 | 8000.0 / 8000 / 8000 | 30 | 0.0% / 0.0% / 0.0% | 0.0% / 0.0% / 0.0% |
| ocr / structural_baseline | Start | 8000.0 / 8000 / 8000 | 0.0 / 0 / 0 | 30 | 0.0% / 0.0% / 0.0% | 100.0% / 100.0% / 100.0% |
| ocr / structural_baseline | End | 8000.0 / 8000 / 8000 | 0.0 / 0 / 0 | 30 | 0.0% / 0.0% / 0.0% | 100.0% / 100.0% / 100.0% |
| wrong_film / input | Start | None / None / None | None / None / None | 0 | N/A / N/A / N/A | N/A / N/A / N/A |
| wrong_film / input | End | None / None / None | None / None / None | 0 | N/A / N/A / N/A | N/A / N/A / N/A |
| wrong_film / structural_baseline | Start | None / None / None | None / None / None | 0 | N/A / N/A / N/A | N/A / N/A / N/A |
| wrong_film / structural_baseline | End | None / None / None | None / None / None | 0 | N/A / N/A / N/A | N/A / N/A / N/A |

NOT_RUN offset: LOCAL_WORKER_URL nie jest skonfigurowany
NOT_RUN drift: LOCAL_WORKER_URL nie jest skonfigurowany
NOT_RUN cut: LOCAL_WORKER_URL nie jest skonfigurowany
NOT_RUN en_groups: LOCAL_WORKER_URL nie jest skonfigurowany
NOT_RUN pl_groups: LOCAL_WORKER_URL nie jest skonfigurowany
NOT_RUN sdh: LOCAL_WORKER_URL nie jest skonfigurowany
NOT_RUN ocr: LOCAL_WORKER_URL nie jest skonfigurowany
NOT_RUN wrong_film: LOCAL_WORKER_URL nie jest skonfigurowany

All metrics including before/after start/end, counts, invariants, review, resources and per-run times are in results.json.
Categories are reported per case; aggregate distributions include all evaluated points and never omit review cases.
No real user subtitles were supplied. Target i5-8400 and RTX 4060 results remain NOT_RUN without access.
