# Conventions for this repository

This file is read automatically by Claude Code sessions working in this
repository.

## Language

**Write commit messages and pull request descriptions in English** (from
2026-09-12). The repository is public and the project is meant to be usable by
people outside Japan; the commit log is part of what they read.

History before 2026-09-12 is in Japanese. **Do not rewrite it.** Rewriting
published history invalidates every commit SHA and forces everyone who has
cloned the repository to recover manually. The change of language is visible
from the dates, which is an honest record of what happened.

**Source comments and documentation stay in Japanese for now.** They are dense
and explain *why* decisions were made; a mechanical translation would lose that.
If the project moves to English, do it deliberately, document by document,
starting with `docs/` — not as a side effect of another change.

`README.md` carries an English section first, then Japanese. Keep that order
when editing it.

The site (`docs/`) keeps one language per page (from 2026-09-24). The bare URL
is Japanese and the English page sits beside it: `index.md` / `en.md`,
`guide/index.md` / `guide/en.md`, `manual/index.md` / `manual/en.md`,
`usage.md` / `usage-en.md`. Each pair links to the other through `alternate`,
and English pages set `home_url` to the English top page. When changing one
page of a pair, change the other in the same commit.

## Where the open work is

Outstanding work lives in GitHub Issues, not in this file and not in `README.md`. Decisions that
were made *not* to do something are recorded there too, as closed issues with the reasoning — so
that the same question does not get re-argued from scratch.

## What this project is

This is the canonical implementation. The macOS-only Swift implementation it
was ported from has been retired (`nakamura196/archival-packager-swift`,
private). Do not write as if the two run in parallel.

## Non-negotiables

- **Originals are never modified.** The application reads source files and
  writes copies elsewhere. Any change that could write to an input path is a
  bug, not a feature.
- **Silent failure is not acceptable.** If something could not be checked,
  say so. "Not scanned" must never be reported as "nothing found" — this has
  already gone wrong once, in the personal-information scan.
- **Claims must be verifiable.** Before writing that the output is compatible
  with another system, compare it against that system's published specification
  and record the comparison (`docs/interoperability.md`).

## Testing

- `uv run pytest -q` — the whole suite must pass before committing
- `uvx ruff check src tests scripts main.py` — must pass
  (configuration lives in `pyproject.toml`)
- `ARCHIVAL_PACKAGER_SCALING=1 uv run pytest -q tests/test_scaling.py` — the
  scale tests, skipped by default

**A bug that has been hit once gets a test.** That is why the suite is large.
When adding a test, write in its docstring *why* it exists, in Japanese.

## Secrets

Store credentials live in 1Password and are injected at run time:

```sh
op run --env-file=store/.env -- python scripts/store_submit.py --check
```

Never write real values into `store/.env`, which holds `op://` references only.
