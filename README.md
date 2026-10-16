# Irabelle Waste Management


[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=notf0und&repository=irabelle-waste-management&category=integration)

A Home Assistant integration that tells you, for every wheelie bin, whether it is **Filling**, **Awaiting collection** or **Emptied**, using a motion iBeacon stuck to the bin and the Bluetooth proxies you already have. It also tells you which bins to take out tonight and when they are all ready to be brought back.

One device per bin, no helpers, no template sensors, no "reload the iBeacon integration" automations.

## Why not the built-in iBeacon Tracker?

Motion beacons (KKM / KBeacon and similar) advertise one iBeacon UUID while still and a second UUID while moving. The iBeacon Tracker turns that into **two devices per bin**. Worse, it periodically copies the latest RSSI of a MAC address to *every* UUID it has ever seen from that MAC, so the "motion" device keeps receiving updates from the idle advertisement long after the bin stopped moving. Only reloading the integration clears it.

This integration subscribes to every advertisement from the bin's MAC address and decodes the raw packet itself, so each packet is classified as *idle* or *motion* on its own. It works best with **ESPHome Bluetooth proxies**, which forward the raw packet bytes.

## Bin status

| Status | When |
| --- | --- |
| **Filling** | The bin's normal (idle) advertisement has been heard continuously for the *back home after* time (default 2 min). |
| **Awaiting collection** | The idle advertisement has not been heard for the *out after* time (default 10 min): the bin is at the kerb. |
| **Emptied** | While *Awaiting collection*, the beacon advertised motion inside the collection window (default 05:00-19:00), i.e. the truck lifted it. If no motion is seen (truck too far from the proxies), the bin is marked emptied when the window closes, provided it was already out when the window opened, or the bin's calendar has a collection today (e.g. put out at 06:00 on collection day). |

The status survives restarts. `status_since` and `status_reason` attributes tell you when and why it last changed.

## Entities

Per bin (one device each):

| Entity | Description |
| --- | --- |
| `sensor.<bin>_status` | `filling` / `awaiting_collection` / `emptied` |
| `binary_sensor.<bin>_motion` | On while the motion advertisement is heard |
| `binary_sensor.<bin>_in_range` | On while the idle advertisement is heard |
| `sensor.<bin>_last_motion` | Last motion advertisement |
| `sensor.<bin>_next_collection` | Next collection date (only when a calendar is set) |
| `sensor.<bin>_signal`, `sensor.<bin>_last_seen` | Diagnostics: strongest proxy RSSI, last idle advertisement |
| `sensor.<bin>_battery` | Battery % from the KKM extension data (service data `0x2080`, sent in the scan response even in iBeacon mode). Fresh batteries report above 100 and are shown as 100%, like the KBeacon app; the raw value is the `reported_level` attribute. Home Assistant runs ESPHome proxies passively by default, so the integration asks for a 10 s active sweep at startup and every 6 h (every 30 min while a bin has no reading yet). |

Hub summaries, meant as triggers and conditions for your own automations:

| Entity | Description |
| --- | --- |
| `sensor.<hub>_bins_to_take_out` | Bins due today/tomorrow that are still *Filling*. Attributes: `bins`, `sentence` ("Residual Bin and Recycling Bin"), `due`, `status_entities` |
| `sensor.<hub>_bins_awaiting_collection` | Bins at the kerb, not emptied yet |
| `sensor.<hub>_bins_to_bring_back` | Bins that have been emptied |
| `binary_sensor.<hub>_ready_to_bring_back` | On once at least one bin is emptied and none is still awaiting collection |

## Collection schedules

Each bin can point at any Home Assistant **calendar** entity plus a keyword matched against the event title (case-insensitive), so the source of the schedule does not matter:

- A council integration, for example [Waste Collection Schedule](https://github.com/mampfes/hacs_waste_collection_schedule), which exposes a calendar per source.
- A fixed schedule with the Waste Collection Schedule `static` source, for example residual every Thursday with glass and plastics on alternate Thursdays:

  ```yaml
  - name: static
    args: { type: Residual, frequency: WEEKLY, weekdays: TH }
  - name: static
    args: { type: Glass, frequency: WEEKLY, interval: 2, weekdays: TH, start: "2026-10-01" }
  - name: static
    args: { type: Plastics, frequency: WEEKLY, interval: 2, weekdays: TH, start: "2026-10-08" }
  ```

- Home Assistant's **Local Calendar** with repeating events, if you prefer to edit the schedule in the UI.

The calendar is only used for the next-collection date and the *bins to take out* summary. The bin status itself comes from the beacon alone.

## Installation

Irabelle Waste Management requires Home Assistant 2026.10 or newer and the Bluetooth integration (ESPHome Bluetooth proxies recommended).

- [Irabelle Waste Management integration source](https://github.com/notf0und/irabelle-waste-management/tree/main/custom_components/irabelle_waste_management)

1. Click the **Open in HACS** button above. It opens HACS on this repository and adds it as a custom *Integration* repository. By hand: HACS → ⋮ → *Custom repositories* → `notf0und/irabelle-waste-management`, category **Integration**.
2. Download Irabelle Waste Management in HACS and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Irabelle Waste Management.** Set the collection window and timing rules (all can be changed later with **Configure**).
4. On the integration page, click **Add bin** for each bin:
   - Pick the beacon from the list of iBeacons your proxies currently hear, or type its MAC address. Keep the bin still so its idle advertisement is the one listed.
   - The idle UUID is filled in for you. Leave the motion UUID as `auto` (any other UUID from the same MAC counts as motion) or enter it explicitly.
   - Optionally choose the collection calendar and a keyword such as `rubbish` or `recycling`.

Install the integration only once and add every bin to the same hub. Use the bin's **⋮ → Reconfigure** to rename it or change its UUIDs or calendar.

## Example automations

Reminder at 19:00 the evening before:

```yaml
triggers:
  - trigger: time
    at: "19:00:00"
conditions:
  - condition: template
    value_template: >
      {{ 'tomorrow' in state_attr('sensor.irabelle_waste_management_bins_to_take_out', 'due').values() }}
actions:
  - action: notify.notify
    data:
      message: >
        Put out the {{ state_attr('sensor.irabelle_waste_management_bins_to_take_out', 'sentence') }} tonight.
```

Bring-back reminder once everything is emptied and someone is home:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.irabelle_waste_management_ready_to_bring_back
    to: "on"
  - trigger: state
    entity_id: binary_sensor.someone_home
    to: "on"
conditions:
  - condition: state
    entity_id: binary_sensor.irabelle_waste_management_ready_to_bring_back
    state: "on"
  - condition: state
    entity_id: binary_sensor.someone_home
    state: "on"
actions:
  - action: notify.notify
    data:
      message: >
        Bring back the {{ state_attr('sensor.irabelle_waste_management_bins_to_bring_back', 'sentence') }}.
```
