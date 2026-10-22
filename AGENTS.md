# AGENTS.md

Rules for anyone, human or agent, changing this repo. Setup, architecture and the PR
checklist live in [CONTRIBUTING.md](CONTRIBUTING.md); user-facing docs in [README.md](README.md).

## Before you change anything

- Read `CONTRIBUTING.md` first. It explains the three entrypoints, why they exist, and the
  two Ocean behaviours (blueprints created once, Port owns the mapping) that bite people.
- Run from the repo root. Ocean resolves `pyproject.toml`, `.env` and `.port/resources`
  relative to the working directory.

## Code

- Python 3.14, Poetry for everything, driven through the `Makefile` (`make install`,
  `make lint`, `make test`): the same layout and targets as an integration in Port's Ocean
  monorepo, so the folder can be copied there unchanged. Never `pip install`; add deps with
  `poetry add` so `poetry.lock` moves with `pyproject.toml`.
- Smallest diff that works. No speculative abstractions, no config for values that never
  change, no scaffolding "for later". Reuse what is already in the repo before writing new.
- Mark deliberate shortcuts with a `ponytail:` comment naming the ceiling and the upgrade
  path. The existing ones are the pattern.
- Auth is the organization-level app identity only: the `stratonextAuth*` settings declared in
  `.port/spec.json` (`OCEAN__INTEGRATION__CONFIG__STRATONEXT_AUTH_*` in `.env`). Do not add
  fixed tokens, CLI credential fallbacks or other auth paths.
- The `/integration/tasks/*` routes are reachable from the internet. They must keep failing
  closed: no secret configured means every request is refused. Validate inputs at that
  boundary, not downstream.
- Any non-trivial logic ships with a test in `tests/`, one file per module. No new test
  frameworks or fixtures beyond `conftest.py`.

## Writing

Applies to every file in the repo: code, comments, docstrings, docs, YAML, JSON.

- No em-dashes (U+2014). Use a colon, a comma or a new sentence. `tests/test_style.py`
  fails CI if one lands.
- Short sentences. Say why, not what, in comments.
- Variable names in docs are the real ones, written in full.

## Before you open a PR

```bash
make lint    # poetry check, mypy, ruff, black, yamllint
make test    # pytest
```

Both must pass. CI runs the same two targets. One change per PR.
