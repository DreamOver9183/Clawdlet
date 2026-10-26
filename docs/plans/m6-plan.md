# M6 計畫紀錄 | M6 Plan Record

> 本檔保存 M6（v0.3.0）在 Claude Code session 中的規劃與實作過程：先由 subagent 研究，再訂計畫、經使用者核准，之後分階段交給 subagent 實作。
> 第一部分是實作後的紀錄，第二部分是**核准時的原始計畫全文**（未修改）。

## Part 1 — 實作紀錄

### 需求

1. 修正 bug：只要開著 Claude Desktop，Clawd 就一直敲鍵盤，不做其他動作。
2. 新增動作與更豐富的互動邏輯，先從其他開源專案找參考，並多用 subagent 從不同方向搜尋、討論。

### 研究（3 個平行 subagent）

- **Bug 診斷：** 原因有三層。I/O 啟發式量錯了程序；FSM 每幀強制打字、沒有上限；transcript／hook 卡在 busy。詳見 Part 2 的 Context。
- **架構盤點：** 狀態機、sprite 產生器、主迴圈、測試模式，以及新增一個動作的完整步驟。
- **開源專案調查：** Shimeji-ee、eSheep、oneko、Microsoft Agent、VPet、clawd-on-desk、claude-pet、claude-code-tamagotchi、DyberPet、Desktop Goose 等。整理成 15 個動作點子和 3 項架構建議，見 [ACKNOWLEDGEMENTS.md](../ACKNOWLEDGEMENTS.md)。

### 使用者的決定

| 問題 | 選擇 |
| :--- | :--- |
| 本輪納入的新動作 | Claude 活動感知的工作動作、分層閒置＋位置感知動作、登場／送信／傾聽（**不做**心情／體力數值） |
| I/O 思考偵測 | 強化偵測，並限制打字時長 |
| 閒置動作挑選邏輯 | 外部 JSON／TOML 設定檔 → 實作時選 JSON（理由見 Part 2 的 Phase 3） |

### Commit 對照

| Phase | Commit | 內容 |
| :--- | :--- | :--- |
| 1 | `251d52a` | 只量 Claude 視窗的程序樹；逐 process 計算 delta；PID 快取以 `(pid, create_time)` 為 key；transcript 的中斷、meta、slash、compact、WAITING；以 session 為單位的 AgentActivity |
| — | `8063552`（merge `9e24f1f`） | 23 個 M6 sprites（在獨立 worktree 與 Phase 1 平行製作） |
| 2 | `2a8c539` | WorkSession：打字分段、休息、60 秒上限與 90 秒冷卻、拿掉每幀重設；打字中戳 Clawd 會抬頭看 |
| 3 | `3fb6960`、`865d08c` | 先 commit golden FSM traces，再加入 `behaviors.json`、`BehaviorSelector`、`paths.py`、系統匣重新載入／開啟設定資料夾，行為不變 |
| 4 | `f8b0600` | 工作中休息、拋接球、蛻殼、「!」升級、錯誤升級、hooks 更新偵測 |
| 5 | `c14163a` | L1 小動作、L2 閒置動作、晃腳、往下看、抓牆、敲視窗側邊 |
| 6 | `04930c4` | 登場、道別、送信、傾聽 |
| 7 | `20ec2cb` | README、`docs/clawdlet.md`、版本升到 0.3.0 |

### 和原計畫不同的地方

- **Phase 1：** 錄製 trace 的重播測試改用 `TrackerConfig()` 的門檻，並把判定從 >50% 提高到 >60%。視窗掃描時每個 PID 只查詢一次。
- **Phase 2：** 打字跟著 session 狀態走，不只看 START 那一刻；收筆電動畫不再被打斷。`note_done` 對 transcript 來源的 DONE 也生效。
- **Phase 3：**
  - `PetState` 移到 `states.py`。
  - 窄標題列（寬度不到 1.4 倍身寬）的情況保留在程式裡，不走行為表。以 300 px 的最小視窗寬度來說，這種情況實際上不會發生。
  - 沒有寫 `version` 的使用者設定檔視為 version 1。
- **Phase 4：**
  - 蛻殼在動畫結束時才生成殼，避免和 sprite 裡畫的殼重複。
  - INTERRUPTED 不會重置錯誤計數。
  - 摸摸沮喪中的 Clawd 會安慰牠。
