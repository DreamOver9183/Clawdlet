# Clawdlet

```ts
let clawd = new WindowCompanion({ gravity: true });
```

> ***`let clawd` roam on your Claude Desktop.***  
> **Clawdlet** — An unofficial, fan-made physics desktop pet that perches on your Claude Desktop window (`Claude.exe`).  
> **Clawdlet** —— 具備 2D 物理重力、視窗邊界感知（Window-Aware）且專屬於 Claude Desktop 桌面視窗的動態像素桌寵。

![Clawdlet Showcase](assets/demo/clawdlet_demo.gif)

---

## ✨ Core Features | 核心特色

### 1. Window-Aware Claude Desktop Perching | 專屬視窗感知與標題列吸附
- **DWM Frame Tracking**: Automatically detects your active `Claude.exe` main window (supporting both Windows Store MSIX & standard Squirrel installs while filtering out background `claude-code` CLI processes) via `DWMWA_EXTENDED_FRAME_BOUNDS` with **0px gap** perching.
- **Autonomous Climbing & Strolling**: Clawdlet actively walks across your taskbar, climbs vertically up the side of your Claude Desktop window, and perches on top of the titlebar to peek down at your conversation.
- **Dynamic Window Physics**:
  - Smoothly follows the window when you drag Claude Desktop at normal speed.
  - Drops into free-fall (`FALLING` → `LAND_SQUISH`) if the window is minimized or closed.
  - Gets violently flung off along a ballistic arc (`THROWN` → `DIZZY` with orbiting stars) if you shake the Claude window rapidly!

### 2. Comedic Side-Profile Laptop Typing | 喜感側面掏筆電重摔打字 (`\_`)
- When Claude Desktop is actively thinking/generating—or when you **double-click** Clawdlet—it pulls a sleek `8×2` Ultrabook out of its belly, jumps high overhead, **slams the laptop down** into a side-profile `\_` shape with shockwave sparks, and stretches out **two long arms** to hammer away at the keyboard!

### 3. Mode-Dependent Laptop Put-Away & Cross-Screen Kick | 依模式分流的收筆電／踢飛大爆炸
- **Standard / Quiet Mode (`標準／安靜模式`)**: Neatly closes the `\_` laptop lid, picks up the folded Ultrabook, tucks it back into its belly, and pats its stomach (`put_away_laptop`).
- **Hyper Mode (`過動模式`)**: Winds up its front-left leg and **violently kicks the laptop all the way across your desktop into the left edge of your monitor (`x = 0`)**, triggering a **14-frame (1.75s) multi-stage pixel-art explosion** (electric short-circuit → 4-layer fireball & flying keycaps → chain-reaction cluster booms → billowing smoke plumes → 8-point anime golden star `★`)!

### 4. Native Win32 System Tray Control | 系統工作列圖示與穿透鎖定
- Lives quietly in the Windows System Tray (`clawdlet.ico`) with a full right-click menu:
  - **Summon to Center / Summon to Claude Window** (`一鍵召喚回畫面中央／Claude 視窗`)
  - **Activity Modes**: `Quiet (安靜)` / `Normal (標準)` / `Hyper (過動)`
  - **Pixel-Art Scaling**: `1x (64px)` / `2x (128px)` / `3x (192px)` anchored at feet level
  - **Click-Through Lock (`WS_EX_TRANSPARENT`)**: 100% mouse click pass-through so it never blocks your workflow
  - **Peek from Screen Edge / Allow Window Push / Copy Claude Code Hook Settings** (`邊緣躲藏／允許推視窗／複製 hook 設定`)

### 5. Reacts to You | 對你有反應（v0.2.0）
- **Cursor awareness (`游標感知`)**: Clawd's eyes follow your cursor while it rests, it sometimes trots over to stand under the cursor, and in Hyper mode it **pounces** at a cursor that sits still above its head.
- **Touch & combos (`觸摸與連擊`)**: single-click to **poke**, rub the mouse back and forth over it to **pet** it (hearts!), click 3+ times fast for a **tantrum**.
- **Sleep schedule (`作息與睡眠`)**: after 90 s without keyboard/mouse input it yawns and dozes off (`Zzz`); touch anything and it jolts awake.
- **Peek from screen edge (`邊緣躲藏`)**: a tray toggle that makes Clawd hide half-visible at the nearest screen edge and lean out to wave when your cursor comes close.

