# Ollama Web Chat v2 🦙

一套輕量、高效且功能齊全的本機 Ollama 繁體中文 Web 客戶端。具備雙向語音互動、豐富的自主工具調用（Function Calling / Tools）、即時硬體效能監控（CPU/GPU/RAM 與溫度監測，整合 SQLite 歷史遙測資料庫），並提供零設定的一鍵式啟動腳本。

---

## 🌟 核心特色

### 1. 🤖 現代化串流對話與模型管理
* **即時串流響應**：流暢打字機效果，具備中斷生成（Esc 鍵或「⏹ 中止生成」按鈕）與重新生成功能。
* **思考鏈可視化**：原生支援 `<think>...</think>` 思考標籤折疊與展開（針對 DeepSeek-R1 等推論模型）。
* **推論效能指標**：即時顯示首字延遲（TTFT）、生成速度（tokens/s）、輸入/輸出 Token 數、累計消耗量與耗時統計。
* **模型生命週期管理**：
  * 線上模型庫快速拉取（`ollama pull`）並即時呈現各層分塊下載進度條與傳輸速率。
  * 模型記憶體駐留控制（一鍵載入至記憶體 / 釋放記憶體）。
  * 刪除本機模型與記憶體駐留監控（`ollama ps`）。

### 2. 🎙️ 雙向語音互動（Voice In & Out）
* **語音辨識輸入（STT）**：整合瀏覽器 Web Speech API，支援連續語音輸入與語音指令（說出「發送」、「傳送」自動送出；說出「停止錄音」結束收音）。
* **智慧語音朗讀（TTS）**：過濾思考過程與 Markdown 語法，優先挑選最高音質之繁體中文語音庫（如微軟 Edge Natural / Online 語音、Google / Siri 語音）。
* **防回音抑制機制**：AI 朗讀期間自動停止麥克風，朗讀結束後設置 400ms 殘響衰減緩衝期，徹底杜絕自說自話的迴音干擾。

### 3. 🛠️ 20 種自主工具調用（Function Calling）
內建智慧工具調用清單，賦予本地大語言模型與外界網路及作業系統互動的強大能力：
| 類別 | 工具名稱 | 功能說明 |
| :--- | :--- | :--- |
| **聯網搜尋** | `search_web` | 透過 DuckDuckGo + Jina Reader 獲取最新網頁搜尋結果 |
| | `read_url` | 解析並抓取指定 URL 網頁的純文字內容 |
| | `get_news` | 查詢 Google News 最新即時新聞 RSS |
| | `search_wikipedia` | 檢索維基百科百科全書條目摘要 |
| **系統與環境** | `get_system_info` | 讀取系統規格（OS、處理器、記憶體、網路介面卡、BIOS） |
| | `run_command` | 本地執行 PowerShell 命令並回傳終端輸出 |
| | `open_application` | 開啟本地應用程式或捷徑檔案 |
| | `read_local_file` | 讀取本地端檔案文字內容 |
| | `list_directory` | 列出本地資料夾結構、檔案大小與修改時間 |
| | `git_status` | 查詢 Git 儲存庫分支、未提交異動與最新 Commit |
| **硬體監控** | `get_hardware_status` | 讀取當前 CPU、GPU、RAM 使用率及即時溫度 |
| | `get_hardware_alerts` | 診斷硬體是否發生高溫過熱（≥82°C）或滿載警戒 |
| **即時資訊** | `calculator` | 數值運算與數學表達式求值 |
| | `get_current_time` | 取得當前本機精確時間與時區 |
| | `get_weather` | 透過 Open-Meteo 查詢全球指定城市即時天氣與預報 |
| | `get_crypto_price` | 透過 CoinGecko 取得加密貨幣即時報價 |
| | `translate_text` | 透過多語系 API 進行即時文字翻譯 |
| | `get_ip_location` | 查詢當前公開 IP 位址與地理位置 |
| | `get_hacker_news` | 抓取 Hacker News 熱門科技頭條 |
| | `generate_image` | 使用 Pollinations.ai 依提示詞生成圖像並呈現 |

### 4. 📈 系統硬體效能儀表板與 SQLite 遙測服務
* **導覽列動態狀態膠囊**：實時顯示 CPU / GPU / RAM 使用率與溫度，支援高溫（≥75°C）與滿載（≥80%）警戒呼吸光效。
* **高解析度 Canvas 折線圖**：支援 60 秒、5 分鐘、1 小時、8 小時、24 小時多時間範圍切換，提供滑鼠十字游標（Crosshair）懸停數值檢視。
* **SQLite 遙測歷史資料庫** (`hw_logger.py` + `hardware.db`)：
  * 採用 SQLite WAL 模式（Write-Ahead Logging），確保高頻寫入效能與並行讀取安全。
  * 整合 LibreHardwareMonitor (LHM) Web 介面（預設埠 8085）。
  * 支援背景自動同步取樣、前端取樣間隔調整（1秒 / 2秒 / 5秒）。
  * 提供歷史資料過期清理機制（Prune）與一鍵匯出完整 CSV 報表。
  * 支援瀏覽器桌面警報推播（過熱/滿載即時提醒）。

