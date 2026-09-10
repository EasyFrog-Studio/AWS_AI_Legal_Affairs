import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import App from '../src/App.jsx'
import CaseDetail from '../src/pages/CaseDetail.jsx'
import { api } from './apiMock.js'
import {
  doneAdmissible,
  doneInadmissible,
  processingAt,
  errorAtF2,
  listRows,
  collectingAllMatched,
  collectingWithMismatch,
} from './fixtures.js'

vi.mock('../src/api', async () => (await import('./apiMock.js')).api)

function renderDetail(id = 'c-1') {
  return render(
    <MemoryRouter initialEntries={[`/cases/${id}`]}>
      <Routes>
        <Route path="/cases/:id" element={<CaseDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

const rail = (name) => screen.getByRole('button', { name })
const isCurrent = (name) => rail(name).getAttribute('aria-current') === 'true'

describe('案件詳情', () => {
  it('切換階段再切回,草稿未儲存內容存活', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const user = userEvent.setup()
    renderDetail()
    const fact = await screen.findByLabelText('事實')
    expect(fact).toHaveValue('事實原文')
    await user.type(fact, ' 補充一段')
    await user.type(screen.getByLabelText('理由'), ' 補充理由')
    await user.type(screen.getByLabelText('主文'), ' 補充主文')

    await user.click(rail(/^F3 案例/))
    expect(isCurrent(/^F3 案例/)).toBe(true)
    await user.click(rail(/^F1 擷取/))
    await user.click(rail(/^決定書草稿/))

    expect(screen.getByLabelText('事實')).toHaveValue('事實原文 補充一段')
    expect(screen.getByLabelText('理由')).toHaveValue('理由原文 補充理由')
    expect(screen.getByLabelText('主文')).toHaveValue('訴願駁回。 補充主文')
    expect(api.updateDraft).not.toHaveBeenCalled()
  })

  it('done 案件預設落在決定書草稿;processing 落在當前階段', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const { unmount } = renderDetail()
    await screen.findByLabelText('事實')
    expect(isCurrent(/^決定書草稿/)).toBe(true)
    unmount()

    api.getCase.mockResolvedValue(processingAt('screening'))
    renderDetail('c-3')
    await screen.findByRole('button', { name: /^程序審查/ })
    expect(isCurrent(/^程序審查/)).toBe(true)
  })

  it('輪詢自動跟隨 current_stage;手動點過左欄後永久停止跟隨', async () => {
    api.getCase
      .mockResolvedValueOnce(processingAt('screening'))
      .mockResolvedValueOnce(processingAt('f2'))
      .mockResolvedValue(processingAt('f3'))
    const { unmount } = renderDetail('c-3')
    await screen.findByRole('button', { name: /^程序審查/ })
    expect(isCurrent(/^程序審查/)).toBe(true)
    await waitFor(() => expect(isCurrent(/^F2 法規/)).toBe(true), { timeout: 3500 })
    unmount()

    api.getCase
      .mockReset()
      .mockResolvedValueOnce(processingAt('screening'))
      .mockResolvedValue(processingAt('f3'))
    const user = userEvent.setup()
    renderDetail('c-3')
    await screen.findByRole('button', { name: /^程序審查/ })
    await user.click(rail(/^F1 擷取/))
    expect(isCurrent(/^F1 擷取/)).toBe(true)
    await waitFor(() => expect(api.getCase.mock.calls.length).toBeGreaterThanOrEqual(2), {
      timeout: 3500,
    })
    await waitFor(() => expect(rail(/^F3 案例/)).toBeInTheDocument())
    expect(isCurrent(/^F1 擷取/)).toBe(true)
    expect(isCurrent(/^F3 案例/)).toBe(false)
  }, 12000)

  it('不受理案件:F2 標「不適用」+ 說明句,不顯示「無」;草稿依據欄隱藏 F2 組', async () => {
    api.getCase.mockResolvedValue(doneInadmissible)
    const user = userEvent.setup()
    renderDetail('c-2')
    await screen.findByLabelText('事實')
    expect(rail(/^F2 法規/)).toHaveTextContent('不適用')
    expect(screen.queryByText('參考法規(F2)')).toBeNull()
    expect(screen.queryByText('無')).toBeNull()
    expect(screen.getByText('參考案例(F3)')).toBeInTheDocument()

    await user.click(rail(/^F2 法規/))
    expect(screen.getByText(/本案經程序審查認定不受理/)).toBeInTheDocument()
    expect(screen.queryByText('無')).toBeNull()
  })

  it('status=error:左欄標記失敗階段「中斷」,內容區顯示錯誤原因', async () => {
    api.getCase.mockResolvedValue(errorAtF2)
    renderDetail('c-4')
    const f2 = await screen.findByRole('button', { name: /^F2 法規/ })
    expect(f2).toHaveTextContent('中斷')
    expect(screen.getByText(/Bedrock 檢索逾時/)).toBeInTheDocument()
  })

  it('有未儲存修改時,離開頁面前提示;取消則留在原頁', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    api.listCases.mockResolvedValue(listRows)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/cases/c-1']}>
        <App />
      </MemoryRouter>,
    )
    await user.type(await screen.findByLabelText('事實'), ' 補充')
    await user.click(screen.getByRole('link', { name: '案件清單' }))
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('事實')).toHaveValue('事實原文 補充')

    confirmSpy.mockReturnValue(true)
    await user.click(screen.getByRole('link', { name: '案件清單' }))
    expect(await screen.findByRole('table')).toBeInTheDocument()
  })
})

