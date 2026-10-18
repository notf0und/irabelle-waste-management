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

## Configure the beacons from Home Assistant

KKM KBeacon / KBeacon Pro beacons (tested with the K4P and BC04P) can be configured over Bluetooth through your ESPHome proxies, like the KBeacon Pro app does. Open a bin and choose **⋮ → Configure**:

| Screen | What it does |
| --- | --- |
| Bin settings | Bin name, collection calendar, battery capacity and the **beacon password** (KKM default `0000000000000000`) |
| Beacon: UUID, major & minor | The iBeacon identity of the slot the bin follows (and the motion trigger if it is missing); the bin follows the change |
| Beacon: general | Advertised name, measured power at 1 m, auto power-on, LED blinking |
| Beacon: advertising slots | Per slot: type (iBeacon, Eddystone UID/URL/TLM, KSensor, System…), interval, transmit power, connectable, trigger-only, BLE 5 mode, channels, iBeacon UUID/major/minor |
| Beacon: triggers | Per trigger: type (motion, buttons, periodic…), actions, slot, UUID change while triggered, duration, interval, power, motion sensitivity and sample rate |
| Beacon: export / import settings | Copy one beacon's setup to others. An import never changes the beacon's name, password, UUID, major or minor; slots and triggers missing from the template are turned off |
| Beacon: power profiler | Estimated average current and battery life from the beacon's settings, like KBeacon Pro (regular advertising only; triggered broadcasts and connections excluded). Set the cell's capacity here (default 1000 mAh, a CR2477) |
| Beacon: identify, change password, factory reset, power off | Identify (beep + LED) only appears for beacons with a beeper. Reset and power off ask for confirmation |

Only the options the beacon reports as supported are offered. Each screen reads the beacon first, so keep it near a proxy. Changing the UUID of the bin's idle advertisement updates the bin automatically.

Every bin also gets a **Battery life** sensor (estimated days left), a **Read beacon settings** button, an **Identify** button on beacons that can beep, (model, firmware and hardware version appear on the device page), and the same operations are available as actions for scripts: `irabelle_waste_management.read_beacon_settings`, `write_beacon_settings`, `export_beacon_settings`, `import_beacon_settings`, `identify_beacon`, `change_beacon_password`, `reset_beacon`, `power_off_beacon`.

Requirements:

- **ESPHome 2026.8.0 or newer on the Bluetooth proxies.** Older firmware cannot complete Bluetooth connections (they time out after 20 s with `reason=0x100` / `status=133`) even though scanning works.
- The beacon's slot must be **connectable** (the default), and the KBeacon app must not be connected to it at the same time.
- Keep the password: a lost password can only be recovered with a factory reset using the beacon's button.

## Collection schedules

Each bin can point at any Home Assistant **calendar** entity plus a keyword matched against the event title (case-insensitive), so the source of the schedule does not matter:

- **A council API**, for example [Waste Collection Schedule](https://github.com/mampfes/hacs_waste_collection_schedule), which exposes a calendar per source.
- **A fixed schedule** (no API), with Home Assistant's built-in **Local Calendar** — see below.

The calendar is only used for the next-collection date and the *bins to take out* summary. The bin status itself comes from the beacon alone.

### Fixed schedule with Local Calendar (step by step)

Example rotation: residual every Thursday, glass and plastics on alternate Thursdays.

1. **Create the calendar** (once): **Settings → Devices & services → Add integration → Local Calendar**, name it e.g. `Bin collections` (this creates `calendar.bin_collections`).
2. **Add one repeating event per bin type**: open **Calendar** in the sidebar, select *Bin collections* and click **+ Add event**:

   | Event title | Start date | All day | Repeat |
   | --- | --- | --- | --- |
   | `Residual` | the next collection Thursday | yes | Weekly, every **1** week, on Thursday |
   | `Glass` | the next Thursday glass goes out | yes | Weekly, every **2** weeks, on Thursday |
   | `Plastics` | the Thursday *after* that one | yes | Weekly, every **2** weeks, on Thursday |

   - The title only has to **contain** the bin's keyword (case-insensitive): `Residual`, `Residual bin` and `residual collection` all match `residual`.
   - Use **all-day** events: only the date matters.
   - Other patterns: "1st and 3rd Thursday of the month" = two events, **Monthly → first Thursday** and **Monthly → third Thursday**.
3. **Link each bin**: in **Add bin** (or the bin's **⋮ → Configure → Bin settings**) choose **Collection calendar** `calendar.bin_collections` and the **Calendar keyword** (`residual`, `glass`, `plastics`). One calendar serves all bins; the keyword tells them apart.
4. **Check**: each bin's **Next collection** sensor shows the right date within seconds (the calendar is re-read whenever it changes, and every 30 minutes). The evening before a collection, `sensor.<hub>_bins_to_take_out` lists the bins due tomorrow that are still at home.

**Holidays / one-off changes**: click the affected occurrence in the calendar and edit or delete **only this event** (e.g. move it to Friday); the integration follows automatically.

### Fixed schedule with Waste Collection Schedule

If Waste Collection Schedule is already installed, its `static` source does the same with recurrence rules and exposes a calendar to point the bins at:

```yaml
- name: static
  args: { type: Residual, frequency: WEEKLY, weekdays: TH }
- name: static
  args: { type: Glass, frequency: WEEKLY, interval: 2, weekdays: TH, start: "2026-10-01" }
- name: static
  args: { type: Plastics, frequency: WEEKLY, interval: 2, weekdays: TH, start: "2026-10-08" }
```

## Installation

Irabelle Waste Management requires Home Assistant 2026.10 or newer and the Bluetooth integration (ESPHome Bluetooth proxies recommended).

- [Irabelle Waste Management integration source](https://github.com/notf0und/irabelle-waste-management/tree/main/custom_components/irabelle_waste_management)

1. Click the **Open in HACS** button above. It opens HACS on this repository and adds it as a custom *Integration* repository. By hand: HACS → ⋮ → *Custom repositories* → `notf0und/irabelle-waste-management`, category **Integration**.
2. Download Irabelle Waste Management in HACS and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Irabelle Waste Management.** Set the collection window and timing rules (all can be changed later with **Configure**).
4. On the integration page, click **Add bin** for each bin:
   - Name it, pick the beacon from the list of iBeacons your proxies hear (strongest first) or type its MAC address, and enter the beacon password if it is not KKM's default `0000000000000000`.
   - The integration connects to the beacon and reads it: the iBeacon **UUID, major and minor** are filled in from the beacon, and changing them writes them to the beacon. If the beacon has no motion trigger that changes its UUID, **Set up the motion trigger** (ticked by default) adds it, so a factory-fresh KKM beacon works without the KBeacon app.
   - Optionally choose the collection calendar, a keyword such as `rubbish` or `recycling`, and the battery capacity.
   - If the beacon cannot be reached (out of range, wrong password, not connectable), fix the password and retry, or tick **Add without connecting** to follow the UUID it advertises.

Install the integration only once and add every bin to the same hub. Use the bin's **⋮ → Configure** to rename it, change its calendar, or configure its beacon.

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
