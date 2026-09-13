import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
    screening: caseData.screening,
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

  it('稿紙底部沒有引用法條清單:法條只在基本資訊的「相關法條」出現一次', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneInadmissible, {
      draft: { ...doneInadmissible.f4, cited_laws: ['訴願法#77', '訴願法#14'] },
    })

    expect(screen.queryByLabelText('引用法條 1')).toBeNull()
    expect(screen.queryByRole('button', { name: '新增法條' })).toBeNull()
    expect(screen.queryByText('引用法條')).toBeNull()

    // 存檔不再帶 cited_laws:畫面上改不到它,送出等於拿舊值覆寫後端現值
    await user.type(screen.getByLabelText('決定書全文'), '補一句')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(api.updateDraftText).toHaveBeenCalledWith('c-2', {
      text: `${doneInadmissible.draft_plain_text}補一句`,
      base_version: 0,
    })
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
  it('受理案件分成主文、事實、理由三格，基本資訊與結尾各自是一個文字框', () => {
    renderWorkspace(doneAdmissibleSectioned, { text: SECTIONED_ADMISSIBLE_TEXT })

    const sections = splitDraft(SECTIONED_ADMISSIBLE_TEXT)
    expect(screen.getByLabelText('主文')).toHaveValue(sections.find((s) => s.key === 'main').body)
    expect(screen.getByLabelText('事實')).toHaveValue(sections.find((s) => s.key === 'fact').body)
    expect(screen.getByLabelText('理由')).toHaveValue(sections.find((s) => s.key === 'reason').body)
    expect(screen.getByRole('textbox', { name: '基本資訊' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '決定書結尾' })).toBeInTheDocument()
    expect(screen.queryByLabelText('決定書全文')).toBeNull()
  })

  it('不受理案件沒有「事實」格，其餘照常', () => {
    renderWorkspace(doneInadmissibleSectioned, { text: SECTIONED_INADMISSIBLE_TEXT })

    expect(screen.getByLabelText('主文')).toBeInTheDocument()
    expect(screen.queryByLabelText('事實')).toBeNull()
    expect(screen.getByLabelText('理由')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '決定書結尾' })).toBeInTheDocument()
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
  it('法規、三類參考見解、案例各自一組,與階段頁一致', () => {
    renderWorkspace(doneAdmissible)

    const panel = screen.getByRole('complementary', { name: '承辦參考依據' })
    expect(within(panel).getByText('參考法規（F2）')).toBeInTheDocument()
    expect(within(panel).getByText('司法院釋字（F2+）')).toBeInTheDocument()
    expect(within(panel).getByText('行政函釋（F2+）')).toBeInTheDocument()
    expect(within(panel).getByText('行政法院裁判（F2+）')).toBeInTheDocument()
    expect(within(panel).queryByText('參考見解（F2+）')).toBeNull()
    expect(within(panel).getByText('參考案例（F3）')).toBeInTheDocument()
    expect(within(panel).getByText('釋字第469號')).toBeInTheDocument()
  })

  it('見解依類別各歸各組,缺的那一類說自己那一類沒有', () => {
    renderWorkspace(doneAdmissible, {
      refs: doneAdmissible.f2_refs.filter((r) => r.doc_kind === '行政函釋'),
    })

    const panel = screen.getByRole('complementary', { name: '承辦參考依據' })
    const group = (heading) => within(panel).getByText(heading).closest('.basis-panel__group')
    expect(
      within(group('行政函釋（F2+）')).getByText('法務部 法律字第0930014628號'),
    ).toBeInTheDocument()
    expect(within(group('司法院釋字（F2+）')).getByText('未檢索到相關釋字。')).toBeInTheDocument()
    expect(
      within(group('行政法院裁判（F2+）')).getByText('未檢索到相關法院裁判。'),
    ).toBeInTheDocument()
  })

  it('檢索中(refs 為 null)時三類各自說檢索中', () => {
    renderWorkspace(doneAdmissible, { refs: null })

    const panel = screen.getByRole('complementary', { name: '承辦參考依據' })
    expect(within(panel).getAllByText('檢索中…')).toHaveLength(3)
  })

  it('不受理案件的法規那一組照常出現,內容說明為何沒有推薦', () => {
    renderWorkspace(doneInadmissible)

    const panel = screen.getByRole('complementary', { name: '承辦參考依據' })
    // toBeVisible 而非 toBeInTheDocument:要求是「顯示」,隱藏起來的元素仍在 DOM 裡
    expect(within(panel).getByText('參考法規（F2）')).toBeVisible()
    expect(
      within(panel).getByText(
        '本案經程序審查認定不受理，依訴願法第 77 條第 2 款逕為不受理決定，未進行法規推薦。',
      ),
    ).toBeVisible()
    expect(within(panel).queryByText('未檢索到相關法規。')).toBeNull()
    expect(within(panel).getByText('司法院釋字（F2+）')).toBeInTheDocument()
    expect(within(panel).getByText('行政函釋（F2+）')).toBeInTheDocument()
    expect(within(panel).getByText('行政法院裁判（F2+）')).toBeInTheDocument()
    expect(within(panel).getByText('參考案例（F3）')).toBeInTheDocument()
  })

  it('不受理但解析不出款次時,說明不寫死款次', () => {
    renderWorkspace(doneInadmissible, { screening: { passed: false, matched_clause: null } })

    const panel = screen.getByRole('complementary', { name: '承辦參考依據' })
    expect(
      within(panel).getByText(
        '本案經程序審查認定不受理，依訴願法第 77 條逕為不受理決定，未進行法規推薦。',
      ),
    ).toBeInTheDocument()
  })

  it('不受理案件的法規那一組不跟著檢索狀態走:laws 為 null 也不顯示檢索中', () => {
    renderWorkspace(doneInadmissible, { laws: null })

    const panel = screen.getByRole('complementary', { name: '承辦參考依據' })
    const group = within(panel).getByText('參考法規（F2）').closest('.basis-panel__group')
    expect(within(group).queryByText('檢索中…')).toBeNull()
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

describe('稿紙版面', () => {
  it('依基本資訊、本文、結尾排序,沒有「全文」與「引用法條」這兩個標題', () => {
    renderWorkspace(doneAdmissibleSectioned, { text: SECTIONED_ADMISSIBLE_TEXT })

    const paper = screen.getByRole('group', { name: '決定書稿紙' })
    expect(
      within(paper)
        .getAllByText(/^(基本資訊|全文|引用法條)$/)
        .map((node) => node.textContent),
    ).toEqual(['基本資訊'])

    const footer = screen.getByLabelText('決定書結尾')
    const main = screen.getByLabelText('主文')
    expect(main.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})

describe('決定書表頭與結尾:整段純文字', () => {
  it('基本資訊與結尾各是一個文字框,十二欄逐列寫成「欄名：」', () => {
    renderWorkspace(doneAdmissible)

    const info = screen.getByLabelText('基本資訊')
    ;['案　　號：　', '要　　旨：　', '發文日期：　', '發文字號：　', '相關法條：　', '　　訴願人　', '　　代理人　', '　　原處分機關　'].forEach(
      (label) => expect(info.value).toContain(label),
    )
    const footer = screen.getByLabelText('決定書結尾')
    ;['訴願審議委員會主任委員　', '委員　', '中華民國　'].forEach((label) =>
      expect(footer.value).toContain(label),
    )
    // 逐欄的輸入框與日期選擇器都不在了:編輯單位是這兩段文字
    expect(screen.queryByRole('combobox', { name: '發文日期年' })).toBeNull()
    expect(screen.queryByRole('textbox', { name: '案號' })).toBeNull()
  })

  it('只改案號那一列時儲存只送表頭,其餘欄位原樣回存', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    const info = screen.getByLabelText('基本資訊')
    fireEvent.change(info, { target: { value: info.value.replace('案　　號：　', '案　　號：　1141061379') } })
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(api.updateDecisionHeader).toHaveBeenCalledWith('c-1', {
      ...doneAdmissible.decision_header,
      case_no: '1141061379',
    })
    expect(api.updateDraftText).not.toHaveBeenCalled()
  })

  it('多行欄位照打:相關法條與委員一行一筆,存回去仍是同一欄', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    const info = screen.getByLabelText('基本資訊')
    fireEvent.change(info, {
      target: {
        value: info.value.replace('相關法條：　', '相關法條：　訴願法 第 79 條\n　　　　　行政罰法 第 18 條'),
      },
    })
    const footer = screen.getByLabelText('決定書結尾')
    fireEvent.change(footer, {
      target: { value: ['主任委員：', '委員：王○○', '委員：李○○', '中華民國：'].join('\n') },
    })
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(api.updateDecisionHeader).toHaveBeenCalledWith('c-1', {
      ...doneAdmissible.decision_header,
      related_laws: '訴願法 第 79 條\n行政罰法 第 18 條',
      committee: '王○○\n李○○',
    })
  })

  it('代理人那一列改成送達代收人,身分與姓名一起送出', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    const info = screen.getByLabelText('基本資訊')
    fireEvent.change(info, { target: { value: info.value.replace('　　代理人　', '　　送達代收人　林○○') } })
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(api.updateDecisionHeader).toHaveBeenCalledWith('c-1', {
      ...doneAdmissible.decision_header,
      agent_role: '送達代收人',
      agent_name: '林○○',
    })
  })

  it('欄名打錯的行擋下整次儲存,不讓那一行被丟掉', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    const info = screen.getByLabelText('基本資訊')
    fireEvent.change(info, { target: { value: `${info.value}\n案虎：打錯的欄名` } })
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(await screen.findByText(/認不出是哪一欄/)).toBeInTheDocument()
    expect(api.updateDecisionHeader).not.toHaveBeenCalled()
    expect(screen.getByLabelText('基本資訊').value).toContain('案虎：打錯的欄名')
  })

  it('表頭與全文一起改,兩支 API 都送且表頭先送', async () => {
    const user = userEvent.setup()
    renderWorkspace(doneAdmissible)

    const info = screen.getByLabelText('基本資訊')
    fireEvent.change(info, { target: { value: info.value.replace('案　　號：　', '案　　號：　新') } })
    await user.type(screen.getByLabelText('決定書全文'), '補一句')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(api.updateDecisionHeader).toHaveBeenCalledWith('c-1', {
      ...doneAdmissible.decision_header,
      case_no: '新',
    })
    expect(api.updateDraftText).toHaveBeenCalledWith('c-1', {
      text: `${doneAdmissible.draft_plain_text}補一句`,
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

    const info = screen.getByLabelText('基本資訊')
    fireEvent.change(info, { target: { value: info.value.replace('案　　號：　', '案　　號：　新') } })
    await user.type(screen.getByLabelText('決定書全文'), '補一句')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(await screen.findByText('儲存表頭失敗')).toBeInTheDocument()
    expect(api.updateDraftText).not.toHaveBeenCalled()
    expect(screen.getByLabelText('基本資訊').value).toContain('案　　號：　新')
    expect(screen.getByLabelText('決定書全文')).toHaveValue(
      `${doneAdmissible.draft_plain_text}補一句`,
    )
  })

  it('表頭有未儲存修改時下載鍵也 aria-disabled', () => {
    renderWorkspace(doneAdmissible)

    const info = screen.getByLabelText('基本資訊')
    fireEvent.change(info, { target: { value: info.value.replace('案　　號：　', '案　　號：　新') } })

    expect(screen.getByRole('button', { name: '下載 PDF' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
  })

  it('沒在編輯時輪詢回來的表頭跟著更新;正在編輯就不被蓋掉', () => {
    const { rerender } = renderWorkspace(doneAdmissible)

    rerender(
      <DraftWorkspace
        {...propsFrom(doneAdmissible, {
          header: { ...doneAdmissible.decision_header, gist: '因違反環保法規事件提起訴願' },
        })}
      />,
    )
    expect(screen.getByLabelText('基本資訊').value).toContain('要　　旨：　因違反環保法規事件提起訴願')

    const info = screen.getByLabelText('基本資訊')
    fireEvent.change(info, { target: { value: info.value.replace('案　　號：　', '案　　號：　正在打的字') } })
    rerender(
      <DraftWorkspace
        {...propsFrom(doneAdmissible, {
          header: { ...doneAdmissible.decision_header, gist: '另一個人改的要旨' },
        })}
      />,
    )
    expect(screen.getByLabelText('基本資訊').value).toContain('案　　號：　正在打的字')
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
