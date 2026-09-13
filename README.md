# 訴願案件審理 AI 輔助系統

新北市政府法制局訴願案件的 AI 輔助審理系統。輸入訴願書、原處分書、訴願答辯書(與選填的送達證書),系統依訴願法第 77 條做程序審查分流,再以 RAG 完成案件資訊擷取(F1)、相似案例比對(F3)、法規推薦(F2)、參考見解(F2+)與決定書草稿生成(F4);承辦人在前端逐階段修改,最後下載決定書 PDF / Word。

FastAPI + React 單容器;AI 後端三選一:`mock`(合成樣本,免憑證)、`aws`(Bedrock + S3 Vectors + DynamoDB)、`local`(ollama + Postgres/pgvector)。

## 快速啟動(mock 模式,不需任何憑證與語料)

```bash
cp .env.example .env        # 填 API_KEY(登入用)與 POSTGRES_PASSWORD / PGADMIN_*(compose 必填);其餘依模式再填
docker compose --env-file .env -f docker/docker-compose.yml up --build
# → http://localhost:8000,登入頁輸入 .env 的 API_KEY
```

`data_show/test_cases/` 附九組可直接拖進網站的合成卷證(PDF),mock 模式開箱即可跑完整條流程。

## 三種模式

| `AI_PROVIDER` | 擷取 / 生成 | 檢索來源 | 案件儲存 | 前置 |
|---|---|---|---|---|
| `mock` | `data_show/sample_appeals/` 的合成樣本,依關鍵詞比對回傳 | 樣本內建 | 記憶體 | 無 |
| `aws` | Bedrock(`BEDROCK_MODEL_ID`) | 五個 Bedrock Knowledge Base(S3 Vectors)+ DynamoDB 精查 | DynamoDB `appeal_cases` | `~/.aws` 有效憑證、`aws_setup/` 建好資料層 |
| `local` | 主機 ollama(`LOCAL_LLM_MODEL` / `LOCAL_EMBED_MODEL` / `LOCAL_VISION_MODEL`) | Postgres + pgvector(compose 內建) | Postgres | 主機 `ollama serve`、`local_setup/` 建好索引 |

`CASE_STORE` 留空即依模式自動對應;三模式共用同一套 pipeline、API 與前端,切換只改 `.env`。

## 環境變數與機密

- 所有設定只從 `.env` 讀(`backend/app/config.py` 是變數清單的權威,`.env.example` 逐項附說明);**程式碼不含任何預設憑證**:`API_KEY` 未設即所有 `/api/*` 回 401,`POSTGRES_URL` 未設則 local 模式啟動即報錯。
- `.env.example` 進版控,機密欄位(`API_KEY`、`GEMINI_API_KEY`、`POSTGRES_PASSWORD`、`POSTGRES_URL`、`PGADMIN_EMAIL`、`PGADMIN_PASSWORD`)一律留空;`.env` 被 `.gitignore` 排除。
- AWS 憑證不在 `.env`:`aws` 模式由 compose 把本機 `~/.aws` 唯讀掛進容器,絕不 build 進 image。`AWS_ACCOUNT_ID` 與部署白名單 `DEPLOY_ALLOWED_INGRESS` 同樣只在 `.env`;`aws_setup/` 各腳本以它比對 STS 身分,防止灌進別的帳號。
- `aws_setup/resources.json`(建置腳本產出的資源 ID 清單)與 `ingest_failures.json` 不進版控,執行 01~03 會重建。

## 語料與資料(不在本 repo)

本 repo 只含程式碼與開箱示範資料,**不含競賽語料與爬蟲語料**。`preprocessing/`、`aws_setup/`、`local_setup/` 三組建庫腳本與 compose 掛載,預期語料放在與本 repo **同層的上一級目錄** `../data/`:

| 路徑 | 內容 | 誰用 |
|---|---|---|
| `../data/資料集/` | 競賽提供的相關法規 11 部、歷史訴願決定書、行政函釋、司法院釋字及行政判解(PDF) | `preprocessing/parse_*.py` 讀,`aws_setup/01_s3.py` 上傳原文 |
| `../data/output/` | 前處理產出的 chunk JSONL 與 `markdown/` 原文 | `preprocessing/` 寫;`aws_setup/02、04、06`、`local_setup/ingest.py` 讀;compose 掛進容器供「原文檢視」 |
| `../data/爬蟲集/` | 爬蟲語料:決定書、法條、參考資料的已切 chunk JSONL 與原始 PDF | `aws_setup/08、10、11`、`local_setup/ingest_crawl.py` 讀;參考資料 PDF 由 compose 掛進容器 |
| `../data/case_files/` | 執行期上傳的卷證 PDF(mock / local 模式落地,aws 模式落 S3) | `docker-compose.yml` 掛載,可用 `CASE_FILES_HOST_DIR` 改 |

