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

Per bin you also get a **Set status** selector (and the `irabelle_waste_management.set_bin_status` action) to correct the status by hand when detection got it wrong, e.g. the bin was emptied but the truck's lift was not heard. Detection carries on from the corrected status.

Hub summaries, meant as triggers and conditions for your own automations:

| Entity | Description |
| --- | --- |
| `sensor.<hub>_bins_to_take_out` | Bins due today/tomorrow that are still *Filling*. Attributes: `bins`, `sentence` ("Residual Bin and Recycling Bin"), `due`, `status_entities` |
| `sensor.<hub>_bins_awaiting_collection` | Bins at the kerb, not emptied yet |
| `sensor.<hub>_bins_to_bring_back` | Bins that have been emptied |
| `binary_sensor.<hub>_ready_to_bring_back` | On once at least one bin is emptied and none is still awaiting collection |

## Beacon firmware

Each bin has a **Firmware** update entity: the version on the beacon against the newest firmware KKM publishes for that beacon's *hardware version* (the same catalogue and matching rules as KKM's own updater), so Home Assistant shows an update notice when KKM releases one. The attributes say whether KKM publishes firmware for that hardware at all: newer board revisions often ship with factory firmware that is newer than anything in the public catalogue. Install updates with the KBeacon Pro app for now.

## Repair notices

The integration raises notices under **Settings → System → Repairs** (they clear themselves once the problem is gone) when:

- a bin's beacon has not been heard for 3 days (flat battery, beacon came off, out of range);
- a beacon's battery is at 20 % or below, or has less than 90 days of estimated life left;
- a connectable ESPHome Bluetooth proxy runs firmware older than 2026.8.0, which cannot configure beacons.

## Configure the beacons from Home Assistant

KKM KBeacon / KBeacon Pro beacons (tested with the K4P and BC04P) can be configured over Bluetooth through your ESPHome proxies, like the KBeacon Pro app does. Open a bin and choose **⋮ → Configure**:

| Screen | What it does |
| --- | --- |
| Bin settings | Bin name, collection calendar, battery capacity and the **beacon password** (KKM default `0000000000000000`) |
| Beacon: UUID, major & minor | The iBeacon identity of the slot the bin follows (and the motion trigger if it is missing); the bin follows the change |
| Beacon: general | Advertised name, measured power at 1 m, auto power-on, LED blinking |
| Beacon: advertising slots | Per slot: type (iBeacon, Eddystone UID/URL/TLM, KSensor, System…), interval, transmit power, connectable, trigger-only, BLE 5 mode, channels, iBeacon UUID/major/minor, Eddystone fields, KSensor content (acceleration, temperature & humidity, light, PIR, VOC, CO₂, magnetometer, record count) |
| Beacon: triggers | Per trigger: type (motion, angle, buttons, temperature/humidity/light thresholds, PIR, cut-off, periodic…), actions, slot, UUID change while triggered, duration, interval, power, and the type's parameters (motion sensitivity and sample rate, °C, %, lux, angles, intervals) |
| Beacon: sensors | For beacons with configurable sensors (temperature & humidity, light, PIR, VOC, CO₂, geomagnetic, Bluetooth scanner): history recording, measuring interval, change thresholds and daily disable periods, as reported by the firmware |
| Beacon: export / import settings | Copy one beacon's setup to others. An import never changes the beacon's name, password, UUID, major or minor; slots and triggers missing from the template are turned off |
| Beacon: power profiler | Estimated average current and battery life from the beacon's settings, like KBeacon Pro (regular advertising only; triggered broadcasts and connections excluded). Set the cell's capacity here (default 1000 mAh, a CR2477) |
| Beacon: identify, change password, factory reset, power off | Identify (beep + LED) only appears for beacons with a beeper. Reset and power off ask for confirmation |

Only the options the beacon reports as supported are offered. Beacons that record sensor history (temperature & humidity, light, PIR, alarm, VOC, CO₂ models) can be read with the `read_beacon_history` action (returns the records) and cleared with `clear_beacon_history`. Each screen reads the beacon first, so keep it near a proxy. Changing the UUID of the bin's idle advertisement updates the bin automatically.

Every bin also gets a **Battery life** sensor (estimated days left), a **Read beacon settings** button, an **Identify** button on beacons that can beep, (model, firmware and hardware version appear on the device page), and the same operations are available as actions for scripts: `irabelle_waste_management.read_beacon_settings`, `write_beacon_settings`, `export_beacon_settings`, `import_beacon_settings`, `identify_beacon`, `change_beacon_password`, `reset_beacon`, `power_off_beacon`, `read_beacon_history`, `clear_beacon_history`, `set_bin_status`.

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

These use the hub entities (with the default hub name *Irabelle Waste Management*; adjust the entity ids if you renamed it):

| Entity | Use |
| --- | --- |
| `sensor.irabelle_waste_management_bins_to_take_out` | Bins due today/tomorrow that are still at home; attribute `due` maps each bin name to `today`/`tomorrow` |
| `sensor.irabelle_waste_management_bins_to_bring_back` | Emptied bins; attribute `bins` lists them |
| `binary_sensor.irabelle_waste_management_ready_to_bring_back` | On once at least one bin is emptied and none is still awaiting collection |