describe('待確認階段(collecting)', () => {
  it('預設落在文件確認,三份皆符合時可以開始分析', async () => {
    api.getCase.mockResolvedValue(collectingAllMatched)
    renderDetail('c-5')

    await screen.findByRole('button', { name: '開始分析' })
    expect(isCurrent(/^文件確認/)).toBe(true)
    expect(screen.getByRole('button', { name: '開始分析' })).toBeEnabled()
    // 三槽皆為 matched:true,徽章應顯示已確認而非「無法確認」
    expect(screen.getAllByText(/確認為此文件/).length).toBe(3)
  })

  it('有文件判斷不符時,開始分析被停用,並標示不符原因', async () => {
    api.getCase.mockResolvedValue(collectingWithMismatch)
    renderDetail('c-6')

    await screen.findByText(/不是送達證書/)
    expect(screen.getByRole('button', { name: '開始分析' })).toBeDisabled()
  })

  it('點擊開始分析呼叫 API 並重新載入案件', async () => {
    api.getCase.mockResolvedValue(collectingAllMatched)
    const user = userEvent.setup()
    renderDetail('c-5')
    await screen.findByRole('button', { name: '開始分析' })
    api.getCase.mockClear()

    await user.click(screen.getByRole('button', { name: '開始分析' }))
    expect(api.analyzeCase).toHaveBeenCalledWith('c-5')
    expect(api.getCase).toHaveBeenCalledTimes(1) // 分析後重新載入
  })

  it('重傳可以直接給 PDF,送出的 formData 帶 file', async () => {
    api.getCase.mockResolvedValue(collectingWithMismatch)
    const user = userEvent.setup()
    renderDetail('c-6')

    await screen.findByText(/不是送達證書/)
    const serviceSlot = screen.getByText('送達證書').closest('.doc-slot')
    await user.click(within(serviceSlot).getByRole('button', { name: '重新上傳' }))
    const file = new File(['%PDF-1.4'], '送達證書.pdf', { type: 'application/pdf' })
    await user.upload(within(serviceSlot).getByLabelText('重新上傳 PDF'), file)
    api.replaceDocument.mockClear()
    await user.click(within(serviceSlot).getByRole('button', { name: '送出' }))

    const formData = api.replaceDocument.mock.calls[0][2]
    expect(formData.get('file')).toBe(file)
    expect(formData.get('text')).toBeNull() // 兩者只送一個,不讓後端猜該用哪個
  })

  it('重傳遇 409(案件已離開收案階段)時同步真實狀態', async () => {
    api.getCase.mockResolvedValue(collectingWithMismatch)
    const conflict = new Error('案件已開始分析,無法再修改文件')
    conflict.status = 409
    api.replaceDocument.mockRejectedValueOnce(conflict)
    const user = userEvent.setup()
    renderDetail('c-6')

    await screen.findByText(/不是送達證書/)
    const serviceSlot = screen.getByText('送達證書').closest('.doc-slot')
    await user.click(within(serviceSlot).getByRole('button', { name: '重新上傳' }))
    await user.type(within(serviceSlot).getByPlaceholderText('或貼上送達證書全文'), '新全文')
    api.getCase.mockClear()
    await user.click(within(serviceSlot).getByRole('button', { name: '送出' }))

    expect(screen.getByText(/已開始分析/)).toBeInTheDocument()
    expect(api.getCase).toHaveBeenCalledTimes(1)
  })

  it('重新上傳單一文件槽,送出後呼叫 replaceDocument 並重新載入', async () => {
    api.getCase.mockResolvedValue(collectingWithMismatch)
    const user = userEvent.setup()
    renderDetail('c-6')

    await screen.findByText(/不是送達證書/)
    const serviceSlot = screen.getByText('送達證書').closest('.doc-slot')
    await user.click(within(serviceSlot).getByRole('button', { name: '重新上傳' }))
    await user.type(within(serviceSlot).getByPlaceholderText('或貼上送達證書全文'), '新的送達證書全文')
    api.getCase.mockClear()
    await user.click(within(serviceSlot).getByRole('button', { name: '送出' }))

    expect(api.replaceDocument).toHaveBeenCalledWith('c-6', 'service', expect.anything())
    expect(api.getCase).toHaveBeenCalledTimes(1)
  })
})

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