路徑皆可由 `.env` 覆寫(`DATA_OUTPUT_DIR`、`CASE_FILES_HOST_DIR`、`CRAWL_*_DIR`)。沒有語料時 `mock` 模式完全不受影響。

**114 年決定書是留出測試集**,`preprocessing/parse_decisions.py`、`aws_setup/01_s3.py`、`local_setup/ingest*.py` 都以 `HOLDOUT_YEARS` 排除,不得灌進任何檢索庫;其全文 21 份已內建於 `data_show/decisions_114/` 供評測。

## 目錄

| 目錄 | 內容 |
|---|---|
| `backend/app/` | FastAPI。`main.py` 路由;`pipeline.py` F1 → 程序審查 → F3 → (F2 →) F2+ → F4 逐階段落庫;`providers/` 三模式(`aws_retrieval.py` 為 aws 模式 F2/F2+/F3 檢索與重排);`store.py` 三種案件儲存;程序審查可計算層 `deadline*.py` / `procedural_checks.py` / `notice_clause.py` / `transit.py` / `holidays.py`;`ocr.py` 掃描件逐頁轉錄;`pdf_render.py` / `docx_render.py` 決定書輸出;`throttle.py` Bedrock 速率閘;`prompts/` 提示詞;`data/` 假日表、在途期間表、法規 PCode 對照表 |
| `backend/tests/` | pytest,不需模型與憑證 |
| `backend/tools/` | `eval_ocr.py` OCR 辨識率(`AI_PROVIDER=aws` 或 `local`) |
| `frontend/` | Vite + React SPA:`/login`、`/`(案件清單)、`/new`(上傳)、`/cases/:id`(詳情:文件確認 / F1 / 程序審查 / 參考依據 / 決定書);`src/pages/`、`src/components/`、`src/styles/`;`tests/` vitest |
| `preprocessing/` | 官方 PDF → chunk JSONL + Markdown(`parse_laws / parse_decisions / parse_interpretations / parse_answers`),爬蟲補件與參考資料(`parse_crawl_*`),外部對照表抓取(`fetch_holidays / fetch_transit_table / fetch_law_urls` → `backend/app/data/`);同層 `test_parse_*.py` |
| `aws_setup/` | S3 / DynamoDB / S3 Vectors / Bedrock KB 建置與灌資料腳本 01~12(見下),`config.py` 共用常數;`tests/` |
| `local_setup/` | local 模式建索引:`ingest.py`(官方語料)、`ingest_crawl.py`(爬蟲語料,含映射層與測試);細節見 `local_setup/README.md` |
| `eval/` | 正確性評測:`answer_key.json` + `scoring.py` + `run_eval.py`;見 `eval/README.md` |
| `docker/` | 多階段 Dockerfile(前端建置 → 字型 → Python 執行層)+ compose(`web` :8000 / `postgres` :5433 / `pgadmin` :5050)+ `initdb/01_schema.sql` |
| `deploy/` | `push_ecr.ps1` 建 repo 並推 image;`ecs_fargate.py` ECS Fargate + ALB + 白名單 SG;`verify_f1.py` 對已部署站台端到端驗收;`apprunner.py` 保留未用 |
| `data_show/` | 進版控的展示資料:`sample_appeals/` mock 樣本(8 份 + `_fallback`)、`test_cases/` 合成測資九組(每組四份輸入 + 官方決定書;`example6` 另有 `_NOTEXT` 純影像版供 OCR 驗證)、`decisions_114/` 114 年決定書全文 21 份 |

## 處理流程

```
POST /api/cases(四槽 PDF)→ 建案即背景起跑
  → F1 結構化擷取(約 65 欄)→ 程序審查(訴願法 §77 逐款)→ passed?
       ├─ 否(inadmissible):F3 同類不受理案例 → F2+ 參考見解 → F4 不受理草稿(跳過 F2)
       └─ 是(admissible)  :F3 相似案例 → F2 法規推薦(候選來自 F3 案例的 law_ids)→ F2+ → F4 草稿
  → 前端逐階段編輯(PATCH f1 / screening / draft-text / decision-header / draft/result)
  → POST reanalyze?from=f1|screening|f2 線性重跑;POST finalize;GET draft.pdf / draft.docx
```

- **F3 先於 F2**:aws 模式 F2 的候選法規取自 F3 重排後 25 件案例的 `law_ids` 聯集,再加 KB-LAW `in` filter 檢索;候選為空退回全庫檢索並標示。
- **F2+** 三類(司法院釋字 / 行政函釋 / 行政法院裁判)各自獨立 KB,各取前 3,受理與不受理兩條 track 都跑,不進 F4 可引用清單。
- **重排**:F2 / F2+ / F3 都由 sonnet 自排(`prompts/rerank.txt`),每案 5 次呼叫,走 `throttle.py` 的速率閘(`BEDROCK_MIN_INTERVAL_SECONDS`,預設 1 秒)。
- **掃描件**:`pdf_extract.py` 判定可讀比例不足即走 `ocr.py` 逐頁轉錄(aws 走 Bedrock 多模態,local 走 ollama 視覺模型)。

