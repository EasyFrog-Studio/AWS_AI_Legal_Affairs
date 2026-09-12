import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import App from '../src/App.jsx'
import { api } from './apiMock.js'
import {
  doneAdmissible,
  doneInadmissible,
  processingAt,
  errorAtF2,
  listRows,
  collectingAllMatched,
} from './fixtures.js'
import { renderDetail, rail, isCurrent, refsStage } from './renderCaseDetail.jsx'

vi.mock('../src/api', async () => (await import('./apiMock.js')).api)

describe('頁首標題', () => {
  it('標題就是左欄選中項的名字,案號以較小字跟在後面', async () => {
    api.getCase.mockResolvedValue(processingAt('f1'))
    renderDetail('c-3')

    const heading = await screen.findByRole('heading', { name: 'F1 擷取' })
    // 案號不進標題本身:標題只講看的是哪一段,案號是附註
    expect(heading).not.toHaveTextContent('c-3')
    expect(screen.getByText('c-3')).toHaveClass('page-header__meta')
  })

  it('選到決定書草稿時標題是「決定書草稿」,案號照舊跟在後面', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    renderDetail()

    expect(await screen.findByRole('heading', { name: '決定書草稿' })).toBeInTheDocument()
    expect(screen.getByText('c-1')).toHaveClass('page-header__meta')
  })

  it('切換左欄,標題跟著換成該項的名字', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const user = userEvent.setup()
    renderDetail()

    await screen.findByRole('heading', { name: '決定書草稿' })
    await user.click(rail(/^程序審查/))
    expect(screen.getByRole('heading', { name: '程序審查' })).toBeInTheDocument()
    await user.click(rail(/^文件確認/))
    expect(screen.getByRole('heading', { name: '文件確認' })).toBeInTheDocument()
  })
})

describe('案件詳情', () => {
  it('切換階段再切回,草稿未儲存內容存活', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const user = userEvent.setup()
    renderDetail()
    const box = await screen.findByLabelText('決定書全文')
    expect(box).toHaveValue('主文 訴願駁回。 事實 事實原文 理由 理由原文')
    await user.type(box, ' 補充一段')

    await user.click(rail(/^參考依據/))
    expect(isCurrent(/^參考依據/)).toBe(true)
    await user.click(rail(/^F1 擷取/))
    await user.click(rail(/^決定書草稿/))

    expect(screen.getByLabelText('決定書全文')).toHaveValue(
      '主文 訴願駁回。 事實 事實原文 理由 理由原文 補充一段',
    )
    expect(api.updateDraftText).not.toHaveBeenCalled()
  })

  it('done 案件預設落在決定書草稿;processing 落在當前階段', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const { unmount } = renderDetail()
    await screen.findByLabelText('決定書全文')
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
    // f2/f2_refs/f3 三個後端階段都對應左欄同一個「參考依據」節點
    await waitFor(() => expect(isCurrent(/^參考依據/)).toBe(true), { timeout: 3500 })
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
    await waitFor(() => expect(rail(/^參考依據/)).toBeInTheDocument())
    expect(isCurrent(/^F1 擷取/)).toBe(true)
    expect(isCurrent(/^參考依據/)).toBe(false)
  }, 12000)

  it('不受理案件:法規那一組說「不適用」+ 說明句,不顯示「無」;草稿依據欄隱藏 F2 組', async () => {
    api.getCase.mockResolvedValue(doneInadmissible)
    const user = userEvent.setup()
    renderDetail('c-2')
    await screen.findByLabelText('決定書全文')
    expect(screen.queryByText('參考法規（F2）')).toBeNull()
    expect(screen.queryByText('無')).toBeNull()
    expect(screen.getByText('參考案例（F3）')).toBeInTheDocument()

    // 「不適用」是法規那一組的事,不是整個參考依據節點的事——參考見解與案例兩條 track 都跑
    await user.click(rail(/^參考依據/))
    const stage = within(refsStage())
    expect(stage.getByText(/本案經程序審查認定不受理/)).toBeInTheDocument()
    expect(stage.getByRole('tab', { name: '參考見解（F2+）' })).toBeInTheDocument()
    expect(stage.getByText('相似案例（F3）')).toBeInTheDocument()
    expect(screen.queryByText('無')).toBeNull()
  })

  it('status=error:左欄標記失敗階段「中斷」,內容區顯示錯誤原因', async () => {
    api.getCase.mockResolvedValue(errorAtF2)
    renderDetail('c-4')
    const refs = await screen.findByRole('button', { name: /^參考依據/ })
    expect(refs).toHaveTextContent('中斷')
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
    await user.type(await screen.findByLabelText('決定書全文'), ' 補充')
    await user.click(screen.getByRole('link', { name: '案件清單' }))
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('決定書全文')).toHaveValue(
      '主文 訴願駁回。 事實 事實原文 理由 理由原文 補充',
    )

    confirmSpy.mockReturnValue(true)
    await user.click(screen.getByRole('link', { name: '案件清單' }))
    expect(await screen.findByRole('table')).toBeInTheDocument()
  })
})