- **Phase 5：**
  - L2 權重改成 0.15／0.12／0.10（舊程式的機率單位），保持 30:25:20 的比例。
  - 兩種抓牆不受安靜模式延長。
  - 120 條 golden traces 中有 62 條改變，都是新動作被觸發造成的。逐項證明另存成 `golden_traces_pre_phase5.json` 與對應測試。
- **Phase 6：**
  - 送信的 90 秒逾時從 Clawd 拿著信出發時才開始算。
  - 送信時用追游標的 1.4 倍速度。
  - `--spawn` 的預設改成隨機登場。

### 驗證狀態

- Linux 容器（pywin32 stub）：664 passed、1 skipped，4 個測試需要 Windows。
- 尚待 Windows 實機驗證的項目見 [windows-test-checklist.md](../windows-test-checklist.md)。

---

## Part 2 — 核准時的原始計畫

### Clawdlet M6：修正「一直打字」＋ 新動作與更豐富的互動

#### Context

**Bug：** 只要開著 Claude Desktop，Clawd 就一直打字，其他動作都不做。三個 subagent 交叉追查後確認原因有三層：

1. **I/O 啟發式誤判**（主因，`src/clawdlet/window_tracker.py` 的 `poll_thinking_status`，約 L240-294）
   - 會把所有 `claude.exe` 的 I/O 加總，連 `--type=utility`（network、storage、node/MCP 服務）也算進去，原生 CLI `%USERPROFILE%\.local\bin\claude.exe` 也會被算到（因為路徑裡沒有 "claude-code"）。
   - 各 process 的 I/O 合成一個總和，只要 process 集合有變動，就會算出很大的假 delta。
   - `_query_process_info` 的 PID 快取永遠不會失效：cmdline 讀取被拒（AccessDenied）時會快取成空值，結果 renderer／GPU process 能繞過過濾；PID 被重用時也還是拿舊的快取。
   - 門檻只有 3 KB/s：雜訊 30% 時約 53% 時間判為思考，雜訊 50% 時約 90%。
2. **FSM 放大效應**（`src/clawdlet/behavior.py`，約 L1159、L1294）
   - 只要有一個 tick 判為 thinking，就強制進 TYPING，而且每一幀都把計時器歸零。
   - 打字沒有時間上限、也沒有冷卻；打字時睡覺、poke、pet 都被擋住。
   - 收到 DONE 後慶祝 1.6 秒，又立刻回去打字。
3. **Transcript／hook 卡在 busy**
   - `transcript_watch.classify_record` 把任何 `user` 紀錄都當成 GENERATING，包括中斷、isMeta、slash 指令、compact 摘要。
   - 每 60 秒重送 BUSY，加上 180 秒的 expiry，最長會卡約 6 分鐘。
   - 使用者中斷時 Stop hook 不會觸發；權限提示期間也仍算 busy。

**新功能**（使用者選定的範圍，參考 Shimeji-ee、MS Agent、oneko、eSheep、VPet、clawd-on-desk、Desktop Goose）：

- Claude 活動感知的工作動作
- 分層閒置＋位置感知動作
- 登場／送信／傾聽
- 閒置動作改由**外部 JSON 行為設定檔**決定（Shimeji behaviors.xml 風格）

不做心情／體力數值。

**貫穿所有階段的原則：** 新的隨機抽樣一律不用 `self._rng`，改用新的 `_work_rng`、`_behavior_rng`、`_fidget_rng`（各用 `rng_seed` 加上偏移量）。每次休息決策呼叫 `_rng` 的次數維持不變，這樣 seed 3/5/11/42 的既有測試才不會漂移。

---

#### Phase 1 — 強化偵測、修正 agent 事件語意（bug A）

**`window_tracker.py`**

- **只量 Claude 視窗那棵 process 樹：** `poll_thinking_status(now, main_pid)` 的 `main_pid` 由鎖定視窗的 `GetWindowThreadProcessId` 取得。只計算主程序，加上 cmdline 帶 `--type=utility --utility-sub-type=network.mojom.NetworkService` 的直接子程序。
  - 讀不到 cmdline 的直接跳過（fail closed）。
  - 這樣原生 CLI、node/MCP、renderer、GPU 自然被排除。
  - 每 5 秒或 `main_pid` 改變時重建這份清單。
