// 以中心放射的彩繪玻璃切面:rays 條射線 × rings 圈,角度與半徑帶抖動;固定 seed 使每次渲染相同
function rng(seed) {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6d2b79f5) >>> 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function pick(rnd, weights) {
  let r = rnd() * weights.reduce((a, b) => a + b, 0)
  for (let i = 0; i < weights.length; i++) {
    r -= weights[i]
    if (r < 0) return i
  }
  return weights.length - 1
}

export function fan({ cx, cy, rays, rings, r0, step, seed, weights, jitter = 0.4, split = true }) {
  const rnd = rng(seed)
  const slice = (Math.PI * 2) / rays
  const angle = Array.from({ length: rays }, (_, i) => i * slice + (rnd() - 0.5) * slice * jitter)
  const radius = angle.map(() =>
    Array.from({ length: rings }, (_, j) => r0 + step * j + (rnd() - 0.5) * step * jitter),
  )
  const FAR = 4000
  const pt = (i, j) => {
    if (j < 0) return [cx, cy]
    const k = i % rays
    const r = j >= rings ? FAR : radius[k][j]
    return [cx + Math.cos(angle[k]) * r, cy + Math.sin(angle[k]) * r]
  }
  const out = []
  const push = (pts) =>
    out.push({
      points: pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' '),
      tone: pick(rnd, weights),
    })
  for (let i = 0; i < rays; i++) {
    for (let j = 0; j <= rings; j++) {
      const a = pt(i, j - 1)
      const b = pt(i + 1, j - 1)
      const c = pt(i + 1, j)
      const d = pt(i, j)
      if (j === 0) push([a, c, d])
      else if (!split) push([a, b, c, d])
      else if (rnd() < 0.5) {
        push([a, b, c])
        push([a, c, d])
      } else {
        push([a, b, d])
        push([b, c, d])
      }
    }
  }
  return out
}
