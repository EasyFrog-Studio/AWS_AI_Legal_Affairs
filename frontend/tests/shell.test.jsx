import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import App from '../src/App.jsx'
import { api } from './apiMock.js'
import { listRows } from './fixtures.js'

vi.mock('../src/api', async () => (await import('./apiMock.js')).api)

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

describe('App shell', () => {
  it('登入頁沒有左欄;其他頁有全域導覽', async () => {
    api.listCases.mockResolvedValue(listRows)
    const { unmount } = renderAt('/login')
    expect(screen.queryByRole('navigation', { name: '全域導覽' })).toBeNull()
    unmount()
    renderAt('/')
    const nav = await screen.findByRole('navigation', { name: '全域導覽' })
    expect(nav).toHaveTextContent('案件清單')
    expect(nav).toHaveTextContent('新建案件')
    expect(screen.getByRole('button', { name: '登出' })).toBeInTheDocument()
  })
})