- **逐 process 計算 delta：** 以 `(pid, create_time)` 當 key。
  - 第一次取樣只記基準值，不計入。
  - 出現負值就重設基準。
  - 消失的 key 直接刪掉。
- **`_query_process_info` 快取：** key 改成 `(pid, create_time)`。失敗結果只快取 10 秒；掃描時沒看到的 entry 就清掉。
- `ThinkingSample` 增加 `main_pid`、`procs`（每個 process 的 role 與 rate）、`skipped`。
- 順便新增：
  - `WindowSnapshot.foreground`（判斷 `GetForegroundWindow() == locked_hwnd`）
  - `focus_window(hwnd)`（restore 後 `SetForegroundWindow`）

  兩者都是給 Phase 6 用的。

**`config.py` 的 `TrackerConfig`**

- `thinking_io_threshold` 從 3072 調到 **8192**。用既有錄製的 trace 重播：工具等待那段不會觸發，串流期間仍有 62% 判為 active，所以 8 KB/s 是上限。
- 新增 `thinking_utility_subtypes`、`thinking_tree_refresh_s`。

**`transcript_watch.py`**

- `isMeta` → 回傳 None。
- `isCompactSummary`、`<command-name>`、`<local-command-stdout>`、`<local-command-caveat>`、`system/stop_hook_summary` → 視為 IDLE。
- `[Request interrupted by user` → 新的 `TurnPhase.INTERRUPTED`。
- `system/compact_boundary` → IDLE，並發出 COMPACT（Phase 4 會用到）。
- `attachment`、`queue-operation`、`atis-latch`、`last-prompt` → 回傳 None。
- 在 TOOL 階段、檔案 25 秒沒變動時，只發一次 `WAITING`（很可能在等權限提示），不算 busy。
- `BUSY_FRESH_S` 從 180 改成 90。

**`agent_events.py`**

- `AgentEvent` 新增 `WAITING`、`INTERRUPTED`、`SUBAGENT_START`、`SUBAGENT_STOP`、`COMPACT`。
- `AgentActivity` 改成以 session 為單位記錄狀態：`busy` 或 `waiting`、最後時間、subagent 數、來源。
  - ATTENTION／WAITING → waiting，不打字。
  - DONE／FAILED／END／INTERRUPTED → 移除該 session。
  - 新增 `describe()` 供 debug 輸出。
- `map_hook_payload` 讀取 `notification_type`（是 enum，不含敏感內容）：`idle_prompt` 和 `auth_success` 忽略，其餘仍是 ATTENTION。

**`behavior.py`**：hook session 也接受 transcript 來的 INTERRUPTED（因為 Stop hook 不會因中斷而觸發），處理方式是收起筆電、不慶祝。

**`app.py` 的 `--debug`**：改成兩行輸出：

```
io main=…KB/s net=…(first) total=… active=n/3 thinking=…
agent busy=[sid8 src age] waiting=[…] | work=…
```

`tools/dump_windows.py` 也要印出每個 process 被判成什麼 role。

#### Phase 2 — 工作階段 WorkSession（bug B，和 Phase 1 一起完整修好 bug）

**新增 `src/clawdlet/work_session.py`**（純邏輯，不碰 Tk/Win32）：

- `WorkSignal(heuristic, agent_busy, subagents, manual)`：`agent_busy` 或 `manual` 才算「強訊號」。
- `WorkDirective`：`START`／`BREAK`／`RESUME`／`END`／`CAP`。
- `WorkConfig`：
  - 每段打字時長依模式：quiet 20–40 秒、normal 14–28 秒、hyper 8–16 秒。
  - 休息 2.5–4.5 秒。
  - 只有 I/O 啟發式訊號時，最多連續打字 60 秒，之後冷卻 90 秒。
  - hook 送來 DONE 後 8 秒內忽略 I/O 尾巴。

**`behavior.py`**

- 拿掉「每幀強制 TYPING」的兩處（perch 分支與 floor 分支），改呼叫 `_step_work(win)`，只在入口狀態收到 START 時才開始打字。
- 狀態集合拆成：
  - `_WORK_ENTRY_STATES`：原本的 `_BUSY_INTERRUPTIBLE` 扣掉工作類狀態。
  - `_WORK_STATES`：TYPING＋各種休息狀態。
