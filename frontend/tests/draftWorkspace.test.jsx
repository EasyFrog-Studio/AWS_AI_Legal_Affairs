import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { api } from './apiMock.js'
import {
  doneAdmissible,
  doneInadmissible,
  doneAdmissibleSectioned,
  doneInadmissibleSectioned,
} from './fixtures.js'
import DraftWorkspace from '../src/pages/DraftWorkspace.jsx'
import { splitDraft, joinDraft } from '../src/pages/draftSections.js'

vi.mock('../src/api', async () => (await import('./apiMock.js')).api)

beforeEach(() => {
  api.updateDraftText.mockClear()
  api.updateDraftResult.mockClear()
  api.updateDecisionHeader.mockClear()
  api.downloadDraftPdf.mockClear()
  api.downloadDraftDocx.mockClear()
})

/** DraftWorkspace 的 props 由案件資料組出,直接 render 元件本身(不經 CaseDetail)。 */
function propsFrom(caseData, extra = {}) {
  return {
    caseId: caseData.case_id,
    draft: caseData.f4,
    text: caseData.draft_plain_text,
    laws: caseData.f2,
    refs: caseData.f2_refs,
    cases: caseData.f3,
    track: caseData.track,
    header: caseData.decision_header,
    versionCount: caseData.draft_versions?.length ?? 0,
    onViewSource: vi.fn(),
    onSaved: vi.fn(),
    ...extra,
  }
}

function renderWorkspace(caseData, extra = {}) {
  return render(<DraftWorkspace {...propsFrom(caseData, extra)} />)
}

// 新格式本文(表頭與結尾已搬到 decision_header,不再混在全文裡);doneAdmissibleSectioned/
// doneInadmissibleSectioned 的 draft_plain_text 仍是舊格式全文,分段測試改用這兩份覆寫 text。
const SECTIONED_ADMISSIBLE_TEXT = [
  '主　文',
  '訴願駁回。',
  '',
  '事　實',
  '訴願人於113年5月1日經稽查違反廢棄物清理法規定，原處分機關依法裁處罰鍰。',
  '',
  '理　由',
  '按訴願法第79條第1項規定，訴願無理由者，應以決定駁回之。本件原處分認事用法並無違誤，應予駁回。',
].join('\n')

const SECTIONED_INADMISSIBLE_TEXT = [
  '主　文',
  '訴願不受理。',
  '',
  '理　由',
  '訴願人提起本件訴願，已逾訴願法第14條第1項所定30日之法定期間，依同法第77條第2款規定，應為不受理之決定。',
].join('\n')

describe('決定書版面', () => {
  it('版本衝突(409)時保留使用者輸入並呼叫 onSaved', async () => {
    const conflict = Object.assign(new Error('這份草稿已被他人更新,請重新載入後再改'), { status: 409 })
    api.updateDraftText.mockRejectedValueOnce(conflict)
    const onSaved = vi.fn()
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible, { onSaved })

    await user.type(screen.getByLabelText('決定書全文'), '我打的字')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(await screen.findByText(/已被他人更新/)).toBeInTheDocument()
    expect(screen.getByLabelText('決定書全文')).toHaveValue(
      `${doneAdmissible.draft_plain_text}我打的字`,
    ) // 不沖掉他打的字
    expect(onSaved).toHaveBeenCalledTimes(1)
  })

  it('草稿頁只有三個動作:儲存與兩種下載,不再有定稿與版本字樣', () => {
    renderWorkspace(doneAdmissible)

    expect(screen.queryByRole('button', { name: /定稿/ })).toBeNull()
    expect(screen.queryByText(/已存/)).toBeNull()
    expect(screen.queryByText(/定稿於/)).toBeNull()
  })

  it('引用法條逐條一格,可改可增可刪,儲存時一併送出', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneInadmissible, {
      draft: { ...doneInadmissible.f4, cited_laws: ['訴願法#77', '訴願法#14'] },
    })

    expect(screen.getByLabelText('引用法條 1')).toHaveValue('訴願法#77')
    expect(screen.getByLabelText('引用法條 2')).toHaveValue('訴願法#14')

    await user.click(screen.getByRole('button', { name: '刪除引用法條 2' }))
    await user.click(screen.getByRole('button', { name: '新增法條' }))
    await user.type(screen.getByLabelText('引用法條 2'), '行政程序法#92')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(api.updateDraftText).toHaveBeenCalledWith('c-2', {
      text: doneInadmissible.draft_plain_text,
      cited_laws: ['訴願法#77', '行政程序法#92'],
      base_version: 0,
    })
  })

  it('沒有引用法條時仍可新增', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    await user.click(screen.getByRole('button', { name: '新增法條' }))
    await user.type(screen.getByLabelText('引用法條 1'), '訴願法#79')

    expect(screen.getByRole('button', { name: '儲存修改' })).not.toHaveAttribute(
      'aria-disabled',
      'true',
    )
  })

  it('沒有修改時儲存鍵不改色,點了說沒有可儲存的修改', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    const save = screen.getByRole('button', { name: '儲存修改' })
    expect(save).toHaveAttribute('aria-disabled', 'true')
    expect(save).toHaveClass('btn-primary', 'btn-submit')
    await user.click(save)

    expect(api.updateDraftText).not.toHaveBeenCalled()
    expect(screen.getByText('沒有可儲存的修改')).toBeInTheDocument()
  })
})

