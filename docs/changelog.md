# Historia zmian

## Niewydane

- Powiadomienia przeglądarkowe o zakończeniu, błędzie i wymaganej decyzji; domyślnie włączone, wymagają otwartej karty i zgody.
- Uproszczony nagłówek ze wskaźnikiem OCR, przezroczysta nawigacja i rozwijane menu mobilne; usunięte wskaźniki ONLINE i API.

## 1.0.0 — 2026-11-10

Pierwsze wydanie:

- Analiza filmów, odcinków i katalogów; referencje wielojęzyczne, OCR PGS/VobSub i diagnostyka czasowa.
- Paczki ZIP z manifestem, raportem i walidacją SHA-256; ręczna ocena niepewnych napisów PL.
- Trwała kolejka katalogów z pomijaniem, wznawianiem i ponawianiem błędnych plików.
- Synchronizacja i tłumaczenie przez AI, zapisane modele, konsola kosztów i wersjonowane SRT.
- Ustawienia bez restartu, archiwum, logowanie z ochroną CSRF oraz biblioteka tylko do odczytu.
- Logo i favicony, wersja w interfejsie oraz obrazy aplikacji i OCR w GHCR.

[Wydania na GitHubie](https://github.com/kCn3333/subtitle-agent/releases) · [Aktualizacja](deployment.md#aktualizacja)

## Testy i wydania

Testy backendu działają w obrazie Python 3.12; testy interfejsu wymagają Node.js:

```sh
podman build --target test -t subtitle-agent:test .
node --test tests/*_ui.test.cjs
```

Przed wydaniem zaktualizuj `__version__` w [app/__init__.py](../app/__init__.py) i historię zmian, uruchom testy i zatwierdź zmiany. Tag `vMAJOR.MINOR.PATCH` musi wskazywać sprawdzony commit i odpowiadać wersji w kodzie. Po pomyślnym workflow `Container` sprawdź oba obrazy i opublikuj GitHub Release. Nie przenoś istniejących tagów.