- RESUME 時用新的 `typing_loop` render override，避免重播 11 幀的開場。
- `_should_sleep` 改用 `work.effective()` 判斷，所以啟發式被 CAP 後不會再擋住睡覺。
- 打字中被 poke 時會短暫看一眼（`WORK_GLANCE`，0.75 秒）。flail／pet 照舊被擋。

**Sprites**：`typing_loop`（取 typing_laptop 的第 11–18 幀）、`work_glance`（4 幀）。

#### Phase 3 — 外部行為設定檔（純重構，行為不變）

**選 JSON 的理由：** stdlib 就能讀寫（tomllib 只能讀，而「開啟設定資料夾」要寫範本）；和 `manifest.json`、Claude 的 `settings.json` 一致；可以附 `behaviors.schema.json`，VS Code 就有自動補完。

**檔案**

- `assets/behaviors/behaviors.default.json`：預設表，隨程式附上。
- `assets/behaviors/behaviors.schema.json`：只給編輯器用。
- 使用者覆寫檔放在 `%APPDATA%\Clawdlet\behaviors.json`。
- 新增 `src/clawdlet/paths.py`，提供 `app_data_dir`、`assets_dir`（處理 `_MEIPASS`）、`default_behaviors_path`、`user_behaviors_path`。`agent_events.default_token_path` 和 `sprites.default_sprites_dir` 都改用它。
- `tools/build_exe.py`：抽出 `pyinstaller_command()` 方便測試，加上 `--add-data assets/behaviors`，預設表驗證失敗就讓 build 失敗。

**新增 `src/clawdlet/behavior_table.py`**

- **載入與驗證：**
  - id 不可重複；state 必須在白名單 `SELECTABLE_STATES` 內；`when` 只允許已知的 key；檢查 weight、chance、duration 的範圍，以及 `next` 參照是否存在。
  - 某一列無效就丟掉該列並印出警告，其餘照用。
  - 整個檔壞掉就退回內建預設表；內建預設表也壞掉就用 `LEGACY_TABLE`。
- **合併規則：** `extends: "default"` 時，同 id 的列做淺層 patch（例如 `{"id":"dig_sand","enabled":false}`），新 id 附加到後面；`extends: "none"` 則整份取代。
- **`BehaviorSelector.pick(trigger, ctx, u, chain_from)`** 分兩輪：
  1. 有 `chance` 的列（oneko 式機率觸發），抽 `_behavior_rng`。
  2. 依 weight 加權（Shimeji 的 Frequency），沿用既有的 roll 重新映射成 `u`。
  - `next` 支援 `next_mode` 為 add／replace（Shimeji 的 NextBehavior），鏈結最多 4 層。
- **`IdleContext`：** surface（floor/perch）、spots（open、screen_edge、window_side、perch_edge、perch_middle）、mode、idle_level（1–3）、user_idle_s、claude_visible、claude_foreground、cursor_in_window、prev_state。
- **列的欄位：** `id`、`state`、`weight`（數字，或 `{quiet,normal,hyper}`）、`chance`、`on`（rest／wall_bump／window_side）、`when.*`、`duration_s`、`scale_by_rest`、`cooldown_s`、`next`、`next_mode`、`enabled`、`description`。
- 引擎強制的狀態（拖曳、掉落、反應、工作、送信、登場）不放進表裡，相當於 Shimeji 的 frequency 0。
- 設定檔另有 `settings` 區塊，放 `idle_level2_after_s`、`fidget_interval_s`、`mail_away_s`、`alert_escalate_s`、`work.*`；還有 `fidgets`、`work_breaks`、`entrances` 三個子表。

**整合到 `behavior.py`**

- `_transition_from_floor_rest`、`_transition_from_perch` 前段的引擎優先分支完全保留：手動打字、慶祝、peek、推視窗、追游標、找 Claude、閒逛。
- 只把最後 BLOB/IDLE 那段換成：
  ```python
  u = (roll - lo) / (1 - lo)
  row = selector.pick("rest", ctx, u, chain_from)
  _start_row(row)
  ```
  `_start_row` 只呼叫一次 `uniform()`。
