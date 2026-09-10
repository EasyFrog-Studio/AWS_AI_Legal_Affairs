import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
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

describe('新建案件(三檔上傳)', () => {
  it('三個文件槽都是文字貼上前,送出按鈕停用', () => {
    renderNewCase()
    expect(screen.getByRole('button', { name: '送出並確認文件' })).toBeDisabled()
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

    expect(screen.getByText('訴願答辯書')).toBeInTheDocument()
    expect(screen.getByText(/選填/)).toBeInTheDocument()

    for (const btn of screen.getAllByRole('button', { name: '貼上文字' })) {
      await user.click(btn)
    }
    const textareas = screen.getAllByPlaceholderText(/請貼上.+全文/)
    await user.type(textareas[0], 'a')
    await user.type(textareas[1], 'b')
    await user.type(textareas[2], 'c')

    expect(screen.getByRole('button', { name: '送出並確認文件' })).toBeEnabled()
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
})
