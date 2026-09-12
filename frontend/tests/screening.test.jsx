import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { api } from './apiMock.js'
import { doneAdmissible, doneInadmissible } from './fixtures.js'
import { renderDetail, rail } from './renderCaseDetail.jsx'
import { ScreeningSection } from '../src/pages/ScreeningSection.jsx'

vi.mock('../src/api', async () => (await import('./apiMock.js')).api)

describe('期間認定', () => {
  it('算得出逾期時列出送達日、末日、收文日', async () => {
    api.getCase.mockResolvedValue(doneInadmissible)
    const user = userEvent.setup()
    renderDetail('c-2')
    await screen.findByRole('button', { name: /^程序審查/ })

    await user.click(rail(/^程序審查/))

    expect(screen.getByText('已逾期')).toBeInTheDocument()
    expect(screen.getByText('2025-06-27')).toBeInTheDocument()
    expect(screen.getByText('2025-10-31')).toBeInTheDocument()
  })

  it('無從認定不顯示為未逾期,並印出待人工確認的原因', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^程序審查/ })

    await user.click(rail(/^程序審查/))

    expect(screen.getByText('無從認定')).toBeInTheDocument()
    expect(screen.queryByText('未逾期')).toBeNull()
    // 期間卡片與頁首橫幅都會講這件事(兩處刻意一致),故此處不只一個節點
    expect(screen.getAllByText(/送達日無法認定/).length).toBeGreaterThan(0)
  })
})

