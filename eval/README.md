# eval — 正確性評測

拿**官方訴願決定書**當答案,量這套系統在真實卷證上答對多少。與 `backend/tests/` 不同:
那些測的是程式邏輯,這裡測的是模型在真實文件上的表現。

## 為什麼不進 pytest

評測需呼叫真實模型(aws 模式的 Bedrock 或 local 模式的 ollama),而 `python -m pytest`
(backend/、eval/)必須在沒有模型與憑證的機器上也跑得完。故手動執行。

## 執行

```bash
cd AWS_AI_Legal_Affairs/eval
AI_PROVIDER=aws python run_eval.py                 # 兩條線全跑;線1 每組約 70 秒(F1 與 F4 各佔近半)
AI_PROVIDER=aws python run_eval.py --only example4 # 只跑一組,除錯用
AI_PROVIDER=aws python run_eval.py --only 07 --skip-cases   # --skip-cases / --skip-decisions 各關掉一條線
python -m pytest                                   # 計分與執行器的測試,不需模型或憑證
```

前置(aws 模式):`~/.aws/credentials` 有效憑證,region 見 `AWS_REGION`。
前置(local 模式,`AI_PROVIDER=local`):主機已 `ollama serve` 且已 pull `LOCAL_LLM_MODEL`,`.env` 的
`POSTGRES_URL_HOST` 已填(F2/F3 會真的查庫,`POSTGRES_URL` 那個位址只有容器內解析得到);ollama 一次
只跑一個生成,評測跑起來會把同時使用網站的請求擠成 timeout,測站前先確認 `/api/ps` 是空的。

卷證預設讀 `data_show/test_cases`,114 年決定書全文預設讀 `data_show/decisions_114`,兩者皆已內建於
repo;語料不在預設位置時可用 `--cases`/`--decisions` 覆寫。報告寫到 `eval/reports/`
(`.gitignore` 排除,不累積歷史,`--out` 可覆寫路徑):`eval_report.md`(給人看)與
`eval_report.json`(給程式讀)。

## 答案從哪裡來

`answer_key.json`,人工從官方決定書逐字抄出來,寫死進版控。三條規則:

1. **只採信官方訴願決定書逐字記載。** 我方的反推筆記(`出處與反推依據.md`)與去識別化對照表
   都不作答案來源——那是我們自己寫的,拿它當答案等於自己出題自己改。
2. **決定書沒寫或寫得有歧義的欄位填 `null`,整欄不計分**,理由寫在同層 `_note`。
   例:`example5` 的決定書同時記載兩個文號,那一欄無法判對錯。
3. **執行期不解析 md。** 答案是人抄的,不是程式從文件推的;推錯了兩邊會一起錯。

`○` 遮罩的欄位(訴願人、代理人)一律不列入——那些字系統本來就抽不到完整值。

## 兩條線

**線1:8 組合成卷證**(`data_show/test_cases/example1..8`,各四份 PDF)。走真實的
`pipeline.run_case`,六層全跑真貨,量四層:程序審查分流(受理與否 + 訴願法§77 款次)、
決定類型、F1 四欄、期間三要素(送達生效日 / 末日 / 機關收文日)。F2/F2+/F3 的檢索結果
不單獨計分,但**必須真的跑**——F4 的輸入就是它們,抽掉等於量一條產品上不存在的路徑。
同理不繞過 pipeline:期間覆寫、§77(1)(3) 後置檢核、款次守門都住在 pipeline 裡。

決定類型這一層要看清楚它量的是什麼:不受理那一側的 `draft_type` 由
`enforce_inadmissible_format` 依分流結果決定,不是 F4 自己判的,所以這個數字是
「最後送到承辦人手上的決定類型對不對」,不等於 F4 的獨立準確率。

**線2:21 份真實 114 年決定書全文**(`data_show/decisions_114/`)。只量 F1 對
真實文本的擷取與案類分類。**刻意不量分流**:決定書的理由欄逐字寫著「依訴願法第 77 條第 2 款」,
把它抄回來不代表系統會判——那種題目量的是抄寫能力,不是判斷能力。

答案鍵第 20 份的 `case_type` 填 `null` 不計分:12 與 20 兩份官方決定書對同一類事件分別寫「申請提供政府資訊」與「申請政府資訊」,官方文書自己就有兩種寫法,沒有唯一正解,依 `_authority` 的「記載有歧義即不計分」處理。

線2 餵的是整份決定書全文,沒有【訴願書】那類分段標頭,與產品實際的四槽輸入不是同一個形狀。決定書自己的發文機關(新北市政府)、發文日期與發文字號排在最顯眼的位置,`agency`/`disposition_date`/`disposition_no` 三欄極易抽成決定書本身而不是原處分,因此 `prompts/f1_extract.txt` 另立一段決定書規則要求取「不服原處分機關…」那一段。

## 三格計分

| 格 | 意義 |
|---|---|
| ✅ correct | 與決定書記載相符 |
| ❌ wrong | 抽錯或判錯,**這一格才是風險** |
| ⚠️ unsure | 系統自陳判斷不出來(填「未載明」、標了 `review_note`) |

unsure 不是失分:系統知道自己不知道,與亂猜是兩回事,承辦人看到的是一個待確認標記而不是一個
錯誤結論。因此程序審查只要帶 `review_note`,結論即使碰巧與答案相同也不記 correct——它並沒有
主張那個結論。

比對前會正規化:全半形、各種連字號、民國日期寫法、文號末尾省略的「號」。這些是同一份文書的
不同寫法,不是抽錯(見 `test_scoring.py`)。案類另脫掉「違反…事件」的外殼再比等值,不做包含比對——包含比對會讓整段抄寫的失控輸出只因為裡面出現法規名就記成答對。

跑不完的組別(模型逾時、輸出失控)不計入題數,單獨列在報告的「執行失敗」段——把它算成答錯會
讓失敗看起來像一個普通的判斷錯誤。

## 檔案

| 檔 | 作用 |
|---|---|
| `answer_key.json` | 人工抄錄的答案鍵,唯一真相 |
| `scoring.py` | 比對與計分的純函式,不碰 IO、不呼叫模型 |
| `test_scoring.py` / `test_run_eval.py` | 計分邏輯與執行器計分段的測試(不需 ollama) |
| `run_eval.py` | 執行器:讀卷證 → 跑 pipeline → 計分 → 出報告 |
