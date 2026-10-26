# Windows 實機測試清單 | Windows Test Checklist

M6（v0.3.0）的自動化測試在 Linux 容器裡用 pywin32 stub 跑過：664 個通過，4 個失敗。這 4 個需要真的 Win32 桌面、Tk 顯示或系統匣，只能在 Windows 上確認。

另外，有幾項行為和實際的 Claude Desktop、Claude Code 安裝方式有關，也只能在實機上驗證。本清單把兩者整理在一起，每次發版前照著跑一遍。

---

## 0. 準備

```powershell
pip install -e ".[dev]"
python -m clawdlet.tools.gen_sprites      # 確認 sprites 可重新產生（不需要 commit 重新編碼的舊 PNG）
```

- Windows 10/11，Claude Desktop 已安裝並登入。Store（MSIX）版和一般安裝版最好各測一次。
- 如果要測 hook 連動，Claude Code 也要裝好。

## 1. 只能在 Windows 上跑的自動化測試

```powershell
python -m pytest -v
```

全部都要通過，尤其是下面這 4 個（在 Linux 容器內會因為沒有顯示器或系統匣而失敗）：

| 測試 | 驗證內容 |
| :--- | :--- |
| `tests/test_sprites_and_overlay.py::test_overlay_and_in_process_win32_tracker` | Tk 透明 overlay 可以建立、繪製；在同一個 process 內追蹤真的 Win32 視窗 |
| `tests/test_sprites_and_overlay.py::test_app_short_run_lifecycle` | `main()` 能完整啟動、跑 0.12 秒後正常結束（rc == 0） |
| `tests/test_tray_and_m3.py::test_tray_dispatcher_and_click_through_exstyle` | 系統匣指令分派；點擊穿透 `WS_EX_TRANSPARENT` 的開關；「召喚到中央」改成隨機登場後，狀態應為 FALLING 或 ENTER_WALK |
| `tests/test_tray_and_m3.py::test_win32_tray_icon_thread_lifecycle` | 系統匣圖示的執行緒能建立 hwnd，並且能乾淨地停止 |

## 2. 「一直打字」修正（Phase 1–2）

以 `clawdlet --debug` 啟動。每 2 秒會印出 `io …` 和 `agent … | work=…` 兩行。

- [ ] **Desktop 閒置 2 分鐘：** `io` 的 `total` 低於 1 KB/s、`thinking=False`，Clawd 不打字，會正常閒逛、爬視窗、睡覺。
- [ ] **在 Desktop 裡對話、回覆串流中：** `thinking=True`，Clawd 打字。回覆結束後約 2 秒內停下。
  - 如果串流時完全不打字，代表新的程序判斷不適用於你的安裝方式。請執行 `clawdlet-dump-windows`，記下每個 `claude.exe` 的 role（`main`／`network`／`skipped:…`），並重新錄製 trace 給 `tests/test_tracker_runtime.py`。
- [ ] **關掉再重開 Claude Desktop：** 不會誤判成思考，`io` 那行出現 `(first)` 標記。
- [ ] **在終端機用原生 Claude Code CLI（`%USERPROFILE%\.local\bin\claude.exe`）跑長任務：** `io` 那行不會出現它的 PID。
- [ ] **只有 I/O 訊號、長時間偏高**（例如 Desktop 在下載大檔）：約 60 秒時出現一次 `last=cap`，接著冷卻 90 秒不打字。
- [ ] **Claude Code 回合（有 hooks）：** 每 14–28 秒休息一次（伸懶腰／托腮思考／喝咖啡／抬頭）；按 Esc 中斷後約 1 秒收起筆電。
- [ ] **打字中單擊 Clawd：** 抬頭看一眼（WORK_GLANCE），然後繼續打字，不會重播掏筆電的開場動畫。
- [ ] **Claude Code 等待權限確認時：** 不打字，頭上出現「!」。

## 3. Claude Code hooks 連動（Phase 4）

- [ ] **之前裝過舊版 hooks：** 系統匣顯示「更新 Claude Code hooks (Update Hooks)」。按下後 `~/.claude/settings.json` 新增 `SubagentStop`、`PreCompact`，使用者自己的 hooks 都保留，並且產生 `settings.json.clawdlet-bak-<timestamp>` 備份。

接著用 `python -m clawdlet.tools.send_hook_event <事件> --session demo` 逐一觸發：

