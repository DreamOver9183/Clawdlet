<p align="center"><b>English</b> | <a href="development.zh-TW.md">繁體中文</a></p>

# Development

## Tests

```powershell
pip install -e ".[dev]"
python -m pytest -v
```

Some tests and behaviors need a real Windows desktop (Tk display, system tray, a running Claude Desktop). Go through the **[Windows test checklist](windows-test-checklist.md)** before a release.

## Building `clawdlet.exe`

```powershell
pip install pyinstaller
python -m clawdlet.tools.build_exe
# Output: dist/clawdlet.exe
```

## Sprites and README GIFs

All sprites are drawn in code; nothing is hand-painted or copied.

```powershell
python -m clawdlet.tools.gen_sprites      # Redraw assets/sprites/*.png and manifest.json
python -m clawdlet.tools.gen_demo_gifs    # Redraw assets/demo/clawdlet_demo.gif and assets/demo/actions/*.gif
```

The per-action GIFs used in the README introduction are listed in `ACTION_GIFS` in [`gen_demo_gifs.py`](../src/clawdlet/tools/gen_demo_gifs.py).

## Dev tools

```powershell
clawdlet --debug                                  # Live state & HWND debug banner
clawdlet-dump-windows                             # Inspect the Claude.exe HWNDs Clawdlet detects
clawdlet --dump-behaviors                         # Print the merged behavior table (warnings on stderr)
python -m clawdlet.tools.send_hook_event Stop     # Send a fake hook event to a running Clawdlet
python -m clawdlet.tools.send_hook_event Notification --notification-type idle_prompt  # ...with a notification_type
python -m clawdlet.tools.send_hook_event PreToolUse --tool-name ExitPlanMode           # ...with a tool_name (plan sign)
```

## Design records

- [Concept doc (中文)](clawdlet.md): vision, FSM, physics model, roadmap
- [M6 plan](plans/m6-plan.md): planning and implementation record for v0.3.0
- [Acknowledgements](ACKNOWLEDGEMENTS.md): which projects inspired which behaviors

---

[← Back to README](../README.md)
