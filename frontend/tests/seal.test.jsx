import { describe, it, expect } from 'vitest'
import { resolveCaseSeal, resolveProgressSeal, resolveResultSeal } from '../src/components/Seal.jsx'

function admissibleCase(draftType) {
  return { status: 'done', track: 'admissible', f4: { draft_type: draftType } }
}

describe('resolveCaseSeal:五種 draft_type', () => {
  it('駁回 → reject(回歸)', () => {
    expect(resolveCaseSeal(admissibleCase('駁回'))).toEqual({ kind: 'reject', text: '駁回' })
  })

  it('原處分撤銷 → pass(回歸)', () => {
    expect(resolveCaseSeal(admissibleCase('原處分撤銷'))).toEqual({ kind: 'pass', text: '原處分撤銷' })
  })

  it('撤銷另處 → pass(原處分被撤銷,訴願有理由)', () => {
    expect(resolveCaseSeal(admissibleCase('撤銷另處'))).toEqual({ kind: 'pass', text: '撤銷另處' })
  })

  it('部分不受理部分駁回 → reject(兩部分皆對訴願人不利)', () => {
    expect(resolveCaseSeal(admissibleCase('部分不受理部分駁回'))).toEqual({
      kind: 'reject',
      text: '部分不受理部分駁回',
    })
  })

  it('失敗路徑:未知 draft_type 不得回 pass/reject,落回既有 fallback', () => {
    const result = resolveCaseSeal(admissibleCase('撤銷'))
    expect(result.kind).not.toBe('pass')
    expect(result.kind).not.toBe('reject')
    expect(result).toEqual({ kind: 'accent', text: '撤銷' })
  })
})

describe('resolveProgressSeal:清單頁「進度」欄,僅四值', () => {
  it('status=error → 處理失敗', () => {
    expect(resolveProgressSeal({ status: 'error' })).toEqual({ kind: 'error', text: '處理失敗' })
  })

  it('status=collecting 且 documents_failed → 處理失敗(文件判定不符,不是待確認)', () => {
    expect(resolveProgressSeal({ status: 'collecting', documents_failed: true })).toEqual({
      kind: 'error',
      text: '處理失敗',
    })
  })

  it('status=collecting 且 documents_failed=false → 待確認', () => {
    expect(resolveProgressSeal({ status: 'collecting', documents_failed: false })).toEqual({
      kind: 'accent',
      text: '待確認',
    })
  })

  it('status=processing → 審理中', () => {
    expect(resolveProgressSeal({ status: 'processing' })).toEqual({ kind: 'accent', text: '審理中' })
  })

  it('status=done → 已審結', () => {
    expect(resolveProgressSeal({ status: 'done' })).toEqual({ kind: 'accent', text: '已審結' })
  })

  it('done 且 needs_review → 待人工確認(併入進度欄,狀況欄只留決定結果)', () => {
    expect(resolveProgressSeal({ status: 'done', needs_review: true })).toEqual({
      kind: 'review',
      text: '待人工確認',
    })
  })

  it('邊界:未知 status 落回審理中,不拋錯', () => {
    expect(resolveProgressSeal({ status: '未知狀態' })).toEqual({ kind: 'accent', text: '審理中' })
  })
})

describe('resolveResultSeal:清單頁「狀況」欄,如實顯示最終決定結果', () => {
  it('result=駁回 → reject', () => {
    expect(resolveResultSeal({ result: '駁回' })).toEqual({ kind: 'reject', text: '駁回' })
  })

  it('result=撤銷另處 → pass', () => {
    expect(resolveResultSeal({ result: '撤銷另處' })).toEqual({ kind: 'pass', text: '撤銷另處' })
  })

  it('沒有 result 但 f4.draft_type 有值時退回讀 f4', () => {
    expect(resolveResultSeal({ result: null, f4: { draft_type: '原處分撤銷' } })).toEqual({
      kind: 'pass',
      text: '原處分撤銷',
    })
  })

  it('失敗/邊界:result 為 null 且無 f4 → 回 null,呼叫端自行印「—」', () => {
    expect(resolveResultSeal({ result: null })).toBeNull()
  })

  it('失敗/邊界:未知結果值不得回 pass/reject,落回 accent fallback', () => {
    const result = resolveResultSeal({ result: '撤銷' })
    expect(result.kind).not.toBe('pass')
    expect(result.kind).not.toBe('reject')
    expect(result).toEqual({ kind: 'accent', text: '撤銷' })
  })
})