describe('程序審查表單:常駐三欄自動儲存', () => {
  it('三欄常駐,如實反映目前結論;受理時不顯示適用條款欄', async () => {
    api.getCase.mockResolvedValue(doneInadmissible)
    const user = userEvent.setup()
    renderDetail('c-2')
    await screen.findByRole('button', { name: /^程序審查/ })
    await user.click(rail(/^程序審查/))

    expect(screen.getByLabelText('審查結論')).toHaveValue('reject')
    expect(screen.getByLabelText('審查適用條款')).toHaveValue('77條第2款')
    expect(screen.getByLabelText('審查理由')).toHaveValue('逾三十日提起。')

    await user.selectOptions(screen.getByLabelText('審查結論'), 'pass')
    expect(screen.queryByLabelText('審查適用條款')).toBeNull()
  })

  it('改結論,select 立即送出 overrideScreening 且 body 正確', async () => {
    api.getCase.mockResolvedValue(doneInadmissible)
    const user = userEvent.setup()
    renderDetail('c-2')
    await screen.findByRole('button', { name: /^程序審查/ })
    await user.click(rail(/^程序審查/))
    api.overrideScreening.mockClear()

    await user.selectOptions(screen.getByLabelText('審查結論'), 'pass')

    expect(api.overrideScreening).toHaveBeenCalledTimes(1)
    expect(api.overrideScreening).toHaveBeenCalledWith('c-2', {
      passed: true,
      matched_clause: null,
      reasoning: '逾三十日提起。',
    })
    expect(await screen.findByRole('status')).toHaveTextContent('已儲存')
  })

  it('改理由,停止輸入並失焦後只送出一次,帶入最新內容', async () => {
    api.getCase.mockResolvedValue(doneInadmissible)
    const user = userEvent.setup()
    renderDetail('c-2')
    await screen.findByRole('button', { name: /^程序審查/ })
    await user.click(rail(/^程序審查/))
    api.overrideScreening.mockClear()

    const reasoning = screen.getByLabelText('審查理由')
    await user.clear(reasoning)
    await user.type(reasoning, '經核送達證書,未逾期。')
    expect(api.overrideScreening).not.toHaveBeenCalled()
    await user.tab()

    expect(api.overrideScreening).toHaveBeenCalledTimes(1)
    expect(api.overrideScreening).toHaveBeenCalledWith('c-2', {
      passed: false,
      matched_clause: '77條第2款',
      reasoning: '經核送達證書,未逾期。',
    })
  })

  it('儲存失敗時保留使用者輸入,狀態列顯示失敗訊息', async () => {
    api.getCase.mockResolvedValue(doneInadmissible)
    api.overrideScreening.mockRejectedValueOnce(new Error('網路逾時'))
    const user = userEvent.setup()
    renderDetail('c-2')
    await screen.findByRole('button', { name: /^程序審查/ })
    await user.click(rail(/^程序審查/))

    const clause = screen.getByLabelText('審查適用條款')
    await user.clear(clause)
    await user.type(clause, '77條第3款')
    await user.tab()

    expect(await screen.findByRole('status')).toHaveTextContent('儲存失敗：網路逾時')
    expect(screen.getByLabelText('審查適用條款')).toHaveValue('77條第3款')
  })

  it('screening_stale 為 false 時,AI 生成停用且說明未變更,點了不呼叫', async () => {
    api.getCase.mockResolvedValue({ ...doneAdmissible, screening_stale: false })
    renderDetail()
    await screen.findByRole('button', { name: /^程序審查/ })
    const user = userEvent.setup()
    await user.click(rail(/^程序審查/))

    const generate = screen.getByRole('button', { name: 'AI 生成' })
    expect(generate).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByText('程序審查結論未變更')).toBeInTheDocument()
    api.reanalyzeCase.mockClear()
    await user.click(generate)
    expect(api.reanalyzeCase).not.toHaveBeenCalled()
  })

  it('screening_stale 為 true 且已有下游結果時,AI 生成先警示;取消不呼叫,確認才呼叫 reanalyzeCase(id,"f2")', async () => {
    api.getCase.mockResolvedValue({ ...doneAdmissible, screening_stale: true })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^程序審查/ })
    await user.click(rail(/^程序審查/))
    const generate = screen.getByRole('button', { name: 'AI 生成' })
    api.reanalyzeCase.mockClear()

    await user.click(generate)
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(api.reanalyzeCase).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    api.getCase.mockClear()
    await user.click(generate)
    expect(api.reanalyzeCase).toHaveBeenCalledWith('c-1', 'f2')
    expect(api.getCase).toHaveBeenCalledTimes(1)
    confirmSpy.mockRestore()
  })

  it('f2/f2_refs/f3/f4 皆為 null 時,AI 生成不警示直接呼叫', async () => {
    api.getCase.mockResolvedValue({
      ...doneAdmissible,
      screening_stale: true,
      f2: null,
      f2_refs: null,
      f3: null,
      f4: null,
    })
    const confirmSpy = vi.spyOn(window, 'confirm')
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^程序審查/ })
    await user.click(rail(/^程序審查/))
    api.reanalyzeCase.mockClear()

    await user.click(screen.getByRole('button', { name: 'AI 生成' }))

    expect(confirmSpy).not.toHaveBeenCalled()
    expect(api.reanalyzeCase).toHaveBeenCalledWith('c-1', 'f2')
    confirmSpy.mockRestore()
  })

  it('案件處理中時,結論/適用條款/理由整表唯讀', async () => {
    api.getCase.mockResolvedValue({ ...doneInadmissible, status: 'processing' })
    const user = userEvent.setup()
    renderDetail('c-2')
    await screen.findByRole('button', { name: /^程序審查/ })
    await user.click(rail(/^程序審查/))

    expect(screen.getByLabelText('審查結論')).toBeDisabled()
    expect(screen.getByLabelText('審查適用條款')).toBeDisabled()
    expect(screen.getByLabelText('審查理由')).toBeDisabled()
  })

  it('案件處理中且 screening_stale 為 true 時,AI 生成即使外觀是 aria-disabled,點了也不呼叫 API', async () => {
    // btn-submit 的 aria-disabled 刻意保留 pointer-events,只用游標說明按不下去(見 components.css)——
    // 真正擋下 API 呼叫的責任在 handler 自己,不能只靠 CSS/屬性。f2/f2_refs/f3/f4 都設 null,
    // 排除 confirm() 分支(jsdom 沒實作 window.confirm,會讓斷言測到錯的原因)。
    api.getCase.mockResolvedValue({
      ...doneAdmissible,
      status: 'processing',
      screening_stale: true,
      f2: null,
      f2_refs: null,
      f3: null,
      f4: null,
    })
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^程序審查/ })
    await user.click(rail(/^程序審查/))

    const generate = screen.getByRole('button', { name: 'AI 生成' })
    expect(generate).toHaveAttribute('aria-disabled', 'true')
    api.reanalyzeCase.mockClear()
    await user.click(generate)
    expect(api.reanalyzeCase).not.toHaveBeenCalled()
  })

  it('AI 生成前等在途的 debounce 儲存送出,reanalyzeCase 在 overrideScreening 之後才呼叫', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    api.getCase.mockResolvedValue({ ...doneInadmissible, screening_stale: true })
    const user = userEvent.setup()
    renderDetail('c-2')
    await screen.findByRole('button', { name: /^程序審查/ })
    await user.click(rail(/^程序審查/))
    api.overrideScreening.mockClear()
    api.reanalyzeCase.mockClear()

    // 打字但不 blur、不等 1 秒:此刻儲存仍在 debounce 排隊中
    await user.type(screen.getByLabelText('審查理由'), '補充。')
    expect(api.overrideScreening).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'AI 生成' }))

    expect(api.overrideScreening).toHaveBeenCalledTimes(1)
    expect(api.reanalyzeCase).toHaveBeenCalledWith('c-2', 'f2')
    expect(api.overrideScreening.mock.invocationCallOrder[0]).toBeLessThan(
      api.reanalyzeCase.mock.invocationCallOrder[0],
    )
    vi.restoreAllMocks()
  })

  it('輪詢換回未變的值不蓋掉使用者正在編輯但尚未送出的理由欄;換了的其他欄位仍會更新', async () => {
    const onChanged = vi.fn()
    const caseData = { ...doneInadmissible }
    const { rerender } = render(<ScreeningSection caseData={caseData} onChanged={onChanged} />)

    const reasoning = screen.getByLabelText('審查理由')
    await userEvent.setup().type(reasoning, '正在打的字')
    expect(reasoning).toHaveValue('逾三十日提起。正在打的字')

    // 模擬輪詢:理由欄後端仍是舊值(還沒處理完這次修改)不該蓋掉正在打的字;
    // 適用條款欄後端已經變了(例如被重跑或另一個視窗改過),應該跟著更新
    rerender(
      <ScreeningSection
        caseData={{ ...caseData, screening: { ...caseData.screening, matched_clause: '77條第3款' } }}
        onChanged={onChanged}
      />,
    )

    expect(screen.getByLabelText('審查理由')).toHaveValue('逾三十日提起。正在打的字')
    expect(screen.getByLabelText('審查適用條款')).toHaveValue('77條第3款')
  })
})

describe('程序審查推翻', () => {
  it('已被推翻的案件同時顯示人工結論與系統原判', async () => {
    api.getCase.mockResolvedValue({
      ...doneInadmissible,
      screening: { passed: true, matched_clause: null, reasoning: '人工認定未逾期。', review_note: '' },
      screening_system: { passed: false, matched_clause: '77條第2款', reasoning: '系統判逾期。', review_note: '' },
    })
    const user = userEvent.setup()
    renderDetail('c-2')
    await screen.findByRole('button', { name: /^程序審查/ })
    await user.click(rail(/^程序審查/))

    expect(screen.getByText('已由承辦人推翻')).toBeInTheDocument()
    expect(screen.getByText(/系統原判：不受理/)).toBeInTheDocument()
  })
})
