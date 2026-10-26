# Acknowledgements | 銘謝

Clawdlet 的行為設計從許多開源與經典桌寵專案得到啟發。感謝這些專案的作者與社群。

> **說明：** 以下只參考了公開的設計概念（行為種類、觸發條件、狀態結構）。Clawdlet 沒有複製這些專案的程式碼、設定檔或美術素材。所有 sprite 都由 `src/clawdlet/tools/gen_sprites.py` 以程式繪製。各專案的著作權與授權仍屬於原作者。

## 直接影響 M6 設計的專案

| 專案 | 參考了什麼 | 在 Clawdlet 中的對應 |
| :--- | :--- | :--- |
| [Shimeji-ee](https://github.com/chaoskagami/shimeji-ee)（另見 [Shimeji-Desktop](https://github.com/DalekCraft2/Shimeji-Desktop)、[Kilkakon 的 affordances 教學](https://kilkakon.com/shimeji/affordances.php)） | `behaviors.xml` 的 `Frequency` 權重、`Condition` 條件、`NextBehavior`（`Add=true/false`）鏈結；引擎強制動作設為 frequency 0；坐在視窗邊緣晃腳、抓牆 | 外部行為設定檔 `behaviors.json`：`weight`、`when`、`next`＋`next_mode`；PERCH_DANGLE、WALL_SCRATCH |
| [Microsoft Agent](https://learn.microsoft.com/en-us/windows/win32/lwef/agent-states)（[Genie 動畫列表](https://learn.microsoft.com/en-us/windows/win32/lwef/microsoft-agent-animations-for-genie-character)） | IdlingLevel 1–3 的閒置分層；GetAttention → GetAttentionContinued → GetAttentionReturn；Confused／Sad 等情緒動畫 | 閒置分層 L1／L2／L3；「!」提醒升級為揮手／敲視窗；錯誤升級：困惑 → 冒汗 → 沮喪 |
| [oneko.js](https://github.com/adryd325/oneko.js)（[Neko](https://en.wikipedia.org/wiki/Neko_(software))） | 每 tick 低機率觸發的閒置小動作；靠近牆邊時抓牆 | 行為表的 `chance` 機會觸發列；`on: wall_bump` 的抓牆 |
| [eSheep / desktopPet](https://github.com/Adrianotiger/desktopPet) | 依權重抽登場方式；用 `only="taskbar\|window"` 依所在表面篩選動作 | `entrances` 登場表（從 Claude 視窗上掉下、從天而降、從側邊走進來）；`when.surface` |
| [VPet-Simulator](https://github.com/LorisYounger/VPet) | 動畫分成 A_Start → B_Loop → C_End 三段；工作狀態 | READ_SCROLL 的 `loop_start` 開場＋循環；打字分段工作的節奏 |
| [clawd-on-desk](https://github.com/rullerzhou-afk/clawd-on-desk) | Claude Code hook 事件和桌寵狀態的對應（subagent → juggling、PreCompact → sweeping）；彩蛋冷卻 | SubagentStart/Stop → 拋接球、PreCompact → 蛻殼；行為列的 `cooldown_s` |
| [Desktop Goose](https://samperson.itch.io/desktop-goose) | 把東西帶到游標旁邊給使用者 | 離開時任務完成，回來時 Clawd 送信，點信封把 Claude Desktop 叫到前景 |

## 研究時參考、但這次沒有直接採用的專案

- [claude-pet](https://github.com/xtrimsystems/claude-pet)：Shimeji 格式的 Claude Code 桌寵，壓縮對話時分身。
- [claude-code-tamagotchi](https://github.com/Ido-Levi/claude-code-tamagotchi)：依活動量衰減的需求值、分類加權的台詞。
- [DyberPet](https://github.com/ChaozhongLiu/DyberPet)：PySide6 桌寵，有飽食度、好感度與迷你寵物。
- [WindowPet](https://github.com/SeakMengs/WindowPet)：多寵物、點擊穿透。
- [Bongo Cat](https://store.steampowered.com/app/3419430/Bongo_Cat/)：跟著打字敲桌。
- [RunCat](https://github.com/Kyome22/RunCat_for_windows)：跑步速度跟著 CPU 負載。
- [Codex pets](https://penchan.co/en/ai/coding/codex-pets/)：AI 代理狀態對應的 sprite 狀態。

心情、體力、好感度這類數值系統（VPet、tamagotchi、DyberPet）留給之後的版本考慮。

---

Clawd 是 Anthropic 的吉祥物。Clawdlet 是非官方的同人專案，詳見 [README 的免責聲明](../README.md)。
