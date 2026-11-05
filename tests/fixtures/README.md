# VobSub fixture

`vobsub-reference.mkv.b64`: four-second, subtitle-only Matroska sample with one VobSub track, used by `tests/container_ocr_smoke.py`.

Source: FFmpeg samples corpus, `sub/largeres_vobsub.mkv`. Extraction:

```sh
ffmpeg -v error -ss 39 -to 43 -i largeres_vobsub.mkv -map 0:2 -c copy \
  -avoid_negative_ts make_zero -y vobsub-reference.mkv
```

Decode from the repository root; no download is needed during tests:

```sh
base64 -d tests/fixtures/vobsub-reference.mkv.b64 > /tmp/vobsub-reference.mkv
```
