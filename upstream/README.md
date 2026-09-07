# Upstream fixes this adapter needs

Two bugs in `infrahub-sync` 2.0.0 stop any adapter from syncing an attribute
that is not Text. They are not specific to Forward Networks: the IP Fabric
example hits them too, on `mtu` and `speed`. Both patches apply to
`infrahub-sync` 2.0.0 against `infrahub-sdk` 1.17 to 1.23.

Until they are released upstream, apply them to the installed package:

```bash
cd .venv/lib/python3.*/site-packages
patch -p2 < path/to/upstream/0001-generator-accept-schema-api-classes.patch
patch -p2 < path/to/upstream/0002-destination-preserve-attribute-types.patch
```

## 0001: the generator types every field as `str`

`generator.get_kind` decides a field's Python type with
`isinstance(item, AttributeSchema)`. But `client.schema.all()` returns
`AttributeSchemaAPI`, which is a sibling class rather than a subclass, so the
check never matches and every field falls through to the `str` default. A
`Number` becomes `str`, a `Boolean` becomes `str`, and a cardinality-many
relationship becomes `str` instead of `list[str]`. Optional fields also lose
their `| None` and their default.

The effect is a generated model that rejects its own source data:

```
2 validation errors for DcimDeviceType
height    Input should be a valid string [input_value=1, input_type=int]
full_depth  Input should be a valid string [input_value=True, input_type=bool]
```

The patch names both classes in the isinstance checks.

## 0002: the destination stringifies every attribute it reads back

`InfrahubAdapter.infrahub_node_to_diffsync` converts any non-string attribute
value to `str(val)`. A `List` attribute holding `["10.1.0.0/16"]` comes back as
the string `"['10.1.0.0/16']"`, so the second run of a sync fails to load its
own destination:

```
1 validation error for CloudVPC
cidrs  Input should be a valid list [input_value="['10.1.0.0/16']", input_type=str]
```

The two bugs are consistent with each other, which is why the combination
survived: if every model field is `str`, stringifying every value read back
matches. Fixing only the generator exposes this one. The patch keeps the
conversion for `ipaddress` objects, which genuinely have no useful diff
representation, and leaves every other value's type alone.

## Verified

With both applied, a full sync of the bundled fixture into Infrahub 1.11.2
creates 24 objects across 18 kinds, and an immediate re-run reports
`(no diffs)`.
