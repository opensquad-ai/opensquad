# `docs/` — Cross-language and maintainer-facing documentation

> This folder is **not** the entry point for users. If you're new to
> OpenSquad, start at [`doc_en/getting_started.md`](../doc_en/getting_started.md)
> (English) or [`doc_cn/getting_started.md`](../doc_cn/getting_started.md)
> (中文).
>
> For the canonical documentation index, see
> [`doc_en/README.md`](../doc_en/README.md) /
> [`doc_cn/README.md`](../doc_cn/README.md).

## What belongs in `docs/`

Only two kinds of content:

1. **Maintainer-facing docs** — guides for people who maintain or develop
   OpenSquad itself, not for end users. Examples in this folder:
   - [`security_baseline.md`](security_baseline.md) — security baseline
     the project commits to
   - [`GITHUB_SETTINGS.md`](GITHUB_SETTINGS.md) — recommended GitHub
     repository settings (configured in the UI, not from git)
2. **Cross-language, language-neutral docs** — content that doesn't fit
   in either user-facing bucket because it isn't a user guide and isn't
   tied to one language.

## What does NOT belong in `docs/`

- **User-facing English content** → put it in `doc_en/`
- **User-facing Chinese content** → put it in `doc_cn/`
- **Bilingual user-facing content** → mirror the same filename in both
  `doc_en/` and `doc_cn/`

If a doc is user-facing but only translated into one language, put the
translated version in its language folder. The maintainer is responsible
for adding the other language later — **don't** leave a single-language
user-facing doc in `docs/` just because the other translation isn't ready
yet.

## Naming convention

| Folder | Naming |
|--------|--------|
| `doc_en/` | `FOO.md` — no suffix needed |
| `doc_cn/` | `FOO.md` — no suffix needed; the folder implies the language |
| `docs/` (this folder) | `FOO.md` — language-neutral or maintainer-facing only |
| Root | `FOO.md` (EN) / `FOO_ZH.md` (CN) — suffix disambiguates because both live at root |

See [`CONTRIBUTING.md`](../CONTRIBUTING.md) → "Documentation folder
structure" for the full rules with examples.

## Contents of this folder

| File | Audience | Language |
|------|----------|----------|
| `README.md` | (this file — maintainers + new contributors) | EN |
| `banner.svg` | resource | — |
| `security_baseline.md` | maintainers | EN |
| `GITHUB_SETTINGS.md` | maintainers | EN |
| `desktop-known-issues.md` | maintainers | EN |
| [`rust-hybrid-refactor.md`](rust-hybrid-refactor.md) | maintainers (architecture) | ZH |
| [`contracts/`](contracts/) | maintainers — Launcher/Gateway freeze contracts for the Rust hybrid plan | EN |
