import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
    const box = await screen.findByLabelText('決定書全文')
    expect(box).toHaveValue('新北市政府訴願決定書 主文 訴願駁回。 事實 事實原文 理由 理由原文')
    await user.type(box, ' 補充一段')

    await user.click(rail(/^參考依據/))
    expect(isCurrent(/^參考依據/)).toBe(true)
    await user.click(rail(/^F1 擷取/))
    await user.click(rail(/^決定書草稿/))

    expect(screen.getByLabelText('決定書全文')).toHaveValue(
      '新北市政府訴願決定書 主文 訴願駁回。 事實 事實原文 理由 理由原文 補充一段',
    )
    expect(api.updateDraftText).not.toHaveBeenCalled()
  })

  it('done 案件預設落在決定書草稿;processing 落在當前階段', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const { unmount } = renderDetail()
    await screen.findByLabelText('決定書全文')
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
    // f2/f2_refs/f3 三個後端階段都對應左欄同一個「參考依據」節點
    await waitFor(() => expect(isCurrent(/^參考依據/)).toBe(true), { timeout: 3500 })
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
    await waitFor(() => expect(rail(/^參考依據/)).toBeInTheDocument())
    expect(isCurrent(/^F1 擷取/)).toBe(true)
    expect(isCurrent(/^參考依據/)).toBe(false)
  }, 12000)

  it('不受理案件:法規那一組說「不適用」+ 說明句,不顯示「無」;草稿依據欄隱藏 F2 組', async () => {
    api.getCase.mockResolvedValue(doneInadmissible)
    const user = userEvent.setup()
    renderDetail('c-2')
    await screen.findByLabelText('決定書全文')
    expect(screen.queryByText('參考法規(F2)')).toBeNull()
    expect(screen.queryByText('無')).toBeNull()
    expect(screen.getByText('參考案例(F3)')).toBeInTheDocument()

    // 「不適用」是法規那一組的事,不是整個參考依據節點的事——參考見解與案例兩條 track 都跑
    await user.click(rail(/^參考依據/))
    const stage = within(refsStage())
    expect(stage.getByText(/本案經程序審查認定不受理/)).toBeInTheDocument()
    expect(stage.getByText('參考見解(F2+)')).toBeInTheDocument()
    expect(stage.getByText('相似案例(F3)')).toBeInTheDocument()
    expect(screen.queryByText('無')).toBeNull()
  })

  it('status=error:左欄標記失敗階段「中斷」,內容區顯示錯誤原因', async () => {
    api.getCase.mockResolvedValue(errorAtF2)
    renderDetail('c-4')
    const refs = await screen.findByRole('button', { name: /^參考依據/ })
    expect(refs).toHaveTextContent('中斷')
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
    await user.type(await screen.findByLabelText('決定書全文'), ' 補充')
    await user.click(screen.getByRole('link', { name: '案件清單' }))
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('決定書全文')).toHaveValue(
      '新北市政府訴願決定書 主文 訴願駁回。 事實 事實原文 理由 理由原文 補充',
    )

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

  it('選填的訴願答辯書留空時標「未提供」,不擋開始分析', async () => {
    api.getCase.mockResolvedValue(collectingAllMatched)
    renderDetail('c-5')

    await screen.findByRole('button', { name: '開始分析' })
    const slot = screen.getByText('訴願答辯書').closest('.doc-slot')
    // 沒送來跟送來但看不懂是兩件事,不能都顯示「無法自動確認」
    expect(within(slot).getByText(/未提供/)).toBeInTheDocument()
    expect(within(slot).queryByText(/無法自動確認/)).toBeNull()
    // 沒送來過的槽談不上「重新」上傳
    expect(within(slot).getByRole('button', { name: '補上傳' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '開始分析' })).toBeEnabled()
  })

  it('選填的答辯書留空不列進待人工確認,否則每一件新案都恆亮', async () => {
    api.getCase.mockResolvedValue(collectingAllMatched)
    renderDetail('c-5')

    await screen.findByRole('button', { name: '開始分析' })
    // 空槽沒有東西可確認;把它算成待複核等於讓這個警示對每一件新案都亮,訊號就廢了
    expect(screen.queryByText(/訴願答辯書尚未確認無誤/)).toBeNull()
  })

  it('答辯書有內容卻還沒確認時,仍要列進待人工確認', async () => {
    api.getCase.mockResolvedValue({
      ...collectingAllMatched,
      documents: {
        ...collectingAllMatched.documents,
        answer: {
          slot: 'answer',
          source: 'text',
          text: '訴願答辯書全文',
          check: { matched: null, method: 'none', note: '規則判斷特徵不足' },
        },
      },
    })
    renderDetail('c-5')

    await screen.findByText(/訴願答辯書尚未確認無誤/)
  })

  it('答辯書有內容但判斷不符時,一樣擋住開始分析', async () => {
    api.getCase.mockResolvedValue({
      ...collectingAllMatched,
      documents: {
        ...collectingAllMatched.documents,
        answer: {
          slot: 'answer',
          source: 'text',
          text: '原處分書全文',
          check: { matched: false, method: 'rule', note: '文字特徵更接近原處分書,不是訴願答辯書' },
        },
      },
    })
    renderDetail('c-5')

    await screen.findByText(/不是訴願答辯書/)
    expect(screen.getByRole('button', { name: '開始分析' })).toHaveAttribute('aria-disabled', 'true')
  })

  it('有文件判斷不符時,開始分析被停用,並標示不符原因', async () => {
    api.getCase.mockResolvedValue(collectingWithMismatch)
    renderDetail('c-6')

    await screen.findByText(/不是送達證書/)
    const analyze = screen.getByRole('button', { name: '開始分析' })
    expect(analyze).toHaveAttribute('aria-disabled', 'true')
    api.analyzeCase.mockClear()
    await userEvent.setup().click(analyze)
    expect(api.analyzeCase).not.toHaveBeenCalled()
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
    await user.upload(within(serviceSlot).getByLabelText('送達證書 PDF'), file)
    api.replaceDocument.mockClear()
    await user.click(within(serviceSlot).getByRole('button', { name: '送出' }))

    const formData = api.replaceDocument.mock.calls[0][2]
    expect(formData.get('file')).toBe(file)
    expect(formData.get('text')).toBeNull() // 兩者只送一個,不讓後端猜該用哪個
  })

  it('展開重傳時不符的理由要留在畫面上,他是照著那句補件的', async () => {
    api.getCase.mockResolvedValue(collectingWithMismatch)
    const user = userEvent.setup()
    renderDetail('c-6')

    await screen.findByText(/不是送達證書/)
    const serviceSlot = screen.getByText('送達證書').closest('.doc-slot')
    await user.click(within(serviceSlot).getByRole('button', { name: '重新上傳' }))

    expect(within(serviceSlot).getByText(/不是送達證書/)).toBeInTheDocument()
  })

  it('重傳的落件框拒收非 PDF,不讓壞檔走到後端', async () => {
    api.getCase.mockResolvedValue(collectingWithMismatch)
    const user = userEvent.setup()
    renderDetail('c-6')

    await screen.findByText(/不是送達證書/)
    const serviceSlot = screen.getByText('送達證書').closest('.doc-slot')
    await user.click(within(serviceSlot).getByRole('button', { name: '重新上傳' }))
    const zone = within(serviceSlot).getByLabelText('送達證書 PDF').closest('.dropzone')
    const notPdf = new File(['送達證書'], '送達證書.txt', { type: 'text/plain' })
    api.replaceDocument.mockClear()
    fireEvent.drop(zone, { dataTransfer: { files: [notPdf], types: ['Files'] } })

    expect(within(serviceSlot).getByText(/只接受 PDF 檔/)).toBeInTheDocument()
    // 檔案沒被帶進來,送出鍵就不該可按——按下去只會把壞檔送到後端才被退
    expect(within(serviceSlot).getByRole('button', { name: '送出' })).toBeDisabled()
    expect(api.replaceDocument).not.toHaveBeenCalled()
  })

  it('換一槽重傳不把上一槽打到一半的字帶過去', async () => {
    api.getCase.mockResolvedValue(collectingWithMismatch)
    const user = userEvent.setup()
    renderDetail('c-6')

    await screen.findByText(/不是送達證書/)
    const serviceSlot = screen.getByText('送達證書').closest('.doc-slot')
    await user.click(within(serviceSlot).getByRole('button', { name: '重新上傳' }))
    await user.type(within(serviceSlot).getByPlaceholderText('或貼上送達證書全文'), '打到一半')

    const appealSlot = screen.getByText('訴願書').closest('.doc-slot')
    await user.click(within(appealSlot).getByRole('button', { name: '重新上傳' }))

    expect(within(appealSlot).getByPlaceholderText('或貼上訴願書全文')).toHaveValue('')
  })

  it('OCR 取字的槽要在卡片上講出來,日期不能被當成已核對', async () => {
    api.getCase.mockResolvedValue({
      ...collectingAllMatched,
      documents: {
        ...collectingAllMatched.documents,
        service: {
          ...collectingAllMatched.documents.service,
          source: 'pdf',
          filename: '03_送達證書.pdf',
          ocr: true,
        },
      },
    })
    renderDetail('c-5')

    await screen.findByRole('button', { name: '開始分析' })
    const serviceSlot = screen.getByText('送達證書').closest('.doc-slot')
    // 來源直接寫當初上傳的檔名,承辦人對得回自己的卷宗
    expect(within(serviceSlot).getByText(/來源：03_送達證書\.pdf/)).toBeInTheDocument()
    expect(within(serviceSlot).getByText(/OCR 取字/)).toBeInTheDocument()
    // 沒走 OCR 的槽不該跟著標,否則這個提醒等於沒說
    const appealSlot = screen.getByText('訴願書').closest('.doc-slot')
    expect(within(appealSlot).getByText(/來源：貼上文字$/)).toBeInTheDocument()
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

describe('F3 來源連結', () => {
  it('爬蟲語料帶了來源網址時給一個開新分頁的連結,沒有的那筆不畫', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    renderDetail('c-1')

    await screen.findByText(/112-0001/)
    const links = screen.getAllByRole('link', { name: '來源網站' })
    // 兩筆案例只有第一筆有網址;沒有的那筆不得畫出點下去會失敗的連結
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAttribute(
      'href',
      'https://web.law.ntpc.gov.tw/Scripts/Su_contents03.aspx?EANO=1120001',
    )
    expect(links[0]).toHaveAttribute('target', '_blank')
    expect(links[0]).toHaveAttribute('rel', expect.stringContaining('noopener'))
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
    // 教示條款抽不到,欄位仍在(限定在那一欄比對:其他抽不到的欄位也會是「—」)
    const notice = screen.getByText('教示條款').closest('.f1-field')
    expect(within(notice).getByText('—')).toBeInTheDocument()
  })

  it('參考依據頁的案例可以開原文,與草稿頁的依據面板一致', async () => {
    api.getCase.mockResolvedValue({
      ...doneAdmissible,
      f3: [{ ...doneAdmissible.f3[0], source_key: 'markdown/訴願決定書/112-0001.md' }],
    })
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^參考依據/ })
    await user.click(rail(/^參考依據/))

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
    // 預設落點要先落定:seen 是在 effect 裡才記的,搶在它之前斷言會看到尚未消失的點
    await waitFor(() => expect(isCurrent(/^決定書草稿/)).toBe(true))

    // done 案件預設落在決定書草稿,所以草稿沒有點,其餘跑出結果的階段都有
    expect(within(rail(/^F1 擷取/)).getByLabelText('有新結果')).toBeInTheDocument()
    expect(within(rail(/^程序審查/)).getByLabelText('有新結果')).toBeInTheDocument()
    expect(within(rail(/^參考依據/)).getByLabelText('有新結果')).toBeInTheDocument()
    expect(within(rail(/^決定書草稿/)).queryByLabelText('有新結果')).toBeNull()
  })

  it('點進去看過就不再標點', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^程序審查/ })

    await user.click(rail(/^程序審查/))

    expect(within(rail(/^程序審查/)).queryByLabelText('有新結果')).toBeNull()
    expect(within(rail(/^參考依據/)).getByLabelText('有新結果')).toBeInTheDocument()
  })

  it('還沒跑出結果的階段不標點——那不是「有東西可看」', async () => {
    api.getCase.mockResolvedValue(processingAt('screening'))
    renderDetail()
    await screen.findByRole('button', { name: /^參考依據/ })

    expect(within(rail(/^參考依據/)).queryByLabelText('有新結果')).toBeNull()
  })
})

