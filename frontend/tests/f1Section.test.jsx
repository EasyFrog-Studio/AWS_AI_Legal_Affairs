import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, waitFor, act, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { api } from './apiMock.js'
import { doneAdmissible } from './fixtures.js'
import { F1Section } from '../src/pages/F1Section.jsx'
import { F1_GROUPS } from '../src/components/documentSlots.js'

vi.mock('../src/api', async () => (await import('./apiMock.js')).api)

// F1_GROUPS 是欄位權威,測試從這裡推導全欄位骨架與清單欄位鍵,不在測試裡另抄一份 66 欄的清單
const ALL_FIELDS = F1_GROUPS.flatMap((g) => g.sections.flatMap((s) => s.fields))
const LIST_KEYS = new Set(ALL_FIELDS.filter((f) => f.kind === 'list').map((f) => f.key))

/** 依 CaseInfo 全欄位骨架組出「送出應該長什麼樣」的期望值:未提供的欄位補預設空值。 */
function buildFullInfo(source, overrides = {}) {
  const base = {}
  for (const field of ALL_FIELDS) {
    base[field.key] = LIST_KEYS.has(field.key) ? source[field.key] || [] : source[field.key] ?? ''
  }
  return { ...base, ...overrides }
}

function setup(overrides = {}) {
  const onChanged = vi.fn(async () => {})
  const caseData = { ...doneAdmissible, ...overrides }
  render(<F1Section caseData={caseData} onChanged={onChanged} />)
  return { onChanged, caseData }
}

beforeEach(() => {
  api.updateCaseInfo.mockClear()
  api.reanalyzeCase.mockClear()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('F1 五個分頁', () => {
  it('五個分頁存在,一次只渲染一個 tabpanel;切換後顯示對應欄位', async () => {
    const user = userEvent.setup()
    setup()

    const tabs = screen.getAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['訴願書', '送達證書', '原處分書', '訴願答辯書', '綜合判讀'])
    expect(screen.getAllByRole('tabpanel')).toHaveLength(1)
    expect(screen.getByLabelText('訴願人姓名')).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: '送達證書' }))
    expect(screen.getAllByRole('tabpanel')).toHaveLength(1)
    expect(screen.getByLabelText('受送達人')).toBeInTheDocument()
    expect(screen.queryByLabelText('訴願人姓名')).toBeNull()
  })

  it('訴願書分頁含身分證明文件字號、出生年月日等新擷取欄位', () => {
    setup()
    expect(screen.getByLabelText('身分證明文件字號')).toBeInTheDocument()
    expect(screen.getByLabelText('代理人或送達代收人(身分)')).toBeInTheDocument()
    expect(screen.getByText('出生年月日')).toBeInTheDocument()
  })

  it('日期欄是民國年月日三格 combobox,不是文字輸入框', () => {
    setup()
    expect(screen.getByRole('combobox', { name: '出生年月日年' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '出生年月日月' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '出生年月日日' })).toBeInTheDocument()
    const field = screen.getByText('出生年月日').closest('.f1-field')
    expect(within(field).queryByRole('textbox')).toBeNull()
  })

  it('送達方式是下拉選單', async () => {
    const user = userEvent.setup()
    setup()
    await user.click(screen.getByRole('tab', { name: '送達證書' }))
    expect(screen.getByLabelText('送達方式').tagName).toBe('SELECT')
  })

  it('分頁鍵盤可切換:Tab 移到分頁按鈕、按 Enter 切換', async () => {
    const user = userEvent.setup()
    setup()

    const serviceTab = screen.getByRole('tab', { name: '送達證書' })
    serviceTab.focus()
    expect(serviceTab).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(screen.getByLabelText('受送達人')).toBeInTheDocument()
    expect(screen.queryByLabelText('訴願人姓名')).toBeNull()
  })
})

