"""OpenSquad NexusChat Pro 应用图标生成脚本

生成三个平台所需的图标文件：
  - icon.png  (Linux + 通用)
  - icon.ico  (Windows, 含多尺寸)
  - icon.icns (macOS)

用法：python scripts/generate_icons.py

也可作为模块导入调用 generate_all()。
"""

import os
import io
import struct
import sys
from PIL import Image, ImageDraw

# ── 路径 ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
ASSETS_DIR = os.path.join(
    PROJECT_ROOT, "src", "opensquad", "gateway", "nexuschat-pro", "assets"
)

# ── 调色板 ───────────────────────────────────────────────────────────────────
BG_TOP    = (30, 27, 75)      # #1e1b4b  深紫蓝（顶）
BG_BOTTOM = (15, 23, 42)      # #0f172a  深蓝黑（底）
ACCENT_1  = (129, 140, 248)   # #818cf8  淡紫
ACCENT_2  = (52, 211, 153)    # #34d399  翠绿
ACCENT_3  = (56, 189, 248)    # #38bdf8  天蓝
WHITE     = (255, 255, 255)
DIM       = (148, 163, 184)   # #94a3b8  灰


def draw_icon(size: int) -> Image.Image:
    """绘制 512x512 底稿，然后缩放到目标 size。"""
    BASE = 512
    img = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── 1. 圆角矩形背景 ──────────────────────────────────────────────────
    corner_r = BASE // 10  # ~51px
    _rounded_rect(draw, (0, 0, BASE, BASE), corner_r, BG_TOP)

    # 底部微渐变：从下往上画一个半透明深色渐变条
    for i in range(BASE // 2):
        alpha = int(40 * (1 - i / (BASE // 2)))
        draw.rectangle(
            [0, BASE - i, BASE, BASE - i + 1],
            fill=(BG_BOTTOM[0], BG_BOTTOM[1], BG_BOTTOM[2], alpha),
        )

    # ── 2. 主图形：三个相互连接的圆（代表多 Agent）＋ 底部聊天气泡 ──────
    cx, cy = BASE // 2, BASE // 2 - 20  # 整体居中偏上

    # 三个 Agent 节点的位置（等边三角形布局）
    r1 = 64   # 节点圆半径
    r2 = 52   # 节点内部高亮圆半径
    dist = 92 # 节点间距

    nodes = [
        (cx, cy - dist),                          # 上
        (cx - dist * 0.866, cy + dist * 0.5),     # 左下
        (cx + dist * 0.866, cy + dist * 0.5),     # 右下
    ]
    node_colors = [ACCENT_1, ACCENT_2, ACCENT_3]

    # 节点间连线（先画线再画圆，保证圆在顶层）
    for i in range(3):
        for j in range(i + 1, 3):
            _line_gradient(draw, nodes[i], nodes[j], node_colors[i], node_colors[j], width=5)

    # 画节点圆
    for (nx, ny), color in zip(nodes, node_colors):
        # 外圈光晕
        glow_r = r1 + 8
        for g in range(6, 0, -1):
            alpha = 20 // g
            _circle(draw, (nx, ny), glow_r - g * 2, (*color[:3], alpha))
        # 外圈
        _circle(draw, (nx, ny), r1, (255, 255, 255, 40))
        _circle(draw, (nx, ny), r1 - 3, (*color, 220))
        # 内圈高光
        _circle(draw, (nx, ny), r2, (*[_ + 40 for _ in color[:3]], 200))
        _circle(draw, (nx, ny), r2 // 2, (255, 255, 255, 80))

    # ── 3. 底部聊天气泡 ──────────────────────────────────────────────────
    bubble_cx = cx
    bubble_cy = cy + dist * 0.5 + r1 + 40
    bubble_w = 240
    bubble_h = 80

    # 气泡主体（圆角矩形）
    bb = (
        bubble_cx - bubble_w // 2,
        bubble_cy - bubble_h // 2,
        bubble_cx + bubble_w // 2,
        bubble_cy + bubble_h // 2,
    )
    _rounded_rect(draw, bb, 20, (56, 189, 248, 180))  # 半透明天蓝

    # 气泡小三角（指向右上节点方向）
    triangle_center = (bubble_cx + 60, bubble_cy - bubble_h // 2)
    draw.polygon(
        [
            (bubble_cx + 80, bubble_cy - bubble_h // 2 + 2),
            (triangle_center[0] + 20, triangle_center[1] - 18),
            (bubble_cx + 50, bubble_cy - bubble_h // 2 + 2),
        ],
        fill=(56, 189, 248, 180),
    )

    # 气泡内的三条文字线（模拟对话内容）
    for i, w in enumerate([140, 180, 80]):
        line_y = bubble_cy - 18 + i * 22
        line_x = bubble_cx - 80
        draw.rounded_rectangle(
            [line_x, line_y, line_x + w, line_y + 14],
            radius=7,
            fill=(255, 255, 255, 50),
        )

    # ── 4. 小装饰：散落的连接点 ──────────────────────────────────────────
    dots = [
        (cx - 180, cy - 80, 4, ACCENT_3),
        (cx + 170, cy - 40, 3, ACCENT_2),
        (cx - 140, cy + 120, 3, ACCENT_1),
        (cx + 160, cy + 100, 4, ACCENT_3),
    ]
    for dx, dy, dr, dcolor in dots:
        _circle(draw, (dx, dy), dr, (*dcolor, 120))
        _circle(draw, (dx, dy), dr // 2, (255, 255, 255, 100))

    # ── 缩放 ─────────────────────────────────────────────────────────────
    if size != BASE:
        img = img.resize((size, size), Image.LANCZOS)
    return img


# ── 辅助绘图函数 ─────────────────────────────────────────────────────────────


def _rounded_rect(draw, bbox, radius, fill):
    draw.rounded_rectangle(bbox, radius=radius, fill=fill)


def _circle(draw, center, r, fill):
    draw.ellipse(
        [center[0] - r, center[1] - r, center[0] + r, center[1] + r],
        fill=fill,
    )


def _line_gradient(draw, p1, p2, c1, c2, width=4):
    """画一条渐变色线段（通过分段实现）。"""
    steps = 30
    for i in range(steps):
        t = i / steps
        t_next = (i + 1) / steps
        x1 = p1[0] + (p2[0] - p1[0]) * t
        y1 = p1[1] + (p2[1] - p1[1]) * t
        x2 = p1[0] + (p2[0] - p1[0]) * t_next
        y2 = p1[1] + (p2[1] - p1[1]) * t_next
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        draw.line([(x1, y1), (x2, y2)], fill=(r, g, b, 200), width=width)


# ── 文件输出 ─────────────────────────────────────────────────────────────────


def generate_png(size=512, output=None):
    """生成 PNG 图标。"""
    if output is None:
        output = os.path.join(ASSETS_DIR, "icon.png")
    img = draw_icon(size)
    img.save(output, "PNG")
    print(f"[OK] PNG  512x512 → {output}")
    return output


def generate_ico(output=None):
    """生成 Windows ICO 图标（含 16/32/48/256 四个尺寸）。

    Pillow 在 Windows 上的 ICO 插件有限制，这里手动拼装 ICO 文件格式：
      1. 以 PNG 格式保存每个尺寸的图像数据
      2. 拼写标准的 ICO 目录头 + 目录项 + PNG 数据块
    """
    if output is None:
        output = os.path.join(ASSETS_DIR, "icon.ico")
    sizes = [16, 32, 48, 256]

    # 先把每个尺寸渲染为 PNG 字节
    png_data = {}
    for s in sizes:
        img = draw_icon(s)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_data[s] = buf.getvalue()

    # ICO 格式拼装
    num_images = len(sizes)
    header = struct.pack("<HHH", 0, 1, num_images)  # reserved=0, type=1(ico), count

    # 每个目录项 16 字节
    entries = b""
    # 数据起始偏移：header(6) + 每个目录项(16) × N
    data_offset = 6 + num_images * 16
    # 按原始尺寸降序排列让系统选更高品质的资源
    sorted_sizes = sorted(sizes, reverse=True)

    for s in sorted_sizes:
        data = png_data[s]
        # 目录项：width, height, palette, reserved, planes, bpp, size, offset
        w = 0 if s == 256 else s  # 0 表示 256
        h = 0 if s == 256 else s
        entries += struct.pack(
            "<BBBBHHII",
            w, h,          # 宽度、高度（0=256）
            0,             # 调色板大小
            0,             # 保留
            1,             # 颜色平面数
            32,            # 每像素位数
            len(data),     # 图像数据大小
            data_offset,   # 数据偏移
        )
        data_offset += len(data)

    # 拼接所有 PNG 数据（按与目录项相同的顺序）
    payload = b""
    for s in sorted_sizes:
        payload += png_data[s]

    with open(output, "wb") as f:
        f.write(header + entries + payload)

    print(f"[OK] ICO  {sorted_sizes} → {output}  ({num_images} frames, {os.path.getsize(output)} bytes)")
    return output


def generate_icns(output=None):
    """生成 macOS ICNS 图标。

    Pillow 在 Windows 上无法直接写 .icns。这里采用变通方案：
    生成 1024x1024 PNG 作为源文件，并尝试用内置 icns 编码器。
    如果失败则留下 PNG 源文件，打印手动转换提示。
    """
    if output is None:
        output = os.path.join(ASSETS_DIR, "icon.icns")
    png_path = output.replace(".icns", "_temp_1024.png")

    # ICNS 的 "ic10" 格式需要 1024x1024
    img = draw_icon(1024)
    img.save(png_path, "PNG")
    print(f"[OK] PNG  1024x1024 → {png_path}（ICNS 源文件）")

    # 尝试用 Pillow 直接写 ICNS（Windows 上也可能支持，取决于 Pillow 版本）
    try:
        img.save(output, format="ICNS")
        print(f"[OK] ICNS 1024x1024 → {output}")
    except Exception as e:
        print(f"[..] ICNS 无法直接生成 ({e})")
        print(f"     请在有 macOS + Pillow icns 支持的环境下运行：")
        print(f"       python scripts/generate_icons.py --icns-only")
        print(f"     临时方案：使用 icon.png（512x512）作为备选")
    finally:
        if os.path.exists(png_path):
            os.remove(png_path)


def generate_tray(size=22, output=None):
    """生成系统托盘小图标（纯色简洁版）。"""
    if output is None:
        output = os.path.join(ASSETS_DIR, "tray.png")
    BASE = size * 8  # 先画大图再缩放
    img = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx = cy = BASE // 2
    r = BASE * 0.35

    # 三个小圆点代表 agent
    pts = [
        (cx, cy - int(r * 1.1)),
        (cx - int(r * 0.95), cy + int(r * 0.55)),
        (cx + int(r * 0.95), cy + int(r * 0.55)),
    ]
    colors = [ACCENT_1, ACCENT_2, ACCENT_3]

    for i in range(3):
        for j in range(i + 1, 3):
            draw.line([pts[i], pts[j]], fill=DIM + (180,), width=BASE // 16)

    dot_r = BASE // 10
    for (px, py), color in zip(pts, colors):
        _circle(draw, (px, py), dot_r, (*color, 220))
        _circle(draw, (px, py), dot_r // 2, (255, 255, 255, 100))

    if size != BASE:
        img = img.resize((size, size), Image.LANCZOS)
    img.save(output, "PNG")
    print(f"[OK] Tray {size}x{size} → {output}")
    return output


# ── 主入口 ───────────────────────────────────────────────────────────────────


def generate_all():
    """生成全部图标文件。"""
    os.makedirs(ASSETS_DIR, exist_ok=True)
    generate_png()
    generate_ico()
    generate_icns()
    generate_tray()
    print("\n✅ 所有图标已生成到:", ASSETS_DIR)


if __name__ == "__main__":
    if "--icns-only" in sys.argv:
        generate_icns()
    elif "--tray-only" in sys.argv:
        generate_tray()
    elif "--png-only" in sys.argv:
        generate_png()
    elif "--ico-only" in sys.argv:
        generate_ico()
    else:
        generate_all()
