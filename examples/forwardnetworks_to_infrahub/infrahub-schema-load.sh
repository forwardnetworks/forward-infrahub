#!/usr/bin/env bash
# Load the schema this sync targets: the Infrahub schema library, plus the
# small Cloud* extension that ships with forward-infrahub.
#
#   INFRAHUB_ADDRESS=http://localhost:8000 INFRAHUB_API_TOKEN=... ./infrahub-schema-load.sh
#
# Point SCHEMA_LIBRARY at a clone of https://github.com/opsmill/schema-library.
# Needs infrahubctl on PATH: `pip install "infrahub-sdk[ctl]"`. This repo's
# own dev group carries it; run through `uv run ./infrahub-schema-load.sh`
# there instead of installing it separately.
set -euo pipefail

SCHEMA_LIBRARY="${SCHEMA_LIBRARY:-./schema-library}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -d "$SCHEMA_LIBRARY" ]; then
  echo "Clone the schema library first, or set SCHEMA_LIBRARY:" >&2
  echo "  git clone https://github.com/opsmill/schema-library" >&2
  exit 1
fi

# location_site, not location_minimal: both define LocationSite and loading
# both conflicts.
infrahubctl schema load \
  "$SCHEMA_LIBRARY/base/organization.yml" \
  "$SCHEMA_LIBRARY/base/location.yml" \
  "$SCHEMA_LIBRARY/base/dcim.yml" \
  "$SCHEMA_LIBRARY/base/ipam.yml" \
  "$SCHEMA_LIBRARY/extensions/location_site/location_site.yml" \
  "$SCHEMA_LIBRARY/extensions/vlan/vlan.yml" \
  "$SCHEMA_LIBRARY/extensions/vrf/vrf.yml" \
  "$SCHEMA_LIBRARY/extensions/cable/cable.yml" \
  "$SCHEMA_LIBRARY/extensions/lag/lag.yml" \
  "$HERE/schema.yml"

# The destination marks everything it writes with an account named after
# source.name, so operator edits stay distinguishable from synced data.
cat <<'ACCOUNT'

Next, make sure a CoreAccount named "forwardnetworks" exists:

  infrahubctl object load account.yml

where account.yml is:

  ---
  apiVersion: infrahub.app/v1
  kind: Object
  spec:
    kind: CoreAccount
    data:
      - name: forwardnetworks
        label: Forward Networks
        account_type: Script
        password: pick-something-long
ACCOUNT
