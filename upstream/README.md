# The infrahub-sync problems this adapter runs into

Two bugs in `infrahub-sync` 2.0.0 stop a sync on the first attribute that is
not text. Neither is specific to Forward Networks: the IP Fabric example hits
both, on `mtu` and `speed`. Run `forward-infrahub check-environment` to see
which, if either, is present in your install.

## 1. The generator types every field as `str` — solved by a dependency pin

`generator.get_kind` decides a field's Python type with
`isinstance(item, AttributeSchema)`, while `client.schema.all()` returns
`AttributeSchemaAPI`. Until `infrahub-sdk` 1.23.0 the second subclassed the
first, so the check passed; 1.23.0 removed that inheritance and every field
began falling through to the `str` default. A `Number` then rejects its own
value, optional fields lose their `| None`, and a cardinality-many relationship
is typed as a single string.

```
2 validation errors for DcimDeviceType
height      Input should be a valid string [input_value=1, input_type=int]
full_depth  Input should be a valid string [input_value=True, input_type=bool]
```

**No patch needed.** This package pins `infrahub-sdk[all]>=1.17,<1.23`, which
is inside the range `infrahub-sync` itself supports, and the generator is
correct again. `0001-generator-accept-schema-api-classes.patch` remains here for
anyone who needs a newer SDK for other reasons.

Upstream: [issue #187](https://github.com/opsmill/infrahub-sync/issues/187) and
[PR #188](https://github.com/opsmill/infrahub-sync/pull/188), both open, filed
by someone who hit it independently.

## 2. The destination stringifies every attribute it reads back — fixed upstream, unreleased

`InfrahubAdapter.infrahub_node_to_diffsync` converts any non-string attribute
value to `str(val)`. A `List` holding `["10.1.0.0/16"]` comes back as the
printed form of a list, so the second run of a sync cannot load its own
destination:

```
1 validation error for CloudVPC
cidrs  Input should be a valid list [input_value="['10.1.0.0/16']", input_type=str]
```

Fixed on `main` by `8fd9bce0`, "stop stringifying non-IP attribute values", on
2026-05-29. That commit is **not in any release**: the `2.0.0` tag and the PyPI
wheel both still carry the bug, and 2.0.0 was published after it. So either
apply `0002-destination-preserve-attribute-types.patch`, or install
`infrahub-sync` from `main` and keep the SDK pin for problem 1.

```bash
cd .venv/lib/python3.*/site-packages
patch -p2 < path/to/upstream/0002-destination-preserve-attribute-types.patch
```

## Why they survived together

They are consistent with each other. If every generated field is `str`, then
stringifying every value read back matches. Fixing only the first exposes the
second, which is the order in which they were found here.

## Verified

With the SDK pinned and problem 2 addressed, a sync of a real Forward network
into Infrahub 1.11.2 writes 5,558 objects across 13 kinds and reports no
differences on the following run.
