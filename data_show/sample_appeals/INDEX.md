# sample_appeals 索引

6 份合成訴願書測試樣本，從真實歷史訴願決定書反推而成，供 mock 模式 demo 使用（MockProvider 依訴願人姓名/機關/案由關鍵詞比對輸入文字，命中則回傳對應 expected，未命中回傳 `_fallback`）。

| 代號 | 來源決定書 | screening.passed | matched_clause | draft_type |
|---|---|---|---|---|
| a_overdue_77_1 | 110年/01.110年-社會救助事件-77(1)-逾期不補正-不受理.pdf 的副本.pdf | false | 77條第1款 | 不受理 |
| b_overdue_77_2 | 111年/03.111年-違反廢棄物清理法事件-77(2)-訴願逾期-不受理.pdf 的副本.pdf | false | 77條第2款 | 不受理 |
| c_other77_nonadmin | 112年/13.112年-違反空氣汙染防制法事件-77(8)-非行政處分-不受理.pdf 的副本.pdf | false | 77條第8款 | 不受理 |
| d_dismiss_waste | 110年/17.110年-違反廢棄物清理法事件-79I-訴願無理由-駁回.pdf 的副本.pdf | true | null(通過) | 駁回 |
| e_dismiss_air | 114年/16.114年-違反空氣汙染防制法事件-79I-訴願無理由-駁回.pdf 的副本.pdf | true | null(通過) | 駁回 |
| _fallback | (無,泛用樣本,不對應特定決定書) | true | null(通過) | 駁回 |

## 說明

- 每份樣本檔案為 `{代號}.json`，結構：`{"name","source_decision","appeal_text","expected":{"f1","screening","f2","f3","f4"}}`，欄位名逐字對齊 `DECISIONS.md` 之 Pydantic models（CaseInfo/ScreeningResult/LawRef/SimilarCase/DraftResult）。
- `appeal_text` 為反推撰寫之第一人稱訴願書全文（繁體中文），個資沿用原決定書之遮罩姓名（如「劉○誼」）。
- 不受理三案(a/b/c) 的 `expected.screening.passed = false`；駁回二案(d/e) 為程序審查通過後實體審理無理由，`passed = true`。
- `_fallback.json` 為泛用樣本，`appeal_text` 較簡短，`expected` 完整，作為 MockProvider 未命中任何樣本時的保底回應。