describe('決定書草稿:整份可改、兩種下載', () => {
  it('決定書是一個文字框,存檔送 draft-text', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible, { text: '新北市政府訴願決定書 主文:訴願駁回。' })

    const box = screen.getByLabelText('決定書全文')
    expect(box).toHaveValue('新北市政府訴願決定書 主文:訴願駁回。')
    await user.type(box, ' 補一句')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    // base_version 一起送:兩個視窗同時改時,後送出的那份不該無聲蓋掉前一份
    expect(api.updateDraftText).toHaveBeenCalledWith('c-1', {
      text: '新北市政府訴願決定書 主文:訴願駁回。 補一句',
      cited_laws: [],
      base_version: 0,
    })
    // 逐欄輸入已經不存在:文件只有一份
    expect(screen.queryByLabelText('事實')).toBeNull()
  })

  it('可下載 PDF 與 Word', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    await user.click(screen.getByRole('button', { name: '下載 Word' }))
    expect(api.downloadDraftDocx).toHaveBeenCalledWith('c-1')

    await user.click(screen.getByRole('button', { name: '下載 PDF' }))
    expect(api.downloadDraftPdf).toHaveBeenCalledWith('c-1')
  })

  it('有未儲存的修改時不給下載,點了只提示尚未儲存', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)
    await user.type(screen.getByLabelText('決定書全文'), '改一句')

    const word = screen.getByRole('button', { name: '下載 Word' })
    expect(word).toHaveAttribute('aria-disabled', 'true')
    await user.click(word)
    expect(api.downloadDraftDocx).not.toHaveBeenCalled()
    expect(screen.getByText('尚未儲存修改')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '下載 PDF' }))
    expect(api.downloadDraftPdf).not.toHaveBeenCalled()
  })

  it('儲存之後就能下載', async () => {
    const user = userEvent.setup()
    const onSaved = vi.fn()
    const { rerender } = renderWorkspace(doneAdmissible, { onSaved })
    const box = screen.getByLabelText('決定書全文')
    await user.type(box, '改一句')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))
    expect(onSaved).toHaveBeenCalledTimes(1)

    // 模擬外層重新載入後傳回已儲存的全文,dirty 隨之清空
    rerender(
      <DraftWorkspace
        {...propsFrom(doneAdmissible, {
          onSaved,
          text: `${doneAdmissible.draft_plain_text}改一句`,
        })}
      />,
    )

    await waitFor(() =>
      expect(screen.getByRole('button', { name: '下載 PDF' })).not.toHaveAttribute(
        'aria-disabled',
        'true',
      ),
    )
    await user.click(screen.getByRole('button', { name: '下載 PDF' }))
    expect(api.downloadDraftPdf).toHaveBeenCalledWith('c-1')
  })
})

