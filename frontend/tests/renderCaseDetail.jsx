import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import CaseDetail from '../src/pages/CaseDetail.jsx'

export function renderDetail(id = 'c-1') {
  return render(
    <MemoryRouter initialEntries={[`/cases/${id}`]}>
      <Routes>
        <Route path="/cases/:id" element={<CaseDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

/** 文件確認頁測試共用:render 後手動點進「文件確認」節點。
 * autoTarget 不再把 collecting 狀態的案件預設落在這裡(舊資料視同 done,落在決定書草稿),
 * 故文件確認頁的測試一律要自己導覽過去。 */
export async function renderDocuments(id) {
  const user = userEvent.setup()
  const utils = renderDetail(id)
  await user.click(await screen.findByRole('button', { name: /^文件確認/ }))
  return { user, ...utils }
}

export const pdf = (name) => new File(['%PDF-1.4'], name, { type: 'application/pdf' })
export const rail = (name) => screen.getByRole('button', { name })
export const isCurrent = (name) => rail(name).getAttribute('aria-current') === 'true'

/** 階段頁的「參考依據」區塊。草稿頁的依據欄是 hidden 而非卸載,兩邊同時在 DOM 裡。 */
export function refsStage() {
  return document.querySelector('.refs-stage')
}
