"""
Generate high-fidelity app icons for Session Scribe:
- assets/icons/session_scribe_logo.svg
- assets/icons/session_scribe_logo_1024.png
- assets/icons/session_scribe.ico (multi-resolution: 256, 128, 64, 48, 32, 24, 16)
"""

import math
import os
import sys
from PIL import Image, ImageDraw, ImageFilter

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ICONS_DIR = os.path.join(REPO_ROOT, "assets", "icons")
os.makedirs(ICONS_DIR, exist_ok=True)

SVG_PATH = os.path.join(ICONS_DIR, "session_scribe_logo.svg")
PNG_1024_PATH = os.path.join(ICONS_DIR, "session_scribe_logo_1024.png")
ICO_PATH = os.path.join(ICONS_DIR, "session_scribe.ico")

SVG_CONTENT = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
  <defs>
    <!-- Background Gradient -->
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0F172A" />
      <stop offset="50%" stop-color="#1E293B" />
      <stop offset="100%" stop-color="#0B132B" />
    </linearGradient>
    
    <!-- Accent Gradient for Wave & Quill -->
    <linearGradient id="accentGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38BDF8" />
      <stop offset="50%" stop-color="#60A5FA" />
      <stop offset="100%" stop-color="#818CF8" />
    </linearGradient>

    <!-- Gold Accent for Scribe Nib -->
    <linearGradient id="goldGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#FCD34D" />
      <stop offset="50%" stop-color="#F59E0B" />
      <stop offset="100%" stop-color="#D97706" />
    </linearGradient>

    <!-- Subtle Drop Shadow -->
    <filter id="dropShadow" x="-10%" y="-10%" width="130%" height="130%">
      <feDropShadow dx="0" dy="16" stdDeviation="24" flood-color="#000000" flood-opacity="0.45" />
    </filter>
  </defs>

  <!-- App Squircle Base -->
  <rect x="64" y="64" width="896" height="896" rx="200" fill="url(#bgGrad)" filter="url(#dropShadow)" stroke="#334155" stroke-width="6" />

  <!-- Inner Soft Border Ring -->
  <rect x="76" y="76" width="872" height="872" rx="188" fill="none" stroke="#475569" stroke-width="2" stroke-opacity="0.5" />

  <!-- Soundwave Bars (Left) -->
  <g fill="url(#accentGrad)" opacity="0.95">
    <!-- Bar 1 -->
    <rect x="230" y="440" width="36" height="144" rx="18" />
    <!-- Bar 2 -->
    <rect x="294" y="360" width="36" height="304" rx="18" />
    <!-- Bar 3 -->
    <rect x="358" y="280" width="36" height="464" rx="18" />
    <!-- Bar 4 -->
    <rect x="422" y="380" width="36" height="264" rx="18" />
  </g>

  <!-- Scribe Quill / Pen Emblem (Center & Right) -->
  <g transform="translate(480, 240)">
    <!-- Feather / Scribe Body -->
    <path d="M 120 0 C 180 60, 240 160, 240 280 C 240 400, 160 520, 60 600 L 0 600 C 60 500, 100 400, 100 280 C 100 160, 60 80, 0 20 Z" fill="url(#accentGrad)" opacity="0.9" />
    
    <!-- Central Nib Shaft -->
    <path d="M 60 220 L 60 580 L 10 580 L 10 260 Z" fill="#E2E8F0" opacity="0.85" />
    
    <!-- Gold Nib Tip -->
    <path d="M 10 580 L 60 580 L 35 660 Z" fill="url(#goldGrad)" />
    <!-- Nib Slit -->
    <line x1="35" y1="580" x2="35" y2="640" stroke="#78350F" stroke-width="3" />
    <!-- Breather Hole -->
    <circle cx="35" cy="580" r="5" fill="#78350F" />
  </g>

  <!-- Glowing Sound Arc (Right) -->
  <path d="M 780 380 A 180 180 0 0 1 780 644" fill="none" stroke="url(#accentGrad)" stroke-width="28" stroke-linecap="round" opacity="0.8" />
  <path d="M 850 320 A 260 260 0 0 1 850 704" fill="none" stroke="url(#accentGrad)" stroke-width="20" stroke-linecap="round" opacity="0.5" />
