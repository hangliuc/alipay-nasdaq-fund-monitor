"""
卡片图生成
生成适合小红书发布的竖版基金限购信息卡片（含近1年收益率）
"""

import re
import os
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# ── 颜色 ──────────────────────────────────────────
BG       = "#F5F7FA"
CARD     = "#FFFFFF"
TITLE_BG = "#1A1A2E"
TITLE_FG = "#FFFFFF"
SUB_FG   = "#8892A3"
GREEN    = "#10B981"
RED      = "#EF4444"
TEXT     = "#334155"
MUTED    = "#94A3B8"
DIVIDER  = "#E2E8F0"
LABEL    = "#64748B"

# ── 字体 ──────────────────────────────────────────
_FONT_PATHS = [
    # Docker（与 macOS 相同的 STHeiti Medium）
    "/usr/share/fonts/custom/STHeiti-Medium.ttc",
    # macOS
    "/System/Library/Fonts/STHeiti Medium.ttc",
]

_font_cache = {}


def _font(size: int) -> ImageFont.FreeTypeFont:
    if size in _font_cache:
        return _font_cache[size]
    for p in _FONT_PATHS:
        if os.path.exists(p):
            f = ImageFont.truetype(p, size)
            _font_cache[size] = f
            return f
    return ImageFont.load_default()


# ── 工具函数 ──────────────────────────────────────

def _limit_val(s: str) -> float:
    """解析限额为数值（单位：元），用于排序"""
    if "不限" in (s or ""):
        return float("inf")
    if "暂停" in (s or ""):
        return -1
    m = re.search(r"([\d,.]+)\s*万", s or "")
    if m:
        return float(m.group(1).replace(",", "")) * 10000
    m = re.search(r"([\d,.]+)", s or "")
    return float(m.group(1).replace(",", "")) if m else -2


def _return_val(s: str) -> float:
    """解析近一年收益率，用于主动/被动卡片统一排序。"""
    m = re.search(r"-?[\d,.]+", s or "")
    return float(m.group(0).replace(",", "")) if m else float("-inf")


def _format_return_ui(s: str) -> str:
    """收益率统一为 4 位数字主体（如 86.00%、08.69%）。"""
    m = re.search(r"-?[\d,.]+", s or "")
    if not m:
        return "—"
    value = float(m.group(0).replace(",", ""))
    formatted = f"{value:.2f}"
    if 0 <= value < 10:
        formatted = "0" + formatted
    elif -10 < value < 0:
        formatted = "-0" + formatted[1:]
    return formatted + "%"


def _fmt_limit(s: str) -> str:
    """格式化限额显示（卡片中只展示数字，省略单位"元"）"""
    m = re.search(r"([\d,.]+)\s*万元", s or "")
    if m:
        v = float(m.group(1).replace(",", ""))
        return f"{int(v)}万" if v == int(v) else f"{v}万"
    m = re.search(r"([\d,.]+)\s*元", s or "")
    if m:
        v = float(m.group(1).replace(",", ""))
        return f"{int(v)}" if v == int(v) else f"{v}"
    if "不限" in (s or ""):
        return "不限"
    if "暂停" in (s or ""):
        return "暂停"
    return s or "—"


def _is_suspended(r: dict) -> bool:
    return "暂停" in r.get("purchase_status", "")


def _rounded_rect(draw, xy, r, fill):
    x0, y0, x1, y1 = xy
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
    for cx, cy, s, e in [
        (x0, y0, 180, 270), (x1 - 2*r, y0, 270, 360),
        (x0, y1 - 2*r, 90, 180), (x1 - 2*r, y1 - 2*r, 0, 90),
    ]:
        draw.pieslice([cx, cy, cx + 2*r, cy + 2*r], s, e, fill=fill)


