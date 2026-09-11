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


const DAY = 24 * 60 * 60 * 1000

function rowsAgo(...dayOffsets) {
  return dayOffsets.map((d, i) => ({
    case_id: `c-${String(i + 1).padStart(4, '0')}`,
    created_at: new Date(Date.now() - d * DAY).toISOString(),
    title: `案件${i + 1}`,
    status: 'done',
    track: 'admissible',
    current_stage: 'done',
    case_type: '環保',
  }))
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
  it('搜尋 / 進度 / 案件類別篩選在前端收斂,且只打一次 API', async () => {
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

    await user.selectOptions(screen.getByRole('combobox', { name: '進度' }), '不受理')
    expect(dataRows()).toHaveLength(1)
    expect(dataRows()[0]).toHaveTextContent('c-0001')
    await user.selectOptions(screen.getByRole('combobox', { name: '進度' }), '全部')

    await user.selectOptions(screen.getByRole('combobox', { name: '案件類別' }), '交通')
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
    await user.selectOptions(screen.getByRole('combobox', { name: '進度' }), '待人工確認')
    expect(dataRows()).toHaveLength(1)
    expect(dataRows()[0]).toHaveTextContent('c-0001')
  })

  it('建立時間只到分,不顯示秒', async () => {
    await renderList([{ ...listRows[0], created_at: '2026-08-27T01:02:03+00:00' }])
    const cells = within(dataRows()[0]).getAllByRole('cell')
    expect(cells[4]).toHaveTextContent(/^\d{4}\/\d{1,2}\/\d{1,2} \d{1,2}:\d{2}$/)
  })

  it('一頁 20 筆,上下頁切換,首末頁按鈕停用', async () => {
    const user = userEvent.setup()
    await renderList(rowsAgo(...Array.from({ length: 45 }, (_, i) => i)))
    expect(dataRows()).toHaveLength(20)
    expect(dataRows()[0]).toHaveTextContent('c-0001')

    const prev = screen.getByRole('button', { name: '上一頁' })
    const next = screen.getByRole('button', { name: '下一頁' })
    expect(prev).toBeDisabled()
    expect(screen.getByText('第 1 / 3 頁')).toBeInTheDocument()

    await user.click(next)
    expect(dataRows()).toHaveLength(20)
    expect(dataRows()[0]).toHaveTextContent('c-0021')
    expect(prev).toBeEnabled()

    await user.click(next)
    expect(dataRows()).toHaveLength(5)
    expect(dataRows()[0]).toHaveTextContent('c-0041')
    expect(next).toBeDisabled()

    await user.click(prev)
    expect(dataRows()[0]).toHaveTextContent('c-0021')
  })

  it('20 筆以內不出現分頁列', async () => {
    await renderList(listRows)
    expect(screen.queryByRole('button', { name: '下一頁' })).toBeNull()
  })

  it('篩選後回到第 1 頁,不會卡在空白頁', async () => {
    const user = userEvent.setup()
    await renderList(rowsAgo(...Array.from({ length: 45 }, (_, i) => i)))
    await user.click(screen.getByRole('button', { name: '下一頁' }))
    await user.click(screen.getByRole('button', { name: '下一頁' }))
    expect(dataRows()[0]).toHaveTextContent('c-0041')

    await user.type(screen.getByRole('textbox', { name: '搜尋' }), '案件1')
    expect(screen.queryByText('目前篩選條件下沒有案件。')).toBeNull()
    expect(dataRows()[0]).toHaveTextContent('c-0001')
  })

  it('時間篩選只留區間內的案件', async () => {
    const user = userEvent.setup()
    await renderList(rowsAgo(1, 5, 10, 20, 60, 120, 200))
    const period = screen.getByRole('combobox', { name: '時間' })

    await user.selectOptions(period, '3 天')
    expect(dataRows()).toHaveLength(1)
    expect(dataRows()[0]).toHaveTextContent('c-0001')

    await user.selectOptions(period, '14 天')
    expect(dataRows()).toHaveLength(3)

    await user.selectOptions(period, '1 個月')
    expect(dataRows()).toHaveLength(4)

    await user.selectOptions(period, '半年')
    expect(dataRows()).toHaveLength(6)

    await user.selectOptions(period, '全部')
    expect(dataRows()).toHaveLength(7)
  })

  it('建立時間缺漏或無法解析:不顯示假日期,時間篩選時排除', async () => {
    const user = userEvent.setup()
    const rows = rowsAgo(1, 1)
    rows[0].created_at = null
    rows[1].created_at = '不是日期'
    await renderList([...rows, ...rowsAgo(1).map((r) => ({ ...r, case_id: 'c-9999' }))])
    expect(within(dataRows()[0]).getAllByRole('cell')[4]).toHaveTextContent('')
    expect(within(dataRows()[1]).getAllByRole('cell')[4]).toHaveTextContent('不是日期')

    await user.selectOptions(screen.getByRole('combobox', { name: '時間' }), '7 天')
    expect(dataRows()).toHaveLength(1)
    expect(dataRows()[0]).toHaveTextContent('c-9999')
  })

  it('時間篩選選項為 全部/3/7/14 天/1/3/半年', async () => {
    await renderList(listRows)
    const options = within(screen.getByRole('combobox', { name: '時間' }))
      .getAllByRole('option')
      .map((o) => o.textContent)
    expect(options).toEqual(['全部', '3 天', '7 天', '14 天', '1 個月', '3 個月', '半年'])
  })

  it('時間篩選落空時走「篩選後 0 筆」空狀態,清除篩選可復原', async () => {
    const user = userEvent.setup()
    await renderList(rowsAgo(100, 200))
    await user.selectOptions(screen.getByRole('combobox', { name: '時間' }), '7 天')
    expect(screen.getByText('目前篩選條件下沒有案件。')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '清除篩選' }))
    expect(dataRows()).toHaveLength(2)
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

  it('進度篩選含五種決定類型(不受理/駁回/撤銷另處/原處分撤銷/部分不受理部分駁回)', async () => {
    await renderList(listRows)
    const options = within(screen.getByRole('combobox', { name: '進度' }))
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
