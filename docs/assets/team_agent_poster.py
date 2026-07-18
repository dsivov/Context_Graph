"""Generate a LinkedIn poster for the Team Agent Framework — house style (dark, teal).
All drawing helpers take LOGICAL coords (1080x1350) and scale by S internally."""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFont

S = 2
W, H = 1080 * S, 1350 * S
OUT = "/storage/Work/Context_Graph/docs/assets/team_agent_poster.png"

BG = (0x0f, 0x11, 0x17)
INK = (0xe7, 0xeb, 0xf3)
INK_SOFT = (0xaa, 0xb3, 0xc5)
INK_FAINT = (0x7b, 0x84, 0x99)
PANEL = (0x1b, 0x1f, 0x2a)
LINE = (0x2c, 0x33, 0x46)
TEAL = (0x22, 0xc3, 0xd6)
TEAL2 = (0x19, 0xb8, 0x9a)
BLUE = (0x5b, 0x8d, 0xef)
CYAN_HI = (0xbd, 0xf0, 0xea)
CYAN_HI2 = (0xa7, 0xec, 0xf5)
TEALTX = (0x7f, 0xe3, 0xcf)

MONT = "/usr/share/fonts/julietaula-montserrat-fonts/"
SCP = "/usr/share/fonts/adobe-source-code-pro-fonts/"


def _f(path, size):
    return ImageFont.truetype(path, int(size * S))

def mont(w, size):
    fname = {"bold": "Montserrat-Bold.otf", "reg": "Montserrat-Regular.otf",
             "semi": "Montserrat-SemiBold.otf", "med": "Montserrat-Medium.otf",
             "black": "Montserrat-Black.otf", "xbold": "Montserrat-ExtraBold.otf"}[w]
    try:
        return _f(MONT + fname, size)
    except OSError:
        return _f(MONT + "Montserrat-Bold.otf", size)

def mono(size):
    return _f(SCP + "SourceCodePro-Semibold.otf", size)


bg = np.zeros((H, W, 3), dtype=np.float32)
bg[:] = np.array(BG, dtype=np.float32)
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)

def glow(cx, cy, radius, color, strength):
    d2 = ((xx - cx * S) ** 2 + (yy - cy * S) ** 2) / ((radius * S) ** 2)
    a = np.exp(-d2) * strength
    for i in range(3):
        bg[:, :, i] += color[i] * a

glow(880, 130, 640, TEAL, 0.28)
glow(160, 300, 560, TEAL2, 0.14)
glow(540, 780, 760, BLUE, 0.09)
glow(540, 1180, 720, TEAL, 0.09)
np.clip(bg, 0, 255, out=bg)
img = Image.fromarray(bg.astype(np.uint8), "RGB")
d = ImageDraw.Draw(img, "RGBA")


def measure(text, font):
    b = d.textbbox((0, 0), text, font=font)
    return b[2] - b[0], b[3] - b[1], b

def ctext(cx, y, text, font, fill):
    w, h, b = measure(text, font)
    d.text((cx * S - w / 2 - b[0], y * S - b[1]), text, font=font, fill=fill)

def ltext(x, y, text, font, fill):
    _, _, b = measure(text, font)
    d.text((x * S - b[0], y * S - b[1]), text, font=font, fill=fill)
    return measure(text, font)[0] / S

def spaced(cx, y, text, font, fill, tracking):
    tr = tracking * S
    widths = [measure(c, font)[0] for c in text]
    total = sum(widths) + tr * (len(text) - 1)
    x = cx * S - total / 2
    for c, w in zip(text, widths):
        _, _, b = measure(c, font)
        d.text((x - b[0], y * S - b[1]), c, font=font, fill=fill)
        x += w + tr

def rrect(x0, y0, x1, y1, r, fill=None, outline=None, width=1):
    d.rounded_rectangle([x0 * S, y0 * S, x1 * S, y1 * S], radius=r * S,
                        fill=fill, outline=outline, width=int(width * S))

def line(x0, y0, x1, y1, color, w=1.5):
    d.line([x0 * S, y0 * S, x1 * S, y1 * S], fill=color, width=int(w * S))

def grad_text(cx, top_y, text, font, c1, c2):
    w, h, b = measure(text, font)
    pad = 12 * S
    mask = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
    ImageDraw.Draw(mask).text((pad - b[0], pad - b[1]), text, font=font, fill=255)
    ga = np.zeros((h + 2 * pad, w + 2 * pad, 3), dtype=np.uint8)
    tvec = np.linspace(0, 1, w + 2 * pad)
    for i in range(3):
        ga[:, :, i] = (c1[i] + (c2[i] - c1[i]) * tvec).astype(np.uint8)
    img.paste(Image.fromarray(ga, "RGB"), (int(cx * S - w / 2 - pad), int(top_y * S - pad)), mask)

