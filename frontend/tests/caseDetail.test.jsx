import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import App from '../src/App.jsx'
import CaseDetail from '../src/pages/CaseDetail.jsx'
import { api } from './apiMock.js'
import { doneAdmissible, doneInadmissible, processingAt, errorAtF2, listRows } from './fixtures.js'

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
