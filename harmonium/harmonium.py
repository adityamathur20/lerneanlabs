"""
Harmonium
==========

WHITE KEYS (Q row):
  Q  W  E  R  T  Y  U  |  I  O  P  [  ]  backslash
  C  D  E  F  G  A  B  |  C  D  E  F  G  A

BLACK KEYS (number row, starting from 2):
  2   3       5   6   7   |   9   0   -       =   (backspace)
  C#  D#      F#  G#  A#  |  C#  D#  F#      G#   A#

  (4 and 8 are gaps — no black key between E-F and B-C)

ALT oct1 whites (home row):  A  S  D  F  G  H  J

BELLOWS:  hold SPACE  (~25s buffer)
OCTAVE:   left / right arrow  (-2 to +2)
TONE:     TAB  to cycle presets
QUIT:     ESC
"""

import pygame
import numpy as np
from pynput import keyboard as kb
import sys
import math
import threading

SAMPLE_RATE = 44100
pygame.mixer.pre_init(frequency=SAMPLE_RATE, size=-16, channels=1, buffer=512)
pygame.init()

WIN_W, WIN_H = 1160, 640
screen = pygame.display.set_mode((WIN_W, WIN_H))
pygame.display.set_caption("Harmonium")
clock = pygame.time.Clock()

# ── Colors ────────────────────────────────────────────────────────────────────
BG          = (16, 9, 4)
WOOD_DARK   = (52, 26, 7)
WOOD_MID    = (82, 46, 16)
WOOD_LIGHT  = (130, 72, 28)
GOLD        = (210, 172, 52)
GOLD_DIM    = (110, 86, 20)
GOLD_BRIGHT = (255, 222, 80)
WHITE_KEY   = (243, 231, 207)
WHITE_PRESS = (255, 208, 55)
BLACK_KEY   = (20, 12, 5)
BLACK_PRESS = (190, 140, 22)
TEXT_LIGHT  = (218, 195, 150)
TEXT_DIM    = (120, 100, 68)
RED_FELT    = (110, 26, 16)
SHADOW      = (7, 3, 1)
BELLOWS_COL = (255, 175, 38)
MODE_ON     = (42, 130, 75)
MODE_OFF    = (36, 36, 28)
OCT_BTN     = (55, 35, 12)
OCT_ACTIVE  = (160, 110, 30)

pygame.font.init()
def fnt(name, size, bold=False):
    try:
        return pygame.font.SysFont(name, size, bold=bold)
    except Exception:
        return pygame.font.SysFont(None, size)

F_LABEL = fnt("Georgia", 12, True)
F_NOTE  = fnt("Georgia", 10)
F_TITLE = fnt("Georgia", 28, True)
F_SMALL = fnt("Georgia", 10)
F_HINT  = fnt("Georgia", 11)
F_MED   = fnt("Georgia", 12, True)

# ── Tone presets ──────────────────────────────────────────────────────────────
# Harmonium = free reed aerophone.
# Real reed timbre: dominated by ODD harmonics (1, 3, 5, 7...) like a clarinet/square wave,
# with even harmonics present but weaker. Also has:
#   - slow tremolo/vibrato from air pressure fluctuation
#   - slight inharmonicity (two reeds slightly detuned = chorus)
#   - breathy noise component
#   - fast attack, very long sustain