</svg>
"""


def generate_png_and_ico():
    with open(SVG_PATH, "w", encoding="utf-8") as f:
        f.write(SVG_CONTENT)
    print(f"Wrote SVG to: {SVG_PATH}")

    # Render high-resolution PIL image (1024x1024)
    size = 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Outer squircle background with gradient
    radius = 200
    margin = 64
    x0, y0 = margin, margin
    x1, y1 = size - margin, size - margin

    # Draw gradient background squircle
    for y in range(y0, y1):
        ratio = (y - y0) / (y1 - y0)
        # Gradient: #0F172A (15, 23, 42) -> #1E293B (30, 41, 59) -> #0B132B (11, 19, 43)
        if ratio < 0.5:
            r = int(15 + (30 - 15) * (ratio * 2))
            g = int(23 + (41 - 23) * (ratio * 2))
            b = int(42 + (59 - 42) * (ratio * 2))
        else:
            r = int(30 + (11 - 30) * ((ratio - 0.5) * 2))
            g = int(41 + (19 - 41) * ((ratio - 0.5) * 2))
            b = int(59 + (43 - 59) * ((ratio - 0.5) * 2))
        draw.line([(x0, y), (x1, y)], fill=(r, g, b, 255))

    # Mask to rounded rectangle
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=255)

    # Combine with mask
    bg_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg_img.paste(img, (0, 0), mask)

    draw = ImageDraw.Draw(bg_img)

    # Draw subtle inner border ring
    draw.rounded_rectangle([x0 + 4, y0 + 4, x1 - 4, y1 - 4], radius=radius - 4, outline=(71, 85, 105, 140), width=4)

    # Draw Soundwave Bars
    bars = [
        (230, 440, 36, 144, (56, 189, 248)),
        (294, 360, 36, 304, (74, 163, 251)),
        (358, 280, 36, 464, (96, 165, 250)),
        (422, 380, 36, 264, (129, 140, 248)),
    ]
    for bx, by, bw, bh, col in bars:
        draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=bw // 2, fill=(*col, 240))

    # Draw Quill / Scribe emblem
    # Feather curve
    feather_points = [
        (600, 240),
        (680, 320),
        (720, 440),
        (700, 560),
        (620, 660),
        (540, 840),
        (510, 840),
        (560, 720),
        (590, 600),
        (580, 460),
        (540, 340),
        (500, 260),
    ]
    draw.polygon(feather_points, fill=(56, 189, 248, 230))

    # Scribe Shaft
    draw.polygon([(520, 460), (555, 460), (535, 800), (505, 800)], fill=(226, 232, 240, 220))

    # Gold Nib tip
    draw.polygon([(505, 800), (535, 800), (520, 860)], fill=(245, 158, 11, 255))
    draw.line([(520, 800), (520, 850)], fill=(120, 53, 15, 255), width=3)
    draw.ellipse([517, 797, 523, 803], fill=(120, 53, 15, 255))

    # Sound Arcs (right)
    draw.arc([600, 350, 880, 630], start=-60, end=60, fill=(96, 165, 250, 200), width=24)
    draw.arc([560, 290, 960, 690], start=-55, end=55, fill=(129, 140, 248, 140), width=16)

    # Save 1024x1024 PNG
    bg_img.save(PNG_1024_PATH, "PNG")
    print(f"Wrote PNG 1024 to: {PNG_1024_PATH}")

    # Save multi-size ICO: 256, 128, 64, 48, 32, 24, 16
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    bg_img.save(ICO_PATH, format="ICO", sizes=sizes)
    print(f"Wrote Multi-Resolution ICO to: {ICO_PATH}")


if __name__ == "__main__":
    generate_png_and_ico()

