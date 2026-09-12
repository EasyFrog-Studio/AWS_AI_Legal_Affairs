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
    expect(dataRows()[0]).toHaveTextContent('c-0002')
    await user.clear(screen.getByRole('textbox', { name: '搜尋' }))

    await user.type(screen.getByRole('textbox', { name: '搜尋' }), '建管')
    expect(dataRows()).toHaveLength(1)
    await user.clear(screen.getByRole('textbox', { name: '搜尋' }))

    await user.selectOptions(screen.getByRole('combobox', { name: '進度' }), '審理中')
    expect(dataRows()).toHaveLength(1)
    expect(dataRows()[0]).toHaveTextContent('c-0003')
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

  it('完全沒有案件:顯示「尚無案件。」與新增案件按鈕', async () => {
    await renderList([])
    expect(await screen.findByText('尚無案件。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '新增案件' })).toBeInTheDocument()
  })

  it('needs_review 的案件在進度欄印「待人工確認」,狀況欄不重複,並可據此篩選', async () => {
    const user = userEvent.setup()
    await renderList([{ ...listRows[0], needs_review: true }, listRows[1]])

    const cells = within(dataRows()[0]).getAllByRole('cell')
    expect(cells[3]).toHaveTextContent('待人工確認')
    expect(cells[2]).not.toHaveTextContent('待人工確認')
    await user.selectOptions(screen.getByRole('combobox', { name: '進度' }), '待人工確認')
    expect(dataRows()).toHaveLength(1)
    expect(dataRows()[0]).toHaveTextContent('c-0001')
  })

  it('建立時間只到分,不顯示秒', async () => {
    await renderList([{ ...listRows[0], created_at: '2026-08-27T01:02:03+00:00' }])
    const cells = within(dataRows()[0]).getAllByRole('cell')
    expect(cells[4]).toHaveTextContent(/^\d{4}\/\d{1,2}\/\d{1,2} \d{1,2}:\d{2}$/)
  })

  it('清單不顯示訴願書檔名欄', async () => {
    await renderList(listRows)
    const headers = within(screen.getByRole('table'))
      .getAllByRole('columnheader')
      .map((h) => h.textContent)
    expect(headers).toEqual(['案號', '案件類別', '狀況', '進度', '建立時間'])
    expect(screen.queryByText('環保裁罰逾期')).toBeNull()
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

  it('進度篩選涵蓋五種進度值(待確認/審理中/待人工確認/處理失敗/已審結)', async () => {
    await renderList(listRows)
    const options = within(screen.getByRole('combobox', { name: '進度' }))
      .getAllByRole('option')
      .map((o) => o.textContent)
    expect(options).toEqual(['全部', '待確認', '審理中', '待人工確認', '處理失敗', '已審結'])
  })

  it('狀況篩選只有五種決定類型,不含待人工確認', async () => {
    await renderList(listRows)
    const options = within(screen.getByRole('combobox', { name: '狀況' }))
      .getAllByRole('option')
      .map((o) => o.textContent)
    expect(options).toEqual([
      '全部',
      '不受理',
      '駁回',
      '撤銷另處',
      '原處分撤銷',
      '部分不受理部分駁回',
    ])
  })

  it('collecting 案件依 documents_failed 決定進度是「待確認」還是「處理失敗」', async () => {
    const rows = [
      {
        case_id: 'c-0101',
        created_at: new Date().toISOString(),
        title: '文件不符案',
        status: 'collecting',
        documents_failed: true,
        track: null,
        current_stage: 'f1',
        case_type: null,
      },
      {
        case_id: 'c-0102',
        created_at: new Date().toISOString(),
        title: '正常收案',
        status: 'collecting',
        documents_failed: false,
        track: null,
        current_stage: 'f1',
        case_type: null,
      },
    ]
    await renderList(rows)
    expect(within(dataRows()[0]).getAllByRole('cell')[3]).toHaveTextContent('處理失敗')
    expect(within(dataRows()[1]).getAllByRole('cell')[3]).toHaveTextContent('待確認')
  })

  it('進度與狀況分欄:done 案件進度顯示已審結,狀況如實顯示 result,無 result 顯示「—」', async () => {
    const rows = [
      {
        case_id: 'c-0201',
        created_at: new Date().toISOString(),
        title: '撤銷另處案',
        status: 'done',
        result: '撤銷另處',
        track: 'admissible',
        current_stage: 'done',
        case_type: '環保',
      },
      {
        case_id: 'c-0202',
        created_at: new Date().toISOString(),
        title: '尚無結果案',
        status: 'done',
        result: null,
        track: 'admissible',
        current_stage: 'done',
        case_type: '環保',
      },
    ]
    await renderList(rows)
    const row0 = within(dataRows()[0]).getAllByRole('cell')
    expect(row0[3]).toHaveTextContent('已審結')
    expect(row0[2]).toHaveTextContent('撤銷另處')

    const row1 = within(dataRows()[1]).getAllByRole('cell')
    expect(row1[3]).toHaveTextContent('已審結')
    expect(row1[2]).toHaveTextContent('—')
  })

  it('狀況篩選:選駁回只留 result=駁回;待人工確認改由進度篩選', async () => {
    const rows = [
      {
        case_id: 'c-0301',
        created_at: new Date().toISOString(),
        title: '駁回案',
        status: 'done',
        result: '駁回',
        track: 'admissible',
        current_stage: 'done',
        case_type: '環保',
      },
      {
        case_id: 'c-0302',
        created_at: new Date().toISOString(),
        title: '待複核案',
        status: 'done',
        result: null,
        needs_review: true,
        track: 'admissible',
        current_stage: 'done',
        case_type: '環保',
      },
      {
        case_id: 'c-0303',
        created_at: new Date().toISOString(),
        title: '不受理案',
        status: 'done',
        result: '不受理',
        track: 'inadmissible',
        current_stage: 'done',
        case_type: '環保',
      },
    ]
    const user = userEvent.setup()
    await renderList(rows)

    await user.selectOptions(screen.getByRole('combobox', { name: '狀況' }), '駁回')
    expect(dataRows()).toHaveLength(1)
    expect(dataRows()[0]).toHaveTextContent('c-0301')
    await user.selectOptions(screen.getByRole('combobox', { name: '狀況' }), '全部')

    await user.selectOptions(screen.getByRole('combobox', { name: '進度' }), '待人工確認')
    expect(dataRows()).toHaveLength(1)
    expect(dataRows()[0]).toHaveTextContent('c-0302')
  })

  it('進度篩選:選處理失敗同時涵蓋 error 案件與文件不符的收案', async () => {
    const rows = [
      {
        case_id: 'c-0401',
        created_at: new Date().toISOString(),
        title: '系統錯誤案',
        status: 'error',
        track: null,
        current_stage: 'f1',
        case_type: null,
      },
      {
        case_id: 'c-0402',
        created_at: new Date().toISOString(),
        title: '文件不符案',
        status: 'collecting',
        documents_failed: true,
        track: null,
        current_stage: 'f1',
        case_type: null,
      },
      {
        case_id: 'c-0403',
        created_at: new Date().toISOString(),
        title: '正常收案',
        status: 'collecting',
        documents_failed: false,
        track: null,
        current_stage: 'f1',
        case_type: null,
      },
    ]
    const user = userEvent.setup()
    await renderList(rows)
    await user.selectOptions(screen.getByRole('combobox', { name: '進度' }), '處理失敗')
    expect(dataRows()).toHaveLength(2)
    const ids = dataRows().map((r) => r.textContent)
    expect(ids.some((t) => t.includes('c-0401'))).toBe(true)
    expect(ids.some((t) => t.includes('c-0402'))).toBe(true)
  })
})