TONES = [
    {
        # Classic Indian harmonium — warm, slightly nasal, odd-harmonic dominant
        "name": "Harmonium",
        "desc": "indian reed",
        # (harmonic_number, amplitude)  odd partials dominate
        "partials": [
            (1,  0.50),   # fundamental
            (2,  0.10),   # weak even
            (3,  0.30),   # strong odd
            (4,  0.06),   # weak even
            (5,  0.18),   # strong odd
            (6,  0.04),   # weak even
            (7,  0.10),   # odd
            (9,  0.05),   # odd
            (11, 0.03),   # odd
            # two reeds slightly detuned (chorus/shimmer)
            (1.003, 0.12),
            (2.997, 0.06),
        ],
        "attack":  0.012,
        "decay":   0.0,
        "sustain": 0.92,
        "noise":   0.006,
        "tremolo_rate": 5.5,    # Hz
        "tremolo_depth": 0.04,  # amplitude modulation depth
    },
    {
        # Brighter, more reedy — closer to a harmonium in higher registers
        "name": "Reedy",
        "desc": "bright reed",
        "partials": [
            (1,  0.40),
            (2,  0.08),
            (3,  0.35),
            (4,  0.05),
            (5,  0.22),
            (6,  0.03),
            (7,  0.14),
            (9,  0.08),
            (11, 0.05),
            (13, 0.03),
            (1.004, 0.10),
            (2.996, 0.05),
        ],
        "attack":  0.008,
        "decay":   0.0,
        "sustain": 0.90,
        "noise":   0.012,
        "tremolo_rate": 6.0,
        "tremolo_depth": 0.055,
    },
    {
        # Pipe organ / western harmonium — rounder, more even-harmonic balance
        "name": "Organ",
        "desc": "pipe organ",
        "partials": [
            (1,  0.55),
            (2,  0.22),
            (3,  0.18),
            (4,  0.10),
            (5,  0.08),
            (6,  0.05),
            (8,  0.03),
            (1.001, 0.08),
        ],
        "attack":  0.020,
        "decay":   0.0,
        "sustain": 0.95,
        "noise":   0.003,
        "tremolo_rate": 4.5,
        "tremolo_depth": 0.025,
    },
    {
        # Piano-like — strong attack, even harmonics, decays
        "name": "Piano",
        "desc": "struck string",
        "partials": [
            (1,  0.50),
            (2,  0.25),
            (3,  0.12),
            (4,  0.09),
            (5,  0.06),
            (6,  0.04),
            (7,  0.02),
            (1.0015, 0.08),   # slight detuning of second string
        ],
        "attack":  0.004,
        "decay":   0.35,
        "sustain": 0.45,
        "noise":   0.005,
        "tremolo_rate": 0.0,
        "tremolo_depth": 0.0,
    },
]
current_tone = 0


def make_tone(freq, preset, duration=4.0):
    n_samples = int(SAMPLE_RATE * duration)
    t = np.linspace(0.0, duration, n_samples, dtype=np.float64)

    wave = np.zeros(n_samples, dtype=np.float64)
    for harm, amp in preset["partials"]:
        wave += np.sin(2.0 * np.pi * freq * harm * t) * amp

    # Tremolo (amplitude modulation — simulates bellows flutter)
    tr = preset.get("tremolo_rate", 0)
    td = preset.get("tremolo_depth", 0)
    if tr > 0 and td > 0:
        tremolo = 1.0 + td * np.sin(2.0 * np.pi * tr * t)
        wave *= tremolo

    # Reed breath noise (band-limited — low-pass character)
    rng = np.random.default_rng(seed=42)
    noise_raw = rng.standard_normal(n_samples)
    # Simple low-pass: average with neighbours to make noise breathier
    noise_lp = np.convolve(noise_raw, np.ones(8) / 8, mode="same")
    wave += noise_lp * preset["noise"]

    # Envelope
    env    = np.ones(n_samples, dtype=np.float64)
    att_n  = max(1, int(preset["attack"]  * SAMPLE_RATE))
    dec_n  = max(0, int(preset["decay"]   * SAMPLE_RATE))
    sl     = preset["sustain"]
    fade_n = int(0.18 * n_samples)

    env[:att_n] = np.linspace(0.0, 1.0, att_n)
    if dec_n > 0 and att_n + dec_n < n_samples - fade_n:
        env[att_n: att_n + dec_n]       = np.linspace(1.0, sl, dec_n)
        env[att_n + dec_n: n_samples - fade_n] = sl
    else:
        env[att_n: n_samples - fade_n]  = sl
    env[n_samples - fade_n:] = np.linspace(sl, 0.02, fade_n)

    wave = np.clip(wave * env, -1.0, 1.0)
    return (wave * 27000).astype(np.int16)


