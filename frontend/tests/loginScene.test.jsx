import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, fireEvent, act } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import Login from '../src/pages/Login.jsx'
import { playExit } from '../src/login-scene'

vi.mock('../src/api', async () => (await import('./apiMock.js')).api)

// 用 fireEvent 不用 userEvent:fake timers 會卡住 user-event 內部的 setTimeout
function renderLogin() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<div>案件清單頁</div>} />
      </Routes>
    </MemoryRouter>,
  )
}
const scene = () => document.querySelector('.login-scene')
const backdrop = () => document.querySelector('.login-backdrop')
const endEvent = (name) =>
  Object.assign(new Event('animationend', { bubbles: true }), { animationName: name })
const SHAPES = 'path, polygon, rect, ellipse, circle, line'

function setHidden(value) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => value })
}

describe('login-scene', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    delete document.hidden
  })

  it('i. 登入成功:表單停用、data-state=success,離場結束才導向', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true })))
    renderLogin()
    const input = document.querySelector('#apiKey')
    fireEvent.change(input, { target: { value: 'ok' } })
    fireEvent.submit(document.querySelector('form'))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50)
    })
    expect(document.querySelector('.login').dataset.state).toBe('success')
    expect(input).toBeDisabled()
    expect(scene().dataset.exiting).toBe('1')
    expect(document.body).not.toHaveTextContent('案件清單頁')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(850)
    })
    expect(document.body).toHaveTextContent('案件清單頁')
  })

  it('a. 兩層皆 aria-hidden 且無可聚焦元素', () => {
    renderLogin()
    for (const layer of [scene(), backdrop()]) {
      expect(layer).toHaveAttribute('aria-hidden', 'true')
      expect(layer.querySelectorAll('a, button, input, select, textarea, [tabindex]')).toHaveLength(0)
    }
  })

  it('g. 場景是 .login__card 的第一個子節點;背景層是 .login 的第一個子節點', () => {
    renderLogin()
    expect(document.querySelector('.login__card').firstElementChild).toBe(scene())
    expect(document.querySelector('.login').firstElementChild).toBe(backdrop())
  })

  it('b. playExit 設 data-exiting,等自身 ls-exit 的 animationend 才 resolve', async () => {
    vi.useFakeTimers()
    renderLogin()
    let done = false
    const p = playExit().then(() => {
      done = true
    })
    expect(scene().dataset.exiting).toBe('1')
    await vi.advanceTimersByTimeAsync(100)
    expect(done).toBe(false)
    fireEvent(scene().firstElementChild, endEvent('ls-exit')) // 子元素冒泡上來的不算
    await vi.advanceTimersByTimeAsync(0)
    expect(done).toBe(false)
    fireEvent(scene(), endEvent('ls-exit'))
    await p
    expect(done).toBe(true)
  })

  it('b2. 其他動畫的 animationend 不算;850ms 保底 resolve', async () => {
    vi.useFakeTimers()
    renderLogin()
    let done = false
    const p = playExit().then(() => {
      done = true
    })
    fireEvent(scene(), endEvent('ls-shimmer'))
    await vi.advanceTimersByTimeAsync(800)
    expect(done).toBe(false)
    await vi.advanceTimersByTimeAsync(100)
    await p
    expect(done).toBe(true)
  })

  it('b3. 無場景時立即 resolve', async () => {
    await expect(playExit()).resolves.toBeUndefined()
  })

  it('b4. reduced-motion 時立即 resolve、不設 data-exiting', async () => {
    const mm = vi
      .spyOn(window, 'matchMedia')
      .mockImplementation(() => ({ matches: true, addEventListener() {}, removeEventListener() {} }))
    renderLogin()
    await expect(playExit()).resolves.toBeUndefined()
    expect(scene().dataset.exiting).toBeUndefined()
    mm.mockRestore()
  })

  it('c. 分頁隱藏 → 場景 .is-paused;回前景解除', () => {
    renderLogin()
    setHidden(true)
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(scene()).toHaveClass('is-paused')
    setHidden(false)
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(scene()).not.toHaveClass('is-paused')
  })

  it('d. 卸載時移除 visibilitychange 監聽', () => {
    const add = vi.spyOn(document, 'addEventListener')
    const remove = vi.spyOn(document, 'removeEventListener')
    const { unmount } = renderLogin()
    const added = add.mock.calls.filter(([t]) => t === 'visibilitychange').length
    unmount()
    const removed = remove.mock.calls.filter(([t]) => t === 'visibilitychange').length
    expect(added).toBeGreaterThan(0)
    expect(removed).toBe(added)
  })

  it('e. 進場旗標每分頁一次', () => {
    const first = renderLogin()
    expect(scene().dataset.enter).toBe('1')
    expect(sessionStorage.getItem('login-scene-entered')).toBe('1')
    first.unmount()
    renderLogin()
    expect(scene().dataset.enter).toBeUndefined()
  })

  it('j. 點擊卡片外:敲一下並帶一圈漣漪;敲擊進行中(340ms)的點擊不打斷;點卡片內不動', () => {
    vi.useFakeTimers()
    renderLogin()
    const knock = () => document.querySelector('.sc__knock')
    const rings = () => document.querySelectorAll('.sc__ring--knock').length
    expect(knock()).not.toHaveClass('is-knock')
    fireEvent.click(document.querySelector('.login'))
    expect(knock()).toHaveClass('is-knock')
    expect(rings()).toBe(1)
    const first = knock()
    fireEvent.click(document.querySelector('.login')) // 敲擊中連點:不重掛、不加漣漪,讓這一敲落下
    expect(rings()).toBe(1)
    expect(knock()).toBe(first)
    vi.advanceTimersByTime(340)
    fireEvent.click(document.querySelector('.login')) // 敲完後再點:再敲一次,漣漪各自獨立
    expect(rings()).toBe(2)
    expect(knock()).not.toBe(first)
    fireEvent.click(document.querySelector('#apiKey'))
    expect(rings()).toBe(2)
  })

  it('f. 預算:兩層圖形節點合計 ≤ 300、閃動切面 ≤ 16', () => {
    renderLogin()
    const shapes = scene().querySelectorAll(SHAPES).length + backdrop().querySelectorAll(SHAPES).length
    expect(shapes).toBeLessThanOrEqual(300)
    const lit = document.querySelectorAll('.geo__lit').length
    expect(lit).toBeGreaterThan(0)
    expect(lit).toBeLessThanOrEqual(16)
  })
})
