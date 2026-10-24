# 專案概念書：Clawdlet
**`let clawd = new DesktopCompanion();` —— 非官方物理系 Claude Desktop 視窗伴侶**

---

## 1. 專案願景與核心定位 (Executive Summary)

### 1.1 專案背景
隨著 Anthropic 旗下 Claude 系列模型與桌面端工具的普及，社群吉祥物「Clawd」（陶土橘紅色的章魚／螃蟹狀生物）已成為開發者與重度使用者極具共鳴的視覺符號。然而，目前 GitHub 上的相關開源桌寵（如 `clawd-on-desk`、`Clyde`）皆聚焦於 **「CLI 終端機狀態監控器」**——即固定在螢幕角落、被動接收 Claude Code Hooks 並播放對應動畫的靜態看板。

### 1.2 核心價值主張
**`Clawdlet`** 旨在填補現有生態系的空白，打造一款 **「具備物理重力、視窗邊界感知（Window-Aware）且專屬於 Claude Desktop GUI 視窗」** 的動態桌寵（類似經典 Shimeji 架構）。它不只是一張會動貼圖，而是能察覺使用者正在使用 Claude Desktop，主動爬上視窗頂部陪伴、隨視窗拖曳而產生慣性晃動或掉落、並在閒置時於螢幕四處遊走的「活體桌面生物」。

### 1.3 品牌識別與命名定義 (Brand Identity)

本專案正式定名為 **`Clawdlet`**，兼具「小生物的呆萌感」與「工程師會心一笑的技術雙關」：

| 識別維度 | 設定內容 | 設計意涵與雙關亮點 |
| :--- | :--- | :--- |
| **正式名稱** | **`Clawdlet`** | 英文名詞字尾 `-let` 代表「微小可愛的事物」（如 Droplet 小水滴、Piglet 小豬、Starlet 小星星），賦予「迷你版小 Clawd」的直覺印象。 |
| **技術雙關 (Coding Pun)** | `Applet` / `let clawd` | 1. 致敬經典輕量桌面小工具 **Applet / Widget**。<br>2. 雙關 JavaScript / TypeScript / Rust 宣告變數的關鍵字 **`let clawd = ...`**。 |
| **GitHub Repo 代號** | `clawdlet` | 簡短、好記、全小寫無連字號，符合現代開源工具命名慣例（執行檔可直接命名為 `clawdlet.exe`）。 |
| **專案 Slogan（英文）** | *`let clawd` roam on your Claude Desktop.* | 將程式語法 `let clawd` 自然融入英文祈使句「讓 Clawd 在你的桌面上漫遊」。 |
| **專案 Slogan（程式碼風）** | `let clawd = new WindowCompanion({ gravity: true });` | 適合放在 GitHub `README.md` 頂部 Banner，一秒傳達「具備物理重力與視窗陪伴」的核心特性。 |

---

## 2. 競品分析與差異化切入點 (Market Differentiation)

| 比較維度 | 現有專案 (`clawd-on-desk` / `Clyde`) | 本專案 (**`Clawdlet`**) |
| :--- | :--- | :--- |
| **核心定位** | CLI Agent 狀態儀表板（Status Widget） | 物理系桌面伴侶（Shimeji-like Companion） |
| **主要連動對象** | Claude Code (CLI)、Cursor、Codex | **Claude Desktop (GUI 桌面視窗)** 為主，CLI 為輔 |
| **空間運動能力** | 定點停留（僅支援滑鼠手動拖移位置） | **全螢幕自主遊走、攀爬螢幕邊緣、吸附視窗頂部** |
| **物理引擎** | 無 | **具備重力加速度、拋擲慣性、邊界碰撞檢測** |
| **互動趣味性** | 批准權限卡片、觀看狀態切換 | 甩動視窗會把牠甩飛、視窗最小化時會驚慌失措 |

---

## 3. 核心功能與行為狀態機設計 (Core Mechanics & FSM)

### 3.1 有限狀態機 (Finite State Machine)
`Clawdlet` 的行為由雙層架構驅動：**底層物理引擎（每幀更新座標）** 與 **上層行為狀態機（決定當前動作意圖）**。

1. **`IDLE`（發呆／呼吸）**：
   * 原地眨眼、微幅晃動底部小觸手，偶爾打哈欠或變成一坨扁平的烙餅狀（Blob mode）。
