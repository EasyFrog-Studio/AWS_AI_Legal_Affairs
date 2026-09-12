import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import NewCase from '../src/pages/NewCase.jsx'
import { api } from './apiMock.js'

vi.mock('../src/api', async () => (await import('./apiMock.js')).api)
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

const mockNavigate = vi.fn()

function renderNewCase() {
  return render(
    <MemoryRouter initialEntries={['/new']}>
      <Routes>
        <Route path="/new" element={<NewCase />} />
      </Routes>
    </MemoryRouter>,
  )
}

function pdf(name) {
  return new File(['%PDF-1.4'], name, { type: 'application/pdf' })
}

/** 三個必填槽各上傳一份 PDF 後送出——本輪的案號測試都只差在案號那一格。 */
async function fillRequiredSlotsAndSubmit(user) {
  await user.upload(screen.getByLabelText('訴願書 PDF'), pdf('訴願書.pdf'))
  await user.upload(screen.getByLabelText('原處分書 PDF'), pdf('原處分書.pdf'))
  await user.upload(screen.getByLabelText('訴願答辯書 PDF'), pdf('答辯書.pdf'))
  await user.click(screen.getByRole('button', { name: '送出並確認文件' }))
}

describe('新增案件(四檔上傳)', () => {
  it('只收 PDF:沒有分頁標籤,也沒有貼上文字的輸入框', () => {
    renderNewCase()

    expect(screen.queryByRole('button', { name: '貼上文字' })).toBeNull()
    expect(screen.queryByRole('button', { name: '上傳 PDF' })).toBeNull()
    expect(screen.queryAllByRole('textbox')).toHaveLength(1) // 只剩案號那一格
    expect(screen.getAllByText(/拖曳 PDF/)).toHaveLength(4)
  })

  it('必填三槽都沒帶檔案前,送出按鈕按不動', async () => {
    api.createCase.mockClear()
    const user = userEvent.setup()
    renderNewCase()

    const submit = screen.getByRole('button', { name: '送出並確認文件' })
    expect(submit).toHaveAttribute('aria-disabled', 'true')
    await user.click(submit)
    expect(api.createCase).not.toHaveBeenCalled()
  })

  it('只有送達證書標選填,留空仍可送出且不帶那個欄位', async () => {
    api.createCase.mockClear()
    api.createCase.mockResolvedValue({ case_id: 'c-new', documents: {} })
    const user = userEvent.setup()
    renderNewCase()

    const serviceHead = screen.getByText('送達證書').closest('.doc-slot__head')
    expect(within(serviceHead).getByText('選填')).toBeInTheDocument()
    const answerHead = screen.getByText('訴願答辯書').closest('.doc-slot__head')
    expect(within(answerHead).getByText('必填')).toBeInTheDocument()
    expect(screen.getAllByText('必填')).toHaveLength(3)

    await fillRequiredSlotsAndSubmit(user)

    const formData = api.createCase.mock.calls[0][0]
    expect(formData.get('appeal_file').name).toBe('訴願書.pdf')
    expect(formData.get('disposition_file').name).toBe('原處分書.pdf')
    expect(formData.get('answer_file').name).toBe('答辯書.pdf')
    // 空的選填槽整個不送,而不是帶一個空欄位進去
    expect(formData.get('service_file')).toBeNull()
    expect(mockNavigate).toHaveBeenCalledWith('/cases/c-new')
  })

  it('未附送達證書時,頁面要講出期間一律視為未逾期', () => {
    renderNewCase()

    expect(screen.getByText(/未附送達證書/)).toHaveTextContent('未逾期')
  })

  it('四槽都附上時一併送出', async () => {
    api.createCase.mockClear()
    api.createCase.mockResolvedValue({ case_id: 'c-all', documents: {} })
    const user = userEvent.setup()
    renderNewCase()

    await user.upload(screen.getByLabelText('送達證書 PDF'), pdf('送達證書.pdf'))
    await fillRequiredSlotsAndSubmit(user)

    const formData = api.createCase.mock.calls[0][0]
    expect(formData.get('service_file').name).toBe('送達證書.pdf')
    expect(formData.get('answer_file').name).toBe('答辯書.pdf')
  })

  it('案號欄位在文件槽之前,填了就一併送出', async () => {
    api.createCase.mockClear()
    api.createCase.mockResolvedValue({ case_id: '114年訴字第0123號', documents: {} })
    const user = userEvent.setup()
    renderNewCase()

    const caseIdInput = screen.getByLabelText(/案號/)
    // 在最上面:DOM 順序必須早於第一個文件槽
    const firstSlotLabel = screen.getByText('訴願書')
    expect(
      caseIdInput.compareDocumentPosition(firstSlotLabel) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()

    await user.type(caseIdInput, '114年訴字第0123號')
    await fillRequiredSlotsAndSubmit(user)

    expect(api.createCase.mock.calls[0][0].get('case_id')).toBe('114年訴字第0123號')
  })

  it('案號留白時整個欄位不送出,交給後端產生', async () => {
    api.createCase.mockClear()
    api.createCase.mockResolvedValue({ case_id: 'c-auto', documents: {} })
    const user = userEvent.setup()
    renderNewCase()

    await user.type(screen.getByLabelText(/案號/), '   ')
    await fillRequiredSlotsAndSubmit(user)

    expect(api.createCase.mock.calls[0][0].get('case_id')).toBeNull()
  })

  it('案號重複時顯示後端訊息,不附「請確認檔案格式」的誤導提示', async () => {
    mockNavigate.mockClear()
    api.createCase.mockClear()
    const conflict = new Error('案號 114年訴字第0123號 已存在,請換一個或留白由系統產生')
    conflict.status = 409
    conflict.detail = { message: conflict.message, field: 'case_id' }
    api.createCase.mockRejectedValue(conflict)
    const user = userEvent.setup()
    renderNewCase()

    await user.type(screen.getByLabelText(/案號/), '114年訴字第0123號')
    await fillRequiredSlotsAndSubmit(user)

    expect(await screen.findByText(/案號 114年訴字第0123號 已存在/)).toBeInTheDocument()
    expect(screen.queryByText(/請確認檔案格式/)).toBeNull()
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('送出失敗時顯示錯誤訊息,不導頁', async () => {
    mockNavigate.mockClear()
    api.createCase.mockRejectedValue(new Error('檔案讀取失敗'))
    const user = userEvent.setup()
    renderNewCase()

    await fillRequiredSlotsAndSubmit(user)

    expect(await screen.findByText('檔案讀取失敗')).toBeInTheDocument()
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('拖曳 PDF 到落件框即帶入該槽,送出時一併上傳', async () => {
    api.createCase.mockClear()
    api.createCase.mockResolvedValue({ case_id: 'c-drop', documents: {} })
    const user = userEvent.setup()
    renderNewCase()

    const zone = screen.getByLabelText('訴願書 PDF').closest('.dropzone')
    const dropped = pdf('訴願書.pdf')
    fireEvent.drop(zone, { dataTransfer: { files: [dropped], types: ['Files'] } })
    expect(await screen.findByText('訴願書.pdf')).toBeInTheDocument()

    await user.upload(screen.getByLabelText('原處分書 PDF'), pdf('原處分書.pdf'))
    await user.upload(screen.getByLabelText('訴願答辯書 PDF'), pdf('答辯書.pdf'))
    await user.click(screen.getByRole('button', { name: '送出並確認文件' }))

    expect(api.createCase.mock.calls[0][0].get('appeal_file')).toBe(dropped)
  })

  it('拖進非 PDF 時擋下並說明,該槽仍視為未填', async () => {
    renderNewCase()
    const zone = screen.getByLabelText('訴願書 PDF').closest('.dropzone')
    const docx = new File(['x'], '訴願書.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })
    fireEvent.drop(zone, { dataTransfer: { files: [docx], types: ['Files'] } })

    expect(await screen.findByText(/只接受 PDF/)).toBeInTheDocument()
    expect(screen.queryByText('訴願書.docx')).toBeNull()
    expect(screen.getByRole('button', { name: '送出並確認文件' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
  })

  it('已帶入的檔案可移除,移除後回到可拖曳狀態', async () => {
    const user = userEvent.setup()
    renderNewCase()

    const input = screen.getByLabelText('原處分書 PDF')
    await user.upload(input, pdf('原處分書.pdf'))
    expect(await screen.findByText('原處分書.pdf')).toBeInTheDocument()

    const zone = input.closest('.dropzone')
    await user.click(within(zone).getByRole('button', { name: '移除' }))
    expect(screen.queryByText('原處分書.pdf')).toBeNull()
    expect(within(zone).getByText(/拖曳 PDF/)).toBeInTheDocument()
  })

  it('必填文件未齊時按送出,按鈕下方列出還缺哪幾份', async () => {
    api.createCase.mockClear()
    const user = userEvent.setup()
    renderNewCase()

    await user.upload(screen.getByLabelText('訴願書 PDF'), pdf('訴願書.pdf'))
    await user.click(screen.getByRole('button', { name: '送出並確認文件' }))

    const notice = await screen.findByText(/尚未提供/)
    expect(notice).toHaveTextContent('原處分書')
    expect(notice).toHaveTextContent('訴願答辯書')
    // 送達證書是選填,缺了不算缺件
    expect(notice).not.toHaveTextContent('送達證書')
    expect(api.createCase).not.toHaveBeenCalled()

    await user.upload(screen.getByLabelText('原處分書 PDF'), pdf('原處分書.pdf'))
    await user.upload(screen.getByLabelText('訴願答辯書 PDF'), pdf('答辯書.pdf'))
    expect(screen.queryByText(/尚未提供/)).toBeNull()
  })
})
