# sample_appeals 索引

8 份合成訴願書測試樣本，從 110–113 年歷史訴願決定書反推而成（114 年為留出法測試集，不得作為樣本來源）（`g_` 為合成案例，見下表），供 mock 模式 demo 使用（MockProvider 依訴願人姓名/機關/案由關鍵詞比對輸入文字，命中則回傳對應 expected，未命中回傳 `_fallback`）。

| 代號 | 來源決定書 | screening.passed | matched_clause | draft_type |
|---|---|---|---|---|
| a_overdue_77_1 | 110年/01.110年-社會救助事件-77(1)-逾期不補正-不受理.pdf 的副本.pdf | false | 77條第1款 | 不受理 |
| b_overdue_77_2 | 111年/03.111年-違反廢棄物清理法事件-77(2)-訴願逾期-不受理.pdf 的副本.pdf | false | 77條第2款 | 不受理 |
| g_overdue_77_2_deposit | (合成，寄存送達逾期型；算式依行政程序法§74 與釋字797，寄存當日即生效) | false | 77條第2款 | 不受理 |
| f_overdue_77_2_dated | 111年/04.111年-違反建築法事件-77(2)-訴願逾期-不受理.pdf 的副本.pdf | false | 77條第2款 | 不受理 |
| c_other77_nonadmin | 112年/13.112年-違反空氣汙染防制法事件-77(8)-非行政處分-不受理.pdf 的副本.pdf | false | 77條第8款 | 不受理 |
| d_dismiss_waste | 110年/17.110年-違反廢棄物清理法事件-79I-訴願無理由-駁回.pdf 的副本.pdf | true | null(通過) | 駁回 |
| e_dismiss_air | 112年/15.112年-違反空氣汙染管制法事件-79I-訴願無理由-駁回.pdf 的副本.pdf | true | null(通過) | 駁回 |
| _fallback | (無,泛用樣本,不對應特定決定書) | true | null(通過) | 駁回 |

## 說明

- 每份樣本檔案為 `{代號}.json`，結構：`{"name","source_decision","appeal_text","expected":{"f1","screening","f2","f2_refs","f3","f4"}}`，欄位名逐字對齊 `backend/app/models.py`（CaseInfo/ScreeningResult/LawRef/ReferenceRef/SimilarCase/DraftResult）。`f2_refs` 為 F2+ 參考見解，引用的是 `data/資料集/` 內真實存在的函釋／釋字／裁判；MockProvider 直接以 `["f2_refs"]` 取值，樣本缺這個鍵會拋 KeyError 而不是靜默回空清單。
- `appeal_text` 為反推撰寫之第一人稱訴願書全文（繁體中文），個資沿用原決定書之遮罩姓名（如「劉○誼」）。
- 不受理三案(a/b/c) 的 `expected.screening.passed = false`；駁回二案(d/e) 為程序審查通過後實體審理無理由，`passed = true`。
- `f_overdue_77_2_dated` 的 `appeal_text` 於附註載明送達日（本人簽收）與原處分機關收文日，供 `check_deadline` 以卷內日期實際算出末日（111/8/24 送達 → 111/9/23 屆滿 → 111/9/30 提起，逾期）；其餘樣本未載日期，期間認定會回報「未計算」。
- `_fallback.json` 為泛用樣本，`appeal_text` 較簡短，`expected` 完整，作為 MockProvider 未命中任何樣本時的保底回應。
- `expected.f1` 的六個文書欄位（`receipt_date`／`service_date`／`service_method`／`disposition_fine`／`disposition_notice_clause`／`disposition_recipient`）**只填卷內講得出來的**，其餘留空字串。樣本是用來驗抽取的，替它編一個日期會讓「抽不到」永遠測不出來；前端把空值顯示為「—」，看得出是沒抽到而不是沒這一欄。
- `g_overdue_77_2_deposit` 與 `d_dismiss_waste` 是開箱示範案，`appeal_text` 自帶完整卷內日期；兩案都算得出末日，前者逾期、後者未逾期。
