const REDUCED = '(prefers-reduced-motion: reduce)'
const FALLBACK_MS = 850 // 落槌 + 漣漪 + 0.4s 起的淡出,總長 0.7s

// 設 data-exiting 讓 CSS 播離場,等場景自身的 ls-exit 結束(或保底)後 resolve;永不 reject
export function playExit() {
  const scene = document.querySelector('.login-scene')
  if (!scene || window.matchMedia(REDUCED).matches) return Promise.resolve()
  return new Promise((resolve) => {
    let timer
    const finish = () => {
      clearTimeout(timer)
      scene.removeEventListener('animationend', onEnd)
      resolve()
    }
    const onEnd = (e) => {
      if (e.target === scene && e.animationName === 'ls-exit') finish()
    }
    scene.addEventListener('animationend', onEnd)
    timer = setTimeout(finish, FALLBACK_MS)
    scene.dataset.exiting = '1'
  })
}