### 6. Claude Code Hooks | Claude Code 連動（v0.2.0）
Clawdlet listens on `127.0.0.1:47821` for Claude Code [`http` hooks](https://code.claude.com/docs/en/hooks) and reacts to what your agent is doing:

| Hook event | Clawdlet reaction | 反應 |
| :--- | :--- | :--- |
| `UserPromptSubmit` / `PreToolUse` / `PostToolUse` / `SubagentStart` | Laptop out, typing | 掏筆電打字 |
| `Stop` | Laptop away (tuck or kick), then a **victory dance** | 收／踢筆電後慶祝 |
| `PostToolUseFailure` / `StopFailure` | Nervous **sweat** | 冒冷汗 |
| `Notification` (permission / input needed) | Bobbing golden **`!`** until you respond or click Clawd | 頭上跳「!」 |

Setup | 設定方式:
1. Start Clawdlet once (it creates a private token at `%APPDATA%\Clawdlet\hook_token`).
2. Tray menu → **複製 Claude Code hook 設定 (Copy Hook Settings)**, or run `clawdlet --print-hook-config`.
3. Merge the printed `hooks` block into `~/.claude/settings.json`.

The receiver only binds to localhost, requires the bearer token, reads nothing but `hook_event_name` and `session_id`, never logs payloads, and always answers with an empty `200` so it can never approve, deny, or block anything. `PermissionRequest` is intentionally left out of the generated settings. If Clawdlet isn't running, Claude Code just reports a non-blocking hook connection error.

---

## 🚀 Quick Start | 快速開始

### Requirements | 環境需求
- **OS**: Windows 10 / 11 (64-bit, Per-Monitor DPI V2 supported)
- **Python**: Python 3.11+

### Installation & Run | 安裝與執行

```powershell
# Clone repository
git clone https://github.com/DreamOver9183/Clawdlet.git
cd Clawdlet

# Install in editable mode
pip install -e .

# Launch Clawdlet!
python -m clawdlet
```

### CLI Options | 命令列參數

```powershell
clawdlet --scale 2 --spawn claude          # Spawn above Claude Desktop at 2x (128x128) scale
clawdlet --mode hyper                      # Start directly in Hyper Mode (過動模式)
clawdlet --click-through                   # Start with mouse click-through lock enabled
clawdlet --debug                           # Show live state & HWND debug banner
clawdlet --print-hook-config               # Print the Claude Code hooks block for ~/.claude/settings.json
clawdlet --hook-port 47821                 # Port for the Claude Code hook receiver (default 47821)
clawdlet --no-hooks                        # Don't start the hook receiver
clawdlet --allow-window-push               # Hyper mode may nudge the Claude window a few px (off by default)
clawdlet-dump-windows                      # Diagnostic tool to inspect detected Claude.exe HWNDs
python -m clawdlet.tools.send_hook_event Stop   # Dev: send a fake hook event to a running Clawdlet
```

### Build Standalone `clawdlet.exe` | 打包單一執行檔

```powershell
pip install pyinstaller
python -m clawdlet.tools.build_exe
# Output executable: dist/clawdlet.exe
```

---

## 🎮 Controls & Interactions | 互動操作一覽

| Input | Action | 操作說明 |
| :--- | :--- | :--- |
| **Left-Click + Drag** | Pick up & throw | 左鍵按住並移動超過 4px 即拎起掙扎，甩動放開可依滑鼠軌跡拋飛（高速落地會眼冒金星暈眩） |
| **Single Left-Click** | Poke | 單擊戳一下（睡著時會嚇醒；頭上有「!」時代表已讀） |
| **Double Left-Click** | Toggle Laptop Typing | 左鍵雙擊切換「掏筆電重摔打字」，再次雙擊（或 6 秒後）觸發收筆電／踢飛筆電。單擊／雙擊要等系統雙擊間隔（最多 0.5 秒）結束才判定 |
| **3+ Rapid Clicks** | Tantrum | 連點三下以上會抓狂跺腳 |
| **Rub Mouse Over Pet** | Pet | 不按鍵、滑鼠在身上左右來回摩擦就是撫摸，會冒愛心 |
| **Move Cursor Nearby** | Gaze / Chase / Pounce | 眼睛追著游標看、偶爾走到游標下方；過動模式下游標停在頭上方會撲上去 |
| **Right-Click on Pet** | Playful Hop | 右鍵點擊角色觸發原地開心跳躍 |
| **Leave the PC (90 s)** | Sleep | 90 秒沒碰鍵盤滑鼠就打哈欠睡著，一動就驚醒 |
| **Double-Click Tray Icon** | Summon to Center | 左鍵雙擊右下角系統匣圖示，立即將 Clawdlet 召喚回螢幕中央 |
| **Right-Click Tray Icon** | Open Control Menu | 右鍵點擊系統匣圖示，切換「安靜／標準／過動模式」、「1x/2x/3x 縮放」、「穿透鎖定」、「邊緣躲藏」、「允許推視窗」與「複製 hook 設定」 |

**Allow Window Push (`允許推視窗`)** is off by default. When you turn it on, Hyper mode may — at most once every 15 minutes, only after you've been idle for 20 s, and never on a maximized window — walk up to the Claude window and shove it up to 36 px sideways (staying fully on screen). Any input stops it instantly.

---

## 🧪 Testing | 自動化測試

```powershell
pip install -e ".[dev]"
python -m pytest -v
```

---

## ⚖️ Legal Disclaimer & Dual-Licensing | 免責聲明與雙重授權

**Clawdlet** is an unofficial, non-commercial, fan-made open-source project.  
It is **NOT** affiliated with, endorsed by, or sponsored by Anthropic, PBC.

The "Claude" name, brand, and the original "Clawd" character concept are the intellectual property of Anthropic, PBC.
- **Source Code ([`LICENSE`](LICENSE))**: Licensed under the **MIT License**.
- **Sprite & Art Assets ([`assets/LICENSE.md`](assets/LICENSE.md))**: Explicitly excluded from the MIT License. All fan-made pixel-art assets in `assets/` are provided strictly for personal, non-commercial entertainment and educational use. Commercial redistribution, merchandise, or crypto/NFT usage is strictly prohibited.