describe('F1 自動儲存', () => {
  it('改一個文字欄並 blur,送出整份案件資訊且只有該欄不同', async () => {
    const user = userEvent.setup()
    const { onChanged } = setup()

    const input = screen.getByLabelText('訴願人姓名')
    await user.clear(input)
    await user.type(input, '李○華')
    await user.tab()

    await waitFor(() => expect(api.updateCaseInfo).toHaveBeenCalledTimes(1))
    expect(api.updateCaseInfo).toHaveBeenCalledWith(
      'c-1',
      buildFullInfo(doneAdmissible.f1, { appellant: '李○華' }),
    )
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('連續輸入停止 1 秒後只送出一次(debounce)', async () => {
    vi.useFakeTimers()
    const onChanged = vi.fn(async () => {})
    render(<F1Section caseData={doneAdmissible} onChanged={onChanged} />)

    const input = screen.getByLabelText('訴願人姓名')
    fireEvent.change(input, { target: { value: '李' } })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300)
    })
    fireEvent.change(input, { target: { value: '李○' } })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300)
    })
    fireEvent.change(input, { target: { value: '李○華' } })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })

    expect(api.updateCaseInfo).toHaveBeenCalledTimes(1)
    expect(api.updateCaseInfo).toHaveBeenCalledWith(
      'c-1',
      buildFullInfo(doneAdmissible.f1, { appellant: '李○華' }),
    )
  })

  it('改動已有日期的其中一格,立即送出標準寫法,不等 debounce', async () => {
    // RocDatePicker 從全空狀態逐格選取有已知限制(見報告),故從已有效日期出發只改一格,
    // 仍能驗證「select 改變即送、不等 1 秒 debounce」這個行為
    const user = userEvent.setup()
    const info = { ...doneAdmissible.f1, appellant_birth_date: '民國114年5月1日' }
    setup({ f1: info })

    await user.selectOptions(screen.getByRole('combobox', { name: '出生年月日日' }), '28')

    await waitFor(() => expect(api.updateCaseInfo).toHaveBeenCalledTimes(1))
    expect(api.updateCaseInfo).toHaveBeenCalledWith(
      'c-1',
      buildFullInfo(info, { appellant_birth_date: '民國114年5月28日' }),
    )
  })

  it('日期欄原值無法辨識(如「未載明」)且未動時,存檔仍原封不動送出該原文', async () => {
    const user = userEvent.setup()
    const info = { ...doneAdmissible.f1, appellant_birth_date: '未載明' }
    setup({ f1: info })

    expect(screen.getByText('無法辨識：未載明')).toBeInTheDocument()

    // 只改別的欄位,沒動過日期欄
    const input = screen.getByLabelText('訴願人姓名')
    await user.clear(input)
    await user.type(input, '李○華')
    await user.tab()

    await waitFor(() => expect(api.updateCaseInfo).toHaveBeenCalledTimes(1))
    expect(api.updateCaseInfo).toHaveBeenCalledWith(
      'c-1',
      buildFullInfo(info, { appellant: '李○華', appellant_birth_date: '未載明' }),
    )
  })

  it('清單欄位以換行分行,儲存時拆成陣列並丟掉空行', async () => {
    const user = userEvent.setup()
    setup()

    const box = screen.getByLabelText('事實')
    await user.clear(box)
    await user.type(box, '第一項{Enter}{Enter}第二項')
    await user.tab()

    await waitFor(() => expect(api.updateCaseInfo).toHaveBeenCalledTimes(1))
    expect(api.updateCaseInfo).toHaveBeenCalledWith(
      'c-1',
      buildFullInfo(doneAdmissible.f1, { appeal_facts: ['第一項', '第二項'] }),
    )
  })

  it('清單欄位的空白行(只有空格)視同空行,存檔時一併丟掉', async () => {
    const user = userEvent.setup()
    setup()

    const box = screen.getByLabelText('事實')
    await user.clear(box)
    await user.type(box, '第一項{Enter}   {Enter}第二項  ')
    await user.tab()

    await waitFor(() => expect(api.updateCaseInfo).toHaveBeenCalledTimes(1))
    expect(api.updateCaseInfo).toHaveBeenCalledWith(
      'c-1',
      buildFullInfo(doneAdmissible.f1, { appeal_facts: ['第一項', '第二項'] }),
    )
  })

  it('儲存失敗時錯誤看得見,而且不清掉剛打的字', async () => {
    api.updateCaseInfo.mockRejectedValueOnce(new Error('案件分析中，無法修改案件資訊'))
    const user = userEvent.setup()
    setup()

    const input = screen.getByLabelText('訴願人姓名')
    await user.clear(input)
    await user.type(input, '李○華')
    await user.tab()

    expect(await screen.findByText(/無法修改案件資訊/)).toBeInTheDocument()
    expect(screen.getByLabelText('訴願人姓名')).toHaveValue('李○華')
  })

  it('分析進行中時全部欄位停用,不送出 PATCH', () => {
    setup({ status: 'processing' })
    expect(screen.getByText(/分析進行中/)).toBeInTheDocument()
    expect(screen.getByLabelText('訴願人姓名')).toBeDisabled()
    expect(api.updateCaseInfo).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'AI 生成' })).toHaveAttribute('aria-disabled', 'true')
  })
})

describe('F1 已修改標記', () => {
  it('與 f1_system 不同的欄位標「已修改」並顯示原值,相同的不標', () => {
    setup({ f1_system: { ...doneAdmissible.f1, appellant: '王○明(系統)' } })

    const changed = screen.getByText('訴願人姓名').closest('.f1-field')
    expect(changed.className).toContain('f1-field--edited')
    expect(within(changed).getByTitle(/模型原本擷取/)).toHaveTextContent('王○明(系統)')

    const untouched = screen.getByText('聯絡電話').closest('.f1-field')
    expect(untouched.className).not.toContain('f1-field--edited')
  })

  it('改過的欄位再改回與 f1_system 相同的值,已修改標記消失', async () => {
    const user = userEvent.setup()
    setup({ f1_system: { ...doneAdmissible.f1, appellant: '王○明(系統)' } })

    // 欄位一開始就標「已修改」,label 的可存取名稱含琥珀色旗標文字,故不能用 getByLabelText 精確比對
    const input = within(screen.getByText('訴願人姓名').closest('.f1-field')).getByRole('textbox')
    await user.clear(input)
    await user.type(input, '換一個名字')
    expect(screen.getByText('訴願人姓名').closest('.f1-field').className).toContain('f1-field--edited')

    await user.clear(input)
    await user.type(input, '王○明(系統)')

    expect(screen.getByText('訴願人姓名').closest('.f1-field').className).not.toContain(
      'f1-field--edited',
    )
  })

  it('沒有 f1_system 時一欄都不標', () => {
    setup()
    expect(document.querySelectorAll('.f1-field--edited')).toHaveLength(0)
  })
})

