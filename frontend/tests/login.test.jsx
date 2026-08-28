import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import Login from '../src/pages/Login.jsx'
import { api } from './apiMock.js'

vi.mock('../src/api', async () => (await import('./apiMock.js')).api)

function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<div>案件清單頁</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Login 頁', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    api.setApiKey.mockClear()
  })

  it('送出時以 X-API-Key 驗證', async () => {
    fetch.mockResolvedValue({ ok: true })
    renderLogin()
    await userEvent.type(screen.getByLabelText('密碼'), 'secret')
    await userEvent.click(screen.getByRole('button', { name: '進入系統' }))
    expect(fetch).toHaveBeenCalledWith('/api/cases', { headers: { 'X-API-Key': 'secret' } })
  })

  it('驗證失敗:顯示錯誤、不存 key、不導向', async () => {
    fetch.mockResolvedValue({ ok: false })
    renderLogin()
    await userEvent.type(screen.getByLabelText('密碼'), 'bad')
    await userEvent.click(screen.getByRole('button', { name: '進入系統' }))
    expect(await screen.findByText('驗證失敗,請確認後重新輸入。')).toBeInTheDocument()
    expect(api.setApiKey).not.toHaveBeenCalled()
    expect(screen.queryByText('案件清單頁')).toBeNull()
    expect(document.querySelector('.login').dataset.state).toBe('error')
  })

  it('驗證成功:存 key 並導向', async () => {
    fetch.mockResolvedValue({ ok: true })
    renderLogin()
    await userEvent.type(screen.getByLabelText('密碼'), 'ok')
    await userEvent.click(screen.getByRole('button', { name: '進入系統' }))
    expect(await screen.findByText('案件清單頁', {}, { timeout: 2000 })).toBeInTheDocument()
    expect(api.setApiKey).toHaveBeenCalledWith('ok')
  })
})
