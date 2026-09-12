import { describe, it, expect, vi } from 'vitest'
import { fireEvent, screen, within } from '@testing-library/react'
import { api } from './apiMock.js'
import { collectingAllMatched, collectingWithMismatch } from './fixtures.js'
import { renderDocuments, pdf, isCurrent } from './renderCaseDetail.jsx'

vi.mock('../src/api', async () => (await import('./apiMock.js')).api)

describe('文件確認頁', () => {
  it('四份皆符合時可以開始分析', async () => {
    api.getCase.mockResolvedValue(collectingAllMatched)
    await renderDocuments('c-5')

    expect(isCurrent(/^文件確認/)).toBe(true)
    expect(screen.getByRole('button', { name: '開始分析' })).toHaveAttribute('aria-disabled', 'false')
    // 四槽皆為 matched:true,徽章應顯示已確認而非「無法確認」
    expect(screen.getAllByText(/確認為此文件/).length).toBe(4)
  })

  it('訴願答辯書標必填,只有送達證書是選填', async () => {
    api.getCase.mockResolvedValue(collectingAllMatched)
    await renderDocuments('c-5')

    const answerHead = screen.getByText('訴願答辯書').closest('.doc-slot__head')
    expect(within(answerHead).getByText('必填')).toBeInTheDocument()
    expect(screen.getAllByText('必填')).toHaveLength(3)
    expect(screen.getAllByText('選填')).toHaveLength(1)
  })

  it('舊案的答辯書空槽不再視為「未提供」,但不再擋住開始分析(只警示不擋)', async () => {
    api.getCase.mockResolvedValue({
      ...collectingAllMatched,
      documents: {
        ...collectingAllMatched.documents,
        answer: {
          slot: 'answer',
          source: 'text',
          text: '',
          check: { matched: null, method: 'none', note: '文件內容為空,無法確認' },
        },
      },
    })
    await renderDocuments('c-5')

    const slot = screen.getByText('訴願答辯書').closest('.doc-slot')
    expect(within(slot).queryByText(/未提供/)).toBeNull()
    expect(screen.getByRole('button', { name: '開始分析' })).toHaveAttribute('aria-disabled', 'false')
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
    await renderDocuments('c-5')

    expect(screen.getByText(/訴願答辯書尚未確認無誤/)).toBeInTheDocument()
  })

  it('答辯書有內容但判斷不符時,標示不符原因但不擋開始分析', async () => {
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
    await renderDocuments('c-5')

    expect(screen.getByText(/不是訴願答辯書/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '開始分析' })).toHaveAttribute('aria-disabled', 'false')
  })

  it('有文件判斷不符時,仍可開始分析;f1 尚未產生時不警示直接呼叫', async () => {
    api.getCase.mockResolvedValue(collectingWithMismatch)
    const { user } = await renderDocuments('c-6')

    expect(screen.getByText(/不是送達證書/)).toBeInTheDocument()
    const analyze = screen.getByRole('button', { name: '開始分析' })
    expect(analyze).toHaveAttribute('aria-disabled', 'false')
    api.reanalyzeCase.mockClear()
    api.getCase.mockClear()
    await user.click(analyze)
    expect(api.reanalyzeCase).toHaveBeenCalledWith('c-6', 'f1')
    expect(api.getCase).toHaveBeenCalledTimes(1)
  })

  it('點擊開始分析呼叫 reanalyzeCase(id,"f1") 並重新載入案件', async () => {
    api.getCase.mockResolvedValue(collectingAllMatched)
    const { user } = await renderDocuments('c-5')
    api.getCase.mockClear()
    api.reanalyzeCase.mockClear()

    await user.click(screen.getByRole('button', { name: '開始分析' }))
    expect(api.reanalyzeCase).toHaveBeenCalledWith('c-5', 'f1')
    expect(api.getCase).toHaveBeenCalledTimes(1) // 分析後重新載入
  })

  it('f1 已存在時,開始分析先警示;取消不呼叫,確認才呼叫並重新載入', async () => {
    api.getCase.mockResolvedValue({ ...collectingAllMatched, case_id: 'c-9', f1: { appellant: '王○明' } })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const { user } = await renderDocuments('c-9')
    const analyze = screen.getByRole('button', { name: '開始分析' })
    api.reanalyzeCase.mockClear()

    await user.click(analyze)
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(api.reanalyzeCase).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    api.getCase.mockClear()
    await user.click(analyze)
    expect(api.reanalyzeCase).toHaveBeenCalledWith('c-9', 'f1')
    expect(api.getCase).toHaveBeenCalledTimes(1)
    confirmSpy.mockRestore()
  })

  it('案件正在分析中時,開始分析停用且點了不呼叫', async () => {
    api.getCase.mockResolvedValue({ ...collectingAllMatched, status: 'processing', current_stage: 'f1' })
    const { user } = await renderDocuments('c-5')
    const analyze = screen.getByRole('button', { name: '開始分析' })
    expect(analyze).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByText('分析進行中')).toBeInTheDocument()
    api.reanalyzeCase.mockClear()
    await user.click(analyze)
    expect(api.reanalyzeCase).not.toHaveBeenCalled()
  })

  it('重傳可以直接給 PDF,送出的 formData 帶 file', async () => {
    api.getCase.mockResolvedValue(collectingWithMismatch)
    const { user } = await renderDocuments('c-6')

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
    const { user } = await renderDocuments('c-6')

    const serviceSlot = screen.getByText('送達證書').closest('.doc-slot')
    await user.click(within(serviceSlot).getByRole('button', { name: '重新上傳' }))

    expect(within(serviceSlot).getByText(/不是送達證書/)).toBeInTheDocument()
  })

  it('重傳只收 PDF,沒有貼上文字的輸入框', async () => {
    api.getCase.mockResolvedValue(collectingWithMismatch)
    const { user } = await renderDocuments('c-6')

    const serviceSlot = screen.getByText('送達證書').closest('.doc-slot')
    await user.click(within(serviceSlot).getByRole('button', { name: '重新上傳' }))

    expect(within(serviceSlot).queryByRole('textbox')).toBeNull()
    expect(within(serviceSlot).getByText(/拖曳 PDF/)).toBeInTheDocument()
  })

  it('重傳的落件框拒收非 PDF,不讓壞檔走到後端', async () => {
    api.getCase.mockResolvedValue(collectingWithMismatch)
    const { user } = await renderDocuments('c-6')

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

  it('換一槽重傳不把上一槽挑好的檔案帶過去', async () => {
    api.getCase.mockResolvedValue(collectingWithMismatch)
    const { user } = await renderDocuments('c-6')

    const serviceSlot = screen.getByText('送達證書').closest('.doc-slot')
    await user.click(within(serviceSlot).getByRole('button', { name: '重新上傳' }))
    await user.upload(within(serviceSlot).getByLabelText('送達證書 PDF'), pdf('送達證書.pdf'))

    const appealSlot = screen.getByText('訴願書').closest('.doc-slot')
    await user.click(within(appealSlot).getByRole('button', { name: '重新上傳' }))

    expect(within(appealSlot).queryByText('送達證書.pdf')).toBeNull()
    expect(within(appealSlot).getByRole('button', { name: '送出' })).toBeDisabled()
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
    await renderDocuments('c-5')

    const serviceSlot = screen.getByText('送達證書').closest('.doc-slot')
    // 來源直接寫當初上傳的檔名,承辦人對得回自己的卷宗
    expect(within(serviceSlot).getByRole('button', { name: '預覽送達證書' })).toHaveTextContent(
      '03_送達證書.pdf',
    )
    expect(within(serviceSlot).getByText(/OCR 取字/)).toBeInTheDocument()
    // 貼上文字的槽沒有檔案可預覽,也不該跟著標 OCR,否則這個提醒等於沒說
    const appealSlot = screen.getByText('訴願書').closest('.doc-slot')
    expect(within(appealSlot).getByText('文字輸入')).toBeInTheDocument()
    expect(within(appealSlot).queryByRole('button', { name: /^預覽/ })).toBeNull()
  })

  it('PDF 槽沒有檔名(舊資料)時顯示「PDF」文字,不給無法預覽的按鈕', async () => {
    api.getCase.mockResolvedValue({
      ...collectingAllMatched,
      documents: {
        ...collectingAllMatched.documents,
        disposition: {
          slot: 'disposition',
          source: 'pdf',
          text: '原處分書全文',
          check: { matched: true, method: 'rule', note: '' },
        },
      },
    })
    await renderDocuments('c-5')

    const dispositionSlot = screen.getByText('原處分書').closest('.doc-slot')
    expect(within(dispositionSlot).getByText('PDF')).toBeInTheDocument()
    expect(within(dispositionSlot).queryByRole('button', { name: /^預覽/ })).toBeNull()
  })

  it('PDF 槽的檔名按鈕點擊後,帶金鑰抓 blob 開新分頁預覽原始檔案', async () => {
    api.getCase.mockResolvedValue({
      ...collectingAllMatched,
      documents: {
        ...collectingAllMatched.documents,
        appeal: {
          slot: 'appeal',
          source: 'pdf',
          filename: '訴願書.pdf',
          check: { matched: true, method: 'rule', note: '' },
        },
      },
    })
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    api.getDocumentFile.mockResolvedValue('blob:fake-doc')
    const { user } = await renderDocuments('c-5')

    const appealSlot = screen.getByText('訴願書').closest('.doc-slot')
    await user.click(within(appealSlot).getByRole('button', { name: '預覽訴願書' }))

    expect(api.getDocumentFile).toHaveBeenCalledWith('c-5', 'appeal')
    expect(open).toHaveBeenCalledWith('blob:fake-doc', '_blank', 'noopener')
    open.mockRestore()
  })

  it('重傳遇 409(案件已離開收案階段)時同步真實狀態', async () => {
    api.getCase.mockResolvedValue(collectingWithMismatch)
    const conflict = new Error('案件已開始分析,無法再修改文件')
    conflict.status = 409
    api.replaceDocument.mockRejectedValueOnce(conflict)
    const { user } = await renderDocuments('c-6')

    const serviceSlot = screen.getByText('送達證書').closest('.doc-slot')
    await user.click(within(serviceSlot).getByRole('button', { name: '重新上傳' }))
    await user.upload(within(serviceSlot).getByLabelText('送達證書 PDF'), pdf('送達證書.pdf'))
    api.getCase.mockClear()
    await user.click(within(serviceSlot).getByRole('button', { name: '送出' }))

    expect(screen.getByText(/已開始分析/)).toBeInTheDocument()
    expect(api.getCase).toHaveBeenCalledTimes(1)
  })

  it('重新上傳單一文件槽,送出後呼叫 replaceDocument 並重新載入', async () => {
    api.getCase.mockResolvedValue(collectingWithMismatch)
    const { user } = await renderDocuments('c-6')

    const serviceSlot = screen.getByText('送達證書').closest('.doc-slot')
    await user.click(within(serviceSlot).getByRole('button', { name: '重新上傳' }))
    await user.upload(within(serviceSlot).getByLabelText('送達證書 PDF'), pdf('新的送達證書.pdf'))
    api.getCase.mockClear()
    await user.click(within(serviceSlot).getByRole('button', { name: '送出' }))

    expect(api.replaceDocument).toHaveBeenCalledWith('c-6', 'service', expect.anything())
    expect(api.getCase).toHaveBeenCalledTimes(1)
  })
})