def _center_text(d, w, y, text, font, fill, absolute_center=None):
    bb = d.textbbox((0, 0), text, font=font)
    tw = bb[2] - bb[0]
    x = (absolute_center - tw // 2) if absolute_center else ((w - tw) // 2)
    d.text((x, y), text, fill=fill, font=font)


def _right_text(d, right_x, y, text, font, fill):
    bb = d.textbbox((0, 0), text, font=font)
    d.text((right_x - (bb[2] - bb[0]), y), text, fill=fill, font=font)


def _ellipsize(d, text: str, font, max_width: int) -> str:
    """将名称收束在数值列之前，基金代码始终保持同一行。"""
    if d.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    suffix = "…"
    while text and d.textbbox((0, 0), text + suffix, font=font)[2] > max_width:
        text = text[:-1]
    return text + suffix


def _add_watermark(img: Image.Image, text: str = "HRuning") -> Image.Image:
    """
    在图片中部偏上位置叠加一条斜向半透明防伪水印。
    选择中部位置（约 45% 高度）是为了避免被顶/底截图剪裁掉，
    同时透明度较低，不会干扰主体内容阅读。
    """
    W, H = img.size
    f = _font(80)

    # 在独立画布上渲染文字，然后旋转
    tmp = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    bb = ImageDraw.Draw(tmp).textbbox((0, 0), text, font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]

    wm = Image.new("RGBA", (tw + 60, th + 60), (0, 0, 0, 0))
    wd = ImageDraw.Draw(wm)
    # 深灰 + 低透明度，叠加在白色卡片或浅灰背景上都清晰可辨
    wd.text((30, 30), text, font=f, fill=(30, 30, 50, 56))
    wm = wm.rotate(-22, expand=True, resample=Image.BICUBIC)

    # 透明图层用于合成
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    px = (W - wm.width) // 2
    py = int(H * 0.45) - wm.height // 2  # 垂直 45% 处，居中略偏上
    layer.paste(wm, (px, py), wm)

    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


# ── 主函数 ────────────────────────────────────────

def generate(results: list[dict], output_path: str,
             title: str = "纳斯达克 QDII 基金限购日报",
             subtitle_prefix: str = "C类份额") -> str:
    """生成卡片图并保存，返回 output_path。"""

    # 分组 & 排序（开放按限额从大到小，暂停同理）
    opened, suspended = [], []
    for r in results:
        if r.get("error"):
            continue
        (suspended if _is_suspended(r) else opened).append(r)

    sort_key = lambda r: (-_limit_val(r.get("purchase_limit", "")), r.get("name", ""))
    opened.sort(key=sort_key)
    suspended.sort(key=sort_key)

    today = datetime.now().strftime("%Y-%m-%d")

    # ── 布局 ──
    W, PAD, CPAD = 1080, 60, 40
    ROW_H, GAP, HDR_H, CR, COL_HDR_H = 80, 36, 200, 24, 52
    IL, IR = PAD + CPAD, W - PAD - CPAD
    COL_RET = 700

    open_h = COL_HDR_H + len(opened) * ROW_H + 24
    susp_h = COL_HDR_H + len(suspended) * ROW_H + 24
    H = PAD + HDR_H + 24 + 88 + 24 + open_h + GAP + susp_h + PAD

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # ── 字体 ──
    ft  = _font(48)   # 标题
    fs  = _font(28)   # 副标题
    fsc = _font(32)   # section 标题（可申购/暂停申购）
    fch = _font(24)   # 列标题（近1年/限额）
    fn  = _font(30)   # 基金名称
    fc  = _font(24)   # 基金代码
    fl  = _font(36)   # 限额数字
    fr  = _font(30)   # 收益率数字
    fsn = _font(44)   # 统计栏数字
    fsl = _font(24)   # 统计栏标签

    y = PAD

    # ── 标题 ──
    _rounded_rect(d, (PAD, y, W - PAD, y + HDR_H), CR, TITLE_BG)
    _center_text(d, W, y + 45, title, ft, TITLE_FG)
    _center_text(d, W, y + 120, f"{subtitle_prefix} · {today}", fs, SUB_FG)
    y += HDR_H + 24

    # ── 统计栏 ──
    _rounded_rect(d, (PAD, y, W - PAD, y + 88), 16, CARD)
    sw = (W - PAD * 2) // 3
    for i, (n, lb, co) in enumerate([
        (str(len(opened) + len(suspended)), "只基金", TEXT),
        (str(len(opened)), "可申购", GREEN),
        (str(len(suspended)), "暂停中", RED),
    ]):
        cx = PAD + sw * i + sw // 2
        _center_text(d, cx * 2, y + 8, n, fsn, co, absolute_center=cx)
        _center_text(d, cx * 2, y + 56, lb, fsl, SUB_FG, absolute_center=cx)
    y += 112

    # ── 列表绘制 ──
    def draw_section(y, funds, label, color, is_open):
        h = COL_HDR_H + len(funds) * ROW_H + 24
        _rounded_rect(d, (PAD, y, W - PAD, y + h), CR, CARD)
        d.text((IL, y + 12), label, fill=color, font=fsc)
        _right_text(d, IR, y + 16, "限额", fch, LABEL)
        bb = d.textbbox((0, 0), "近1年", font=fch)
        d.text((COL_RET - (bb[2] - bb[0]) // 2, y + 16), "近1年", fill=LABEL, font=fch)

        ry = y + COL_HDR_H
        for i, r in enumerate(funds):
            if i > 0:
                d.line([(IL, ry), (IR, ry)], fill=DIVIDER, width=1)
            draw_row(d, ry, r, is_open)
            ry += ROW_H
        return y + h

    def draw_row(d, ry, r, is_open):
        nm = r.get("display") or r.get("name", "")
        code = r.get("code", "")
        lim = _fmt_limit(r.get("purchase_limit", ""))
        ret = r.get("return_1y", "—") or "—"

        # 名称 + 代码
        d.text((IL, ry + 18), nm, fill=TEXT, font=fn)
        nb = d.textbbox((0, 0), nm, font=fn)
        d.text((IL + nb[2] - nb[0] + 12, ry + 24), code, fill=MUTED, font=fc)

        # 收益率
        rc = RED if ret != "—" and not ret.startswith("-") else (GREEN if ret.startswith("-") else MUTED)
        rb = d.textbbox((0, 0), ret, font=fr)
        d.text((COL_RET - (rb[2] - rb[0]) // 2, ry + 18), ret, fill=rc, font=fr)

        # 限额
        _right_text(d, IR, ry + 16, lim, fl, GREEN if is_open else RED)

    y = draw_section(y, opened, "可申购", GREEN, True)
    y += GAP
    draw_section(y, suspended, "暂停申购", RED, False)

    # ── 防伪水印 ──
    img = _add_watermark(img)

    # ── 保存 ──
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path, "PNG", quality=95)
    print(f"📸 卡片已生成: {output_path}")
    return output_path