## 測試與建置

```bash
cd backend && python -m pytest          # 後端(pytest.ini 已設 pythonpath)
cd frontend && npm test && npm run build
cd preprocessing && python -m pytest    # 解析器
cd aws_setup && python -m pytest        # 回填與增量灌入的純邏輯
cd local_setup && python -m pytest test_ingest_crawl.py -q
cd eval && python -m pytest             # 計分與執行器,不需模型
```

## 建置資料層

### aws 模式(`aws_setup/`,依序執行,皆可重跑)

| 腳本 | 作用 |
|---|---|
| `01_s3.py` | 建 bucket,上傳 `../data/` 原文 PDF 與 markdown(排除 114 年) |
| `02_dynamodb.py` | 建 `appeal_law_articles` / `appeal_cases`,匯入法條 |
| `03_vectors_kb.py` | S3 Vectors bucket / index、IAM role、KB-LAW 與 KB-CASE |
| `04_ingest.py` | 官方 chunk JSONL 灌 KB-LAW / KB-CASE |
| `05_verify.py` | DynamoDB 精查 + 兩個 KB 檢索驗收 |
| `05_reset_law_sample.py` / `06_ingest_all_laws.py` | 法規庫固定種子重置 / 全量灌入(需 `--execute` 明示) |
| `07_reference_setup.py` → `08_reference_ingest.py` → `09_reference_verify.py` | F2+ 三類 KB + DynamoDB 表:建骨架 → 灌爬蟲參考資料並上傳 PDF → 驗收 |
| `10_ingest_past_decisions.py` | 爬蟲 + 官方決定書灌 KB-CASE 與 `appeal_past_decisions`(法規引述與結語段落不灌;預設 dry-run) |
| `11_backfill_case_law_ids.py` | 把案例的 `related_laws` 解析為 `law_ids` 回填(預設 `--dry-run`,寫入加 `--apply`;須在 10 寫表後) |
| `12_ingest_laws_incremental.py` | 新法規 append-only 追加,既有 `law_id` 不變 |

所有 Bedrock 請求共用節流,嚴格低於每秒 1 次。

### local 模式(`local_setup/`)

```bash
cd local_setup && python ingest.py         # 官方語料 → law_chunks / case_chunks / answer_chunks / law_articles
python ingest_crawl.py                     # 爬蟲語料,經映射層入同幾張表
```

需 compose 的 `postgres` 已啟動、主機 ollama 已 `pull bge-m3`、`.env` 填了 `POSTGRES_URL_HOST`。

## 部署到 AWS

```powershell
.\deploy\push_ecr.ps1                                  # ECR repo(冪等)→ build → push
python deploy\ecs_fargate.py                           # ECS Fargate + ALB,SG 只放行 DEPLOY_ALLOWED_INGRESS 的 /32
python deploy\verify_f1.py --base-url http://<alb-dns> --api-key <API_KEY>   # 上傳四份 PDF → F1 驗收
```

SG 規則每次執行都會「對帳」:白名單以外的 ingress 一律撤掉。

## 評測

```bash
cd eval && AI_PROVIDER=aws python run_eval.py     # ~/.aws 需有效憑證;local 模式改 AI_PROVIDER=local
```

答案鍵 `eval/answer_key.json` 由人工從官方訴願決定書逐字抄錄,決定書未載或有歧義的欄位填 `null` 整欄不計分。
兩條線:合成卷證走真實 pipeline,量分流(受理與否 + 訴願法 §77 款次)、決定類型、F1 欄位與期間三要素;21 份 114 年決定書全文只量 F1。計分分 correct / wrong / unsure 三格,unsure 是系統自陳判斷不出來,不算失分。用法與限制見 `eval/README.md`。

## 設計原則

- **說不準就不猜**:期間算式有任何保留事項(公示送達、採訴願人自述、§98 分支、OCR 取字)就不覆寫程序審查,只標 `review_note` 交人工;§77(3) 的利害關係判斷模型指不出「法規名稱#條號」即不採信。
- **日期不由模型決定**:訴願期間由 `deadline.py` 依訴願法 §14/§16、行政程序法 §48/§74/§98 計算,國定假日與在途期間查表;寄存送達依釋字 797 當日生效,不加 10 日。所有日期欄統一為「民國114年7月4日」寫法(`dates.py`)。
- **法條不由模型生成**:F4 只能引用 F2 清單子集,程式後置過濾;修正日期一律取自 DynamoDB / KB metadata / `law_articles` 表。
- **不受理體例由程式保證**:主文固定「訴願不受理。」、事實欄不記載(訴願法 §89 I③);決定類型五值對應語料實際分布。
- **人改過的留痕**:承辦人改動 F1 / 程序審查 / 決定結果時保留模型原值(`f1_system` / `screening_system` / `f4_system`),下游過期(`f1_stale` / `screening_stale`)由輸入快照比對算出,不另立旗標。
