<p align="center"><b>English</b> | <a href="features.zh-TW.md">繁體中文</a></p>

# Features

Everything Clawdlet does, in detail. For the overview see the [README](../README.md).

- [Window-aware perching](#window-aware-perching)
- [Laptop typing and work sessions](#laptop-typing-and-work-sessions)
- [Putting the laptop away](#putting-the-laptop-away)
- [System tray](#system-tray)
- [Reacts to you](#reacts-to-you)
- [Allow window push](#allow-window-push)

---

## Window-aware perching

- **DWM frame tracking**: Clawdlet finds your active `Claude.exe` main window (Windows Store MSIX and standard Squirrel installs; background `claude-code` CLI processes are filtered out) and reads its bounds with `DWMWA_EXTENDED_FRAME_BOUNDS`, so Clawd sits on the title bar with a 0 px gap.
- **"Thinking" heuristic**: only the I/O of the Claude window's own process and its network-service helper counts (sustained ≥ 8 KB/s). Renderers, the GPU process, node/MCP helpers and a terminal Claude Code are never measured.
- **Climbing and strolling**: Clawd walks along the taskbar, climbs the side of the Claude Desktop window and perches on the title bar to peek down at your conversation.
- **Window physics**:
  - Drag Claude Desktop at normal speed and Clawd rides along.
  - Minimize or close the window and Clawd drops into free fall (`FALLING` → `LAND_SQUISH`).
  - Shake the window fast and Clawd is flung off along a ballistic arc (`THROWN` → `DIZZY`, with orbiting stars).

## Laptop typing and work sessions

When Claude Desktop is thinking or generating, when Claude Code is busy, or when you **double-click** Clawd, it pulls an `8×2` Ultrabook out of its belly, jumps, **slams the laptop down** into a side-profile `\_` with shockwave sparks, and hammers the keyboard with two long arms.

Typing comes in segments with short breaks between them:

| Mode | Segment length |
| :--- | :--- |
| Quiet | 20–40 s |
| Normal | 14–28 s |
| Hyper | 8–16 s |

Breaks are drawn from the behavior table's `work_breaks`: Clawd **stretches**, **rests its chin and thinks** (`…`), **sips from a steaming mug**, or glances up from the laptop. Stretching and coffee have a cooldown so they don't repeat back to back. While subagents run, breaks are replaced by [juggling](claude-code-hooks.md#hook-events).

When Claude Desktop's I/O heuristic is the only signal, typing stops after 60 s and the heuristic is ignored for the next 90 s (Claude Code hooks and transcripts still work). For 8 s after a finished turn the Desktop's trailing I/O is ignored too, so it doesn't restart typing.

## Putting the laptop away

- **Quiet / Normal mode**: Clawd closes the lid, picks up the folded Ultrabook, tucks it back into its belly and pats its stomach (`put_away_laptop`).
- **Hyper mode**: Clawd winds up and **kicks the laptop across the desktop into the left edge of your monitor (`x = 0`)**, setting off a 14-frame (1.75 s) pixel-art explosion: electric short-circuit → fireball and flying keycaps → chain-reaction booms → smoke plumes → a golden `★`.

## System tray

Clawdlet lives in the Windows system tray (`clawdlet.ico`). Double-click the icon to summon Clawd to the center; right-click for the menu:

- **Summon to Center** makes a fresh [entrance](#entrances); **Summon to Claude** always drops Clawd onto Claude's title bar.
- **Coding / Typing Mode**: toggles laptop typing, like double-clicking Clawd.
- **Peek from Screen Edge** ([below](#peek-from-screen-edge)), **Allow Window Push** ([below](#allow-window-push)).
- **Mail When Away**, **Listen While Typing**, **Notify When Needed**: all on by default. They are not remembered between runs; use `--no-mail` / `--no-listen` / `--no-notify` to start with them off.
- **Install Hooks** / **Update Hooks**, **Copy Hook Settings**: see [Claude Code hooks](claude-code-hooks.md).
- **Reload Behaviors**, **Open Config Folder**: see [Behavior config](behavior-config.md).
- **Activity mode**: Quiet / Normal / Hyperactive.
- **Scale**: 1x (64 px) / 2x (128 px) / 3x (192 px), anchored at the feet.
- **Click-through Lock** (`WS_EX_TRANSPARENT`): mouse clicks pass straight through Clawd.
- **Debug HUD**: live state and HWND banner.
- **Exit**: Clawd [waves goodbye](#goodbye) first.

## Reacts to you

### Cursor awareness
Clawd's eyes follow your cursor while it rests. Sometimes it trots over to stand under the cursor, and in Hyper mode it **pounces** at a cursor that sits still above its head.

### Touch and combos
Single-click to **poke**, rub the mouse back and forth over it to **pet** it (hearts), click 3+ times fast for a **tantrum**. Single and double clicks are told apart after the system double-click interval (at most 0.5 s).

### Sleep
After 90 s without keyboard or mouse input it yawns and dozes off (`Zzz`). Touch anything and it jolts awake.

### Peek from screen edge
A tray toggle that makes Clawd hide half-visible at the nearest screen edge and lean out to wave when your cursor comes close.

### Tiered idle
The longer you're away, the more Clawd gets up to:

- **L1**: while resting it now and then glances left and right, blinks, or looks over at Claude's window (down at it when perched on it). Purely cosmetic.
- **L2** (no input for 45 s): it unrolls a **scroll** to read (often followed by **polishing a claw**), or **digs in the sand** on the floor.
- **L3** (90 s): yawn → sleep, straight out of whatever L2 hobby it was on.

### Position-aware actions
- At either end of Claude's title bar it may sit facing out with its **legs dangling** over the edge.
- With your cursor inside the window it may lean over and **peer down** at it.
- Walking into a screen edge without climbing it, it sometimes **scratches the wall** before turning around.
- Arriving at the side of Claude's window it may **drum on it** before climbing up.

The tiered-idle and position-aware actions are rows in the [behavior config](behavior-config.md) (`read_scroll`, `polish_claw`, `dig_sand`, `perch_dangle`, `perch_peer`, `edge_scratch`, `window_drum`, the `fidgets` list, and `settings.idle_level2_after_s` / `fidget_interval_s`), so you can tune their weights, chances, durations and cooldowns or switch them off. So are the entrances (`entrances`) and the mail delay (`settings.mail_away_s`).

### Entrances
At startup (unless you pass `--spawn`) and on **Summon to Center**, Clawd arrives one of three ways, picked from the behavior config's `entrances` (an eSheep-style spawn table):

| Entrance | Weight | Notes |
| :--- | :---: | :--- |
| Drops onto Claude's title bar | 5 | Only when the window is visible; never for Summon to Center |
| Falls from the top of the screen at a random spot | 3 | |
| Walks in from a screen side | 2 | With several monitors, a side that borders another monitor is skipped |

### Goodbye
Choosing **Exit** in the tray makes Clawd wave goodbye before the app closes (at most 2 s; right away if it is in the air, held or climbing). `Ctrl+C` and `--duration` still exit at once.

### Mail when you're away
If a Claude Code turn finishes after you've been away for 30 s (`settings.mail_away_s`), Clawd skips the victory dance and keeps a **letter** for you instead. When you come back (it wakes up first if it dozed off), it carries the envelope over to your cursor and holds it up. **Click it** to take the letter: Clawd hands it over, **brings Claude Desktop to the front** so you can read the answer, and celebrates.

If nobody takes it within 90 s, or that session gets busy again, the letter is dropped. Delivery takes priority over typing for other sessions (not over juggling). Turn it off in the tray or with `--no-mail`.

### Listening while you type
While Claude Desktop is the active window and you're typing into it with the mouse at rest, Clawd tilts its head toward the window and listens, then goes back to what it was doing 1.5 s after you stop.

**Privacy**: Clawdlet never reads which keys you press. It only notices that *some* input happened: the system-wide "last input" time from `GetLastInputInfo` resetting while the cursor stays still (a click or the scroll wheel does the same). There is no `GetAsyncKeyState`, no keyboard hook, and nothing is stored or logged. Turn it off in the tray or with `--no-listen`.

### Notifications
When Claude Code (in a terminal or Claude Desktop's Code tab) stops to wait for you, Clawd holds up a sign that says why:

| Waiting for | Sign |
| :--- | :--- |
| Plan review (`ExitPlanMode`) | A **plan scroll** with ticks |
| Question (`AskUserQuestion`, MCP elicitation) | A bouncing **`?`** |
| Permission prompt | A golden **key** |

- Unanswered after 10 s while Claude Desktop is in the background, Clawd **carries the sign over to your cursor** and keeps it there. **Click Clawd** and Claude Desktop comes to the front. While Claude Desktop is the active window Clawd stays put.
- Without hooks, a tool call whose transcript has been quiet for 8 s gets only a soft **`…`** bubble (over the laptop while Clawd types, or where it stands). It never walks over and fades after 30 s. A long-running command can look the same.
- Several sessions waiting: permission > question > plan.
- Turn it off in the tray or with `--no-notify` (a hook's prompt then gets the plain `!` again).

## Allow window push

Off by default. When you turn it on, Hyper mode may walk up to the Claude window and shove it up to 36 px sideways, staying fully on screen. It happens at most once every 15 minutes, only after you've been idle for 20 s, and never on a maximized window. Any input stops it instantly.

---

[← Back to README](../README.md)
