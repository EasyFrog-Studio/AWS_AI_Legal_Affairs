import math
def raw(L,C,H):
    a=C*math.cos(math.radians(H)); b=C*math.sin(math.radians(H))
    l_=L+0.3963377774*a+0.2158037573*b
    m_=L-0.1055613458*a-0.0638541728*b
    s_=L-0.0894841775*a-1.2914855480*b
    l,m,s=l_**3,m_**3,s_**3
    return (+4.0767416621*l-3.3077115913*m+0.2309699292*s,
            -1.2684380046*l+2.6097574011*m-0.3413193965*s,
            -0.0041960863*l-0.7034186147*m+1.7076147010*s)
def clip(t): return any(v<-0.001 or v>1.001 for v in raw(*t))
def lin(t):  return [max(0.0,min(1.0,v)) for v in raw(*t)]
def lum(c):  return 0.2126*c[0]+0.7152*c[1]+0.0722*c[2]
def cr(a,b):
    x,y=lum(a),lum(b); x,y=max(x,y),min(x,y); return (x+0.05)/(y+0.05)

T={'paper':(.925,.032,85),'surface':(.972,.014,88),'surface-2':(.890,.034,82),
   'forest':(.320,.038,152),'forest-2':(.385,.040,152),'on-forest':(.940,.016,90),
   'on-forest-soft':(.760,.022,100),'ink':(.260,.024,68),'ink-soft':(.450,.028,70),
   'ink-faint':(.510,.026,72),'bark':(.450,.045,65),'rule':(.600,.030,78),
   'rule-strong':(.480,.038,72),'accent':(.430,.170,27),'accent-hover':(.375,.150,27),
   'on-accent':(.975,.012,88),'vermilion-rule':(.600,.115,30),'pass':(.400,.090,155),
   'reject':(.430,.105,42),'error':(.460,.145,30),'focus-on-forest':(.780,.110,34)}
S={k:lin(v) for k,v in T.items()}

CHECKS=[('ink','paper',7),('ink','surface',7),('ink-soft','paper',4.5),
 ('ink-soft','surface',4.5),('ink-faint','paper',4.5),('ink-faint','surface',4.5),
 ('bark','paper',4.5),('rule','paper',3),('rule','surface',3),('rule-strong','paper',3),
 ('vermilion-rule','paper',3),('accent','paper',4.5),('accent','surface',4.5),
 ('on-accent','accent',4.5),('on-accent','accent-hover',4.5),('pass','paper',4.5),
 ('pass','surface',4.5),('reject','paper',4.5),('reject','surface',4.5),
 ('error','paper',4.5),('on-forest','forest',8),('on-forest','forest-2',7),
 ('on-forest-soft','forest',4.5),('focus-on-forest','forest',3),
 ('focus-on-forest','forest-2',3),('surface','forest',3)]

fail=0
for a,b,mn in CHECKS:
    r=cr(S[a],S[b]); ok=r>=mn; fail+=not ok
    print(f"{a+' / '+b:34}{r:6.2f}:1  min {mn:<5}{'PASS' if ok else '*** FAIL ***'}")
r=cr(S['on-forest'],S['forest'])                      # 反白暈光上限
print(f"{'on-forest max (halation)':34}{r:6.2f}:1  max 12   {'PASS' if r<=12 else 'FAIL'}"); fail+=r>12
d=(T['surface'][0]-T['paper'][0])*100                 # 圖地關係
print(f"{'card vs ground':34}{d:6.1f} L  min 4.0  {'PASS' if d>=4 else 'FAIL'}"); fail+=d<4
big=[k for k in ('paper','surface','surface-2','forest','forest-2') if T[k][1]>0.04]
print(f"{'large-area chroma <= 0.04':34}{'':>10}         {'PASS' if not big else 'FAIL '+str(big)}"); fail+=bool(big)
sep=T['pass'][1]-T['forest'][1]                       # 印章綠 vs 環境綠
print(f"{'seal-green vs env-green':34}{sep:6.3f}    min 0.04 {'PASS' if sep>=.04 else 'FAIL'}"); fail+=sep<.04
cl=[k for k,v in T.items() if clip(v)]
print(f"\ngamut clip: {cl or 'none'}\n>>> {'ALL PASS' if not fail and not cl else str(fail)+' FAILED'}")