- 預設表的權重設定成和現在的機率等價：地面完全相同；窄於 1.4 倍身寬的視窗上會有極小差異，會在文件中註明。
- controller 預設只載入內建表，避免測試讀到開發者的 APPDATA；app 啟動時才合併使用者檔。

**Tray 與 CLI**

- Tray 新增「重新載入行為設定 (Reload Behaviors)」、「開啟設定資料夾 (Open Config Folder)」（沒有檔案時先寫一份 `extends: default` 的範本）。
- CLI 新增 `--behaviors PATH`、`--dump-behaviors`。

#### Phase 4 — Claude 活動感知的工作動作

**Hooks**

- `HOOK_EVENT_MAP` 新增：`SubagentStart` → `SUBAGENT_START`（同時算 busy）、`SubagentStop` → `SUBAGENT_STOP`、`PreCompact` → `COMPACT`。
- `DEFAULT_HOOK_EVENTS` 加入 `SubagentStop`、`PreCompact`（不設 matcher）。
- `AgentActivity` 記錄每個 session 的 subagent 數。
- `hook_install.py` 新增 `hook_install_state()`，回傳 none／outdated／current。Tray 在 outdated 時顯示「更新 Claude Code hooks (Update Hooks)」。

**新狀態與 sprite**（全部和 typing 對齊，切換時不會跳動）

| 狀態 | sprite（幀數 @fps） | 觸發 |
|---|---|---|
| WORK_STRETCH / WORK_THINK / WORK_SIP | `work_stretch` 6@4、`work_think` 6@3（顯示 "…"）、`work_sip` 8@4（馬克杯冒蒸氣） | 每段打字結束時，從 `work_breaks` 表中抽一個 |
| JUGGLE | `juggle` 8@8，三顆球 | subagent 數 > 0 時，和 TYPING 交替 |
| MOLT ＋ 殼道具 | `molt` 8@5（1.6 秒）、`molt_shell` 6@2（停留 4 秒後淡出） | PreCompact（transcript 來源則看 compact_boundary） |
| ALERT 升級 | `alert_wave` 6@6；perch 時用 `alert_tap` 4@6 | `!` 超過 10 秒沒人理，就以 2 秒揮手、4 秒 `!` 輪流（MS Agent 的 GetAttention→Continued→Return）。狀態維持 ALERT |
| CONFUSED / SAD | `confused` 6@4（顯示 `?`）、`sad` 6@3 | 90 秒內的錯誤連鎖：第 1 次 CONFUSED → 第 2 次 ERROR_SWEAT → 第 3 次起 SAD；FAILED 直接 SAD；DONE 歸零 |

殼道具會讓 `overlay.py` 抽出 `_sync_prop_window(slot, state)`，並 lazy 建立第二個 Toplevel；`RenderFrameState` 新增 `shell` 欄位。

#### Phase 5 — 分層閒置＋位置感知動作

**L1（只改外觀）：** `_fidget_frame()` 從 `fidgets` 表播放既有 `gaze` 條帶上的姿勢：左右張望、眨眼、看向 Claude 視窗。只用 `_fidget_rng`，所以 FSM 測試完全不受影響。

**L2（使用者閒置 45 秒後）與位置動作：** 都是表裡的列。

| 狀態 | sprite | 條件 |
|---|---|---|
| READ_SCROLL | 8@3，`loop_start` 3（VPet 的 A_Start→B_Loop） | 地面或 perch，L2 |
| POLISH_CLAW | 6@4 | 地面或 perch，L2 |
| DIG_SAND | 6@6 | 地面，L2 |
| PERCH_DANGLE | 6@3，往下偏移 8 sprite px | perch，spot 為 perch_edge（會貼到邊緣、面朝外） |
| PERCH_PEER | 6@3，往下偏移 10 px | perch，cursor_in_window |
| WALL_SCRATCH | 6@8 | `on: wall_bump`（撞到螢幕邊、沒爬上去時），或 `window_side`（抵達 Claude 視窗側邊，抓完再爬） |

- 這些狀態都加進 `_REST_STATES`、工作入口狀態、`_PERCH_TICK_STATES`，以及地面休息分支，所以照樣能接著睡覺、pounce、打字。
- 新增 `_RENDER_DY_SPRITE_PX`：只偏移繪製位置，物理身體仍在標題列上。
- 安全網測試：每個非強制狀態在地面和 perch 各 tick 一次，斷言都不會掉進 `tick()` 結尾那個 `else: IDLE`。