describe('決定書版面', () => {
  it('版本衝突(409)時保留使用者輸入並重新載入', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const conflict = new Error('這份草稿已被他人更新,請重新載入後再改')
    conflict.status = 409
    api.updateDraftText.mockRejectedValueOnce(conflict)
    const user = userEvent.setup()
    renderDetail()
    await user.type(await screen.findByLabelText('決定書全文'), '我打的字')
    api.getCase.mockClear()
    await user.click(screen.getByRole('button', { name: '儲存修改' }))

    expect(screen.getByText(/已被他人更新/)).toBeInTheDocument()
    expect(screen.getByLabelText('決定書全文')).toHaveValue(
      '新北市政府訴願決定書 主文 訴願駁回。 事實 事實原文 理由 理由原文我打的字',
    ) // 不沖掉他打的字
    expect(api.getCase).toHaveBeenCalledTimes(1)
  })

  it('標記定稿後仍可再修改(定稿不鎖)', async () => {
    api.getCase.mockResolvedValue({ ...doneAdmissible, finalized_at: '2026-08-27T05:00:00+00:00' })
    const user = userEvent.setup()
    renderDetail()
    await screen.findByLabelText('決定書全文')

    expect(screen.getByRole('button', { name: '重新定稿' })).toBeInTheDocument()
    expect(screen.getByText(/定稿於/)).toBeInTheDocument()
    expect(screen.getByLabelText('決定書全文')).toBeEnabled()
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

/** 階段頁的「參考依據」區塊。草稿頁的依據欄是 hidden 而非卸載,兩邊同時在 DOM 裡。 */
function refsStage() {
  return document.querySelector('.refs-stack')
}

describe('決定書草稿:整份可改、兩種下載', () => {
  it('決定書是一個文字框,存檔送 draft-text', async () => {
    api.getCase.mockResolvedValue({
      ...doneAdmissible,
      draft_plain_text: '新北市政府訴願決定書 主文:訴願駁回。',
    })
    const user = userEvent.setup()
    renderDetail()

    const box = await screen.findByLabelText('決定書全文')
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
    api.getCase.mockResolvedValue(doneAdmissible)
    const user = userEvent.setup()
    renderDetail()
    await screen.findByLabelText('決定書全文')

    await user.click(screen.getByRole('button', { name: '下載 Word' }))
    expect(api.downloadDraftDocx).toHaveBeenCalledWith('c-1')

    await user.click(screen.getByRole('button', { name: '下載 PDF 寄審' }))
    expect(api.downloadDraftPdf).toHaveBeenCalledWith('c-1')
  })
})

describe('參考見解的存檔原文', () => {
  it('回傳 file 時改走帶金鑰抓檔再開新分頁,不把金鑰放進網址', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    api.getSource.mockResolvedValue({ file: 'reference/行政函釋/某函釋.pdf' })
    api.openSourceFile.mockResolvedValue('blob:fake')
    api.getCase.mockResolvedValue({
      ...doneAdmissible,
      f2_refs: [
        {
          ...doneAdmissible.f2_refs[1],
          source_key: 'reference/行政函釋/某函釋.pdf',
        },
      ],
    })
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^參考依據/ })
    await user.click(rail(/^參考依據/))

    await user.click(within(refsStage()).getByRole('button', { name: '原文' }))

    await waitFor(() => expect(api.openSourceFile).toHaveBeenCalledWith('reference/行政函釋/某函釋.pdf'))
    expect(open).toHaveBeenCalledWith('blob:fake', '_blank', 'noopener')
    open.mockRestore()
  })
})

