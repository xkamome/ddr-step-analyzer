# ddr-step-analyzer（DDR ステップ解析）— Claude Code 約定

用骨架（RTMPose Halpe26）分析 DDR 遊玩影片的踩點、重心、腳尖／腳跟，給出修正建議。本機 Python 後端＋瀏覽器介面，不用任何雲端 API。使用說明見 `README.md`（日文，GitHub 首頁）、`README.zh-TW.md`（中文）與 `はじめにお読みください.txt`（給日文使用者）。

## 使用者與偏好
- 使用者是高階 DDR 玩家（15 級以上），也會把工具交給**不懂電腦的日文使用者**（Windows）。
- **只分析看得到踏板的影片**；貼地側拍（看不到踏板）不要分析（舊資料在 `data/_archive/`）。
- 介面**預設日文**，可切中文；修正建議也是中日雙語。
- 分析結果要標信心：單一斜角鏡頭只量得到重心在畫面水平方向的分量，畫面外的手是推估。

## 檔案與 source of truth
| 檔案 | 內容 |
|---|---|
| `ddrpose/pose.py` | 姿勢擷取（YOLOX＋RTMPose-x）、預覽轉檔、ffmpeg 定位、`--download-models` |
| `ddrpose/stabilize.py` | 鏡頭移動補償：**原則固定**，每 2.5 秒關鍵幀、位移 >3% 且下一關鍵幀確認才換段（分段常數，`motion.npz` 含 `ok`／`seg`／`ver`）；逐幀補償會讓九宮格抖 |
| `ddrpose/glow.py` | 踏板亮燈：累積圖→找四個箭頭板、逐幀亮燈、落地時亮起的板 |
| `ddrpose/analyze.py` | 主分析（三階段，見下）、重心分段模型、指標與**雙語建議**（`_adv(..., (zh, ja))`） |
| `ddrpose/events.py` | 重心大幅移動事件與三步配置統計 |
| `server.py` | FastAPI、工作佇列、校正 API、心跳自動關閉 |
| `web/i18n.js` | **所有介面文字**（zh / ja）；後端短標籤的翻譯在 `VAL` |
| `web/app.js` | 介面（疊圖、俯視圖、時間軸、校正編輯、事件） |
| `tools/setup.ps1` | 給使用者的一鍵安裝（自動裝 Python 3.12、venv、模型） |
| `tools/make_package.py` | 打包 `dist/ddr-step-analyzer_*.zip`（發佈到 GitHub Releases） |

## 分析流程（analyze.py）
- 三階段，靠 calib dict 的內部欄位遞迴：stage 0 取得亮燈板位 → stage 1 分板腳跟基準、用亮燈擬合**腳部點離地高度 `_sole_h`**與腳長寬容 `_foot_ext`（自動校正時也微調九宮格）→ stage 2 最終。
- `calib.json` 只存使用者的 `points`（←↓↑→ 中心，參考幀座標）與 `foot_offset`（手動腳位置偏移，踏板座標），以及每支影片設定 `lights: false`（踏板不亮燈的機台，例：舊白框體的喇叭反光會被誤認成亮燈）、`hidden_arms`（不在畫面的手，`["R"]` 等）；設定由 `/api/videos/{id}/options` 寫入，重設校正時保留。底線開頭的欄位是內部用，不要寫進檔案。
- **非遊玩段不分析**（`_play_mask`）：沒抓到人、鏡頭對不上參考幀、雙腳離常站位置 >1.5 腿長的幀排除（例：拿手機拍結算畫面）；亮燈圖也只累積遊玩幀。
- 下一階段沿用上一階段的自動板位（`_source` 為 auto-glow／auto-feet 時用 `calib['points']`）。
- 以**踏板亮燈為標準答案**：步數與板位以亮燈為準，骨架判斷用哪個部位踩。
- 座標：踏板座標 x 往右、y 往後（↓），箭頭在 ±1；影像座標分「目前幀」與「參考幀」（`frames.M` 轉換），預覽檔解析度 ≠ 原始解析度。

## Windows 上的坑（都踩過）
- 主控台是 **cp932**：Python 輸出前 `sys.stdout.reconfigure(errors='replace')`。
- `tools/setup.ps1` 必須 **UTF-8 with BOM＋CRLF**（Windows PowerShell 5.1），`.bat` 用 CRLF；用 Python 寫檔時注意不要變成 `\r\r\n`。
- venv 放 `%LOCALAPPDATA%\ddr-pose\venv`（避開 260 字元路徑上限與 OneDrive）。
- OpenCV 只裝 `opencv-contrib-python`；`rtmlib` 要 `--no-deps` 安裝（它同時依賴兩套 OpenCV 會互相覆蓋）。
- 從 Bash 丟 heredoc 給 Python 時反斜線會被吃掉 → 含 Windows 路徑的修改腳本用檔案寫。

## 開發約定
- 改後端要**重啟伺服器**（模組不會重載）；改分析邏輯後重跑 `python -m ddrpose.analyze data/<id>`。
- 開發時用 `python server.py --no-auto-exit`（否則關掉分頁 10 秒後伺服器自動結束）。
- 介面文字一律加到 `web/i18n.js` 的 zh 和 ja；建議文字在 `analyze.py` 同時寫中日文。
- 版本在 `ddrpose/__init__.py` 的 `__version__`；發佈：改版本 → `make_package.bat` → GitHub Release（帳號 xkamome）。
- 改完要給別人用：`make_package.bat`，並同步更新 `はじめにお読みください.txt`。
- `data/`（影片、分析結果）是個人資料，不進 git；也不要未經同意改使用者影片的 `calib.json`。

## 待辦／想法
- 影片與俯視圖「對齊」：使用者提過，但還沒確認是版面並排、方向旋轉，還是放進黑邊。
- 網頁版（瀏覽器內推論、影片不上傳）：只做過可行性分析（onnxruntime-web＋Pyodide/JS），建議先做 12 秒片段的速度測試。
- 安裝包可選擇內附 Python 安裝檔（約 25 MB），免依賴 winget／python.org 下載。