describe('F1 與 F3 呈現', () => {
  it('F1 顯示送達證書/原處分書那六欄,抽不到的欄位顯示「—」而不是整欄不畫', async () => {
    api.getCase.mockResolvedValue({
      ...doneAdmissible,
      f1: {
        ...doneAdmissible.f1,
        receipt_date: '114年5月28日',
        service_date: '114年5月28日',
        service_method: '寄存於板橋郵局',
        disposition_fine: '6,000元',
        disposition_notice_clause: '',
        disposition_recipient: '王○明',
      },
    })
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^F1 擷取/ })

    await user.click(rail(/^F1 擷取/))

    expect(screen.getByText('送達方式')).toBeInTheDocument()
    expect(screen.getByText('寄存於板橋郵局')).toBeInTheDocument()
    expect(screen.getByText('6,000元')).toBeInTheDocument()
    expect(screen.getByText('教示條款')).toBeInTheDocument()
    expect(screen.getByText('—')).toBeInTheDocument() // 教示條款抽不到,欄位仍在
  })

  it('F3 階段頁的案例可以開原文,與草稿頁的依據面板一致', async () => {
    api.getCase.mockResolvedValue({
      ...doneAdmissible,
      f3: [{ ...doneAdmissible.f3[0], source_key: 'markdown/訴願決定書/112-0001.md' }],
    })
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^F3 案例/ })
    await user.click(rail(/^F3 案例/))

    const stageCase = screen.getByText(/摘要一/).closest('.similar-case')
    await user.click(within(stageCase).getByRole('button', { name: '原文' }))

    expect(api.getSource).toHaveBeenCalledWith('markdown/訴願決定書/112-0001.md')
  })
})

describe('待人工確認與重跑', () => {
  it('期間有待確認事項時,頁首出現橫幅並列出原因', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    renderDetail()

    await screen.findByRole('status')
    expect(screen.getByRole('status')).toHaveTextContent('此案有事實待人工確認')
    expect(screen.getByRole('status')).toHaveTextContent(/訴願期間/)
  })

  it('OCR 取字的槽在橫幅列出須核對原件', async () => {
    api.getCase.mockResolvedValue({
      ...doneAdmissible,
      documents: {
        service: {
          slot: 'service',
          source: 'pdf',
          text: '送達證書全文',
          ocr: true,
          review_note: '本槽文字由 OCR 取得,日期須人工核對原件',
          check: { matched: true, method: 'rule', note: '' },
        },
      },
    })
    renderDetail()

    await screen.findByRole('status')
    expect(screen.getByRole('status')).toHaveTextContent(/OCR/)
  })

  it('done 與 error 都能重跑分析', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const user = userEvent.setup()
    const { unmount } = renderDetail()
    await user.click(await screen.findByRole('button', { name: '重跑分析' }))
    expect(api.reanalyzeCase).toHaveBeenCalledWith('c-1')
    unmount()

    api.reanalyzeCase.mockClear()
    api.getCase.mockResolvedValue(errorAtF2)
    renderDetail('c-4')
    await user.click(await screen.findByRole('button', { name: '重跑分析' }))
    expect(api.reanalyzeCase).toHaveBeenCalledWith('c-4')
  })

  it('collecting 案件不顯示重跑按鈕(該走開始分析)', async () => {
    api.getCase.mockResolvedValue(collectingAllMatched)
    renderDetail('c-5')

    await screen.findByRole('button', { name: '開始分析' })
    expect(screen.queryByRole('button', { name: '重跑分析' })).toBeNull()
  })
})

describe('程序審查推翻', () => {
  it('送出推翻後呼叫 API 並重新載入', async () => {
    api.getCase.mockResolvedValue(doneInadmissible)
    const user = userEvent.setup()
    renderDetail('c-2')
    await screen.findByRole('button', { name: /^程序審查/ })
    await user.click(rail(/^程序審查/))

    await user.click(screen.getByRole('button', { name: '推翻此結論' }))
    await user.selectOptions(screen.getByLabelText('推翻後結論'), 'pass')
    await user.clear(screen.getByLabelText('推翻理由'))
    await user.type(screen.getByLabelText('推翻理由'), '經核送達證書,未逾期。')
    api.getCase.mockClear()
    await user.click(screen.getByRole('button', { name: '送出推翻' }))

    expect(api.overrideScreening).toHaveBeenCalledWith('c-2', {
      passed: true,
      matched_clause: null,
      reasoning: '經核送達證書,未逾期。',
    })
    expect(api.getCase).toHaveBeenCalledTimes(1)
  })

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
    expect(screen.getByText(/系統原判:不受理/)).toBeInTheDocument()
  })
})