- [ ] `UserPromptSubmit` → `SubagentStart` ×2：打字和拋接球交替。
- [ ] `SubagentStop` ×2 → `Stop`：回到打字，接著收筆電、慶祝。
- [ ] `PreCompact`：蛻殼（約 1.6 秒），舊殼留在原地約 4 秒後淡出；在標題列上蛻殼時，殼會跟著視窗移動。
- [ ] `Notification`，然後等 11 秒：「!」升級為跳起來揮手；perch 在標題列上時則是敲視窗。單擊 Clawd 就解除。
- [ ] `PostToolUseFailure` ×3：依序是困惑（?）、冒冷汗、沮喪。
- [ ] `Notification --notification-type idle_prompt`：沒有反應。

## 4. 分層閒置與位置動作（Phase 5）

- [ ] **游標放在 Clawd 附近不動：** 不時左右張望、眨眼、看向 Claude 視窗（L1）。
- [ ] **離開電腦超過 45 秒：** 出現看卷軸、磨鉗子、挖沙（L2）；超過 90 秒會打哈欠、睡覺（L3）。
- [ ] **坐在標題列兩端：** 有時晃腳，而且腳畫在標題列下方，身體仍坐在標題列上。
- [ ] **游標在 Claude 視窗內：** 有時趴在標題列上往下看。
  - 偏移畫出來的部分不能擋住 Claude 工具列的點擊；透明色像素應該會穿透。
- [ ] **閒逛時撞到螢幕邊、沒有往上爬：** 有時會抓牆。
- [ ] **走到 Claude 視窗側邊準備爬：** 有時先敲幾下視窗側邊再開始爬。

## 5. 登場、道別、送信、傾聽（Phase 6）

- [ ] **啟動（不帶 `--spawn`）：** 隨機出現，可能落在 Claude 視窗上、從螢幕頂端掉下，或從側邊走進來。
  - 從側邊走進來時 overlay 會短暫位在負 x 座標，這部分**尚未實測**。請確認 Tk 接受這個座標，而且畫面不閃爍。
  - 多螢幕時，不會從和另一台螢幕相鄰的那一側走進來。
- [ ] `--spawn above-claude`、`--spawn center` 照舊有效。
- [ ] **系統匣「召喚回畫面中央」：** 隨機登場，但不會落在 Claude 視窗上；「召喚到 Claude 視窗」一定落在 Claude 上。
- [ ] **系統匣「結束」：** 先揮手道別（最多 2 秒）再關閉；再按一次會立即關閉。Ctrl+C 和 `--duration` 一律立即結束。
- [ ] **送信：** 離開 35 秒（不動滑鼠鍵盤），送 `Stop`，再回來動一下滑鼠。
  - Clawd 拿著信封走到游標旁等你，點信封後會遞出信封、冒出愛心，Claude Desktop 被拉到前景，接著慶祝。
  - Windows 有時會拒絕「搶前景」，此時工作列按鈕會閃爍；記錄實際行為。
  - 90 秒沒點，信封就收回去。
- [ ] **傾聽：** Claude Desktop 在前景、滑鼠不動、持續打字時，Clawd 朝視窗歪頭；停止打字約 1.5 秒後恢復。
  - 確認不需要系統管理員權限，防毒軟體也不會警告（程式沒有讀取任何按鍵）。
- [ ] **系統匣切換：** 「離開時送信 (Mail When Away)」和「打字時聆聽 (Listen While Typing)」關閉後，對應功能停止；`--no-mail`、`--no-listen` 也一樣。

## 6. 行為設定檔（Phase 3）

- [ ] **系統匣「開啟設定資料夾」：** 在 `%APPDATA%\Clawdlet\` 建立 `behaviors.json` 範本和 `behaviors.schema.json`。用 VS Code 開啟時有自動補完。
- [ ] **故意寫一列錯誤的設定**（例如未知的 `state`）**後按「重新載入行為設定」：** console 印出警告，其餘設定照常生效。
- [ ] **整個檔案改成無效 JSON：** 印出 `… — using defaults`，Clawd 照常運作。
- [ ] `clawdlet --dump-behaviors`：輸出合法 JSON，沒有警告。

## 7. 打包

```powershell
python -m clawdlet.tools.build_exe
dist\clawdlet.exe --dump-behaviors
```

- [ ] build 時會驗證內建行為表，有警告就失敗。
- [ ] 打包後的 exe 能讀到 `assets/behaviors` 和所有新 sprites（沒有任何動作退回成 idle 呼吸動畫）。

---

測試結果、異常或錄到的 trace，請開 issue 或直接附在 PR。
