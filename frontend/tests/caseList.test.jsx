import { describe, it, expect, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import CaseList from '../src/pages/CaseList.jsx'
import { api } from './apiMock.js'
import { listRows } from './fixtures.js'

vi.mock('../src/api', async () => (await import('./apiMock.js')).api)

function dataRows() {
  const table = screen.getByRole('table')
  return within(table).getAllByRole('row').slice(1)
}

async function renderList(rows) {
  api.listCases.mockClear()
  api.listCases.mockResolvedValue(rows)
  render(
    <MemoryRouter initialEntries={['/']}>
      <CaseList />
    </MemoryRouter>,
  )
  if (rows.length) await screen.findByRole('table')
}

describe('案件清單', () => {
  it('搜尋 / 狀態 / 案類篩選在前端收斂,且只打一次 API', async () => {
    const user = userEvent.setup()
    await renderList(listRows)
    expect(dataRows()).toHaveLength(3)

    await user.type(screen.getByRole('textbox', { name: '搜尋' }), 'c-0002')
    expect(dataRows()).toHaveLength(1)
    expect(dataRows()[0]).toHaveTextContent('交通罰單駁回')
    await user.clear(screen.getByRole('textbox', { name: '搜尋' }))

    await user.type(screen.getByRole('textbox', { name: '搜尋' }), '建管')
    expect(dataRows()).toHaveLength(1)
    await user.clear(screen.getByRole('textbox', { name: '搜尋' }))

    await user.selectOptions(screen.getByRole('combobox', { name: '狀態' }), '不受理')
    expect(dataRows()).toHaveLength(1)
    expect(dataRows()[0]).toHaveTextContent('c-0001')
    await user.selectOptions(screen.getByRole('combobox', { name: '狀態' }), '全部')

    await user.selectOptions(screen.getByRole('combobox', { name: '案類' }), '交通')
    expect(dataRows()).toHaveLength(1)
    expect(dataRows()[0]).toHaveTextContent('c-0002')

    expect(api.listCases).toHaveBeenCalledTimes(1)
  })

  it('篩選後 0 筆:顯示專屬文案與「清除篩選」,清除後列回來(三態)', async () => {
    const user = userEvent.setup()
    await renderList(listRows)
    await user.type(screen.getByRole('textbox', { name: '搜尋' }), '不存在的案號')
    expect(screen.getByText('目前篩選條件下沒有案件。')).toBeInTheDocument()
    expect(screen.queryByText('尚無案件。')).toBeNull()
    await user.click(screen.getByRole('button', { name: '清除篩選' }))
    expect(dataRows()).toHaveLength(3)
  })

  it('完全沒有案件:顯示「尚無案件。」與新建案件按鈕', async () => {
    await renderList([])
    expect(await screen.findByText('尚無案件。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '新建案件' })).toBeInTheDocument()
  })

  it('needs_review 的案件蓋「待人工確認」章,並可據此篩選', async () => {
    const user = userEvent.setup()
    await renderList([{ ...listRows[0], needs_review: true }, listRows[1]])

    expect(dataRows()[0]).toHaveTextContent('待人工確認')
    expect(dataRows()[0]).not.toHaveTextContent('不受理') // 結案章不能蓋在待複核的案子上
    await user.selectOptions(screen.getByRole('combobox', { name: '狀態' }), '待人工確認')
    expect(dataRows()).toHaveLength(1)
    expect(dataRows()[0]).toHaveTextContent('c-0001')
  })

  it('載入失敗:一句原因 + 重新載入', async () => {
    api.listCases.mockRejectedValueOnce(new Error('連線失敗'))
    api.listCases.mockResolvedValue(listRows)
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/']}>
        <CaseList />
      </MemoryRouter>,
    )
    expect(await screen.findByText(/連線失敗/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '重新載入' }))
    await screen.findByRole('table')
    expect(dataRows()).toHaveLength(3)
  })

  it('狀態篩選含五種決定類型(不受理/駁回/撤銷另處/原處分撤銷/部分不受理部分駁回)', async () => {
    await renderList(listRows)
    const options = within(screen.getByRole('combobox', { name: '狀態' }))
      .getAllByRole('option')
      .map((o) => o.textContent)
    expect(options).toEqual([
      '全部',
      '待確認',
      '審理中',
      '待人工確認',
      '已審結',
      '不受理',
      '駁回',
      '撤銷另處',
      '原處分撤銷',
      '部分不受理部分駁回',
      '處理失敗',
    ])
  })
})
