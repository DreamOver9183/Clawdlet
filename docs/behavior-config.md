<p align="center"><b>English</b> | <a href="behavior-config.zh-TW.md">繁體中文</a></p>

# Behavior Config

*New in v0.3.0.* What Clawd does when it is just hanging around comes from a JSON behavior table, in the spirit of Shimeji's `behaviors.xml`.

- The bundled [`assets/behaviors/behaviors.default.json`](../assets/behaviors/behaviors.default.json) reproduces the original idle choices exactly while you're around, and adds the tiered-idle and position-aware rows.
- Your own file **`%APPDATA%\Clawdlet\behaviors.json`** is merged on top of it.
- Engine-driven moves (being dragged, falling, reactions, typing, mail delivery, seeking Claude, wandering) stay in code. The table decides the idle choice after them, plus work breaks, fidgets and how Clawd makes its entrance.

## Example

```json
{
  "$schema": "./behaviors.schema.json",
  "version": 1,
  "extends": "default",
  "behaviors": [
    { "id": "floor_blob", "enabled": false },
    { "id": "perch_look_down", "weight": 0.5, "duration_s": [4, 8] }
  ]
}
```

## Merging

- **`extends: "default"`** (the default): a row with an existing `id` is patched field by field (a nested `when` / `next` / `weight` object is replaced as a whole), new ids are appended, and `settings` are deep-merged.
- **`extends: "none"`** replaces the bundled table.

## Behavior rows

| Field | Meaning |
| :--- | :--- |
| `state` | `IDLE`, `BLOB`, `PERCH_CLAUDE`, `READ_SCROLL`, `POLISH_CLAW`, `DIG_SAND`, `PERCH_DANGLE`, `PERCH_PEER`, `WALL_SCRATCH` |
| `on` | Trigger point: `rest`, or `wall_bump` / `window_side` for the scratching rows |
| `weight` | A number, or `{"quiet", "normal", "hyper"}` per mode |
| `chance` | Optional; rolled on its own before the weighted pick |
| `when` | Conditions: `surface`, `spot`, `mode`, idle level, `user_idle_s`, `claude_visible` / `claude_foreground`, `cursor_in_window`, `prev_state` / `not_prev_state` |
| `duration_s` | How long the behavior lasts |
| `scale_by_rest` | Scale the duration by the mode's rest multiplier |
| `cooldown_s` | Minimum time before the row can be picked again |
| `next` + `next_mode` | Follow-up behavior; `add` / `replace`, up to 4 steps |
| `enabled` | `false` switches the row off |
| `description` | Free text |

## Other tables

- **`entrances`**: rows have `kind` (`drop_on_claude`, `drop_top`, `walk_in_side`), `weight` and `when` (for example `claude_visible`). `{ "id": "walk_in_side", "enabled": false }` keeps Clawd from walking in.
- **`fidgets`**: rows play poses from the `gaze` strip over a resting Clawd every `settings.fidget_interval_s`. Either `"poses": [[gaze_index, hold_s], ...]` (index `(dy+1)*3+(dx+1)`: 3 left, 4 ahead, 5 right, 7 down, 9 blink) or `"target": "claude_window"` with `hold_s`.
- **`work_breaks`**: the breaks between typing segments (stretch, think, coffee, glance).

## Settings

| Key | Meaning |
| :--- | :--- |
| `idle_level2_after_s` | Seconds without input before L2 idle behaviors (default 45) |
| `fidget_interval_s` | Seconds between L1 fidgets, `[lo, hi]` (default `[4, 9]`) |
| `mail_away_s` | How long you must be away before a finished turn becomes a letter (default 30) |
| `alert_escalate_s` | Seconds before an unanswered `!` turns into waving, and a sign is carried to the cursor (default 10) |
| `work.segment_s` | Typing segment length per mode (`quiet` / `normal` / `hyper`) |
| `work.break_s` | Break length between segments (default `[2.5, 4.5]`) |
| `work.heuristic_max_s` / `work.heuristic_cooldown_s` | Typing cap and cooldown when Desktop I/O is the only signal (default 60 / 90) |
| `work.juggle_segment_s` | Juggling / typing turn length while subagents run (default `[6, 10]`) |

## Errors are forgiving

An invalid row is dropped with a warning naming it, and the rest still load. A file that is not valid JSON or has the wrong `version` is ignored with one message (`[Clawdlet] behaviors.json: … — using defaults`).

## Tools

- **Tray → Reload Behaviors** applies your edits without restarting.
- **Tray → Open Config Folder** creates an empty `behaviors.json` template (plus a copy of `behaviors.schema.json`) if missing and opens the folder.
- **Autocompletion**: keep `"$schema": "./behaviors.schema.json"` and VS Code offers every key and value while you type.
- **CLI**: `clawdlet --dump-behaviors` prints the merged table (what Clawd actually uses) and any warnings; `clawdlet --behaviors PATH` loads a different override file.

---

[← Back to README](../README.md)