2. **`WANDER`（自主漫遊）**：
   * 在 Windows 工作列（Taskbar）上方或當前所在的視窗標題列左右走動。
3. **`CLIMB`（攀爬邊界）**：
   * 接觸到螢幕左右邊緣或目標視窗側邊時，轉向 $90^\circ$ 垂直向上攀爬。
4. **`PERCH_CLAUDE`（Claude 視窗專屬吸附）**：
   * 當偵測到 `Claude.exe` 處於開啟且非最小化狀態時，提高前往該視窗頂部邊框（Title Bar）的權重，趴在視窗上陪伴使用者工作。
5. **`FALLING & BOUNCE`（重力下墜與落地擠壓）**：
   * 當所在視窗突然關閉、最小化，或被使用者用滑鼠抓起放開時，觸發自由落體運動。落地瞬間播放具彈性的「壓扁回彈（Squish & Stretch）」動畫。
6. **`THROWN / DIZZY`（慣性甩飛與暈眩）**：
   * 當使用者快速拖曳移動 Claude Desktop 視窗（位移速度超過閾值 $v_{\text{threshold}}$），或直接用滑鼠左鍵抓取並甩出時，`Clawdlet` 會脫離視窗邊緣並沿拋物線飛出，落地後冒出小星星進入短暫暈眩狀態。

### 3.2 簡化 2D 物理模型
為確保桌寵移動與拋擲手感自然，渲染迴圈固定於 $\Delta t \approx 16.67\text{ ms}$（即 $60\text{ FPS}$），套用基礎運動學公式：

* **重力與速度更新**：
  $$v_y(t + \Delta t) = v_y(t) + g \cdot \Delta t$$
* **空氣阻力與水平摩擦衰減**：
  $$v_x(t + \Delta t) = v_x(t) \cdot \mu_{\text{drag}}$$
* **位置更新**：
  $$P(t + \Delta t) = P(t) + \mathbf{v}(t + \Delta t) \cdot \Delta t$$

---

## 4. 技術架構與選型規劃 (Technical Architecture)

### 4.1 系統架構圖
1. **視窗感知層 (OS Window Tracker)**：
   * 透過 Win32 API（`user32.dll`）輪詢或掛鉤（WinEventHook）。
   * 核心 API：`EnumWindows`、`GetForegroundWindow`、`GetWindowRect`、`IsIconic`（檢查最小化）、`GetWindowThreadProcessId`。
   * 過濾邏輯：鎖定執行檔名稱為 `Claude.exe` 之可見頂層視窗，提取其邊界矩形 $(X_{\text{left}}, Y_{\text{top}}, X_{\text{right}}, Y_{\text{bottom}})$ 作為動態平台碰撞箱（Bounding Box）。
2. **狀態與物理核心 (Physics & FSM Core)**：
   * 獨立計時器負責計算碰撞檢測（工作列高度 `SPI_GETWORKAREA`、螢幕邊界、Claude 視窗頂部邊緣）。
3. **透明渲染層 (Transparent Overlay GUI)**：
   * 無邊框（Frameless）、永遠置頂（Always on Top）、背景全透明（Translucent Background）、非繪圖區域滑鼠穿透。

### 4.2 開發語言與框架選型（雙軌策略）

* **階段一（快速原型 PoC）：Python 3.11 + PyQt6 + pywin32**
  * **優勢**：可在 300 行程式碼內迅速驗證透明視窗、滑鼠拖曳拋擲物理與 `Claude.exe` 座標追蹤邏輯，方便快速調整手感。
* **階段二（正式開源發行版）：Rust + Tauri v2 或 C# (.NET 8 WPF)**
  * **優勢**：打包體積小（$< 15\text{ MB}$）、常駐記憶體極低（$< 30\text{ MB}$），無須使用者安裝肥大的執行環境，符合桌面常駐小工具的輕量化標準。

---

## 5. 美術與動畫資產規格 (Art & Sprite Specifications)

為維持開源合規並展現風格，所有角色貼圖均採 **自繪原創像素風（Pixel Art）或簡約向量手繪風**，基礎畫布建議為 $64 \times 64\text{ px}$ 或 $128 \times 128\text{ px}$（支援整數倍無損縮放）：

