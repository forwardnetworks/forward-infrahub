# Forward Networks to Infrahub

## Before the first run

1. **Load the schema.** This sync targets the Infrahub schema library plus the
   `Cloud*` extension in `schema.yml`:

   ```bash
   git clone https://github.com/opsmill/schema-library
   INFRAHUB_ADDRESS=http://localhost:8000 INFRAHUB_API_TOKEN=... \
     ./infrahub-schema-load.sh
   ```

2. **Create the account.** The Infrahub destination attributes everything it
   writes to a `CoreAccount` named after `source.name`, so create one called
   `forwardnetworks`. The load script prints the object to use.

3. **Set the credentials.** `config.yml` reads them from the environment:

   ```bash
   export FORWARD_URL=https://fwd.app
   export FORWARD_USERNAME=... FORWARD_PASSWORD=...
   export FORWARD_NETWORK_ID=...
   export INFRAHUB_API_TOKEN=...
   ```

   Edit `network_id` in `config.yml`, or leave it and rely on
   `FORWARD_NETWORK_ID`.

## Run it

```bash
infrahub-sync generate --name from-forwardnetworks --directory .
infrahub-sync diff     --name from-forwardnetworks
infrahub-sync sync     --name from-forwardnetworks
```

`generate` reads the live Infrahub schema, so the schema has to be loaded
first. Run `diff` before `sync` on a network you have not synced before: it
prints what would be created without writing anything.

## Publish the queries

The bundled queries run inline until you publish them, which works but means
every run reads the whole network. Publishing them to Forward's query library
lets later runs read only what changed between two snapshots:

```bash
forward-infrahub publish-queries --config config.yml
forward-infrahub audit-queries   --config config.yml
```

`audit-queries` compares the committed source against what this package ships,
so an upgrade that changes a query tells you to publish again.

## Try it without Forward

Point the adapter at a fixture to see the shape of the data it produces:

```yaml
source:
  name: forwardnetworks
  settings:
    replay_fixture: ../../tests/fixtures/forward_rows.json
    network_id: demo
```

## What gets synced

| Infrahub kind | From |
| --- | --- |
| `OrganizationManufacturer`, `DcimPlatform`, `DcimDeviceType` | device platform data |
| `LocationSite` | Forward locations |
| `DcimDevice` | collected devices, with chassis serial and OS version |
| `InterfacePhysical`, `InterfaceVirtual` | interfaces, split on whether they are ethernet |
| `IpamNamespace`, `IpamVRF` | network instances |
| `IpamVLANGroup`, `IpamVLAN` | VLANs, grouped per site |
| `IpamPrefix` | connected and routed prefixes, both families |
| `IpamIPAddress` | interface addresses, including the device's primary |
| `DcimCable` | resolved interface links |
| `CloudAccount`, `CloudVPC`, `CloudSubnet`, `CloudService` | cloud inventory |

Devices Forward could not collect in the chosen snapshot are excluded: the
queries require a completed collection, so a device that failed collection is
absent rather than present and wrong.