describe('F1 訴願事實與已修改標記', () => {
  it('訴願書那一組畫得出訴願人自述的事實', async () => {
    api.getCase.mockResolvedValue({
      ...doneAdmissible,
      f1: { ...doneAdmissible.f1, appeal_facts: ['114年6月27日在三峽區遭稽查'] },
    })
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^F1 擷取/ })
    await user.click(rail(/^F1 擷取/))

    const group = screen.getByRole('group', { name: '訴願書' })
    expect(within(group).getByText('訴願事實')).toBeInTheDocument()
    expect(within(group).getByText('114年6月27日在三峽區遭稽查')).toBeInTheDocument()
  })

  it('承辦人改過的欄位換色標記,沒改過的不標', async () => {
    api.getCase.mockResolvedValue({
      ...doneAdmissible,
      f1: { ...doneAdmissible.f1, service_date: '114年6月1日' },
      f1_system: { ...doneAdmissible.f1, service_date: '114年5月28日' },
    })
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^F1 擷取/ })
    await user.click(rail(/^F1 擷取/))

    // 改過的那一欄看得出是人改的,否則承辦人下次分不清哪些字是自己確認過的
    const edited = screen.getByText('送達時間').closest('.f1-field')
    expect(edited.className).toContain('f1-field--edited')
    expect(within(edited).getByTitle(/模型原本擷取/)).toHaveTextContent('114年5月28日')

    const untouched = screen.getByText('原處分字號').closest('.f1-field')
    expect(untouched.className).not.toContain('f1-field--edited')
  })

  it('沒有修改紀錄的案件一欄都不標', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^F1 擷取/ })
    await user.click(rail(/^F1 擷取/))

    expect(document.querySelectorAll('.f1-field--edited')).toHaveLength(0)
  })
})