# ── Key mapping ───────────────────────────────────────────────────────────────
# semitone 0 = C,  1 = C#,  2 = D, ... 11 = B,  12 = C (oct+1), ... 24 = C (top)
#
# BLACK KEY POSITIONS on number row, starting from 2:
#
#  Physical:  1   2   3   4   5   6   7   8   9   0   -   =
#  Oct1:          C#  D#  [gap F-E]  F#  G#  A#
#  Oct2:                              [gap]  C#  D#  [gap]  F#  G#  A#
#
#  Mapping (gaps at positions 4,8 = no black key between E-F and B-C):
#  2=C#(1)  3=D#(3)  [4=gap]  5=F#(6)  6=G#(8)  7=A#(10)
#  [8=gap]  9=C#(13)  0=D#(15)  [no gap key]  -=F#(18)  ==G#(20)
#  and we need A#(22): use backspace char '\x7f' or... let's use `\` (backslash key)
#
# Actually for clean physical layout:
#  Oct1 blacks: 2 3 . 5 6 7    (. = gap at 4)
#  Oct2 blacks: 9 0 . - =  \   (. = gap at 8, \ for A#22)

PRIMARY_MAP = [
    # ── Oct1 whites (Q row) ──
    ("q",   0),   # C3
    ("w",   2),   # D3
    ("e",   4),   # E3
    ("r",   5),   # F3
    ("t",   7),   # G3
    ("y",   9),   # A3
    ("u",  11),   # B3
    # ── Oct2 whites (Q row continued) ──
    ("i",  12),   # C4
    ("o",  14),   # D4
    ("p",  16),   # E4
    ("[",  17),   # F4
    ("]",  19),   # G4
    ("\\", 21),   # A4
    # ── Oct1 blacks (number row, start from 2) ──
    ("2",   1),   # C#3   ← above Q (C3)
    ("3",   3),   # D#3   ← above W (D3)
    # 4 = gap (no black between E and F)
    ("5",   6),   # F#3   ← above R (F3)
    ("6",   8),   # G#3   ← above T (G3)
    ("7",  10),   # A#3   ← above Y (A3)
    # 8 = gap (no black between B and C)
    # ── Oct2 blacks (number row continued) ──
    ("9",  13),   # C#4   ← above I (C4)
    ("0",  15),   # D#4   ← above O (D4)
    # no key for gap between E4-F4
    ("-",  18),   # F#4   ← above [ (F4)
    ("=",  20),   # G#4   ← above ] (G4)
    # A#4 = semitone 22 — use backtick or `1`
    # 1 is now free since we start blacks from 2
    ("1",  22),   # A#4   ← above \ (A4) — using freed '1' key
    # Top C = semitone 24 — use `
    ("`",  24),   # C5    (top)
    # B4 = semitone 23 — use `8` (the gap key, repurposed)
    ("8",  23),   # B4    (oct2 B, the gap position)
]

# Secondary: original home-row oct1 whites (A S D F G H J)
SECONDARY_MAP = [
    ("a",  0),
    ("s",  2),
    ("d",  4),
    ("f",  5),
    ("g",  7),
    ("h",  9),
    ("j", 11),
]

ALL_MAP = PRIMARY_MAP + SECONDARY_MAP

key_to_st = {}
for mk, st in ALL_MAP:
    key_to_st[mk] = st

CHROMATIC = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]

def is_black(st):
    return (st % 12) in (1, 3, 6, 8, 10)

BASE_OCTAVE  = 3
octave_shift = 0

def st_freq(st):
    return 16.352 * (2.0 ** (((BASE_OCTAVE + octave_shift) * 12 + st) / 12.0))

def disp_name(st):
    return f"{CHROMATIC[st % 12]}{BASE_OCTAVE + octave_shift + st // 12}"

# ── Sound cache ───────────────────────────────────────────────────────────────
sound_cache = {}

def get_snd(st):
    k = (current_tone, BASE_OCTAVE + octave_shift, st)
    if k not in sound_cache:
        pcm = make_tone(st_freq(st), TONES[current_tone])
        sound_cache[k] = pygame.sndarray.make_sound(pcm)
    return sound_cache[k]

def preload():
    print(f"Loading: {TONES[current_tone]['name']}...")
    seen = set()
    for _, st in ALL_MAP:
        if st not in seen:
            get_snd(st)
            seen.add(st)
    print("Done.")

threading.Thread(target=preload, daemon=True).start()

# ── State ─────────────────────────────────────────────────────────────────────
pressed_keys  = set()
active_sounds = {}
key_glow      = {}
key_lock      = threading.Lock()