describe('F3 來源連結', () => {
  it('爬蟲語料帶了來源網址時給一個開新分頁的連結,沒有的那筆不畫', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    renderDetail('c-1')

    await screen.findByText(/112-0001/)
    const links = screen.getAllByRole('link', { name: '來源網站' })
    // 兩筆案例只有第一筆有網址;沒有的那筆不得畫出點下去會失敗的連結
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAttribute(
      'href',
      'https://web.law.ntpc.gov.tw/Scripts/Su_contents03.aspx?EANO=1120001',
    )
    expect(links[0]).toHaveAttribute('target', '_blank')
    expect(links[0]).toHaveAttribute('rel', expect.stringContaining('noopener'))
  })
})

describe('待人工確認與重跑', () => {
  it('期間有待確認事項時,頁首出現橫幅並列出原因', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    renderDetail()

    await screen.findByRole('status')
    expect(screen.getByRole('status')).toHaveTextContent('此案有事實待人工確認')
    expect(screen.getByRole('status')).toHaveTextContent(/訴願期間/)
  })

  it('OCR 取字的槽在橫幅列出須核對原件', async () => {
    api.getCase.mockResolvedValue({
      ...doneAdmissible,
      documents: {
        service: {
          slot: 'service',
          source: 'pdf',
          text: '送達證書全文',
          ocr: true,
          review_note: '本槽文字由 OCR 取得,日期須人工核對原件',
          check: { matched: true, method: 'rule', note: '' },
        },
      },
    })
    renderDetail()

    await screen.findByRole('status')
    expect(screen.getByRole('status')).toHaveTextContent(/OCR/)
  })

  it('頁首不再有「重跑分析」按鈕:done 與 error 案件都一樣', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const { unmount } = renderDetail()
    await screen.findByLabelText('決定書全文')
    expect(screen.queryByRole('button', { name: '重跑分析' })).toBeNull()
    unmount()

    api.getCase.mockResolvedValue(errorAtF2)
    renderDetail('c-4')
    await screen.findByRole('button', { name: /^參考依據/ })
    expect(screen.queryByRole('button', { name: '重跑分析' })).toBeNull()
  })

  it('collecting 是舊資料的殘留狀態,不再有獨立進行中態:左欄「文件確認」一律是完成標記', async () => {
    api.getCase.mockResolvedValue(collectingAllMatched)
    const user = userEvent.setup()
    renderDetail('c-5')

    const item = await screen.findByRole('button', { name: /^文件確認/ })
    expect(item.querySelector('.rail-item__marker')).toHaveClass('rail-item__marker--done')
    expect(item).not.toHaveTextContent('進行中')

    await user.click(item)
    await screen.findByRole('button', { name: '開始分析' })
    expect(screen.queryByRole('button', { name: '重跑分析' })).toBeNull()
  })
})