#### Phase 6 — 登場／道別／送信／傾聽

- **登場**（eSheep spawn 表，對應 `entrances`）：
  - `drop_on_claude`、`drop_top`（從工作區頂端掉下）、`walk_in_side`（新狀態 ENTER_WALK，在物理之前推進，避免被牆面 clamp）。
  - 多螢幕時，用 virtual screen 判斷，避開和其他螢幕相鄰的那一側。
  - 啟動時，以及「召喚到中央」時抽一種。
- **道別：** Tray 的 Exit 先播 GOODBYE（`wave_goodbye` 6@5），最多等 2 秒；SIGINT 和 `--duration` 仍立即結束。
- **送信**（Desktop Goose 風格）：
  - DONE 時如果使用者已離開 ≥ `mail_away_s`（30 秒），先不慶祝，改存一封信。
  - 使用者回來後：MAIL_CARRY（`walk_mail`）走到游標附近 → MAIL_WAIT（`hold_mail`）。
  - 點擊後：MAIL_GIVE（`give_mail`）→ 呼叫 `focus_window` 把 Claude Desktop 拉到前景 → CELEBRATE。
  - 90 秒逾時或同一 session 又開始 busy，就丟掉這封信。
- **傾聽（保護隱私、不讀按鍵）：**
  - 利用 `pointer.idle_s` 的重置脈衝來判斷：重置了但游標沒動，代表有輸入。
  - 2 秒內 ≥ 3 次、且 Claude 是前景視窗、沒在工作也沒有信件時，進入 LISTEN（`listen_tilt`，朝視窗歪頭）。
  - 不用 `GetAsyncKeyState`／`WH_KEYBOARD_LL`：太像 keylogger，可能被防毒軟體標記。
- **開關：** CLI 的 `--no-mail`、`--no-listen`；Tray 的「離開時送信 (Mail When Away)」、「打字時聆聽 (Listen While Typing)」。

#### Phase 7 — 文件（隨每個 phase 一起更新）

- `README.md`（雙語）：
  - §5 加入新動作。
  - §6 hook 表加入 Subagent→拋球、PreCompact→蛻殼、警示升級、錯誤升級。
  - 更新隱私說明（現在會讀 `notification_type` 與結構標記）。
  - 說明「更新 hooks」。
  - 新增「🧩 Behavior Config | 行為設定檔」一節。
  - 補上新的 CLI flags。
- `docs/clawdlet.md`：§3.1 加入新狀態與 WorkSession 設計，§7 加入 Milestone 6。

#### 要動到的檔案

- **修改：** `src/clawdlet/behavior.py`、`window_tracker.py`、`transcript_watch.py`、`agent_events.py`、`hook_install.py`、`app.py`、`tray.py`、`overlay.py`、`config.py`、`sprites.py`、`tools/gen_sprites.py`、`tools/build_exe.py`、`tools/dump_windows.py`、`tools/send_hook_event.py`（加 `--notification-type`）。
- **新增：** `src/clawdlet/work_session.py`、`behavior_table.py`、`paths.py`、`assets/behaviors/behaviors.default.json`、`behaviors.schema.json`、`tests/conftest.py`（session 層級共用的 sprite fixture）。
- **重用既有：** `_draw_canonical_clawd`、`_draw_side_laptop_32`、`_draw_heart`、`_draw_z`、`_draw_bang`、`_make_strip`（`gen_sprites.py`）；`_can_react`、`_return_to_rest`、`_advance_reaction` 的 timed dict、chase steppers、`drop_y_above_window`（`behavior.py`）；`_fx_rng` 的模式；hook install 本身已是 idempotent（會先移除自己的 entry 再加）。

#### Verification