| 動作名稱 (Animation State) | 建議幀數 (Frames) | 循環 (Loop) | 視覺表現重點 |
| :--- | :---: | :---: | :--- |
| `idle_breathe` | 4 | 是 | 身體微幅上下起伏，眼睛偶爾眨動 |
| `walk_horizontal` | 6 | 是 | 底部小觸手交替擺動前進 |
| `climb_vertical` | 4 | 是 | 身體貼合邊緣，觸手上下攀附 |
| `perch_look_down` | 4 | 是 | 趴在 Claude 視窗頂端，低頭往視窗內部偷看 |
| `dragged_struggle` | 2–4 | 是 | 被滑鼠拎起來時拉長身體，小腳懸空亂踢 |
| `free_fall` | 2 | 是 | 下墜時受風阻影響，觸手朝上飄起 |
| `land_squish` | 3 | 否 | 撞擊地面瞬間壓扁成烙餅狀，隨後回彈恢復 |

---

## 6. 開源合規與法務防護架構 (Legal & Licensing Strategy)

由於 `Clawdlet` 為公開在 GitHub 的非營利開源專案，必須嚴格執行 **「商標切割」** 與 **「程式碼／美術雙重授權」**，以符合社群二創規範並規避 DMCA 風險。

### 6.1 命名與品牌界線
* **禁止使用**：`Claude-Desktop-Pet`、`Anthropic-Mascot` 等易造成官方混淆之字眼。
* **標準化專案命名**：GitHub Repository 統一命名為 **`clawdlet`**，並在 Description 首句標明：`Clawdlet — An unofficial, fan-made physics desktop pet that perches on your Claude Desktop window.`

### 6.2 雙重授權結構 (Dual-Licensing)
* **根目錄 `LICENSE`（僅限程式碼）**：採用 **MIT License**，允許社群自由 Fork、修改物理引擎與視窗追蹤邏輯。
* **素材目錄 `assets/LICENSE.md`（美術與角色設計）**：明確排除在 MIT 授權之外。聲明角色概念源自 Anthropic，本專案內之自繪像素圖僅供非商業個人娛樂與學習使用，嚴禁任何第三方將素材用於商業軟體、周邊販售或加密貨幣／NFT 項目。

### 6.3 README 免責聲明標準範本
```markdown
## Legal Disclaimer
**Clawdlet** is an unofficial, non-commercial, fan-made open-source project. 
It is NOT affiliated with, endorsed by, or sponsored by Anthropic, PBC.

The "Claude" name, brand, and the original "Clawd" character concept are the 
intellectual property of Anthropic, PBC. All source code in this repository is 
licensed under the MIT License, while the fan-made sprite assets in the `assets/` 
directory are provided strictly for personal, non-commercial use.
```

---

## 7. 開發里程碑規劃 (Development Roadmap)

* **Milestone 1：物理與視窗追蹤核心 (Core Engine PoC)**
  * 完成無邊框透明視窗渲染與滑鼠拖拽／重力拋擲公式。
  * 實作 Win32 `Claude.exe` 視窗搜尋與頂部邊緣吸附（跟隨視窗移動、偵測最小化掉落）。
* **Milestone 2：狀態機與基礎像素圖載入 (FSM & Basic Sprites)**
  * 繪製第一版核心動作 Sprite Sheet（發呆、走路、掉落、抓起）。
  * 完成狀態機自動切換邏輯（隨機漫步 $\leftrightarrow$ 尋找 Claude 視窗 $\leftrightarrow$ 趴下休息）。
* **Milestone 3：系統匣控制與體驗打磨 (Polish & Tray Menu)**
  * 加入系統工作列圖示（System Tray Icon），支援：一鍵召喚回畫面中央、調整移動活躍度（安靜模式／過動模式）、調整縮放比例（$1\times / 2\times / 3\times$）、穿透鎖定模式（Click-through Mode）。
* **Milestone 4：GitHub 正式開源發布 (Open Source Release)**
  * 撰寫中英雙語 `README.md`、配置雙重 License 文件、錄製 GIF 實機展示動圖（展示把 `Clawdlet` 甩到 Claude 視窗上的互動過程），並打包首個 Windows Release（`clawdlet.exe` 單檔或安裝包）。