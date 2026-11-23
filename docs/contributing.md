# Contributing

How changes to forward-infrahub are made, and how the code is laid out.

## How a change is made

1. **A written decision.** Anything that changes behaviour or structure starts as a short
   written decision: the problem, the options, and what we'll do, as numbered points.
2. **Expected behaviour, agreed before code.** Each point becomes concrete cases (given this,
   when that, then this) that the owner agrees describe "correct". They're the contract.
3. **A failing test first.** Each case gets a test, written from the case and not from the
   code. Every test has to fail before the change exists, for the reason the case gives. A test
   that passes before the change proves nothing.
4. **Then the build.** Write the code until those tests pass and the full suite is green.
5. **Then review.** A second reader checks the change against the decision: every caller and
   re-export of anything that moved, and the real test output. Changes to tests, or to a check
   the change is gated by, are reviewed on their own.

Small fixes can skip the written decision, but never the failing test.

## Layout

The package is layered:

- **domain:** the model and pure logic (mapping, scope, identity, query contracts, row diffs).
  It does no I/O.
- **ports:** `src/forward_infrahub/ports.py`, the Protocols for what the core needs from outside
  (Forward, the row store, the query catalogue), and the core names adapters may use; the
  adapter's settings (`settings.py`) sit here too.
- **use cases:** fetch, snapshot and publish. They take their collaborators as arguments.
- **adapters:** the infrahub-sync plugin and the command line on one side; the Forward client,
  the replay client, the row cache and the bundled queries on the other.
- **composition root:** `src/forward_infrahub/composition_root.py`, where collaborators are
  built. Nothing else constructs them.

Put new code in the layer it belongs to, and import the core through `ports.py`. Review checks
the layering; if a change seems to need an exception, it's usually code in the wrong layer.

## Checks

```bash
uv sync --all-groups
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest -q -W error::UserWarning
uv run forward-infrahub check-contracts
```

These are the checks CI runs on every change; CI also builds the wheel and tracks
infrahub-sync's development branch. None of them needs Forward or Infrahub: `replay_fixture`
points the adapter at a JSON file of rows, so the whole suite runs offline.

## Commits

Each commit is one change, and its message ends with a `Verified:` line saying how it was
checked: the command and its result, or `none`.

## License

By contributing you agree your contribution is licensed under the project's license
([LICENSE](../LICENSE), FCL-1.0-ALv2).
