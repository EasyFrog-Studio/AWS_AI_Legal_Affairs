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

/** 三個必填槽切到文字分頁、各填一字後送出——本輪新增的案號測試都只差在案號那一格。 */
async function fillRequiredSlotsAndSubmit(user) {
  for (const btn of screen.getAllByRole('button', { name: '貼上文字' })) {
    await user.click(btn)
  }
  const textareas = screen.getAllByPlaceholderText(/請貼上.+全文/)
  await user.type(textareas[0], 'a')
  await user.type(textareas[1], 'b')
  await user.type(textareas[2], 'c')
  await user.click(screen.getByRole('button', { name: '送出並確認文件' }))
}

describe('新建案件(三檔上傳)', () => {
  it('三個文件槽都是文字貼上前,送出按鈕按不動', async () => {
    api.createCase.mockClear()
    const user = userEvent.setup()
    renderNewCase()

    const submit = screen.getByRole('button', { name: '送出並確認文件' })
    expect(submit).toHaveAttribute('aria-disabled', 'true')
    await user.click(submit)
    expect(api.createCase).not.toHaveBeenCalled()
  })

  it('三份文字皆填妥後可送出,成功後導向案件詳情頁', async () => {
    api.createCase.mockResolvedValue({ case_id: 'c-new', documents: {} })
    const user = userEvent.setup()
    renderNewCase()

    // 三個槽都切到「貼上文字」再輸入,PDF 是每槽預設分頁
    for (const btn of screen.getAllByRole('button', { name: '貼上文字' })) {
      await user.click(btn)
    }

    const textareas = screen.getAllByPlaceholderText(/請貼上.+全文/)
    expect(textareas).toHaveLength(4)
    await user.type(textareas[0], '訴願書全文內容')
    await user.type(textareas[1], '送達證書全文內容')
    await user.type(textareas[2], '原處分書全文內容')

    await user.click(screen.getByRole('button', { name: '送出並確認文件' }))

    expect(api.createCase).toHaveBeenCalledTimes(1)
    const formData = api.createCase.mock.calls[0][0]
    expect(formData.get('appeal_text')).toBe('訴願書全文內容')
    expect(formData.get('service_text')).toBe('送達證書全文內容')
    expect(formData.get('disposition_text')).toBe('原處分書全文內容')
    // 答辯書留空:不帶這個欄位,而不是帶一個空字串進去
    expect(formData.get('answer_text')).toBeNull()
    expect(mockNavigate).toHaveBeenCalledWith('/cases/c-new')
  })

  it('訴願答辯書是選填,標示選填且留空不擋送出', async () => {
    renderNewCase()
    const user = userEvent.setup()

    const answerHead = screen.getByText('訴願答辯書').closest('.doc-slot__head')
    expect(within(answerHead).getByText('選填')).toBeInTheDocument()
    // 其餘三槽標必填,承辦人不必回頭數哪幾份不能少
    expect(screen.getAllByText('必填')).toHaveLength(3)

    for (const btn of screen.getAllByRole('button', { name: '貼上文字' })) {
      await user.click(btn)
    }
    const textareas = screen.getAllByPlaceholderText(/請貼上.+全文/)
    await user.type(textareas[0], 'a')
    await user.type(textareas[1], 'b')
    await user.type(textareas[2], 'c')

    expect(screen.getByRole('button', { name: '送出並確認文件' })).toHaveAttribute(
      'aria-disabled',
      'false',
    )
  })

  it('有附答辯書時一併送出', async () => {
    api.createCase.mockClear()
    api.createCase.mockResolvedValue({ case_id: 'c-ans', documents: {} })
    const user = userEvent.setup()
    renderNewCase()

    for (const btn of screen.getAllByRole('button', { name: '貼上文字' })) {
      await user.click(btn)
    }
    const textareas = screen.getAllByPlaceholderText(/請貼上.+全文/)
    await user.type(textareas[0], 'a')
    await user.type(textareas[1], 'b')
    await user.type(textareas[2], 'c')
    await user.type(textareas[3], '訴願答辯書全文內容')

    await user.click(screen.getByRole('button', { name: '送出並確認文件' }))

    expect(api.createCase.mock.calls[0][0].get('answer_text')).toBe('訴願答辯書全文內容')
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

    for (const btn of screen.getAllByRole('button', { name: '貼上文字' })) {
      await user.click(btn)
    }
    const textareas = screen.getAllByPlaceholderText(/請貼上.+全文/)
    await user.type(textareas[0], 'a')
    await user.type(textareas[1], 'b')
    await user.type(textareas[2], 'c')
    await user.click(screen.getByRole('button', { name: '送出並確認文件' }))

    expect(await screen.findByText('檔案讀取失敗')).toBeInTheDocument()
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('拖曳 PDF 到落件框即帶入該槽,送出時一併上傳', async () => {
    api.createCase.mockClear()
    api.createCase.mockResolvedValue({ case_id: 'c-drop', documents: {} })
    const user = userEvent.setup()
    renderNewCase()

    const zone = screen.getByLabelText('訴願書 PDF').closest('.dropzone')
    const pdf = new File(['%PDF-1.4'], '訴願書.pdf', { type: 'application/pdf' })
    fireEvent.drop(zone, { dataTransfer: { files: [pdf], types: ['Files'] } })
    expect(await screen.findByText('訴願書.pdf')).toBeInTheDocument()

    for (const btn of screen.getAllByRole('button', { name: '貼上文字' }).slice(1)) {
      await user.click(btn)
    }
    const textareas = screen.getAllByPlaceholderText(/請貼上.+全文/)
    await user.type(textareas[0], 'b')
    await user.type(textareas[1], 'c')
    await user.click(screen.getByRole('button', { name: '送出並確認文件' }))

    expect(api.createCase.mock.calls[0][0].get('appeal_file')).toBe(pdf)
  })

  it('拖進非 PDF 時擋下並說明,該槽仍視為未填', async () => {
    renderNewCase()
    const zone = screen.getByLabelText('送達證書 PDF').closest('.dropzone')
    const docx = new File(['x'], '送達證書.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })
    fireEvent.drop(zone, { dataTransfer: { files: [docx], types: ['Files'] } })

    expect(await screen.findByText(/只接受 PDF/)).toBeInTheDocument()
    expect(screen.queryByText('送達證書.docx')).toBeNull()
    expect(screen.getByRole('button', { name: '送出並確認文件' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
  })

  it('已帶入的檔案可移除,移除後回到可拖曳狀態', async () => {
    const user = userEvent.setup()
    renderNewCase()

    const input = screen.getByLabelText('原處分書 PDF')
    const pdf = new File(['%PDF-1.4'], '原處分書.pdf', { type: 'application/pdf' })
    await user.upload(input, pdf)
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

    for (const btn of screen.getAllByRole('button', { name: '貼上文字' })) {
      await user.click(btn)
    }
    await user.type(screen.getByPlaceholderText('請貼上訴願書全文'), 'a')
    await user.click(screen.getByRole('button', { name: '送出並確認文件' }))

    const notice = await screen.findByText(/尚未提供/)
    expect(notice).toHaveTextContent('送達證書')
    expect(notice).toHaveTextContent('原處分書')
    // 答辯書是選填,缺了不算缺件
    expect(notice).not.toHaveTextContent('訴願答辯書')
    expect(notice).not.toHaveTextContent('訴願書、')
    expect(api.createCase).not.toHaveBeenCalled()

    await user.type(screen.getByPlaceholderText('請貼上送達證書全文'), 'b')
    await user.type(screen.getByPlaceholderText('請貼上原處分書全文'), 'c')
    expect(screen.queryByText(/尚未提供/)).toBeNull()
  })
})
