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

  it('對照資料過期時,頁面上就看得到該重抓的提示', async () => {
    api.listCases.mockResolvedValue(listRows)
    api.health.mockResolvedValueOnce({
      status: 'ok',
      provider: 'mock',
      warning: '國定假日表僅收錄至 2027 年,未及 2028 年,請執行 preprocessing/fetch_holidays.py 重抓',
    })
    renderAt('/')

    expect(await screen.findByText(/fetch_holidays.py/)).toBeInTheDocument()
  })

  it('health 沒有警告時不佔畫面', async () => {
    api.listCases.mockResolvedValue(listRows)
    api.health.mockResolvedValueOnce({ status: 'ok', provider: 'mock', warning: '' })
    renderAt('/')

    await screen.findByRole('table')
    expect(screen.queryByText(/對照資料須更新/)).toBeNull()
  })
})
