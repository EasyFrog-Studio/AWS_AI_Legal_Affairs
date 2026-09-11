# 訴願案件審理 AI 輔助系統

新北市政府法制局訴願案件的 AI 輔助審理系統。輸入訴願書、原處分書、送達證書(與選填的訴願答辯書),系統依訴願法第 77 條做程序審查分流,再以 RAG 完成案件資訊擷取(F1)、法規推薦(F2)、相似案例比對(F3)與決定書草稿生成(F4);承辦人在前端修改草稿並下載 PDF。

FastAPI + React 單容器;AI 後端三選一:`mock`(合成樣本,免憑證)、`aws`(Bedrock + S3 Vectors + DynamoDB)、`local`(ollama + Postgres/pgvector)。

## 快速啟動(mock 模式,不需任何憑證與語料)

```bash
cp .env.example .env        # 填 API_KEY(登入用);其餘機密欄位依模式再填
docker compose --env-file .env -f docker/docker-compose.yml up --build
# → http://localhost:8000,登入頁輸入 .env 的 API_KEY
```

`data_show/test_cases/` 附九組可直接拖進網站的合成卷證(PDF),mock 模式開箱即可跑完整條流程。

## 環境變數與機密

- 所有設定只從 `.env` 讀(`backend/app/config.py` 是變數清單的權威);**程式碼不含任何預設憑證**:`API_KEY` 未設即所有 `/api/*` 回 401,`POSTGRES_URL` 未設則 local 模式啟動即報錯。
- `.env.example` 進版控,機密欄位(`API_KEY`、`GEMINI_API_KEY`、`POSTGRES_PASSWORD`、`POSTGRES_URL`、`PGADMIN_EMAIL`、`PGADMIN_PASSWORD`)一律留空;`.env` 被 `.gitignore` 排除。
- AWS 憑證不在 `.env`:`aws` 模式由 compose 把本機 `~/.aws` 唯讀掛進容器,絕不 build 進 image。

## 語料與資料(不在本 repo)

本 repo 只含程式碼與開箱示範卷證,**不含競賽語料**。`preprocessing/`、`aws_setup/`、`local_setup/` 這三組建庫腳本預期語料放在與本 repo **同層的上一級目錄** `../data/`:

| 路徑 | 內容 | 誰用 |
|---|---|---|
| `../data/資料集/` | 競賽提供的歷史訴願決定書 101 份(110–114 年)、相關法規 11 部、行政函釋、司法院釋字及行政判解 | `preprocessing/parse_*.py` 讀 |
| `../data/output/` | 前處理產出的 chunk JSONL 與 markdown | `preprocessing/` 寫,`aws_setup/04_ingest.py`、`local_setup/ingest.py` 讀 |

合成測資(example1–8、1b)與 114 年決定書全文已內建於本 repo 的 `data_show/`,不必另外擺放。
取得競賽資料集後照上表擺放即可;沒有語料時 `mock` 模式完全不受影響。**114 年的 21 件決定書是留出測試集**,`preprocessing`、`aws_setup/01_s3.py`、`04_ingest.py`、`local_setup/ingest*.py` 都以 `HOLDOUT_YEARS` 排除,不得灌進任何檢索庫。

## 目錄

| 目錄 | 內容 |
|---|---|
| `backend/app/` | FastAPI:`main.py` 路由、`pipeline.py` F1→程序審查→F2/F3→F4、`deadline*.py`/`procedural_checks.py`/`notice_clause.py`/`transit.py` 程序審查的可計算層、`providers/` 三模式、`store.py` 三種案件儲存 |
| `backend/tests/` | pytest;`python -m pytest`(backend/) |
| `backend/tools/` | `eval_ocr.py` OCR 辨識率(`AI_PROVIDER=aws` 或 `local`) |
| `frontend/` | Vite + React 四頁 SPA;`npm test`、`npm run build` |
| `preprocessing/` | PDF → chunk JSONL(一次性、地端) |
| `aws_setup/` | S3 / DynamoDB / S3 Vectors / Bedrock KB 建置腳本 01–05 |
| `local_setup/` | local 模式建索引(ollama embedding → pgvector) |
| `docker/` | 多階段 Dockerfile + compose(web / postgres / pgadmin)+ `initdb/` schema |
| `deploy/` | ECR 推送、ECS Fargate、ALB |
| `data_show/` | mock 樣本 `sample_appeals/`、合成測資 `test_cases/`(九組)、114 年決定書全文 `decisions_114/` |

## 評測

```bash
cd eval && AI_PROVIDER=aws python run_eval.py     # ~/.aws 需有效憑證
```

local 模式亦可(`AI_PROVIDER=local`,需主機 ollama 與 `.env` 的 `POSTGRES_URL_HOST`)。

答案鍵 `eval/answer_key.json` 由人工從官方訴願決定書逐字抄錄,決定書未載或有歧義的欄位填 `null` 整欄不計分。
量分流(受理與否 + 訴願法§77 款次)、決定類型、F1 欄位與期間三要素,計分分 correct / wrong / unsure 三格;
unsure 是系統自陳判斷不出來,不算失分。用法與限制見 `eval/README.md`。

## 設計原則

- **說不準就不猜**:期間算式有任何保留事項(公示送達、採訴願人自述、§98 分支、OCR 取字)就不覆寫程序審查,只標 `review_note` 交人工;§77(3) 的利害關係判斷模型指不出「法規名稱#條號」即不採信。
- **日期不由模型決定**:訴願期間由 `deadline.py` 依訴願法 §14/§16、行政程序法 §48/§74/§98 計算,國定假日與在途期間查表;寄存送達依釋字 797 當日生效,不加 10 日。
- **法條不由模型生成**:F4 只能引用 F2 清單子集,程式後置過濾;修正日期一律取自 DynamoDB / KB metadata。
- **不受理體例由程式保證**:主文固定「訴願不受理。」、事實欄不記載(訴願法 §89 I③);決定類型五值對應語料實際分布。