describe('F1 OCR 提示', () => {
  it('文件為 OCR 時分頁有 OCR 標記與常駐提示句', () => {
    setup({ documents: { appeal: { slot: 'appeal', source: 'pdf', text: '…', ocr: true } } })
    const tab = screen.getByRole('tab', { name: /訴願書/ })
    expect(within(tab).getByText('OCR')).toBeInTheDocument()
    expect(screen.getByText(/本文件由 OCR 取字/)).toBeInTheDocument()
  })

  it('文件非 OCR 時分頁沒有 OCR 標記也沒有提示句', () => {
    setup({ documents: { appeal: { slot: 'appeal', source: 'pdf', text: '…', ocr: false } } })
    const tab = screen.getByRole('tab', { name: /訴願書/ })
    expect(within(tab).queryByText('OCR')).toBeNull()
    expect(screen.queryByText(/本文件由 OCR 取字/)).toBeNull()
  })
})

describe('F1 底部 AI 生成', () => {
  it('f1_stale 為 false 時停用,點了不呼叫 API', async () => {
    const user = userEvent.setup()
    setup({ f1_stale: false })

    const btn = screen.getByRole('button', { name: 'AI 生成' })
    expect(btn).toHaveAttribute('aria-disabled', 'true')
    await user.click(btn)

    expect(api.reanalyzeCase).not.toHaveBeenCalled()
    expect(screen.getByText('案件資訊未變更')).toBeInTheDocument()
  })

  it('f1_stale 為 true 且已有程序審查結論:先警示,取消不呼叫、確認才呼叫並重新載入', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    const user = userEvent.setup()
    const { onChanged } = setup({ f1_stale: true })

    confirmSpy.mockReturnValueOnce(false)
    await user.click(screen.getByRole('button', { name: 'AI 生成' }))
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(api.reanalyzeCase).not.toHaveBeenCalled()

    confirmSpy.mockReturnValueOnce(true)
    await user.click(screen.getByRole('button', { name: 'AI 生成' }))
    expect(api.reanalyzeCase).toHaveBeenCalledWith('c-1', 'screening')
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('screening 尚未產生時,AI 生成不跳確認直接執行', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    const user = userEvent.setup()
    const { onChanged } = setup({ f1_stale: true, screening: null })

    await user.click(screen.getByRole('button', { name: 'AI 生成' }))

    expect(confirmSpy).not.toHaveBeenCalled()
    expect(api.reanalyzeCase).toHaveBeenCalledWith('c-1', 'screening')
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('AI 生成前先等在途的 debounce 儲存送出,updateCaseInfo 在 reanalyzeCase 之前完成', async () => {
    setup({ f1_stale: true, screening: null })

    // fireEvent.change 不移動焦點,不觸發 blur——只留下 debounce 排隊中的儲存,
    // 排除「點按鈕本身順帶 blur」這條路徑,單純驗證 AI 生成前的 flush()
    const input = screen.getByLabelText('聯絡電話')
    fireEvent.change(input, { target: { value: '0912345678' } })
    expect(api.updateCaseInfo).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'AI 生成' }))

    await waitFor(() => expect(api.updateCaseInfo).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(api.reanalyzeCase).toHaveBeenCalledWith('c-1', 'screening'))
    expect(api.updateCaseInfo.mock.invocationCallOrder[0]).toBeLessThan(
      api.reanalyzeCase.mock.invocationCallOrder[0],
    )
  })
})

describe('F1 輪詢不覆蓋正在打的字', () => {
  it('輪詢換回未變的欄位不蓋掉使用者正在編輯但尚未送出的內容;換了的其他欄位仍會更新', () => {
    const onChanged = vi.fn(async () => {})
    const caseData = { ...doneAdmissible }
    const { rerender } = render(<F1Section caseData={caseData} onChanged={onChanged} />)

    const input = screen.getByLabelText('訴願人姓名')
    fireEvent.change(input, { target: { value: '正在打的字' } })
    expect(input).toHaveValue('正在打的字')

    // 模擬輪詢:訴願人姓名後端仍是舊值(還沒處理完這次修改)不該蓋掉正在打的字;
    // 聯絡電話後端已經變了(例如被重跑或另一個視窗改過),應該跟著更新
    rerender(
      <F1Section
        caseData={{ ...caseData, f1: { ...caseData.f1, appellant_phone: '0900000000' } }}
        onChanged={onChanged}
      />,
    )

    expect(screen.getByLabelText('訴願人姓名')).toHaveValue('正在打的字')
    expect(screen.getByLabelText('聯絡電話')).toHaveValue('0900000000')
  })
})