bellows_pressure = 0.5
bellows_pumping  = False
FILL_RATE        = 1.8
DRAIN_RATE       = 1.0 / 25.0
KEY_DRAIN        = 0.012
bellows_anim     = 0.0

# ── Keyboard listener ─────────────────────────────────────────────────────────
def on_press(key):
    global bellows_pumping, current_tone, octave_shift
    try:
        char = key.char
        if char is None:
            return
        if char in key_to_st and char not in pressed_keys:
            st  = key_to_st[char]
            snd = get_snd(st)
            with key_lock:
                pressed_keys.add(char)
                snd.set_volume(max(0.0, min(1.0, bellows_pressure * 0.9)))
                snd.play(-1)
                active_sounds[char] = snd
                key_glow[char] = 1.0
    except AttributeError:
        if key == kb.Key.space:
            bellows_pumping = True
        elif key == kb.Key.tab:
            with key_lock:
                current_tone = (current_tone + 1) % len(TONES)
                for s in active_sounds.values(): s.fadeout(100)
                active_sounds.clear(); pressed_keys.clear()
            threading.Thread(target=preload, daemon=True).start()
        elif key == kb.Key.left:
            octave_shift = max(-2, octave_shift - 1)
            with key_lock:
                for s in active_sounds.values(): s.fadeout(80)
                active_sounds.clear(); pressed_keys.clear()
        elif key == kb.Key.right:
            octave_shift = min(2, octave_shift + 1)
            with key_lock:
                for s in active_sounds.values(): s.fadeout(80)
                active_sounds.clear(); pressed_keys.clear()
        elif key == kb.Key.esc:
            pygame.event.post(pygame.event.Event(pygame.QUIT))

def on_release(key):
    global bellows_pumping
    try:
        char = key.char
        if char and char in pressed_keys:
            with key_lock:
                pressed_keys.discard(char)
                if char in active_sounds:
                    active_sounds[char].fadeout(400)
                    del active_sounds[char]
    except AttributeError:
        if key == kb.Key.space:
            bellows_pumping = False

listener = kb.Listener(on_press=on_press, on_release=on_release)
listener.start()

# ── Bellows ───────────────────────────────────────────────────────────────────
def update_bellows(dt_ms):
    global bellows_pressure, bellows_anim
    dt = dt_ms / 1000.0
    if bellows_pumping:
        bellows_pressure = min(1.0, bellows_pressure + FILL_RATE * dt)
    drain = DRAIN_RATE + len(pressed_keys) * KEY_DRAIN
    bellows_pressure = max(0.0, bellows_pressure - drain * dt)
    vol = bellows_pressure * 0.9
    with key_lock:
        for s in active_sounds.values():
            s.set_volume(max(0.0, min(1.0, vol)))
    bellows_anim += dt * (10.0 if bellows_pumping else 1.5 + bellows_pressure * 3.0)

# ── Layout ────────────────────────────────────────────────────────────────────
WHITE_STS = [s for s in range(25) if not is_black(s)]
BLACK_STS  = [s for s in range(25) if is_black(s)]

MARGIN = 52
AW     = WIN_W - MARGIN * 2
WKW    = AW // len(WHITE_STS)
WKH    = 238
BKW    = int(WKW * 0.58)
BKH    = int(WKH * 0.60)
KEY_Y  = 222

white_x = {st: MARGIN + i * WKW for i, st in enumerate(WHITE_STS)}
black_x = {}
for st in BLACK_STS:
    lo, hi = st - 1, st + 1
    if lo in white_x and hi in white_x:
        black_x[st] = (white_x[lo] + white_x[hi]) // 2 + (WKW - BKW) // 2

st_disp = {st: mk for mk, st in PRIMARY_MAP}

