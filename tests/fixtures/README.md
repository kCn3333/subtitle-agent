# VobSub test fixture

`vobsub-reference.mkv.b64` is a Base64-encoded, subtitle-only Matroska fixture used by the
container integration test. It was reduced to a four-second, single-track sample from the
public FFmpeg samples corpus (`sub/largeres_vobsub.mkv`) with:

```sh
ffmpeg -v error -ss 39 -to 43 -i largeres_vobsub.mkv -map 0:2 -c copy \
  -avoid_negative_ts make_zero -y vobsub-reference.mkv
```

The text representation keeps the repository fixture reviewable and lets the test recreate
the exact binary without downloading anything during the test run.