describe('決定書草稿分段', () => {
  it('受理案件分成主文、事實、理由三格，表頭與結尾是結構化區塊不是文字框', () => {
    renderWorkspace(doneAdmissibleSectioned, { text: SECTIONED_ADMISSIBLE_TEXT })

    const sections = splitDraft(SECTIONED_ADMISSIBLE_TEXT)
    expect(screen.getByLabelText('主文')).toHaveValue(sections.find((s) => s.key === 'main').body)
    expect(screen.getByLabelText('事實')).toHaveValue(sections.find((s) => s.key === 'fact').body)
    expect(screen.getByLabelText('理由')).toHaveValue(sections.find((s) => s.key === 'reason').body)
    expect(screen.getByRole('region', { name: '決定書表頭' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '決定書結尾' })).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: '決定書表頭' })).toBeNull()
    expect(screen.queryByRole('textbox', { name: '決定書結尾' })).toBeNull()
    expect(screen.queryByLabelText('決定書全文')).toBeNull()
  })

  it('不受理案件沒有「事實」格，其餘照常', () => {
    renderWorkspace(doneInadmissibleSectioned, { text: SECTIONED_INADMISSIBLE_TEXT })

    expect(screen.getByLabelText('主文')).toBeInTheDocument()
    expect(screen.queryByLabelText('事實')).toBeNull()
    expect(screen.getByLabelText('理由')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '決定書結尾' })).toBeInTheDocument()
  })

  it('在理由末尾打字後儲存，送出的全文是 join 回去的完整逐字結果', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissibleSectioned, { text: SECTIONED_ADMISSIBLE_TEXT })

    const reasonBox = screen.getByLabelText('理由')
    await user.type(reasonBox, '，本件另予敘明。')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    const original = splitDraft(SECTIONED_ADMISSIBLE_TEXT)
    const expectedText = joinDraft(
      original.map((s) => (s.key === 'reason' ? { ...s, body: s.body + '，本件另予敘明。' } : s)),
    )
    expect(api.updateDraftText).toHaveBeenCalledWith('c-7', {
      text: expectedText,
      cited_laws: [],
      base_version: 0,
    })
  })

  it('儲存失敗(版本衝突)時畫面顯示錯誤，理由格保留剛打的字', async () => {
    api.updateDraftText.mockRejectedValueOnce(Object.assign(new Error('版本衝突'), { status: 409 }))
    const user = userEvent.setup()
    renderWorkspace(doneAdmissibleSectioned, { text: SECTIONED_ADMISSIBLE_TEXT })

    const reasonBox = screen.getByLabelText('理由')
    await user.type(reasonBox, '我打的字')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(await screen.findByText('版本衝突')).toBeInTheDocument()
    expect(screen.getByLabelText('理由')).toHaveValue(
      splitDraft(SECTIONED_ADMISSIBLE_TEXT).find((s) => s.key === 'reason').body + '我打的字',
    )
  })
})

describe('草稿頁的參考依據欄', () => {
  it('法規、參考見解、案例三組都在同一欄,與階段頁一致', () => {
    renderWorkspace(doneAdmissible)

    const panel = screen.getByRole('complementary', { name: '承辦參考依據' })
    expect(within(panel).getByText('參考法規（F2）')).toBeInTheDocument()
    expect(within(panel).getByText('參考見解（F2+）')).toBeInTheDocument()
    expect(within(panel).getByText('參考案例（F3）')).toBeInTheDocument()
    expect(within(panel).getByText('釋字第469號')).toBeInTheDocument()
  })

  it('不受理案件隱藏法規那一組,參考見解與案例照常', () => {
    renderWorkspace(doneInadmissible)

    const panel = screen.getByRole('complementary', { name: '承辦參考依據' })
    expect(within(panel).queryByText('參考法規（F2）')).toBeNull()
    expect(within(panel).getByText('參考見解（F2+）')).toBeInTheDocument()
    expect(within(panel).getByText('參考案例（F3）')).toBeInTheDocument()
  })
})

