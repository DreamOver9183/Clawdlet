<p align="center"><a href="behavior-config.md">English</a> | <b>繁體中文</b></p>

# 行為設定檔

*v0.3.0 新增。* Clawd 閒晃時要做什麼，由一份 JSON 行為表決定（概念來自 Shimeji 的 `behaviors.xml`）。

- 內建的 [`assets/behaviors/behaviors.default.json`](../assets/behaviors/behaviors.default.json) 在你活動時與原本的閒置選擇完全相同，另外加上分層閒置與位置感知的列。
- 你的 **`%APPDATA%\Clawdlet\behaviors.json`** 會合併在它上面。
- 引擎強制的動作（拖曳、掉落、反應、打字、送信、找 Claude、閒逛）仍由程式決定，表格只決定之後的閒置動作，以及工作休息、小動作與登場方式。

## 範例

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

## 合併規則

- **`extends: "default"`**（預設）：相同 `id` 的列逐欄覆寫（巢狀的 `when`／`next`／`weight` 物件整個取代），新的 `id` 附加在後，`settings` 深層合併。
- **`extends: "none"`**：整份取代內建表。

## 行為列欄位

| 欄位 | 說明 |
| :--- | :--- |
| `state` | `IDLE`、`BLOB`、`PERCH_CLAUDE`、`READ_SCROLL`、`POLISH_CLAW`、`DIG_SAND`、`PERCH_DANGLE`、`PERCH_PEER`、`WALL_SCRATCH` |
| `on` | 觸發點：`rest`；抓牆類的列用 `wall_bump`／`window_side` |
| `weight` | 數字，或依模式分開的 `{"quiet", "normal", "hyper"}` |
| `chance` | 選填；在加權抽籤之前單獨擲骰 |
| `when` | 條件：`surface`、`spot`、`mode`、閒置等級、`user_idle_s`、`claude_visible`／`claude_foreground`、`cursor_in_window`、`prev_state`／`not_prev_state` |
| `duration_s` | 持續時間 |
| `scale_by_rest` | 依模式的休息倍率縮放持續時間 |
| `cooldown_s` | 再次被抽中前的最短間隔 |
| `next` + `next_mode` | 接續動作；`add`／`replace`，最多鏈 4 層 |
| `enabled` | 設為 `false` 就關掉這一列 |
| `description` | 自由說明文字 |

## 其他子表

- **`entrances`（登場）**：每列有 `kind`（`drop_on_claude`、`drop_top`、`walk_in_side`）、`weight` 與 `when`（例如 `claude_visible`）。`{ "id": "walk_in_side", "enabled": false }` 可讓 Clawd 不從側邊走進來。
- **`fidgets`（小動作）**：每隔 `settings.fidget_interval_s`，在休息中的 Clawd 身上播一段 `gaze` 條帶的姿勢。可用 `"poses": [[gaze_index, hold_s], ...]`（索引 `(dy+1)*3+(dx+1)`：3 左、4 前、5 右、7 下、9 眨眼），或 `"target": "claude_window"` 搭配 `hold_s` 看向 Claude 視窗。
- **`work_breaks`（工作休息）**：打字段落之間的休息（伸懶腰、托腮、喝咖啡、抬頭看）。

## settings

| 鍵 | 說明 |
| :--- | :--- |
| `idle_level2_after_s` | 多少秒沒操作進入 L2 閒置（預設 45） |
| `fidget_interval_s` | L1 小動作的間隔秒數 `[lo, hi]`（預設 `[4, 9]`） |
| `mail_away_s` | 回合結束時你要離開多久才改成送信（預設 30） |
| `alert_escalate_s` | 「!」多少秒沒人理就改成揮手，牌子也會拿到游標旁（預設 10） |
| `work.segment_s` | 各模式（`quiet`／`normal`／`hyper`）的打字段落長度 |
| `work.break_s` | 段落之間的休息長度（預設 `[2.5, 4.5]`） |
| `work.heuristic_max_s`／`work.heuristic_cooldown_s` | 只有 Desktop I/O 訊號時的打字上限與冷卻（預設 60／90） |
| `work.juggle_segment_s` | subagent 在跑時拋球／打字輪替長度（預設 `[6, 10]`） |

## 容錯

有問題的列會被丟掉並顯示列名警告，其餘照常載入。不是合法 JSON 或 `version` 不對的檔案會整份忽略，只顯示一則訊息（`[Clawdlet] behaviors.json: … — using defaults`）。

## 工具

- **系統匣 →「重新載入行為設定」**：不用重開就套用修改。
- **系統匣 →「開啟設定資料夾」**：沒有檔案時會建立空的 `behaviors.json` 範本（附一份 `behaviors.schema.json`），並開啟資料夾。
- **自動補完**：保留 `"$schema": "./behaviors.schema.json"`，VS Code 打字時就會提示所有鍵與值。
- **CLI**：`clawdlet --dump-behaviors` 印出合併後的表（Clawd 實際使用的版本）與警告；`clawdlet --behaviors PATH` 改用其他覆寫檔。

---

[← 回到 README](../README.zh-TW.md)
