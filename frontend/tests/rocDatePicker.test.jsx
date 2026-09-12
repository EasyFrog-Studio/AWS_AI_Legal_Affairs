import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useState } from 'react'
import RocDatePicker, { parseRocDate, formatRocDate } from '../src/components/RocDatePicker.jsx'

function Controlled({ initial = '', onChange = () => {} }) {
  const [value, setValue] = useState(initial)
  return (
    <>
      <RocDatePicker
        id="d"
        label="送達時間"
        value={value}
        onChange={(v) => {
          setValue(v)
          onChange(v)
        }}
      />
      <output data-testid="value">{value}</output>
    </>
  )
}

const pick = (unit, n) => fireEvent.change(screen.getByRole('combobox', { name: `送達時間${unit}` }), { target: { value: String(n) } })

describe('RocDatePicker', () => {
  it('從全空逐格選年、月、日,湊滿三格才送出標準寫法', () => {
    const onChange = vi.fn()
    render(<Controlled onChange={onChange} />)
    pick('年', 114)
    expect(screen.getByRole('combobox', { name: '送達時間年' })).toHaveValue('114')
    expect(onChange).not.toHaveBeenCalled()
    pick('月', 7)
    expect(onChange).not.toHaveBeenCalled()
    pick('日', 4)
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith('民國114年7月4日')
    expect(screen.getByTestId('value')).toHaveTextContent('民國114年7月4日')
  })

  it('已有日期時改一格立即送出;只吃「[民國]N年M月D日」開頭的寫法', () => {
    const onChange = vi.fn()
    render(<Controlled initial="114年5月28日" onChange={onChange} />)
    expect(screen.getByRole('combobox', { name: '送達時間月' })).toHaveValue('5')
    pick('日', 30)
    expect(onChange).toHaveBeenCalledWith('民國114年5月30日')
  })

  it('解析不出的原值三格留空並印無法辨識,選定後覆寫', () => {
    const onChange = vi.fn()
    render(<Controlled initial="未載明" onChange={onChange} />)
    expect(screen.getByText('無法辨識：未載明')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '送達時間年' })).toHaveValue('')
    pick('年', 113)
    pick('月', 1)
    pick('日', 2)
    expect(onChange).toHaveBeenLastCalledWith('民國113年1月2日')
    expect(screen.queryByText(/無法辨識/)).not.toBeInTheDocument()
  })

  it('清除送出空字串;外部值改變時三格跟著更新', () => {
    const onChange = vi.fn()
    const { rerender } = render(<RocDatePicker id="d" label="送達時間" value="民國114年7月4日" onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '清除' }))
    expect(onChange).toHaveBeenCalledWith('')
    rerender(<RocDatePicker id="d" label="送達時間" value="民國110年2月3日" onChange={onChange} />)
    expect(screen.getByRole('combobox', { name: '送達時間年' })).toHaveValue('110')
    expect(screen.getByRole('combobox', { name: '送達時間日' })).toHaveValue('3')
  })

  it('parseRocDate / formatRocDate 互為反函式,無效輸入回 null', () => {
    expect(parseRocDate('民國114年7月4日')).toEqual({ year: 114, month: 7, day: 4 })
    expect(formatRocDate(parseRocDate('中華民國 114 年 7 月 4 日'))).toBe('民國114年7月4日')
    expect(parseRocDate('114.7.4')).toBeNull()
    expect(parseRocDate('')).toBeNull()
  })
})