describe('決定結果修改', () => {
  it('選了新結果要按儲存修改才送出', async () => {
    const onSaved = vi.fn()
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible, { onSaved })

    await user.selectOptions(screen.getByLabelText('結果'), '撤銷另處')
    expect(api.updateDraftResult).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(api.updateDraftResult).toHaveBeenCalledWith('c-1', { draft_type: '撤銷另處' })
    expect(onSaved).toHaveBeenCalledTimes(1)
  })

  it('只改結果時不動全文,不多存一版', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    await user.selectOptions(screen.getByLabelText('結果'), '原處分撤銷')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(api.updateDraftText).not.toHaveBeenCalled()
    expect(api.updateDraftResult).toHaveBeenCalledWith('c-1', { draft_type: '原處分撤銷' })
  })

  it('全文與結果一起改,兩支 API 都送', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    await user.type(screen.getByLabelText('決定書全文'), '補一句')
    await user.selectOptions(screen.getByLabelText('結果'), '撤銷另處')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(api.updateDraftText).toHaveBeenCalledWith('c-1', {
      text: `${doneAdmissible.draft_plain_text}補一句`,
      cited_laws: [],
      base_version: 0,
    })
    expect(api.updateDraftResult).toHaveBeenCalledWith('c-1', { draft_type: '撤銷另處' })
  })

  it('改過的案件只顯示現行結果,不在稿紙上談系統原判', () => {
    renderWorkspace(doneAdmissible, {
      draft: { ...doneAdmissible.f4, draft_type: '撤銷另處' },
    })

    expect(screen.getByLabelText('結果')).toHaveValue('撤銷另處')
    expect(screen.queryByText(/系統原判/)).toBeNull()
  })

  it('儲存失敗時錯誤看得見,選單留著承辦人挑的值不回捲', async () => {
    api.updateDraftResult.mockRejectedValueOnce(new Error('案件正在分析中'))
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    await user.selectOptions(screen.getByLabelText('結果'), '撤銷另處')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(await screen.findByText('案件正在分析中')).toBeInTheDocument()
    expect(screen.getByLabelText('結果')).toHaveValue('撤銷另處')
  })

  it('未儲存的結果修改擋住下載', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    await user.selectOptions(screen.getByLabelText('結果'), '撤銷另處')
    await user.click(screen.getByRole('button', { name: '下載 PDF' }))

    expect(api.downloadDraftPdf).not.toHaveBeenCalled()
    expect(screen.getByText('尚未儲存修改')).toBeInTheDocument()
  })
})

