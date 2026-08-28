const MAX_LIT = 16

// lit:指定色階的切面加 .geo__lit 慢速閃動(至多 16 面)
export default function Facets({ polys, prefix, lit }) {
  let litCount = 0
  return polys.map((p, i) => {
    const isLit = lit !== undefined && p.tone === lit && litCount < MAX_LIT
    if (isLit) litCount += 1
    return (
      <polygon
        key={i}
        points={p.points}
        className={`geo__f ${prefix}--${p.tone}${isLit ? ' geo__lit' : ''}`}
        style={
          isLit ? { '--lit-delay': `${-(i % 7) * 1.3}s`, '--lit-period': `${7 + (i % 5)}s` } : undefined
        }
      />
    )
  })
}
