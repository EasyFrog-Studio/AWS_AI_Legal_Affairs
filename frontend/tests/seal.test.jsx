import { describe, it, expect } from 'vitest'
import { resolveCaseSeal } from '../src/components/Seal.jsx'

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