describe('決定書表頭與結尾', () => {
  it('表頭九欄與結尾三欄都能以 label 找到', () => {
    renderWorkspace(doneAdmissible)

    ;[
      '案號',
      '要旨',
      '發文日期',
      '發文字號',
      '相關法條',
      '訴願人',
      '代理人或送達代收人',
      '代理人姓名',
      '原處分機關',
      '主任委員',
      '委員',
      '決定日期',
    ].forEach((label) => {
      expect(screen.getByLabelText(label)).toBeInTheDocument()
    })
  })

  it('發文日期與決定日期是三格 combobox，沒有同名 textbox', () => {
    renderWorkspace(doneAdmissible)

    expect(screen.getByRole('combobox', { name: '發文日期年' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '發文日期月' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '發文日期日' })).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: '發文日期' })).toBeNull()
    expect(screen.getByRole('combobox', { name: '決定日期年' })).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: '決定日期' })).toBeNull()
  })

  it('只改案號時儲存只送表頭,不送全文', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    await user.clear(screen.getByLabelText('案號'))
    await user.type(screen.getByLabelText('案號'), '新')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(api.updateDecisionHeader).toHaveBeenCalledWith('c-1', {
      ...doneAdmissible.decision_header,
      case_no: '新',
    })
    expect(api.updateDraftText).not.toHaveBeenCalled()
  })

  it('案號與全文一起改,兩支 API 都送且表頭先送', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    await user.clear(screen.getByLabelText('案號'))
    await user.type(screen.getByLabelText('案號'), '新')
    await user.type(screen.getByLabelText('決定書全文'), '補一句')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(api.updateDecisionHeader).toHaveBeenCalledWith('c-1', {
      ...doneAdmissible.decision_header,
      case_no: '新',
    })
    expect(api.updateDraftText).toHaveBeenCalledWith('c-1', {
      text: `${doneAdmissible.draft_plain_text}補一句`,
      cited_laws: [],
      base_version: 0,
    })
    expect(api.updateDecisionHeader.mock.invocationCallOrder[0]).toBeLessThan(
      api.updateDraftText.mock.invocationCallOrder[0],
    )
  })

  it('updateDecisionHeader 失敗時顯示錯誤,不送全文,輸入保留', async () => {
    api.updateDecisionHeader.mockRejectedValueOnce(new Error('儲存表頭失敗'))
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    await user.clear(screen.getByLabelText('案號'))
    await user.type(screen.getByLabelText('案號'), '新')
    await user.type(screen.getByLabelText('決定書全文'), '補一句')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(await screen.findByText('儲存表頭失敗')).toBeInTheDocument()
    expect(api.updateDraftText).not.toHaveBeenCalled()
    expect(screen.getByLabelText('案號')).toHaveValue('新')
    expect(screen.getByLabelText('決定書全文')).toHaveValue(
      `${doneAdmissible.draft_plain_text}補一句`,
    )
  })

  it('代理人或送達代收人可選並填姓名,一起送出', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    await user.selectOptions(screen.getByLabelText('代理人或送達代收人'), '代理人')
    await user.type(screen.getByLabelText('代理人姓名'), '林○○')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(api.updateDecisionHeader).toHaveBeenCalledWith('c-1', {
      ...doneAdmissible.decision_header,
      agent_role: '代理人',
      agent_name: '林○○',
    })
  })

  it('表頭有未儲存修改時下載鍵也 aria-disabled', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    await user.clear(screen.getByLabelText('案號'))
    await user.type(screen.getByLabelText('案號'), '新')

    expect(screen.getByRole('button', { name: '下載 PDF' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
  })

  it('輪詢換回未變的表頭不蓋掉使用者正在編輯但尚未儲存的欄位;換了的欄位仍會更新', async () => {
    const user = userEvent.setup()
    const { rerender } = renderWorkspace(doneAdmissible)

    await user.clear(screen.getByLabelText('案號'))
    await user.type(screen.getByLabelText('案號'), '正在打的字')

    // 模擬重跑或另一視窗改了要旨,案號在後端仍是舊值:案號的編輯不該被蓋掉,要旨要跟著更新
    rerender(
      <DraftWorkspace
        {...propsFrom(doneAdmissible, {
          header: { ...doneAdmissible.decision_header, gist: '因違反環保法規事件提起訴願' },
        })}
      />,
    )

    expect(screen.getByLabelText('案號')).toHaveValue('正在打的字')
    expect(screen.getByLabelText('要旨')).toHaveValue('因違反環保法規事件提起訴願')
  })

  it('原處分撤銷與撤銷另處不印教示條款,其餘結果顯示', () => {
    const { rerender } = renderWorkspace(doneAdmissible, {
      draft: { ...doneAdmissible.f4, draft_type: '原處分撤銷' },
    })
    expect(screen.queryByText(/臺北高等行政法院/)).toBeNull()

    rerender(
      <DraftWorkspace
        {...propsFrom(doneAdmissible, { draft: { ...doneAdmissible.f4, draft_type: '撤銷另處' } })}
      />,
    )
    expect(screen.queryByText(/臺北高等行政法院/)).toBeNull()

    rerender(
      <DraftWorkspace
        {...propsFrom(doneAdmissible, { draft: { ...doneAdmissible.f4, draft_type: '駁回' } })}
      />,
    )
    expect(screen.getByText(/臺北高等行政法院/)).toBeInTheDocument()
  })
})
