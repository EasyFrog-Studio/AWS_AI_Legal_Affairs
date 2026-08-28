// 插畫層 viewBox 400×320,地平線 y=286。畫序:地面 → 光暈 → 高背椅 → 法典砝碼 → 天秤 → 法槌
// 天秤:樑以支點 (140,100) 為軸;盤以吊點 (--hang-x,100) 為軸反向旋轉保持水平,再加微擺

function Pan({ x, swing, swingDelay }) {
  return (
    <g className="sc__hang" style={{ '--hang-x': `${x}px` }}>
      <g className="sc__swing" style={{ '--swing': swing, '--swing-delay': swingDelay }}>
        <path className="s-gold2" d={`M${x} 100 L${x - 30} 170 M${x} 100 L${x + 30} 170 M${x} 100 V168`} />
        <path className="f-gold" d={`M${x - 34} 172 Q${x} 200 ${x + 34} 172 Z`} />
        <path className="f-gold2" d={`M${x} 172 Q${x + 17} 192 ${x + 34} 172 Z`} />
        <ellipse className="f-gold3" cx={x} cy="172" rx="34" ry="5" />
      </g>
    </g>
  )
}

// 天秤後方的聚光:三層同心淡光疊出柔邊,讓金色天秤成為視覺主體
function Spotlight() {
  return (
    <g className="ob__glow">
      <ellipse cx="150" cy="180" rx="160" ry="140" />
      <ellipse cx="150" cy="180" rx="120" ry="105" />
      <ellipse cx="150" cy="180" rx="80" ry="70" />
    </g>
  )
}

function Chair() {
  return (
    <g>
      <path className="f-wood2" d="M148 214 V92 Q148 52 200 46 Q252 52 252 92 V214 Z" />
      <path className="f-wood" d="M162 208 V100 Q162 70 200 64 Q238 70 238 100 V208 Z" />
      <path className="f-wood3 o20" d="M200 64 Q238 70 238 100 V208 H200 Z" />
      <path className="f-gold" d="M200 30 L209 44 L200 52 L191 44 Z" />
      <rect className="f-wood3" x="126" y="178" width="34" height="10" rx="2" />
      <rect className="f-wood3" x="240" y="178" width="34" height="10" rx="2" />
      <rect className="f-wood2" x="132" y="188" width="8" height="24" />
      <rect className="f-wood2" x="260" y="188" width="8" height="24" />
      <rect className="f-wood" x="128" y="210" width="144" height="18" />
      <rect className="f-wood2" x="128" y="228" width="144" height="14" />
      <rect className="f-wood2" x="136" y="242" width="12" height="44" />
      <rect className="f-wood2" x="252" y="242" width="12" height="44" />
      <rect className="f-wood" x="148" y="266" width="104" height="6" />
    </g>
  )
}

function BooksAndWeight() {
  return (
    <g>
      <rect className="f-olive3" x="18" y="274" width="80" height="12" rx="1" />
      <rect className="f-gold" x="22" y="274" width="3" height="12" />
      <rect className="f-wood2" x="24" y="262" width="72" height="12" rx="1" />
      <rect className="f-gold" x="28" y="262" width="3" height="12" />
      <path className="f-gold2" d="M44 262 V248 Q44 244 48 244 H68 Q72 244 72 248 V262 Z" />
      <path className="s-gold" d="M50 244 Q58 232 66 244" />
      <rect className="f-gold3" x="46" y="250" width="6" height="12" />
    </g>
  )
}

function Scale() {
  return (
    <g>
      <path className="f-gold2" d="M92 286 H188 L180 274 H100 Z" />
      <path className="f-gold" d="M104 274 H176 L170 264 H110 Z" />
      <rect className="f-gold2" x="132" y="250" width="16" height="14" />
      <rect className="f-gold3" x="136" y="106" width="4" height="146" />
      <rect className="f-gold2" x="140" y="106" width="4" height="146" />
      <g className="sc__beam">
        <path className="f-gold" d="M40 96 H240 V104 H40 Z" />
        <path className="f-gold3" d="M40 96 H240 V98 H40 Z" />
        <path className="f-gold2" d="M40 101 H240 V104 H40 Z" />
        <path className="f-gold" d="M40 96 L32 100 L40 104 Z" />
        <path className="f-gold" d="M240 96 L248 100 L240 104 Z" />
        <Pan x={46} swing="4.6s" swingDelay="-1.1s" />
        <Pan x={234} swing="5.4s" swingDelay="-3.4s" />
      </g>
      <path className="f-gold" d="M140 82 L149 96 L140 110 L131 96 Z" />
      <path className="f-gold3" d="M140 82 L131 96 L140 110 Z" />
    </g>
  )
}

// 法槌:敲擊姿態——槌頭直立、端面平貼擊墊(中心 (300,238)),握柄自槌頭中段水平向右、與槌頭成 90°;
// 握點是柄尾 (381,238),敲擊以此為軸整組抬起再落下。knocks 是每次敲擊的 id:法槌以最後一個重新掛載重播,
// 每個 id 各有一圈獨立漣漪、在落槌瞬間(0.22s)擴散,不被下一次敲擊打斷;固定的 .sc__ring 留給登入成功
function Gavel({ knocks }) {
  const last = knocks[knocks.length - 1] ?? 0
  return (
    <g>
      <rect className="f-wood2" x="266" y="270" width="68" height="16" rx="2" />
      <ellipse className="f-wood3" cx="300" cy="270" rx="34" ry="7" />
      <circle className="sc__ring" cx="300" cy="266" r="40" vectorEffect="non-scaling-stroke" />
      {knocks.map((id) => (
        <circle key={id} className="sc__ring sc__ring--knock" cx="300" cy="266" r="40" vectorEffect="non-scaling-stroke" />
      ))}
      <g key={last} className={`sc__knock${last ? ' is-knock' : ''}`}>
        <g className="sc__gavel">
          <g transform="translate(300 238)">
            <rect className="f-wood3" x="11" y="-4" width="64" height="8" rx="3" />
            <rect className="f-wood2" x="11" y="1" width="64" height="3" />
            <rect className="f-gold2" x="73" y="-5" width="8" height="10" rx="2" />
            <rect className="f-wood" x="-11" y="-28" width="22" height="56" rx="4" />
            <rect className="f-wood3" x="-11" y="-24" width="6" height="48" />
            <rect className="f-gold" x="-11" y="-28" width="22" height="7" rx="2" />
            <rect className="f-gold" x="-11" y="21" width="22" height="7" rx="2" />
            <rect className="f-gold3" x="-9" y="-26" width="18" height="2" />
            <rect className="f-gold3" x="-9" y="23" width="18" height="2" />
          </g>
        </g>
      </g>
    </g>
  )
}

export default function Objects({ knocks }) {
  return (
    <>
      <path className="ob__floor" d="M0 286 H400 V320 H0 Z" />
      <Spotlight />
      <Chair />
      <BooksAndWeight />
      <Scale />
      <Gavel knocks={knocks} />
    </>
  )
}
