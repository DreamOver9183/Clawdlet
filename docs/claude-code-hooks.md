<p align="center"><b>English</b> | <a href="claude-code-hooks.zh-TW.md">繁體中文</a></p>

# Claude Code Hooks

Clawdlet listens on `127.0.0.1:47821` for Claude Code [`http` hooks](https://code.claude.com/docs/en/hooks) and reacts to what your agent is doing. It works with Claude Code in a terminal and in Claude Desktop's Code tab.

- [Hook events](#hook-events)
- [Three ways to connect](#three-ways-to-connect)
- [Updating hooks](#updating-hooks)
- [Hooks and the transcript watcher together](#hooks-and-the-transcript-watcher-together)
- [Privacy and security](#privacy-and-security)

---

## Hook events

| Hook event | Clawdlet reaction |
| :--- | :--- |
| `UserPromptSubmit` / `PreToolUse` / `PostToolUse` | Laptop out, typing in segments with breaks (stretch / think / coffee) |
| `SubagentStart` / `SubagentStop` | While subagents run, Clawd **juggles** them at the laptop, taking turns with typing (6–10 s each); back to typing when the last one stops |
| `PreCompact` | **Molts**: sheds its shell and steps out of it; the empty husk lies there for a few seconds, then fades (hook-less sessions: the transcript's `compact_boundary`) |
| `Stop` | Laptop away (tuck or kick), then a **victory dance**, or, if you've been away, a [letter](features.md#mail-when-youre-away) to deliver when you're back |
| `PostToolUseFailure` | Escalates within 90 s: **confused** `?` → nervous **sweat** → **sad**; a finished turn starts over |
| `StopFailure` | **Sad** right away, no victory dance |
| `PreToolUse` for `ExitPlanMode` / `AskUserQuestion` | A [notification](features.md#notifications) sign: **plan scroll** / **`?`** |
| `Notification` `permission_prompt` / `elicitation_dialog` | The **key** / **`?`** sign; carried over to your cursor if unanswered |
| `Notification` (anything else) | Bobbing golden **`!`** until you respond or click Clawd; still unanswered after 10 s, it **waves** both claws (knocks on the title bar when perched) for 2 s every 6 s |

`alert_escalate_s` and `work.juggle_segment_s` live in the [behavior config](behavior-config.md)'s `settings`.

## Three ways to connect

### 1. Zero-config (default)

Clawdlet watches recently active Claude Code session transcripts (`~/.claude/projects/*/*.jsonl`, or `$CLAUDE_CONFIG_DIR`) and reacts with typing and a victory dance.

- It only reads the structure of the records near the end of each file: record type / subtype (`api-request`, `tool_use`, `end_turn`, `compact_boundary`…), flags like `isMeta`, and whether a user record *starts with* one of Claude Code's own markers (`<command-name>`, `[Request interrupted by user`…). It never reads the conversation itself, which is neither stored nor logged.
- Slash commands don't count as work, pressing Esc puts the laptop away without a victory dance, and a tool call that stays silent for 25 s (most likely a permission prompt) stops the typing.
- For notifications it also reads the **name** of a pending `tool_use` (never its input) to spot a plan review or a question, and the top-level `entrypoint` to tell Claude Desktop sessions from terminal ones. A permission prompt can only be guessed (the 8 s `…` hint).
- It cannot show the `!` alert, and the transcript format is internal to Claude Code, so it is best-effort.
- Disable with `--no-transcript-watch`.

### 2. One-click install (recommended)

Tray menu → **Install Hooks** (asks for confirmation), or:

```powershell
clawdlet --install-hooks
```

Clawdlet backs up `~/.claude/settings.json` to `settings.json.clawdlet-bak-<timestamp>`, adds only its own `http` entries pointing at `127.0.0.1`, and leaves everything else untouched. Remove them from the same tray item or with `clawdlet --uninstall-hooks`.

### 3. Manual

Tray menu → **Copy Hook Settings**, or `clawdlet --print-hook-config`, then merge the `hooks` block into `~/.claude/settings.json` yourself.

## Updating hooks

Newer Clawdlet versions listen to more hook events (`SubagentStop` and `PreCompact` were added for juggling and molting). If your `settings.json` has hooks from an older Clawdlet (or for another port or an old token), the tray shows **Update Hooks** instead of the install item. Choosing it (or running `clawdlet --install-hooks` again) replaces Clawdlet's own entries in place, with a backup, and keeps your other hooks, including your own `PreCompact` hooks.

## Hooks and the transcript watcher together

When both hooks and the transcript watcher see the same session, hooks win, so a turn never celebrates twice. The exceptions:

- An Esc interrupt fires no hook and is always taken from the transcript.
- A plan review or question the transcript spots is still shown, in case no hook announced it.
- For Claude Desktop sessions the transcript's `…` hint is kept, because Desktop is assumed not to send `permission_prompt` notifications (not yet verified on a real Desktop install).

## Privacy and security

The hook receiver:

- binds to localhost only;
- requires the bearer token stored in `%APPDATA%\Clawdlet\hook_token`;
- reads nothing but `hook_event_name`, `session_id`, the `notification_type` enum and `tool_name`. This is why `idle_prompt` / `auth_success` notifications don't raise a `!`, Clawd stops typing while a permission prompt waits for you, and `ExitPlanMode` / `AskUserQuestion` get their own sign. `tool_name` is only read from bodies up to 64 KB, never from `tool_input`;
- never logs payloads;
- always answers with an empty `200`, so it can never approve, deny or block anything.

`PermissionRequest` is intentionally left out of the generated settings. If Clawdlet isn't running, Claude Code just reports a non-blocking hook connection error.

To test the receiver without Claude Code, see [Development](development.md#dev-tools).

---

[← Back to README](../README.md)
