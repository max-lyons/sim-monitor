#!/usr/bin/env python3
"""Generate the SimMonitor.app icon and install it.

Requires: pip install Pillow
Usage: python generate_icon.py
"""

import os
import subprocess
import tempfile

from PIL import Image, ImageDraw


def draw_icon(size):
    """Draw a chart/graph icon at the given size."""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = size * 0.1
    s = size

    # Rounded rect background
    r = size * 0.15
    draw.rounded_rectangle(
        [pad * 0.5, pad * 0.5, s - pad * 0.5, s - pad * 0.5],
        radius=r, fill=(22, 27, 34, 255)
    )

    # Chart area
    left = pad * 2
    right = s - pad * 1.5
    top = pad * 2
    bottom = s - pad * 2.5

    # Grid lines
    grid_color = (48, 54, 61, 180)
    for i in range(4):
        y = top + (bottom - top) * i / 3
        draw.line([(left, y), (right, y)], fill=grid_color, width=max(1, size // 128))

    # Line chart data
    points_blue = [
        (0.0, 0.7), (0.1, 0.5), (0.2, 0.6), (0.3, 0.35),
        (0.4, 0.45), (0.5, 0.3), (0.6, 0.35), (0.7, 0.25),
        (0.8, 0.2), (0.9, 0.22), (1.0, 0.15)
    ]
    points_green = [
        (0.0, 0.9), (0.1, 0.75), (0.2, 0.8), (0.3, 0.55),
        (0.4, 0.6), (0.5, 0.5), (0.6, 0.45), (0.7, 0.5),
        (0.8, 0.4), (0.9, 0.42), (1.0, 0.38)
    ]

    line_width = max(2, size // 64)

    def to_pixels(points):
        return [(left + (right - left) * x, top + (bottom - top) * y) for x, y in points]

    blue_px = to_pixels(points_blue)
    green_px = to_pixels(points_green)

    draw.line(green_px, fill=(63, 185, 80, 255), width=line_width, joint='curve')
    draw.line(blue_px, fill=(88, 166, 255, 255), width=line_width, joint='curve')

    # Dots at endpoints
    dot_r = max(2, size // 80)
    for px in [blue_px[-1], green_px[-1]]:
        draw.ellipse([px[0]-dot_r, px[1]-dot_r, px[0]+dot_r, px[1]+dot_r],
                     fill=(255, 255, 255, 255))

    return img


def main():
    app_dir = os.path.expanduser('~/Applications/SimMonitor.app')
    resources_dir = os.path.join(app_dir, 'Contents', 'Resources')
    os.makedirs(resources_dir, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        iconset_dir = os.path.join(tmpdir, 'SimMonitor.iconset')
        os.makedirs(iconset_dir)

        sizes = {
            'icon_16x16.png': 16,
            'icon_16x16@2x.png': 32,
            'icon_32x32.png': 32,
            'icon_32x32@2x.png': 64,
            'icon_128x128.png': 128,
            'icon_128x128@2x.png': 256,
            'icon_256x256.png': 256,
            'icon_256x256@2x.png': 512,
            'icon_512x512.png': 512,
            'icon_512x512@2x.png': 1024,
        }

        for name, sz in sizes.items():
            path = os.path.join(iconset_dir, name)
            draw_icon(sz).save(path)
            # Re-export with sips for proper color profile
            subprocess.run(['sips', '-s', 'format', 'png', path, '--out', path],
                           capture_output=True)
            print(f'  {name} ({sz}x{sz})')

        icns_path = os.path.join(resources_dir, 'AppIcon.icns')
        subprocess.run(['iconutil', '-c', 'icns', iconset_dir, '-o', icns_path],
                       check=True)
        print(f'Icon installed: {icns_path}')


if __name__ == '__main__':
    main()
