# tokens.css 對比度驗算:改色後執行 `python verify_tokens.py`,全部 PASS 才能進版
import math


def raw(L, C, H):
    a = C * math.cos(math.radians(H))
    b = C * math.sin(math.radians(H))
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (
        +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )


def clip(t):
    return any(v < -0.001 or v > 1.001 for v in raw(*t))


def lin(t):
    return [max(0.0, min(1.0, v)) for v in raw(*t)]


def lum(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def cr(a, b):
    x, y = lum(a), lum(b)
    x, y = max(x, y), min(x, y)
    return (x + 0.05) / (y + 0.05)


T = {
    'paper': (.945, .022, 90), 'surface': (.975, .012, 90), 'surface-2': (.915, .024, 90),
    'rail': (.37, .035, 150), 'rail-2': (.43, .035, 150), 'rail-mid': (.35, .035, 150), 'rail-deep': (.33, .035, 150),
    'on-rail': (.92, .02, 90), 'on-rail-soft': (.86, .02, 100),
    'ink': (.30, .02, 150), 'ink-soft': (.44, .02, 150), 'ink-faint': (.45, .02, 150),
    'wood': (.45, .05, 60), 'link': (.40, .05, 150),
    'rule': (.76, .02, 110), 'rule-soft': (.86, .018, 100), 'rule-strong': (.56, .03, 130),
    'gold': (.74, .12, 85), 'gold-deep': (.55, .11, 80),
    'pass': (.42, .08, 150), 'reject': (.45, .09, 45), 'processing': (.48, .10, 78), 'error': (.46, .13, 30),
}
S = {k: lin(v) for k, v in T.items()}

# (前景, 背景, 最低比):內文 4.5、內文主色 7、元件邊界與焦點環 3
CHECKS = [
    ('ink', 'paper', 7), ('ink', 'surface', 7), ('ink', 'surface-2', 7),
    ('ink-soft', 'paper', 4.5), ('ink-soft', 'surface', 4.5), ('ink-faint', 'paper', 4.5),
    ('wood', 'paper', 4.5), ('wood', 'surface', 4.5), ('link', 'paper', 4.5),
    ('rule-strong', 'paper', 3), ('rule-strong', 'surface', 3),
    ('gold-deep', 'paper', 3), ('gold-deep', 'surface', 3),
    ('on-rail', 'rail', 7), ('on-rail', 'rail-2', 4.5), ('on-rail-soft', 'rail', 6), ('on-rail-soft', 'rail-2', 4.5),
    ('gold', 'rail', 3), ('gold', 'rail-2', 3), ('surface', 'rail', 3),
    ('pass', 'surface', 4.5), ('reject', 'surface', 4.5), ('processing', 'surface', 4.5),
    ('error', 'paper', 4.5), ('error', 'surface', 4.5),
]

fail = 0
for a, b, mn in CHECKS:
    r = cr(S[a], S[b])
    ok = r >= mn
    fail += not ok
    print(f"{a + ' / ' + b:34}{r:6.2f}:1  min {mn:<5}{'PASS' if ok else '*** FAIL ***'}")

r = cr(S['ink'], S['paper'])  # 太高會眩光
print(f"{'ink max (glare)':34}{r:6.2f}:1  max 12   {'PASS' if r <= 12 else 'FAIL'}")
fail += r > 12
d = (T['surface'][0] - T['paper'][0]) * 100  # 卡片要比底亮
print(f"{'card vs ground':34}{d:6.1f} L  min 3.0  {'PASS' if d >= 3 else 'FAIL'}")
fail += d < 3
big = [k for k in ('paper', 'surface', 'surface-2', 'rail', 'rail-2', 'rail-deep') if T[k][1] > 0.04]
print(f"{'large-area chroma <= 0.04':34}{'':>10}         {'PASS' if not big else 'FAIL ' + str(big)}")
fail += bool(big)
cl = [k for k, v in T.items() if clip(v)]
print(f"\ngamut clip: {cl or 'none'}\n>>> {'ALL PASS' if not fail and not cl else str(fail) + ' FAILED'}")