describe('中斷階段的重新執行', () => {
  it('中斷在程序審查:內容區顯示錯誤與「重新執行」,呼叫 reanalyzeCase(id,"screening")', async () => {
    api.getCase.mockResolvedValue({
      ...processingAt('screening'),
      case_id: 'c-4',
      status: 'error',
      error: 'Bedrock 逾時。',
    })
    const user = userEvent.setup()
    renderDetail('c-4')

    await screen.findByText(/Bedrock 逾時/)
    await user.click(screen.getByRole('button', { name: '重新執行' }))
    expect(api.reanalyzeCase).toHaveBeenCalledWith('c-4', 'screening')
  })

  it('中斷在參考依據(F3):「重新執行」呼叫 reanalyzeCase(id,"f2"),不警示', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    api.getCase.mockResolvedValue({
      ...processingAt('f3'),
      case_id: 'c-4',
      status: 'error',
      error: 'Bedrock 檢索逾時。',
    })
    const user = userEvent.setup()
    renderDetail('c-4')

    await screen.findByRole('button', { name: /^參考依據/ })
    await user.click(screen.getByRole('button', { name: '重新執行' }))
    expect(api.reanalyzeCase).toHaveBeenCalledWith('c-4', 'f2')
    expect(confirmSpy).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('中斷在 F1:「重新執行」呼叫 reanalyzeCase(id,"f1")', async () => {
    api.getCase.mockResolvedValue({
      ...processingAt('f1'),
      case_id: 'c-4',
      status: 'error',
      error: 'F1 擷取失敗。',
    })
    const user = userEvent.setup()
    renderDetail('c-4')

    await screen.findByText(/F1 擷取失敗/)
    await user.click(screen.getByRole('button', { name: '重新執行' }))
    expect(api.reanalyzeCase).toHaveBeenCalledWith('c-4', 'f1')
  })
})

describe('staleness 待確認橫幅', () => {
  it('f1_stale 出現在待確認橫幅', async () => {
    api.getCase.mockResolvedValue({ ...doneAdmissible, f1_stale: true })
    renderDetail()

    expect(await screen.findByRole('status')).toHaveTextContent('案件資訊已修改')
  })

  it('screening_stale 出現在待確認橫幅', async () => {
    api.getCase.mockResolvedValue({ ...doneAdmissible, screening_stale: true })
    renderDetail()

    expect(await screen.findByRole('status')).toHaveTextContent('程序審查結論已修改')
  })
})

describe('同頁不會同時看到兩個「AI 生成」', () => {
  it('F1 頁與程序審查頁各自的 AI 生成互斥,切換時最多只看得到一個', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const user = userEvent.setup()
    renderDetail()
    await screen.findByLabelText('決定書全文')

    await user.click(rail(/^F1 擷取/))
    expect(screen.getAllByRole('button', { name: 'AI 生成' })).toHaveLength(1)

    await user.click(rail(/^程序審查/))
    expect(screen.getAllByRole('button', { name: 'AI 生成' })).toHaveLength(1)

    await user.click(rail(/^決定書草稿/))
    expect(screen.queryAllByRole('button', { name: 'AI 生成' })).toHaveLength(0)
  })
})