def grad_text_left(left_x, top_y, text, font, c1, c2):
    w, h, b = measure(text, font)
    pad = 12 * S
    mask = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
    ImageDraw.Draw(mask).text((pad - b[0], pad - b[1]), text, font=font, fill=255)
    ga = np.zeros((h + 2 * pad, w + 2 * pad, 3), dtype=np.uint8)
    tvec = np.linspace(0, 1, w + 2 * pad)
    for i in range(3):
        ga[:, :, i] = (c1[i] + (c2[i] - c1[i]) * tvec).astype(np.uint8)
    img.paste(Image.fromarray(ga, "RGB"), (int(left_x * S - pad), int(top_y * S - pad)), mask)


CX = 540

# eyebrow
spaced(CX, 96, "A HERMES EXTENSION   ·   POWERED BY CONTEXT GRAPH", mono(14), INK_FAINT, 4)

# wordmark — cohermes ("co" teal, "hermes" cyan→blue gradient)
wf = mont("xbold", 112)
w_co = measure("co", wf)[0] / S
w_he = measure("hermes", wf)[0] / S
x0 = CX - (w_co + w_he) / 2
ltext(x0, 152, "co", wf, TEAL)
grad_text_left(x0 + w_co, 152, "hermes", wf, CYAN_HI, BLUE)

# tagline
ctext(CX, 328, "One graph. One team.", mont("bold", 38), INK)

# subtitle
ctext(CX, 392, "Many developers. Many agents. One shared brain.", mont("reg", 25), INK_SOFT)

# ---- visual: agents -> hub -> artifacts ----
chip_y0, chip_y1 = 500, 578
chips = [("DEV A", 205), ("DEV B", 540), ("DEV C", 875)]
hub_cx, hub_top, hub_bot = 540, 690, 828
for label, cx in chips:
    line(cx, chip_y1, hub_cx, hub_top, TEAL + (140,), 1.5)
for label, cx in chips:
    rrect(cx - 118, chip_y0, cx + 118, chip_y1, 14, fill=PANEL + (255,), outline=BLUE + (255,), width=1.5)
    ctext(cx, chip_y0 + 24, label, mont("semi", 20), INK)
    ctext(cx, chip_y0 + 52, "developer + agent", mono(11), INK_FAINT)

rrect(hub_cx - 252, hub_top, hub_cx + 252, hub_bot, 18, fill=(0x12, 0x2a, 0x2f, 255), outline=TEAL + (255,), width=2.4)
ctext(hub_cx, hub_top + 32, "CONTEXT  GRAPH", mont("bold", 33), CYAN_HI)
ctext(hub_cx, hub_top + 88, "the shared, governed knowledge graph", mono(12.5), (0x8f, 0xe6, 0xf2))

art_y0, art_y1 = 915, 973
arts = [("decisions", 180), ("code reviews", 405), ("tasks", 630), ("commits", 850)]
for label, cx in arts:
    line(hub_cx, hub_bot, cx, art_y0, TEAL2 + (110,), 1.3)
for label, cx in arts:
    w, _, _ = measure(label, mont("semi", 17))
    half = w / (2 * S) + 24
    rrect(cx - half, art_y0, cx + half, art_y1, 999, fill=(0x1b, 0x1f, 0x2a, 255), outline=TEAL2 + (210,), width=1.4)
    ctext(cx, art_y0 + 20, label, mont("semi", 17), TEALTX)

# ---- the hook ----
hx = CX - 372
line(hx - 14, 1055, hx - 14, 1213, TEAL, 5)  # accent bar
ltext(hx, 1058, "Hermes remembers,", mont("bold", 38), INK)
ltext(hx, 1110, "per developer. We make", mont("bold", 38), INK)
seg = "the team remember — "
adv = ltext(hx, 1162, seg, mont("bold", 38), INK)
ltext(hx + adv, 1162, "together.", mont("bold", 38), TEAL)

# ---- feature pills (single row) ----
pills = ["extends hermes", "subscription-native", "reuse-first"]
pf = mono(15)
ws = [measure(p, pf)[0] / S + 44 for p in pills]
gap = 18
tot = sum(ws) + gap * (len(pills) - 1)
x = CX - tot / 2
py = 1258
for p, w in zip(pills, ws):
    rrect(x, py, x + w, py + 46, 999, fill=(0x22, 0x27, 0x36, 255), outline=LINE + (255,), width=1.3)
    ctext(x + w / 2, py + 23, p, pf, INK_SOFT)
    x += w + gap

# ---- footer ----
ctext(CX, 1322, "cohermes  ·  an extension of Hermes  ·  built on Context Graph  ·  the (h, r, t, rc) quadruple",
      mono(12), INK_FAINT)

final = img.resize((1080, 1350), Image.LANCZOS)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
final.save(OUT)
print("saved", OUT, final.size)