# ── Drawing ───────────────────────────────────────────────────────────────────
def draw_wood(surf, rect):
    x, y, w, h = rect
    pygame.draw.rect(surf, WOOD_DARK, rect)
    for i in range(0, h, 6):
        c = tuple(min(255, cc + (24 + (i % 18) * 2) // 5) for cc in WOOD_MID)
        pygame.draw.line(surf, c, (x, y + i), (x + w, y + i))
    pygame.draw.rect(surf, WOOD_LIGHT, (x, y, w, 3))
    pygame.draw.rect(surf, SHADOW,     (x, y + h - 3, w, 3))
    pygame.draw.rect(surf, GOLD_DIM,   rect, 2)

def draw_bellows_side(surf, bx, by, bw, bh, pressure, anim):
    pygame.draw.rect(surf, WOOD_DARK, (bx, by, bw, bh))
    folds  = 12
    fold_h = bh / folds
    comp   = pressure * 0.28
    for i in range(folds):
        fy   = by + int(i * fold_h * (1 - comp * 0.4))
        fh   = max(2, int(fold_h * (1 - comp * 0.5)))
        fy  += int(math.sin(anim + i * 0.65) * pressure * 2.5)
        base = WOOD_LIGHT if i % 2 == 0 else WOOD_MID
        if pressure > 0.05:
            col = (
                min(255, int(base[0] + (BELLOWS_COL[0] - base[0]) * pressure * 0.55)),
                min(255, int(base[1] + (BELLOWS_COL[1] - base[1]) * pressure * 0.42)),
                int(base[2] * (1 - pressure * 0.28)),
            )
        else:
            col = base
        pygame.draw.rect(surf, col, (bx + 2, fy, bw - 4, fh - 1))
        pygame.draw.line(surf, GOLD if pressure > 0.1 else GOLD_DIM,
                         (bx + 2, fy), (bx + bw - 2, fy))
    pygame.draw.rect(surf, GOLD if pressure > 0.1 else GOLD_DIM, (bx, by, bw, bh), 2)

def draw_pressure_bar(surf, x, y, w, h, p):
    pygame.draw.rect(surf, WOOD_DARK, (x, y, w, h), border_radius=4)
    if p > 0.01:
        fw  = int((w - 4) * p)
        col = (
            min(255, int(80  + (BELLOWS_COL[0] - 80)  * p)),
            min(255, int(50  + (BELLOWS_COL[1] - 50)  * p)),
            min(255, int(10  + (BELLOWS_COL[2] - 10)  * p)),
        )
        pygame.draw.rect(surf, col, (x + 2, y + 2, fw, h - 4), border_radius=3)
    pygame.draw.rect(surf, GOLD_DIM, (x, y, w, h), 1, border_radius=4)

def draw_tone_btns(surf, cx, y):
    bw, bh, gap = 112, 36, 8
    bx = cx - (len(TONES) * bw + (len(TONES) - 1) * gap) // 2
    for i, p in enumerate(TONES):
        a = (i == current_tone)
        pygame.draw.rect(surf, MODE_ON if a else MODE_OFF, (bx, y, bw, bh), border_radius=5)
        pygame.draw.rect(surf, GOLD if a else GOLD_DIM, (bx, y, bw, bh), 1, border_radius=5)
        nm = F_MED.render(p["name"], True, GOLD if a else TEXT_DIM)
        ds = F_SMALL.render(p["desc"], True, TEXT_LIGHT if a else TEXT_DIM)
        surf.blit(nm, (bx + bw // 2 - nm.get_width() // 2, y + 4))
        surf.blit(ds, (bx + bw // 2 - ds.get_width() // 2, y + bh - 14))
        bx += bw + gap

def draw_oct_selector(surf, cx, y):
    shifts = [-2, -1, 0, 1, 2]
    bw, bh, gap = 42, 26, 6
    bx = cx - (len(shifts) * bw + (len(shifts) - 1) * gap) // 2
    lbl = F_SMALL.render("OCTAVE SHIFT  left / right arrows", True, TEXT_DIM)
    surf.blit(lbl, (cx - lbl.get_width() // 2, y - 13))
    for s in shifts:
        a = (s == octave_shift)
        pygame.draw.rect(surf, OCT_ACTIVE if a else OCT_BTN, (bx, y, bw, bh), border_radius=4)
        pygame.draw.rect(surf, GOLD if a else GOLD_DIM, (bx, y, bw, bh), 1, border_radius=4)
        t  = f"+{s}" if s > 0 else str(s)
        tl = F_MED.render(t, True, GOLD_BRIGHT if a else TEXT_DIM)
        surf.blit(tl, (bx + bw // 2 - tl.get_width() // 2, y + 5))
        bx += bw + gap

def key_label(mk):
    labels = {"\\": "\\", "[": "[", "]": "]", "-": "-", "=": "=",
              "`": "`",  "1": "1",  "8": "8"}
    return labels.get(mk, mk.upper())

def draw_keyboard(surf):
    for st in WHITE_STS:
        x    = white_x[st]
        w, h = WKW - 2, WKH
        mk   = st_disp.get(st)
        all_mk = [k for k, s in key_to_st.items() if s == st]
        any_p  = any(k in pressed_keys for k in all_mk)
        glow   = max((key_glow.get(k, 0) for k in all_mk), default=0)
        g      = max(glow, 1.0 if any_p else 0.0)

        col = (
            int(WHITE_KEY[0] + (WHITE_PRESS[0] - WHITE_KEY[0]) * g),
            int(WHITE_KEY[1] + (WHITE_PRESS[1] - WHITE_KEY[1]) * g),
            int(WHITE_KEY[2] * (1 - g * 0.5)),
        ) if g > 0.05 else WHITE_KEY

        pygame.draw.rect(surf, SHADOW,   (x + 3, KEY_Y + 3, w, h), border_radius=4)
        pygame.draw.rect(surf, col,      (x, KEY_Y, w, h), border_radius=5)
        pygame.draw.rect(surf, RED_FELT, (x, KEY_Y, w, 7), border_radius=3)
        pygame.draw.rect(surf, GOLD if any_p else (145, 125, 95),
                         (x, KEY_Y, w, h), 1, border_radius=5)
        if st == 12:
            pygame.draw.line(surf, GOLD_DIM, (x - 1, KEY_Y), (x - 1, KEY_Y + h), 2)
        if mk:
            dn = F_NOTE.render(disp_name(st), True, TEXT_DIM if not any_p else WOOD_DARK)
            kl = F_LABEL.render(key_label(mk), True, BLACK_KEY if not any_p else WOOD_DARK)
            surf.blit(kl, (x + w // 2 - kl.get_width() // 2, KEY_Y + h - 42))
            surf.blit(dn, (x + w // 2 - dn.get_width() // 2, KEY_Y + h - 26))

    for st in BLACK_STS:
        x = black_x.get(st)
        if x is None:
            continue
        w, h = BKW, BKH
        mk   = st_disp.get(st)
        all_mk = [k for k, s in key_to_st.items() if s == st]
        any_p  = any(k in pressed_keys for k in all_mk)
        glow   = max((key_glow.get(k, 0) for k in all_mk), default=0)
        g      = max(glow, 1.0 if any_p else 0.0)

        col = (
            int(BLACK_KEY[0] + (BLACK_PRESS[0] - BLACK_KEY[0]) * g),
            int(BLACK_KEY[1] + (BLACK_PRESS[1] - BLACK_KEY[1]) * g),
            int(BLACK_KEY[2] + (BLACK_PRESS[2] - BLACK_KEY[2]) * g),
        ) if g > 0.05 else BLACK_KEY

        pygame.draw.rect(surf, SHADOW, (x + 2, KEY_Y + 2, w, h), border_radius=3)
        pygame.draw.rect(surf, col,    (x, KEY_Y, w, h), border_radius=4)
        pygame.draw.rect(surf, (52, 35, 10) if not any_p else (170, 120, 16),
                         (x + 2, KEY_Y + 2, w - 4, 9), border_radius=3)
        pygame.draw.rect(surf, GOLD if any_p else GOLD_DIM,
                         (x, KEY_Y, w, h), 1, border_radius=4)
        if mk:
            dn = F_SMALL.render(disp_name(st), True, TEXT_DIM if not any_p else GOLD)
            kl = F_SMALL.render(key_label(mk), True, TEXT_LIGHT if not any_p else GOLD_BRIGHT)
            surf.blit(kl, (x + w // 2 - kl.get_width() // 2, KEY_Y + h - 32))
            surf.blit(dn, (x + w // 2 - dn.get_width() // 2, KEY_Y + h - 20))

def draw_legend(surf, y):
    rows = [
        ("Whites:",  "Q  W  E  R  T  Y  U  |  I  O  P  [  ]  \\"),
        ("Blacks:",  "2  3  .  5  6  7  .  |  9  0  .  -  =  1  (8=B4  `=C5)"),
        ("Alt oct1:","A  S  D  F  G  H  J"),
    ]
    for i, (lbl, val) in enumerate(rows):
        surf.blit(F_SMALL.render(lbl, True, GOLD_DIM), (MARGIN,       y + i * 14))
        surf.blit(F_SMALL.render(val, True, TEXT_DIM), (MARGIN + 80,  y + i * 14))

def draw_ui(surf):
    surf.fill(BG)
    draw_wood(surf, (0, 0, WIN_W, 212))

    title = F_TITLE.render("HARMONIUM", True, GOLD)
    surf.blit(title, (WIN_W // 2 - title.get_width() // 2, 6))
    pygame.draw.line(surf, GOLD_DIM, (WIN_W // 2 - 190, 50), (WIN_W // 2 + 190, 50), 1)

    tab_lbl = F_SMALL.render("TAB to cycle tone", True, TEXT_DIM)
    surf.blit(tab_lbl, (WIN_W // 2 - tab_lbl.get_width() // 2, 54))
    draw_tone_btns(surf, WIN_W // 2, 66)
    draw_oct_selector(surf, WIN_W // 2, 122)

    bar_lbl = F_SMALL.render("AIR PRESSURE  (~25s buffer)", True, TEXT_DIM)
    surf.blit(bar_lbl, (WIN_W // 2 - bar_lbl.get_width() // 2, 160))
    draw_pressure_bar(surf, WIN_W // 2 - 145, 173, 290, 16, bellows_pressure)

    sc = BELLOWS_COL if bellows_pumping else (36, 28, 9)
    pygame.draw.rect(surf, sc, (WIN_W // 2 - 58, 193, 116, 15), border_radius=3)
    sl = F_SMALL.render("[ SPACE ] pump bellows", True,
                         WOOD_DARK if bellows_pumping else TEXT_DIM)
    surf.blit(sl, (WIN_W // 2 - sl.get_width() // 2, 196))

    bwp = 48
    draw_bellows_side(surf, 5,             KEY_Y, bwp, WKH, bellows_pressure, bellows_anim)
    draw_bellows_side(surf, WIN_W - 5 - bwp, KEY_Y, bwp, WKH, bellows_pressure, bellows_anim)

    pygame.draw.rect(surf, WOOD_MID,
                     (MARGIN - 8, KEY_Y - 10, WIN_W - MARGIN * 2 + 16, WKH + 18),
                     border_radius=6)
    pygame.draw.rect(surf, GOLD_DIM,
                     (MARGIN - 8, KEY_Y - 10, WIN_W - MARGIN * 2 + 16, WKH + 18),
                     2, border_radius=6)

    by2 = KEY_Y + WKH + 12
    draw_wood(surf, (0, by2, WIN_W, WIN_H - by2))
    draw_keyboard(surf)
    draw_legend(surf, by2 + 6)

    with key_lock:
        seen = set()
        playing = []
        for k, s in key_to_st.items():
            if k in pressed_keys and s not in seen:
                playing.append(disp_name(s))
                seen.add(s)

    sy = by2 + 50
    if playing:
        status, col = "♪  " + "  +  ".join(playing), GOLD
    elif bellows_pressure < 0.07:
        status, col = "Bellows empty — hold SPACE to pump", BELLOWS_COL
    else:
        status, col = "Play freely — hold SPACE to refill when needed", TEXT_DIM

    ss = F_HINT.render(status, True, col)
    surf.blit(ss, (WIN_W // 2 - ss.get_width() // 2, sy))
    esc = F_SMALL.render("ESC to quit", True, (50, 40, 26))
    surf.blit(esc, (WIN_W - esc.get_width() - 8, WIN_H - 14))

# ── Main loop ─────────────────────────────────────────────────────────────────
running = True
while running:
    dt_ms = clock.tick(60)
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

    for kc in list(key_glow):
        key_glow[kc] = 1.0 if kc in pressed_keys else max(0.0, key_glow[kc] - 0.055)

    update_bellows(dt_ms)
    draw_ui(screen)
    pygame.display.flip()

listener.stop()
pygame.quit()
sys.exit()