Replace `notify.notify` with your own notifier. For Alexa (Alexa Media Player) use `notify.alexa_media` with `data: {type: tts}` and `target:` your Echo media players.

### Take the bins out: every half hour the evening before

Announces every 30 minutes from 18:00 to 21:30 the day before collection. A bin drops out of the list about 10 minutes after it is put out (it becomes *Awaiting collection*), so if two bins are due and only one goes out, the next announcements only mention the one still at home, and they stop once all are out.

```yaml
alias: Bins - take out reminder
triggers:
  - trigger: time_pattern
    minutes: /30
conditions:
  - condition: time
    after: "18:00:00"
    before: "21:30:01"
  - condition: template
    value_template: "{{ take_out | length > 0 }}"
variables:
  # Bins due tomorrow and still at home, e.g. ["residual", "recycling"]
  take_out: >-
    {%- set ns = namespace(bins=[]) -%}
    {%- for name, due in (state_attr('sensor.irabelle_waste_management_bins_to_take_out', 'due') or {}).items() if due == 'tomorrow' -%}
      {%- set ns.bins = ns.bins + [name | replace(' Bin', '') | lower] -%}
    {%- endfor -%}
    {{ ns.bins }}
actions:
  - variables:
      bin_list: >-
        {{ take_out[0] if take_out | length == 1
           else take_out[:-1] | join(', ') ~ ' and ' ~ take_out[-1] }}
      plural: "{{ 's' if take_out | length > 1 else '' }}"
  - action: notify.notify
    data:
      message: Don't forget to put out the {{ bin_list }} bin{{ plural }} tonight!
mode: single
```

Example: *"Don't forget to put out the residual and recycling bins tonight!"*, then *"…the recycling bin tonight!"* once the residual bin is out.

### Bring the bins back: on a schedule

Announces every 30 minutes from 18:00 to 21:00 while emptied bins are waiting at the kerb.

```yaml
alias: Bins - bring back reminder
triggers:
  - trigger: time_pattern
    minutes: /30
conditions:
  - condition: time
    after: "18:00:00"
    before: "21:00:01"
  - condition: state
    entity_id: binary_sensor.irabelle_waste_management_ready_to_bring_back
    state: "on"
actions:
  - variables:
      back: >-
        {{ (state_attr('sensor.irabelle_waste_management_bins_to_bring_back', 'bins') or [])
           | map('replace', ' Bin', '') | map('lower') | list }}
      bin_list: >-
        {{ back[0] if back | length == 1 else back[:-1] | join(', ') ~ ' and ' ~ back[-1] }}
      plural: "{{ 's' if back | length > 1 else '' }}"
  - action: notify.notify
    data:
      message: Waste collection is done. Time to bring back the {{ bin_list }} bin{{ plural }}!
mode: single
```

### Bring the bins back: when someone is home (at most once per hour)

Announces when motion is detected in the living room (use any presence or motion sensor) while the bins are ready to come back. `mode: single` plus the one-hour `delay` at the end is the throttle: further motion during that hour is ignored. The time condition keeps it quiet at night.

```yaml
alias: Bins - bring back on motion
triggers:
  - trigger: state
    entity_id: binary_sensor.living_room_motion
    from: "off"
    to: "on"
conditions:
  - condition: state
    entity_id: binary_sensor.irabelle_waste_management_ready_to_bring_back
    state: "on"
  - condition: time
    after: "07:00:00"
    before: "22:00:00"
actions:
  - variables:
      back: >-
        {{ (state_attr('sensor.irabelle_waste_management_bins_to_bring_back', 'bins') or [])
           | map('replace', ' Bin', '') | map('lower') | list }}
      bin_list: >-
        {{ back[0] if back | length == 1 else back[:-1] | join(', ') ~ ' and ' ~ back[-1] }}
      plural: "{{ 's' if back | length > 1 else '' }}"
  - action: notify.notify
    data:
      message: Welcome back! The {{ bin_list }} bin{{ plural }} can be brought back in.
  - delay:
      hours: 1
mode: single
max_exceeded: silent
```

### Morning reminder for a bin that was forgotten

If a bin due **today** is still at home at 06:30, remind once more before the truck comes:

```yaml
alias: Bins - forgotten bin this morning
triggers:
  - trigger: time
    at: "06:30:00"
conditions:
  - condition: template
    value_template: >
      {{ 'today' in (state_attr('sensor.irabelle_waste_management_bins_to_take_out', 'due') or {}).values() }}
actions:
  - action: notify.notify
    data:
      message: >
        Collection is today and the
        {{ (state_attr('sensor.irabelle_waste_management_bins_to_take_out', 'due') or {}).items()
           | selectattr(1, 'eq', 'today') | map(attribute=0) | join(' and ') }}
        is still at home!
mode: single
```

## Development

Tests cover the bin state machine, the KBeacon protocol (login, framing, sensor history), settings templates, the power profiler (checked against KBeacon Pro's own figures) and every configuration screen, using configurations captured from real K4P and BC04P beacons:

```bash
pip install -r requirements_test.txt
python -m pytest
```

Every push runs Home Assistant's hassfest, the HACS validation and the tests.
