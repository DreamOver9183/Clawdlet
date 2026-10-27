<p align="center"><a href="claude-code-hooks.md">English</a> | <b>繁體中文</b></p>

# Claude Code Hooks

Clawdlet 在 `127.0.0.1:47821` 接收 Claude Code 的 [`http` hooks](https://code.claude.com/docs/en/hooks)，依你的 agent 正在做的事做出反應。終端機與 Claude Desktop 的 Code 分頁都適用。

- [Hook 事件](#hook-事件)
- [三種連動方式](#三種連動方式)
- [更新 hooks](#更新-hooks)
- [hooks 與 transcript 同時存在時](#hooks-與-transcript-同時存在時)
- [隱私與安全](#隱私與安全)

---

## Hook 事件

| Hook 事件 | Clawdlet 的反應 |
| :--- | :--- |
| `UserPromptSubmit` / `PreToolUse` / `PostToolUse` | 掏筆電分段打字，段落間伸懶腰／托腮思考／喝咖啡 |
| `SubagentStart` / `SubagentStop` | 有 subagent 在跑時，Clawd 在筆電旁**拋球**，和打字輪流（各 6–10 秒）；最後一個結束就回去打字 |
| `PreCompact` | **蛻殼**：脫下舊殼走出來，空殼留在原地幾秒後淡出（沒裝 hooks 時看 transcript 的 `compact_boundary`） |
| `Stop` | 收起或踢飛筆電，然後**慶祝**；你不在時改成回來後[送信](features.zh-TW.md#離開時送信) |
| `PostToolUseFailure` | 90 秒內連續失敗會升級：**困惑**「?」→ 緊張**冒汗** → **沮喪**；回合結束就歸零 |
| `StopFailure` | 直接**沮喪**，不慶祝 |
| `PreToolUse`（`ExitPlanMode` / `AskUserQuestion`） | [通知](features.zh-TW.md#需要我時通知)牌子：**計畫卷軸**／**「?」** |
| `Notification` `permission_prompt` / `elicitation_dialog` | **鑰匙**／**「?」** 牌子；沒人理就拿到游標旁 |
| `Notification`（其他） | 頭上跳金色 **「!」**，直到你回應或點 Clawd；10 秒還沒人理就**揮動雙鉗**（坐在標題列上時改成敲標題列），每 6 秒揮 2 秒 |

揮手前的等待時間 `alert_escalate_s` 與拋球／打字輪替長度 `work.juggle_segment_s` 可在[行為設定檔](behavior-config.zh-TW.md)的 `settings` 調整。

## 三種連動方式

### 1. 零設定（預設）

Clawdlet 會監看最近活動的 Claude Code session transcript（`~/.claude/projects/*/*.jsonl`，或 `$CLAUDE_CONFIG_DIR`），用打字與慶祝做出反應。

- 只讀每個檔案結尾附近紀錄的結構：紀錄的 type／subtype（`api-request`、`tool_use`、`end_turn`、`compact_boundary`…）、`isMeta` 等旗標，以及使用者紀錄是否**以** Claude Code 自己的標記**開頭**（`<command-name>`、`[Request interrupted by user`…）。從不讀取對話內容，也不保存或記錄。
- slash 指令不算工作；按 Esc 中斷會收起筆電但不慶祝；工具呼叫 25 秒沒動靜（多半在等權限確認）就停止打字。
- 為了通知，也會讀等待中 `tool_use` 的**工具名稱**（不讀輸入內容）來辨認 plan 審核與發問，並讀頂層的 `entrypoint` 分辨 Claude Desktop 與終端機的 session。工具授權只能推測（8 秒的「…」提示）。
- 無法顯示「!」提醒；transcript 格式是 Claude Code 的內部格式，因此只能盡力而為。
- 用 `--no-transcript-watch` 關閉。

### 2. 一鍵安裝（推薦）

系統匣選單 →「**安裝 Claude Code hooks**」（會先確認），或：

```powershell
clawdlet --install-hooks
```

Clawdlet 會先把 `~/.claude/settings.json` 備份成 `settings.json.clawdlet-bak-<timestamp>`，只加入指向 `127.0.0.1` 的自家 `http` 項目，其他設定原封不動。要移除時，從同一個系統匣項目或用 `clawdlet --uninstall-hooks`。

### 3. 手動

系統匣選單 →「**複製 Claude Code hook 設定**」，或執行 `clawdlet --print-hook-config`，再自行把 `hooks` 區塊合併到 `~/.claude/settings.json`。

## 更新 hooks

新版 Clawdlet 會聽更多 hook 事件（為了拋球與蛻殼加入了 `SubagentStop` 與 `PreCompact`）。如果你的 `settings.json` 裡是舊版 Clawdlet 裝的 hooks（或 port、token 不同），系統匣會顯示「**更新 Claude Code hooks**」取代安裝項目。點下去（或再跑一次 `clawdlet --install-hooks`）會先備份，再就地替換 Clawdlet 自己的項目，其他 hooks（包括你自己的 `PreCompact` hooks）都保留。

## hooks 與 transcript 同時存在時

hooks 與 transcript 監看看到同一個 session 時，以 hooks 為準，所以同一個回合不會慶祝兩次。例外：

- Esc 中斷不會觸發 hook，一律以 transcript 為準。
- transcript 看到的 plan 審核／發問仍會顯示，以防沒有 hook 通知。
- Claude Desktop session 保留 transcript 的「…」提示，因為假設 Desktop 不送 `permission_prompt` 通知（尚未在實機確認）。

## 隱私與安全

hook 接收器：

- 只綁定 localhost；
- 需要 bearer token（存在 `%APPDATA%\Clawdlet\hook_token`）；
- 只讀 `hook_event_name`、`session_id`、`notification_type` 列舉值與 `tool_name`。因此 `idle_prompt`／`auth_success` 通知不會跳「!」、等待權限確認時 Clawd 會停止打字，`ExitPlanMode`／`AskUserQuestion` 也有各自的牌子。`tool_name` 只從 64 KB 以內的內容讀取，從不讀 `tool_input`；
- 從不記錄 payload；
- 一律回傳空的 `200`，所以永遠無法核准、拒絕或阻擋任何動作。

產生的設定刻意不包含 `PermissionRequest`。Clawdlet 沒在執行時，Claude Code 只會回報一個不影響運作的 hook 連線錯誤。

不透過 Claude Code 測試接收器的方法，見[開發](development.zh-TW.md#開發工具)。

---

[← 回到 README](../README.zh-TW.md)