---

## 📂 專案檔案結構

```text
├── ollama-chat-v2.html           # 主要 Web 前端頁面（深色主題、繪圖 Canvas、語音與工具互動）
├── _webserver-v2.ps1             # PowerShell 輕量本地伺服器、API 反向代理與命令執行橋樑
├── hw_logger.py                  # Python 硬體遙測日誌後端（SQLite 管理、REST API 服務與 CLI）
├── test_hw_logger.py             # 針對硬體遙測日誌後端的 pytest 單元測試套件
├── One_File._Double-Click._Done.bat # 一鍵全自動啟動腳本（重啟 Ollama CORS、啟動服務並開瀏覽器）
├── start.bat                     # 精簡啟動腳本
├── sysinfo.js                    # 本機規格快取腳本（由啟動腳本自動提取 systeminfo 生成）
├── hardware.db                   # SQLite 硬體遙測持久化資料庫（執行後自動建立）
└── README.md                     # 專案中文說明文件
```

---

## 🚀 快速開始

### 環境需求
1. **Windows 10 / 11**
2. **[Ollama](https://ollama.com/)**（已安裝且已就緒）
3. **Python 3.10+**（執行硬體遙測日誌服務與單元測試）
4. **PowerShell 5.1+**（Windows 內建）
5. *(選用)* **[LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)**：若需取得 CPU/GPU 負載及溫度，請開啟 LibreHardwareMonitor 並在「Options」中啟用「Remote Web Server」（預設埠號 8085）。

---

### 一鍵啟動方式（推薦）

直接雙擊執行：
```cmd
One_File._Double-Click._Done.bat
```

腳本將自動執行以下動作：
1. 確保以啟用 CORS 跨域（`OLLAMA_ORIGINS=*`）環境重啟 Ollama 服務。
2. 背景啟動硬體遙測 SQLite 服務 (`hw_logger.py --serve`，連接埠 8086)。
3. 背景啟動 PowerShell 本地 Web 伺服器 (`_webserver-v2.ps1`，連接埠 8080)。
4. 自動喚起預設瀏覽器開啟 `http://localhost:8080/ollama-chat-v2.html`。
5. 在主控台視窗中按下任意鍵即可一次關閉所有背景服務。

---

### 手動啟動步驟

#### 1. 啟動 Ollama 服務（必須啟用 CORS）
在 PowerShell 中執行：
```powershell
$env:OLLAMA_ORIGINS="*"
ollama serve
```

#### 2. (選用) 啟動硬體遙測後端服務
```powershell
python hw_logger.py --serve --port 8086
```

#### 3. 啟動本地 Web 伺服器
```powershell
powershell -ExecutionPolicy Bypass -File .\_webserver-v2.ps1 -Port 8080
```
開啟瀏覽器前往：`http://localhost:8080/`

---

## 🛠️ 命令列工具（hw_logger CLI）

`hw_logger.py` 亦可作為獨立命令列工具使用：

```bash
# 初始化資料庫
python hw_logger.py --init

# 擷取單次 LibreHardwareMonitor 遙測資料並寫入資料庫
python hw_logger.py --once

# 查詢過去 1 小時（3600 秒）內的效能資料（JSON 輸出）
python hw_logger.py --query 3600 --max-points 300

# 檢視資料庫統計資訊（資料筆數、資料庫大小、時間跨度）
python hw_logger.py --stats

# 清理保留天數以外的過期紀錄（例如清理 7 天前的數據）
python hw_logger.py --prune 7

# 將全部歷史資料匯出為 CSV 檔案
python hw_logger.py --export-csv hardware_export.csv

# 啟動獨立 HTTP REST API 伺服器
python hw_logger.py --serve --port 8086
```

---

## 🧪 執行單元測試

專案內建完整的測試套件 `test_hw_logger.py`，涵蓋資料庫初始化、WAL 模式檢查、遙測解析、高頻取樣聚合、資料庫清理、CSV 匯出以及 HTTP REST API 伺服器路由測試。

執行測試指令：
```bash
python -m pytest test_hw_logger.py -v
```

---

## 🔒 安全性說明

* `_webserver-v2.ps1` 中的 `/api/run` 端點允許透過前端或 AI 工具調用執行本地 PowerShell 命令、開啟應用程式及讀取本機檔案。
* 此設計專為**個人本地開發環境**打造。若預計將此服務公開至區域網路或網際網路，請務必加入身分驗證或停用 `/api/run` 介面，以防任意指令執行。

---

## 📄 授權條款

本專案採用 MIT 授權條款，歡迎自由修改與分享。