1. **每個 phase 都跑 `python -m pytest -v`。** 各 phase 的重點測試：
   - **P1：**
     - 新 process 帶 10 MB 既有 I/O 出現時，rate 仍為 0。
     - 第一次取樣不計入。
     - PID 重用時快取失效。
     - 原生 CLI、node utility、讀不到 cmdline 的子程序都被排除。
     - 30% 雜訊（4–7 KB/s）永遠不會判為思考。
     - 既有的錄製 trace 重播在 8 KB/s 仍通過。
     - transcript 的 isMeta、slash 指令、compact、中斷、`stop_hook_summary`、2.1.310 紀錄類型，以及 25 秒 WAITING。
     - ATTENTION 不算 busy；`idle_prompt` 會被忽略。
   - **P2：**
     - 只有 I/O 啟發式持續 150 秒：約 60 秒時出現一次 CAP，之後 90 秒沒有 TYPING。
     - hook busy 持續 120 秒：連續 TYPING 不超過一段打字的時長，中間有休息。
     - `_state_timer` 會持續累加，不再被每幀歸零。
     - `test_reactions.py`：打字時 poke 改為 True，會進 WORK_GLANCE。
   - **P3：**
     - 重構前先 commit golden trace：seed {3,5,11,42} × 3 種模式 × 地面／perch 各 120 秒的狀態序列 hash，重構後必須完全相同。
     - 預設表 0 警告。
     - 使用者覆寫各情境：patch、停用、新增、`extends:none`、壞 JSON、未知 state／key、負 weight。
     - `chance` 列不會動到 `ctrl._rng.getstate()`。
     - `pyinstaller_command()` 有帶 behaviors 資料夾。
   - **P4–P6：** 拋球、蛻殼與殼道具、alert 在 9.9 秒和 10.5 秒時的動畫名稱、錯誤 1→2→3 的連鎖、hook 的 outdated 偵測、登場分布、walk-in 的 x 範圍、道別最多 2 秒、送信流程（含 focuser 被呼叫）、LISTEN 的進入與排除條件。
   - **全域：** 每個 `animation_name` 和 render override 都必須存在於 manifest（防止 `SpriteSheetBank` 靜默退回 idle_breathe）。
   - **需要更新的既有測試：** `test_tool_failure_sweats_then_resumes_typing`（第一次錯誤改成 CONFUSED）、`test_hook_install.py` 的 PreCompact 斷言、未知事件測試改用 `SessionStart`。
2. `python -m clawdlet.tools.gen_sprites` 重新產生 sprites 並 commit `assets/sprites`，在 1× 和 3× 下目視檢查新條帶。
3. `python -m clawdlet.tools.build_exe`，接著 `dist/clawdlet.exe --dump-behaviors`，應為 0 警告。
4. **Windows 上跑 `clawdlet --debug`（需要使用者在本機協助）：**
   - Desktop 閒置 2 分鐘：io < 1 KB/s，不打字。
   - Desktop 對話串流中：會打字，結束約 2 秒內停下。
   - 重開 Claude Desktop：不誤判，log 出現 `(first)` 標記。
   - 終端機用原生 CLI 跑長任務：io 那行不會出現它。
   - Claude Code 回合：每 14–28 秒休息一次；按 Esc 中斷後約 1 秒收筆電。
5. **`python -m clawdlet.tools.send_hook_event --session demo` 腳本測試：**
   - `UserPromptSubmit` → `SubagentStart` ×2（拋球）→ `SubagentStop` ×2 → `Stop`（慶祝）
   - `PreCompact`（蛻殼）
   - `Notification` 後等 11 秒（揮手升級）
   - `PostToolUseFailure` ×3
   - `Notification --notification-type idle_prompt`（沒有反應）
   - 離開 35 秒後送 `Stop`，回來時看到信件，點擊後 Claude 跑到前景。

#### 風險

- **process 樹的假設：** 視窗擁有者可能不是 Electron 的 browser process，或 MSIX 打包會拒絕讀子程序的 cmdline。兩種情況都 fail closed（Desktop 不會觸發思考）。Phase 1 一開始先用 `dump_windows` 確認；必要時改用備案：exe 路徑和視窗擁有者相同，且是 network 子類型的 utility。
- **錄製 trace 可能需要重錄：** 原本的 trace 含 node 服務，排除後串流訊號可能變弱，出貨前要重錄。
- **版本相依：** transcript 結構標記和 `notification_type` 都是內部格式，認不得的一律退回現行行為。
- **PERCH_DANGLE／PEER 的繪製偏移會蓋到 Claude 內容區：** color-key 的像素可以穿透點擊，但需要實測。
- **舊安裝的 hooks：** 使用者重新安裝前，Tray 會一直顯示「更新」。