describe('草稿頁的參考依據欄', () => {
  it('法規、參考見解、案例三組都在同一欄,與階段頁一致', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    renderDetail()
    await screen.findByLabelText('決定書全文')

    const panel = screen.getByRole('complementary', { name: '承辦參考依據' })
    expect(within(panel).getByText('參考法規(F2)')).toBeInTheDocument()
    expect(within(panel).getByText('參考見解(F2+)')).toBeInTheDocument()
    expect(within(panel).getByText('參考案例(F3)')).toBeInTheDocument()
    expect(within(panel).getByText('釋字第469號')).toBeInTheDocument()
  })

  it('不受理案件隱藏法規那一組,參考見解與案例照常', async () => {
    api.getCase.mockResolvedValue(doneInadmissible)
    renderDetail('c-2')
    await screen.findByLabelText('決定書全文')

    const panel = screen.getByRole('complementary', { name: '承辦參考依據' })
    expect(within(panel).queryByText('參考法規(F2)')).toBeNull()
    expect(within(panel).getByText('參考見解(F2+)')).toBeInTheDocument()
    expect(within(panel).getByText('參考案例(F3)')).toBeInTheDocument()
  })
})

describe('F2+ 參考見解', () => {
  it('兩種異質見解都畫得出來:釋字無發文機關無原文鈕,函釋有發文機關可開原文', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^參考依據/ })

    await user.click(rail(/^參考依據/))

    const yizi = within(refsStage()).getByText('釋字第469號').closest('.reference-ref')
    expect(within(yizi).getByText('司法院釋字')).toBeInTheDocument()
    expect(within(yizi).getByText(/怠於執行職務之國家賠償責任/)).toBeInTheDocument()
    expect(within(yizi).getByText(/未收錄/)).toBeInTheDocument()
    expect(within(yizi).queryByRole('button', { name: '原文' })).toBeNull()

    const hanshi = within(refsStage())
      .getByText('法務部 法律字第0930014628號')
      .closest('.reference-ref')
    expect(within(hanshi).getByText('法務部')).toBeInTheDocument() // 發文機關獨立一欄,非名稱的一部分
    expect(within(hanshi).getByText('民國 93 年 04 月 13 日')).toBeInTheDocument()
    await user.click(within(hanshi).getByRole('button', { name: '原文' }))
    expect(api.getSource).toHaveBeenCalledWith(
      'markdown/行政函釋/法務部93年4月13日法律字0930014628號函-寄存送達.md',
    )
  })

  it('左欄只有一個參考依據節點,三組依法規→參考見解→案例的順序同頁排列', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^參考依據/ })

    // 左欄不再有 F2 / F2+ / F3 三個獨立節點
    const railLabels = screen
      .getAllByRole('button')
      .map((b) => b.textContent)
      .filter((t) => /^(F2 法規|F2\+ 參考見解|F3 案例|參考依據)/.test(t))
    expect(railLabels).toHaveLength(1)
    expect(railLabels[0]).toMatch(/^參考依據/)

    await user.click(rail(/^參考依據/))
    const titles = screen
      .getAllByRole('heading', { level: 3 })
      .map((h) => h.textContent)
      .filter((t) => /F2|F3/.test(t))
    expect(titles).toEqual(['推薦法規(F2)', '參考見解(F2+)', '相似案例(F3)'])
  })

  it('不受理案件一樣有參考見解,不出現「不適用」', async () => {
    api.getCase.mockResolvedValue(doneInadmissible)
    const user = userEvent.setup()
    renderDetail('c-2')
    await screen.findByRole('button', { name: /^參考依據/ })

    expect(rail(/^參考依據/)).not.toHaveTextContent('不適用')
    await user.click(rail(/^參考依據/))
    expect(within(refsStage()).getByText('釋字第469號')).toBeInTheDocument()
  })

  it('三態可區分:正在跑才說檢索中,沒跑過說尚未執行,跑完沒結果說未檢索到', async () => {
    // 已審結的舊案件沒有這一段結果,不能一直說「檢索中…」——那與正在跑分不出來
    api.getCase.mockResolvedValue({ ...doneAdmissible, f2_refs: null })
    const user = userEvent.setup()
    let view = renderDetail()
    await screen.findByRole('button', { name: /^參考依據/ })
    await user.click(rail(/^參考依據/))
    expect(within(refsStage()).getByText('尚未執行')).toBeInTheDocument()
    expect(within(refsStage()).queryByText('檢索中…')).toBeNull()
    view.unmount()

    api.getCase.mockResolvedValue({
      ...doneAdmissible,
      status: 'processing',
      current_stage: 'f2_refs',
      f2_refs: null,
    })
    view = renderDetail()
    await screen.findByRole('button', { name: /^參考依據/ })
    await user.click(rail(/^參考依據/))
    expect(within(refsStage()).getByText('檢索中…')).toBeInTheDocument()
    view.unmount()

    api.getCase.mockResolvedValue({ ...doneAdmissible, f2_refs: [] })
    renderDetail()
    await screen.findByRole('button', { name: /^參考依據/ })
    await user.click(rail(/^參考依據/))
    expect(within(refsStage()).getByText('未檢索到相關參考見解。')).toBeInTheDocument()
  })
})