describe('新結果提示點', () => {
  it('分析完成後,尚未看過的階段在左欄標一個點;正在看的那個不標', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    renderDetail()
    await screen.findByRole('button', { name: /^F1 擷取/ })
    // 預設落點要先落定:seen 是在 effect 裡才記的,搶在它之前斷言會看到尚未消失的點
    await waitFor(() => expect(isCurrent(/^決定書草稿/)).toBe(true))

    // done 案件預設落在決定書草稿,所以草稿沒有點,其餘跑出結果的階段都有
    expect(within(rail(/^F1 擷取/)).getByLabelText('有新結果')).toBeInTheDocument()
    expect(within(rail(/^程序審查/)).getByLabelText('有新結果')).toBeInTheDocument()
    expect(within(rail(/^參考依據/)).getByLabelText('有新結果')).toBeInTheDocument()
    expect(within(rail(/^決定書草稿/)).queryByLabelText('有新結果')).toBeNull()
  })

  it('點進去看過就不再標點', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^程序審查/ })

    await user.click(rail(/^程序審查/))

    expect(within(rail(/^程序審查/)).queryByLabelText('有新結果')).toBeNull()
    expect(within(rail(/^參考依據/)).getByLabelText('有新結果')).toBeInTheDocument()
  })

  it('還沒跑出結果的階段不標點——那不是「有東西可看」', async () => {
    api.getCase.mockResolvedValue(processingAt('screening'))
    renderDetail()
    await screen.findByRole('button', { name: /^參考依據/ })

    expect(within(rail(/^參考依據/)).queryByLabelText('有新結果')).toBeNull()
  })
})

describe('參考見解的存檔原文', () => {
  it('回傳 file 時改走帶金鑰抓檔再開新分頁,不把金鑰放進網址', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    api.getSource.mockResolvedValue({ file: 'reference/行政函釋/某函釋.pdf' })
    api.openSourceFile.mockResolvedValue('blob:fake')
    api.getCase.mockResolvedValue({
      ...doneAdmissible,
      f2_refs: [
        {
          ...doneAdmissible.f2_refs[1],
          source_key: 'reference/行政函釋/某函釋.pdf',
        },
      ],
    })
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^參考依據/ })
    await user.click(rail(/^參考依據/))
    await user.click(within(refsStage()).getByRole('tab', { name: '參考見解（F2+）' }))

    await user.click(within(refsStage()).getByRole('button', { name: '原文' }))

    await waitFor(() => expect(api.openSourceFile).toHaveBeenCalledWith('reference/行政函釋/某函釋.pdf'))
    expect(open).toHaveBeenCalledWith('blob:fake', '_blank', 'noopener')
    open.mockRestore()
  })
})

