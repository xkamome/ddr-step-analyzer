# 精準度實驗計畫（exp/accuracy-study，2026-10-13）

驗收標準見 `../ACCEPTANCE.md`；進度見 `../STATUS.md`。Python 一律用實驗 venv：
`$LOCALAPPDATA/ddr-pose/venv-exp/Scripts/python.exe`（在 Bash 裡用 `experiments/py.sh <script>`）。

## 檔案
| 檔案 | 內容 |
|---|---|
| `common.py` | 影片清單、讀資料、標準答案（亮燈）、評分（事件配對 F1、時間誤差、板位正確率） |
| `extract_poses.py` | 背景重跑骨架：RTMW-x（133 點 → 轉成 Halpe26 排列）＋ RTMW3D-x，OpenVINO GPU，逐支存檔可續跑 |
| `gt.py` | 從 glow.npz＋最終板位算每幀每板亮燈 → `results/gt.json`、`cache/<vid>/gt.npz` |
| `eval_contact.py` | 方法 `pose_halpe`／`pose_rtmw`：現行 analyze（lights:false，純骨架）在 cache 複本上跑，對標準答案評分 |
| `learn_contact.py` | 方法 `learned_*`：用亮燈當標籤訓練落地判定，留一支法評分 |
| `com.py` | 重心：`winter2d`（現行）、`rtmw3d`、`bonelen`；物理合理性＋兩兩比對 → `results/com.json` |
| `test_integration.py` | 整合後在 cache 複本上跑 7 支影片 → `results/integration.json` |
| `REPORT.md` | 報告（也發成 Artifact，網址寫到 `results/report_url.txt`） |

## 評分定義
- 標準答案事件＝每塊板「由暗轉亮」（glow.light_onsets，用最終板位）。
- 預測事件＝骨架判定的落地（frame、板位）。同板、時間差 ≤ 4 幀內一對一配對（匈牙利/貪婪依時間差）。
- F1／precision／recall；`timing_mae_ms`＝配對成功者的 |預測幀−亮燈幀| 平均 ×1000/fps（另記帶號中位數＝系統延遲）；
  `panel_acc`＝只看時間配對（不管板位）成功的事件中，板位正確的比例。

## 順序（優先：踩下去判定 > 無燈 > 重心）
1. venv、背景骨架重跑（最久，先開）
2. gt.py → eval_contact（pose_halpe）
3. 骨架重跑完 → eval_contact（pose_rtmw）
4. learn_contact（halpe、rtmw）
5. com.py
6. 整合 analyze.py（贏的落地判定；重心深度若可信則加）→ test_integration → commit
7. REPORT.md＋Artifact → commit → verify 通過
