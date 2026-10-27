<p align="center"><a href="development.md">English</a> | <b>繁體中文</b></p>

# 開發

## 自動化測試

```powershell
pip install -e ".[dev]"
python -m pytest -v
```

部分測試與行為需要真的 Windows 桌面（Tk 顯示、系統匣、執行中的 Claude Desktop），發版前請照 **[Windows 實機測試清單](windows-test-checklist.md)** 跑一遍。

## 打包 `clawdlet.exe`

```powershell
pip install pyinstaller
python -m clawdlet.tools.build_exe
# 輸出：dist/clawdlet.exe
```

## Sprite 與 README 動圖

所有 sprite 都由程式繪製，沒有手繪或複製的素材。

```powershell
python -m clawdlet.tools.gen_sprites      # 重新繪製 assets/sprites/*.png 與 manifest.json
python -m clawdlet.tools.gen_demo_gifs    # 重新產生 assets/demo/clawdlet_demo.gif 與 assets/demo/actions/*.gif
```

README 介紹段落的各動作 GIF 定義在 [`gen_demo_gifs.py`](../src/clawdlet/tools/gen_demo_gifs.py) 的 `ACTION_GIFS`。

## 開發工具

```powershell
clawdlet --debug                                  # 顯示即時狀態與 HWND 除錯列
clawdlet-dump-windows                             # 檢查 Clawdlet 偵測到的 Claude.exe HWND
clawdlet --dump-behaviors                         # 印出合併後的行為表（警告輸出到 stderr）
python -m clawdlet.tools.send_hook_event Stop     # 對執行中的 Clawdlet 送出假的 hook 事件
python -m clawdlet.tools.send_hook_event Notification --notification-type idle_prompt  # 指定 notification_type
python -m clawdlet.tools.send_hook_event PreToolUse --tool-name ExitPlanMode           # 指定 tool_name（plan 牌子）
```

## 設計紀錄

- [專案概念書](clawdlet.md)：願景、狀態機、物理模型、里程碑
- [M6 計畫紀錄](plans/m6-plan.md)：v0.3.0 的規劃與實作過程
- [銘謝](ACKNOWLEDGEMENTS.md)：哪些行為參考了哪些專案

---

[← 回到 README](../README.zh-TW.md)