describe('F1 卷證總匯表', () => {
  const withAnswer = {
    ...doneAdmissible,
    f1: {
      ...doneAdmissible.f1,
      service_date: '114年5月28日',
      service_method: '寄存於板橋郵局',
      answer_statement: '本件訴願駁回。',
      answer_self_revoked: '否',
      answer_arguments: ['訴願人確有違規事實', '裁處於法有據'],
    },
  }

  const group = (name) => screen.getByRole('group', { name })

  it('依四份文件分組,各欄落在自己的來源文件底下', async () => {
    api.getCase.mockResolvedValue(withAnswer)
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^F1 擷取/ })
    await user.click(rail(/^F1 擷取/))

    expect(within(group('訴願書')).getByText('王○明')).toBeInTheDocument()
    expect(within(group('送達證書')).getByText('寄存於板橋郵局')).toBeInTheDocument()
    expect(within(group('原處分書')).getByText('北環稽字第1130001號')).toBeInTheDocument()
    expect(within(group('訴願答辯書')).getByText('本件訴願駁回。')).toBeInTheDocument()
    expect(within(group('訴願答辯書')).getByText(/訴願人確有違規事實/)).toBeInTheDocument()
    // 送達方式屬送達證書,不該同時出現在原處分書那組
    expect(within(group('原處分書')).queryByText('寄存於板橋郵局')).toBeNull()
  })

  it('機關尚未答辯時,那一組講得出是「尚未答辯」而不是留白或「無」', async () => {
    api.getCase.mockResolvedValue(doneAdmissible) // 樣本無答辯書欄位
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^F1 擷取/ })
    await user.click(rail(/^F1 擷取/))

    expect(within(group('訴願答辯書')).getByText('尚未答辯')).toBeInTheDocument()
    expect(within(group('訴願答辯書')).queryByText('無')).toBeNull()
  })

  it('單值欄位可就地改,送出的是整份案件資訊且只有該欄變了', async () => {
    api.getCase.mockResolvedValue(withAnswer)
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^F1 擷取/ })
    await user.click(rail(/^F1 擷取/))

    await user.click(within(group('送達證書')).getByRole('button', { name: '修改送達方式' }))
    const input = screen.getByLabelText('送達方式')
    await user.clear(input)
    await user.type(input, '本人簽收')
    await user.click(screen.getByRole('button', { name: '儲存' }))

    expect(api.updateCaseInfo).toHaveBeenCalledWith('c-1', {
      ...withAnswer.f1,
      service_method: '本人簽收',
    })
  })

  it('清單欄位以換行分行,儲存時拆成陣列並丟掉空行', async () => {
    api.getCase.mockResolvedValue(withAnswer)
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^F1 擷取/ })
    await user.click(rail(/^F1 擷取/))

    await user.click(within(group('訴願答辯書')).getByRole('button', { name: '修改機關主張' }))
    const box = screen.getByLabelText('機關主張')
    await user.clear(box)
    await user.type(box, '第一項主張\n\n第二項主張')
    await user.click(screen.getByRole('button', { name: '儲存' }))

    expect(api.updateCaseInfo).toHaveBeenCalledWith('c-1', {
      ...withAnswer.f1,
      answer_arguments: ['第一項主張', '第二項主張'],
    })
  })

  it('分析進行中不給改,不讓使用者送出後才看到 409', async () => {
    api.getCase.mockResolvedValue({ ...withAnswer, status: 'processing', current_stage: 'f2' })
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^F1 擷取/ })
    await user.click(rail(/^F1 擷取/))

    expect(screen.queryByRole('button', { name: '修改送達方式' })).toBeNull()
    expect(screen.getByText(/分析進行中/)).toBeInTheDocument()
  })

  it('儲存失敗時錯誤看得見,而且不清掉剛打的字', async () => {
    api.getCase.mockResolvedValue(withAnswer)
    api.updateCaseInfo.mockRejectedValueOnce(new Error('案件分析中,無法修改案件資訊'))
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^F1 擷取/ })
    await user.click(rail(/^F1 擷取/))

    await user.click(within(group('送達證書')).getByRole('button', { name: '修改送達方式' }))
    const input = screen.getByLabelText('送達方式')
    await user.clear(input)
    await user.type(input, '本人簽收')
    await user.click(screen.getByRole('button', { name: '儲存' }))

    expect(await screen.findByText(/無法修改案件資訊/)).toBeInTheDocument()
    expect(screen.getByLabelText('送達方式')).toHaveValue('本人簽收')
  })
})
