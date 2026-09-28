# 訴願審理平台：訴願案件審理 AI 輔助系統

給政府訴願承辦人用的審理工具。承辦人上傳一件訴願案的卷證 PDF，系統自動讀完、判斷這件案子該不該受理、找出相關法條與過往案例，最後產出一份可以直接修改、下載的訴願決定書草稿。

以新北市政府訴願案件為對象開發（黑客松作品），AI 端使用 AWS Bedrock（Claude Sonnet）與 Knowledge Base 做檢索增強生成。

> **關於本頁截圖**：AWS 環境已於競賽結束後關閉，以下截圖皆為本機 Docker 以 **mock 模式** 執行的實際操作畫面。mock 模式的 AI 回應來自內建的合成樣本，流程、畫面與程式判斷邏輯與正式環境相同。

---

## 它解決什麼問題

訴願承辦人收到一件案子，要做的事大致是：

1. 從訴願書、原處分書、送達證書、答辯書裡把當事人、日期、處分內容一欄一欄抄出來；
2. 依訴願法第 77 條逐款檢查程序：有沒有逾期、當事人適不適格、是不是行政處分……；
3. 翻法條、翻以前類似的決定書、翻釋字與函釋；
4. 依固定體例寫出決定書。

這些工作重複、耗時，而且一個日期算錯就是整份決定書出錯。本系統把這四步串成一條自動流程，承辦人的角色從「從零開始寫」變成「檢查並修改 AI 的草稿」。

## 功能導覽

### 1. 登入與案件清單

![登入頁](docs/screenshots/01-login.png)

案件清單可依進度、決定結果、案件類別、時間篩選，決定結果以印章樣式呈現（不受理、駁回、撤銷等）。

![案件清單](docs/screenshots/02-case-list.png)

### 2. 上傳卷證，上傳即開始分析

四個文件槽：訴願書、原處分書、訴願答辯書為必填，送達證書為選填。按下送出後系統立刻在背景跑完整條流程，不必再按「開始分析」；左側「審理歷程」會標出目前跑到哪一階段，畫面自動跟著前進。

![新增案件](docs/screenshots/03-new-case.png)

![分析進行中](docs/screenshots/04-auto-analysis.png)

### 3. 文件確認：放錯檔案會被擋下

系統先確認每份 PDF 真的是它所放的那種文件。下面第二張圖故意把原處分書放進「訴願答辯書」槽，系統判斷出「文字特徵更接近原處分書」並要求重新上傳。掃描檔（沒有文字層）會自動走 OCR。

![文件確認通過](docs/screenshots/05-document-check.png)

![文件放錯槽](docs/screenshots/06-document-check-reject.png)

### 4. F1 案件資訊擷取

AI 從四份文件擷取約 65 個欄位，依文件分成五個分頁（訴願書／送達證書／原處分書／訴願答辯書／綜合判讀），欄位排列對照實際公文的印刷格式。「綜合判讀」整理出案由類別、爭點與援引法條。

![F1 擷取](docs/screenshots/07-f1-extract.png)

![F1 綜合判讀](docs/screenshots/08-f1-summary.png)

所有欄位都能直接修改、自動儲存，日期一律用民國年月日選擇器輸入。承辦人改過資料後，系統會提示「程序審查尚未依修改後的資料重跑」，按下「AI 生成」即從這一站往後重跑，前面的結果保留不動。

![修改後提示重跑](docs/screenshots/09-f1-edited-stale.png)

### 5. 程序審查：依訴願法第 77 條分流

AI 依第 77 條八款逐一檢核，得出「受理」或「不受理（第幾款）」。**訴願期間不交給 AI 算**：由程式依訴願法與行政程序法計算送達生效日、期間末日，並查國定假日表與在途期間表，再與機關收文日比對。

受理案（期間內提起）：

![程序審查：受理](docs/screenshots/10-screening-admissible.png)

不受理案（寄存送達，依行政程序法第 74 條與釋字 797 號寄存當日即生效，算出已逾期）：

![程序審查：不受理](docs/screenshots/11-screening-inadmissible.png)

### 6. 參考依據：法規、過往案例、釋字、函釋、裁判

五個分頁各自獨立檢索，互不排擠：

- **法規法條**：推薦的條文與全文，附官方法規資料庫連結；不受理案依流程不做法規推薦，畫面會說明原因。
- **過往案例**：語意比對歷年訴願決定書，列出最相似的案件與當時的結果。
- **司法院釋字／行政函釋／行政法院裁判**：供承辦人論理時參考，可開原文。

![參考依據：法規](docs/screenshots/12-refs-laws.png)

![參考依據：過往案例](docs/screenshots/13-refs-cases.png)

![參考依據：行政函釋](docs/screenshots/14-refs-rulings.png)

### 7. 決定書草稿：直接修改、下載 PDF／Word

依決定結果（不受理／駁回／撤銷另處／原處分撤銷／部分不受理部分駁回）產出完整體例的決定書：表頭、主文、事實、理由、教示條款。右側並列參考法規、釋字、函釋、案例，方便對照著改。

駁回案：

![決定書草稿：駁回](docs/screenshots/15-draft-dismiss.png)

不受理案：主文固定為「訴願不受理。」並依法不記載事實欄，這由程式保證，不靠 AI 自律。

![決定書草稿：不受理](docs/screenshots/16-draft-inadmissible.png)

下載的 PDF：

![匯出 PDF](docs/screenshots/17-pdf-export.png)

## 設計重點：AI 不確定的地方，不讓它猜

法律文書錯一個日期或一條法條就是錯的決定，所以系統把「AI 負責的」跟「程式保證的」分開：

| 項目 | 做法 |
|---|---|
| 訴願期間 | 程式依法條計算，查假日表與在途期間表；AI 不決定日期 |
| 法條修正日期 | 一律從資料庫帶出，禁止模型生成 |
| 決定書引用的法條 | 只能是法規推薦清單的子集，程式在生成後過濾 |
| 不受理體例 | 主文套語與事實欄不記載由程式強制 |
| 判斷不出來時 | 不硬給答案，改標「須人工確認」並列在頁首提示區，承辦人一眼看得到 |
| 人改過的資料 | 保留模型原值，改過的欄位可追溯；下游結果過期會提示重跑 |

## 在本機執行（mock 模式，不需要 AWS）

需要 Docker。

```bash
cp .env.example .env
# 編輯 .env：AI_PROVIDER=mock、API_KEY=0000（登入密碼），
# 並填 POSTGRES_PASSWORD、PGADMIN_EMAIL、PGADMIN_PASSWORD（compose 必填，任意值即可）
docker compose --env-file .env -f docker/docker-compose.yml up --build
```

開啟 http://localhost:8000 ，以 `.env` 的 `API_KEY` 登入。

可直接上傳的示範卷證：

| 目錄 | 內容 |
|---|---|
| `data_show/demo_cases/dismiss/` | 受理後駁回（本頁截圖的駁回案） |
| `data_show/demo_cases/inadmissible/` | 寄存送達、逾期不受理（本頁截圖的不受理案） |
| `data_show/test_cases/example*/` | 九組合成卷證，涵蓋逾期、當事人不適格、非行政處分、駁回、撤銷等情形 |

## 技術

Python / FastAPI、React + Vite、AWS Bedrock（Claude Sonnet、Titan Embeddings）、Bedrock Knowledge Base + S3 Vectors、DynamoDB、ECS Fargate、Docker。AI 後端可切換 `mock`（免憑證展示）、`aws`（正式）、`local`（本機 ollama + Postgres/pgvector）三種模式。

架構、資料層建置、部署與評測方式見 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。