describe('F2+ 參考見解', () => {
  it('兩種異質見解都畫得出來:釋字無發文機關無原文鈕,函釋有發文機關可開原文', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^參考依據/ })

    await user.click(rail(/^參考依據/))
    await user.click(within(refsStage()).getByRole('tab', { name: '參考見解（F2+）' }))

    const yizi = within(refsStage()).getByText('釋字第469號').closest('.reference-ref')
    expect(within(yizi).getByText('司法院釋字')).toBeInTheDocument()
    expect(within(yizi).getByText(/怠於執行職務之國家賠償責任/)).toBeInTheDocument()
    expect(within(yizi).getByText(/未收錄/)).toBeInTheDocument()
    expect(within(yizi).queryByRole('button', { name: '原文' })).toBeNull()

    const hanshi = within(refsStage())
      .getByText('法務部 法律字第0930014628號')
      .closest('.reference-ref')
    expect(within(hanshi).getByText('法務部')).toBeInTheDocument() // 發文機關獨立一欄,非名稱的一部分
    expect(within(hanshi).getByText('民國 93 年 04 月 13 日')).toBeInTheDocument()
    await user.click(within(hanshi).getByRole('button', { name: '原文' }))
    expect(api.getSource).toHaveBeenCalledWith(
      'markdown/行政函釋/法務部93年4月13日法律字0930014628號函-寄存送達.md',
    )
  })

  it('左欄只有一個參考依據節點;法規與參考見解是同一列分頁,一次只顯示一個,案例另成一組在下方', async () => {
    api.getCase.mockResolvedValue(doneAdmissible)
    const user = userEvent.setup()
    renderDetail()
    await screen.findByRole('button', { name: /^參考依據/ })

    // 左欄不再有 F2 / F2+ / F3 三個獨立節點
    const railLabels = screen
      .getAllByRole('button')
      .map((b) => b.textContent)
      .filter((t) => /^(F2 法規|F2\+ 參考見解|F3 案例|參考依據)/.test(t))
    expect(railLabels).toHaveLength(1)
    expect(railLabels[0]).toMatch(/^參考依據/)

    await user.click(rail(/^參考依據/))
    const stage = within(refsStage())
    expect(stage.getAllByRole('tab').map((t) => t.textContent)).toEqual(['推薦法規（F2）', '參考見解（F2+）'])
    expect(stage.getAllByRole('tabpanel')).toHaveLength(1)
    // 預設停在法規:看得到法條卡,看不到參考見解與其說明句
    expect(stage.getByText(/訴願法 第 14 條/)).toBeInTheDocument()
    expect(stage.queryByText('釋字第469號')).toBeNull()
    expect(stage.queryByText(/不列入決定書的引用法條/)).toBeNull()
    // 案例不在分頁裡,仍是獨立一組
    expect(stage.getByRole('heading', { level: 3, name: '相似案例（F3）' })).toBeInTheDocument()

    await user.click(stage.getByRole('tab', { name: '參考見解（F2+）' }))
    expect(stage.getByRole('tab', { name: '參考見解（F2+）' })).toHaveAttribute('aria-selected', 'true')
    expect(stage.getAllByRole('tabpanel')).toHaveLength(1)
    expect(stage.getByText('釋字第469號')).toBeInTheDocument()
    expect(stage.getByText(/不列入決定書的引用法條/)).toBeInTheDocument()
    expect(stage.queryByText(/訴願法 第 14 條/)).toBeNull()
    expect(stage.getByRole('heading', { level: 3, name: '相似案例（F3）' })).toBeInTheDocument()
  })

  it('不受理案件一樣有參考見解,不出現「不適用」', async () => {
    api.getCase.mockResolvedValue(doneInadmissible)
    const user = userEvent.setup()
    renderDetail('c-2')
    await screen.findByRole('button', { name: /^參考依據/ })

    expect(rail(/^參考依據/)).not.toHaveTextContent('不適用')
    await user.click(rail(/^參考依據/))
    await user.click(within(refsStage()).getByRole('tab', { name: '參考見解（F2+）' }))
    expect(within(refsStage()).getByText('釋字第469號')).toBeInTheDocument()
  })

  it('三態可區分:正在跑才說檢索中,沒跑過說尚未執行,跑完沒結果說未檢索到', async () => {
    // 已審結的舊案件沒有這一段結果,不能一直說「檢索中…」——那與正在跑分不出來
    api.getCase.mockResolvedValue({ ...doneAdmissible, f2_refs: null })
    const user = userEvent.setup()
    let view = renderDetail()
    await screen.findByRole('button', { name: /^參考依據/ })
    await user.click(rail(/^參考依據/))
    await user.click(within(refsStage()).getByRole('tab', { name: '參考見解（F2+）' }))
    expect(within(refsStage()).getByText('尚未執行')).toBeInTheDocument()
    expect(within(refsStage()).queryByText('檢索中…')).toBeNull()
    view.unmount()

    api.getCase.mockResolvedValue({
      ...doneAdmissible,
      status: 'processing',
      current_stage: 'f2_refs',
      f2_refs: null,
    })
    view = renderDetail()
    await screen.findByRole('button', { name: /^參考依據/ })
    await user.click(rail(/^參考依據/))
    await user.click(within(refsStage()).getByRole('tab', { name: '參考見解（F2+）' }))
    expect(within(refsStage()).getByText('檢索中…')).toBeInTheDocument()
    view.unmount()

    api.getCase.mockResolvedValue({ ...doneAdmissible, f2_refs: [] })
    renderDetail()
    await screen.findByRole('button', { name: /^參考依據/ })
    await user.click(rail(/^參考依據/))
    await user.click(within(refsStage()).getByRole('tab', { name: '參考見解（F2+）' }))
    expect(within(refsStage()).getByText('未檢索到相關參考見解。')).toBeInTheDocument()
  })
})
