# Manual verifications

Record every manual acceptance here: what was checked, the date, the
browser/device, and the result.

## T-3.4 — Browser capture: both voices present

**What to do:** open `http://localhost:8000/` (with `make run`), put a real
phone call on speaker next to the mic, press Start, talk on both sides for
~30 s, press Stop. Confirm in the decision feed / gateway logs that turns
from **both** speakers were transcribed, and that the "≤ 3.4 kHz (far /
phone voice)" meter moved while the other person was talking.

| Date | Browser | Device | Far-end voice present? | Notes |
|------|---------|--------|------------------------|-------|
| _pending_ | | | | |

## T-4.3 — Console live view: screenshot at INTERVENE

**What to do:** `make run`, open `http://localhost:8000/`, set a session id (e.g.
`demo`), click **Watch**, then in another shell:
`curl -X POST 'http://localhost:8000/replay/fx_gift_card_en_001?session=demo&speed=6'`.
Watch the gauge climb, the transcript fill with role colours + signal chips, and
the timeline draw the signal ticks and state transitions. Screenshot at
`INTERVENE` and commit it to `docs/`.

| Date | Screenshot | Notes |
|------|-----------|-------|
| _pending_ | | |