describe('新結果提示點', () => {
  it('分析完成後,尚未看過的階段在左欄標一個點;正在看的那個不標', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    renderDetail()
    await screen.findByRole('button', { name: /^F1 擷取/ })

    // done 案件預設落在決定書草稿,所以草稿沒有點,其餘跑出結果的階段都有
    expect(within(rail(/^F1 擷取/)).getByLabelText('有新結果')).toBeInTheDocument()
    expect(within(rail(/^程序審查/)).getByLabelText('有新結果')).toBeInTheDocument()
    expect(within(rail(/^F3 案例/)).getByLabelText('有新結果')).toBeInTheDocument()
    expect(within(rail(/^決定書草稿/)).queryByLabelText('有新結果')).toBeNull()
  })

  it('點進去看過就不再標點', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^程序審查/ })

    await user.click(rail(/^程序審查/))

    expect(within(rail(/^程序審查/)).queryByLabelText('有新結果')).toBeNull()
    expect(within(rail(/^F3 案例/)).getByLabelText('有新結果')).toBeInTheDocument()
  })

  it('還沒跑出結果的階段不標點——那不是「有東西可看」', async () => {
    api.getCase.mockResolvedValue(processingAt('screening'))
    renderDetail()
    await screen.findByRole('button', { name: /^F3 案例/ })

    expect(within(rail(/^F3 案例/)).queryByLabelText('有新結果')).toBeNull()
  })
})

describe('決定書版面', () => {
  it('草稿頁顯示完整決定書骨架,三段本文仍可編輯', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    renderDetail()

    expect(await screen.findByText('新北市政府訴願決定書')).toBeInTheDocument()
    expect(screen.getByText(/訴願人\s*王大明/)).toBeInTheDocument()
    expect(screen.getByText(/訴願審議委員會主任委員/)).toBeInTheDocument()
    expect(screen.getByText(/中華民國/)).toBeInTheDocument()
    expect(screen.getByLabelText('事實')).toBeEnabled()
  })

  it('版面載入失敗時明說「不是完整決定書」,不讓殘缺版面看起來像正常的', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    api.getDecisionSkeleton.mockRejectedValueOnce(new Error('boom'))
    renderDetail()

    expect(await screen.findByText(/版面載入失敗/)).toBeInTheDocument()
    expect(screen.getByLabelText('事實')).toBeEnabled() // 仍可編輯,不是整頁壞掉
  })
})

describe('草稿版本與定稿', () => {
  it('儲存時帶 base_version,並顯示已存版本數', async () => {
    api.getCase.mockResolvedValue({ ...doneAdmissible, draft_versions: [{ saved_at: 'x', fact: '', reason: '', main_text: '' }] })
    const user = userEvent.setup()
    renderDetail()
    await user.type(await screen.findByLabelText('事實'), '改一下')
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(api.updateDraft).toHaveBeenCalledWith('c-1', {
      fact: '事實原文改一下',
      reason: '理由原文',
      main_text: '訴願駁回。',
      base_version: 1,
    })
    expect(screen.getByText(/已存 1 版/)).toBeInTheDocument()
  })

  it('版本衝突(409)時保留使用者輸入並重新載入', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const conflict = new Error('這份草稿已被他人更新,請重新載入後再改')
    conflict.status = 409
    api.updateDraft.mockRejectedValueOnce(conflict)
    const user = userEvent.setup()
    renderDetail()
    await user.type(await screen.findByLabelText('事實'), '我打的字')
    api.getCase.mockClear()
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(screen.getByText(/已被他人更新/)).toBeInTheDocument()
    expect(screen.getByLabelText('事實')).toHaveValue('事實原文我打的字') // 不沖掉他打的字
    expect(api.getCase).toHaveBeenCalledTimes(1)
  })

  it('標記定稿後仍可再修改(定稿不鎖)', async () => {
    api.getCase.mockResolvedValue({ ...doneAdmissible, finalized_at: '2026-08-27T05:00:00+00:00' })
    const user = userEvent.setup()
    renderDetail()
    await screen.findByLabelText('事實')

    expect(screen.getByRole('button', { name: '重新定稿' })).toBeInTheDocument()
    expect(screen.getByText(/定稿於/)).toBeInTheDocument()
    expect(screen.getByLabelText('事實')).toBeEnabled()
  })

  it('引用法條顯示在草稿頁', async () => {
    api.getCase.mockResolvedValue({
      ...doneInadmissible,
      f4: { ...doneInadmissible.f4, cited_laws: ['訴願法#77', '訴願法#14'] },
    })
    renderDetail('c-2')

    expect(await screen.findByText('訴願法#77、訴願法#14')).toBeInTheDocument()
  })
})
