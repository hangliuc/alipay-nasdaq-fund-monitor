#!/usr/bin/env python3
"""Generate a new Xiaohongshu card series from config.json fund definitions.

The cards deliberately exclude funds without a currently obtainable official
direct-sales limit.  Values are fetched fresh: performance and agency limits
come from the existing market sources; direct limits come only from the
official fund-company adapters.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fund_monitor.fetch.direct_sales import fetch_official_limits
from fund_monitor.fetch.sources import eastmoney_jjjz
from fund_monitor.fetch.sources import eastmoney_ranking
from fund_monitor.output.image import generate_direct_sales


CONFIG_PATH = ROOT / "config.json"
BACKDROP_PATH = ROOT / "data/card_assets/qdi-card-backdrop-v1.png"
LUXE_BACKDROP_PATH = ROOT / "data/card_assets/qdi-dark-luxe-gold-backdrop-v1.png"
OUTPUT_DIR = ROOT / "data/cards/xiaohongshu-direct-sales"
EXCLUDED_CODES = {"019442", "018036", "017093", "016702"}
PAGE_SIZE = 11

FONT_SCHEMES = {
    "a": Path("/Users/shareit/Library/Fonts/NotoSerifSC[wght].ttf"),
    "b": Path("/System/Library/AssetsV2/com_apple_MobileAsset_Font7/3419f2a427639ad8c8e139149a287865a90fa17e.asset/AssetData/PingFang.ttc"),
    "c": Path("/System/Library/Fonts/Supplemental/Songti.ttc"),
    "d": Path("/System/Library/Fonts/STHeiti Medium.ttc"),
}
# UI v2 跟随飞书推送：STHeiti 的字面和原卡片保持一致；旧方案函数仍可
# 通过修改该变量做设计试验，但正式输出固定使用该字体。
ACTIVE_FONT_SCHEME = "d"
FEISHU_FONT_PATH = FONT_SCHEMES["b"]
FONT_PATHS = [
    FONT_SCHEMES[ACTIVE_FONT_SCHEME],
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/usr/share/fonts/custom/STHeiti-Medium.ttc"),
]


def font(size: int):
    for path in [FONT_SCHEMES[ACTIVE_FONT_SCHEME], *FONT_PATHS[1:]]:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def feishu_font(size: int):
    """PingFang SC Medium：与 active_20260826 飞书卡片一致的无衬线字面。"""
    if FEISHU_FONT_PATH.exists():
        return ImageFont.truetype(str(FEISHU_FONT_PATH), size, index=7)
    return font(size)


def number_font(size: int):
    path = Path("/Users/shareit/Library/Fonts/DMSerifDisplay-Regular.ttf")
    return ImageFont.truetype(str(path), size) if path.exists() else font(size)


def display_font(size: int):
    path = Path("/Users/shareit/Library/Fonts/CormorantGaramond[wght].ttf")
    return ImageFont.truetype(str(path), size) if path.exists() else number_font(size)


def value_font(text: str, size: int):
    return number_font(size) if re.fullmatch(r"[0-9.,%+-]+", text or "") else font(size)


def value(text: str) -> float:
    text = text or ""
    if "不限" in text:
        return float("inf")
    if "暂停" in text:
        return -1.0
    match = re.search(r"([\d,.]+)\s*万", text)
    if match:
        return float(match.group(1).replace(",", "")) * 10000
    match = re.search(r"([\d,.]+)", text)
    return float(match.group(1).replace(",", "")) if match else -2.0


def compact_limit(text: str) -> str:
    text = (text or "未获取").strip()
    if "暂停" in text:
        return "暂停"
    if "不限" in text:
        return "不限"
    match = re.search(r"([\d,.]+)\s*万(?:元)?", text)
    if match:
        number = float(match.group(1).replace(",", ""))
        return f"{number:g}万"
    match = re.search(r"([\d,.]+)\s*元", text)
    if match:
        number = float(match.group(1).replace(",", ""))
        return f"{number:g}"
    return text


def return_value(text: str) -> float:
    match = re.search(r"-?[\d.]+", text or "")
    return float(match.group()) if match else float("-inf")


def format_return(text: str) -> str:
    """Use two decimal places so every percentage shows four numeric digits."""
    match = re.search(r"(-?[\d.]+)", text or "")
    return f"{float(match.group(1)):.2f}%" if match else "—"


def text_right(draw, x, y, text, fnt, fill):
    box = draw.textbbox((0, 0), text, font=fnt)
    draw.text((x - (box[2] - box[0]), y), text, font=fnt, fill=fill)


def ellipsize(draw, text: str, fnt, max_width: int) -> str:
    """Fit labels within a fixed data column without ever touching the value."""
    if draw.textbbox((0, 0), text, font=fnt)[2] <= max_width:
        return text
    suffix = "…"
    while text and draw.textbbox((0, 0), text + suffix, font=fnt)[2] > max_width:
        text = text[:-1]
    return text + suffix


def rounded(draw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def make_card(rows: list[dict], page: int, pages: int, date_text: str, output: Path):
    width, height = 1080, 1440
    background = Image.open(BACKDROP_PATH).convert("RGB").resize((width, height))
    image = background.copy()
    draw = ImageDraw.Draw(image, "RGBA")

    # A translucent reading surface leaves the AI-created background visible.
    rounded(draw, (44, 324, 1036, 1336), 34, (6, 19, 49, 222), (102, 140, 215, 82), 2)
    draw.text((68, 78), "QDII 额度雷达", font=font(31), fill=(255, 205, 145, 255))
    draw.text((66, 126), "直销 VS 代销", font=font(74), fill=(255, 255, 249, 255))
    draw.text((68, 218), "近一年收益率排名 · 官方直销额度", font=font(31), fill=(200, 215, 244, 255))
    text_right(draw, 1008, 235, date_text, font(25), (185, 203, 236, 255))

    header_y = 362
    draw.text((80, header_y), "排名 / 基金", font=font(26), fill=(153, 183, 233, 255))
    text_right(draw, 620, header_y, "近1年", font(26), (153, 183, 233, 255))
    text_right(draw, 806, header_y, "直销", font(26), (153, 183, 233, 255))
    text_right(draw, 1000, header_y, "代销", font(26), (153, 183, 233, 255))
    draw.line((80, 410, 1000, 410), fill=(121, 154, 213, 105), width=2)

    row_y, row_h = 424, 78
    rank_start = (page - 1) * PAGE_SIZE + 1
    for i, item in enumerate(rows):
        y = row_y + i * row_h
        if i % 2 == 0:
            rounded(draw, (66, y - 8, 1014, y + 62), 13, (104, 139, 201, 20))
        rank = rank_start + i
        rounded(draw, (82, y + 5, 126, y + 49), 12,
                (255, 121, 77, 225) if rank <= 3 else (57, 91, 155, 185))
        rank_label = f"{rank:02d}"
        rank_box = draw.textbbox((0, 0), rank_label, font=font(23))
        draw.text((104 - (rank_box[2] - rank_box[0]) / 2, y + 13), rank_label,
                  font=font(23), fill=(255, 255, 255, 255))
        draw.text((145, y), item["display"], font=font(29), fill=(247, 249, 255, 255))
        draw.text((145, y + 37), item["code"], font=font(20), fill=(145, 174, 225, 255))
        ret = format_return(item["return_1y"])
        ret_color = (255, 137, 96, 255) if not ret.startswith("-") and ret != "—" else (98, 212, 180, 255)
        text_right(draw, 620, y + 10, ret, font(30), ret_color)
        direct = compact_limit(item["direct_sales_limit"])
        agency = compact_limit(item["purchase_limit"])
        text_right(draw, 806, y + 10, direct, font(29), (255, 230, 183, 255))
        text_right(draw, 1000, y + 10, agency, font(29), (196, 221, 255, 255))

    draw.line((80, 1280, 1000, 1280), fill=(121, 154, 213, 105), width=2)
    draw.text((80, 1303), "额度单位：元；“暂停”为当前不可申购", font=font(22), fill=(158, 183, 223, 255))
    text_right(draw, 1000, 1303, f"{page} / {pages}", font(22), (158, 183, 223, 255))
    image.save(output, "PNG", optimize=True)


def make_group_card(rows: list[dict], group: str, date_text: str, output: Path):
    """One 3:4 dark-luxe-gold card for a whole fund group (up to 16 rows)."""
    width, height = 1080, 1440
    background = Image.open(LUXE_BACKDROP_PATH).convert("RGB").resize((width, height))
    image = background.copy()
    draw = ImageDraw.Draw(image, "RGBA")

    # Signature corner brackets from the supplied dark-luxe-gold reference.
    for left, top, sx, sy in ((48, 48, 1, 1), (1032, 48, -1, 1), (48, 1392, 1, -1), (1032, 1392, -1, -1)):
        draw.line((left, top, left + sx * 62, top), fill=(212, 178, 106, 145), width=2)
        draw.line((left, top, left, top + sy * 62), fill=(212, 178, 106, 145), width=2)

    group_en = "PASSIVE INDEX" if group == "被动型" else "ACTIVE QDII"
    draw.text((82, 82), group_en, font=font(22), fill=(212, 178, 106, 255))
    draw.text((80, 120), f"{group}基金", font=font(68), fill=(242, 233, 210, 255))
    draw.text((83, 206), "近一年收益率排名 · 直销 / 代销额度", font=font(27), fill=(202, 212, 235, 255))
    draw.line((80, 270, 1000, 270), fill=(212, 178, 106, 170), width=2)

    # Two controlled list columns keep every configured, supported fund on one card.
    col_x = (72, 550)
    col_w = 458
    for col, start in enumerate((0, 8)):
        x = col_x[col]
        draw.text((x + 12, 302), "排名  基金 / 代码", font=font(19), fill=(212, 178, 106, 220))
        text_right(draw, x + 338, 302, "近1年", font(19), (212, 178, 106, 220))
        text_right(draw, x + col_w - 10, 302, "直销 / 代销", font(19), (212, 178, 106, 220))
        for index, item in enumerate(rows[start:start + 8]):
            rank = start + index + 1
            y = 344 + index * 112
            rounded(draw, (x, y, x + col_w, y + 96), 18,
                    (20, 26, 46, 204), (212, 178, 106, 75), 1)
            rounded(draw, (x + 12, y + 14, x + 57, y + 59), 12,
                    (168, 24, 24, 225) if rank <= 3 else (38, 45, 67, 235),
                    (212, 178, 106, 105), 1)
            label = f"{rank:02d}"
            box = draw.textbbox((0, 0), label, font=font(20))
            draw.text((x + 34 - (box[2] - box[0]) / 2, y + 25), label, font=font(20), fill=(242, 233, 210, 255))
            label = ellipsize(draw, item["display"], font(24), 220)
            draw.text((x + 70, y + 13), label, font=font(24), fill=(245, 244, 240, 255))
            draw.text((x + 70, y + 51), item["code"], font=font(18), fill=(164, 178, 207, 255))
            ret = format_return(item["return_1y"])
            ret_color = (239, 59, 59, 255) if not ret.startswith("-") and ret != "—" else (23, 178, 106, 255)
            text_right(draw, x + 338, y + 22, ret, font(23), ret_color)
            direct = compact_limit(item["direct_sales_limit"])
            agency = compact_limit(item["purchase_limit"])
            text_right(draw, x + col_w - 12, y + 15, direct, font(23), (242, 233, 210, 255))
            text_right(draw, x + col_w - 12, y + 52, agency, font(20), (179, 204, 255, 255))

    draw.line((160, 1312, 920, 1312), fill=(212, 178, 106, 130), width=1)
    draw.text((82, 1341), "额度单位：元；“暂停”为当前不可申购", font=font(18), fill=(188, 197, 216, 255))
    text_right(draw, 997, 1341, date_text, font(18), (188, 197, 216, 255))
    image.save(output, "PNG", optimize=True)


def make_reference_group_card(rows: list[dict], group: str, date_text: str, output: Path):
    """4×4 block card using the supplied Nasdaq annual-returns visual system."""
    width, height = 1080, 1440
    image = Image.new("RGB", (width, height), "#05060b")
    # Same three subdued lights as the reference: gold / rise red / fall green.
    light = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ld = ImageDraw.Draw(light)
    ld.ellipse((720, -400, 1540, 440), fill=(212, 178, 106, 32))
    ld.ellipse((-560, 350, 420, 1120), fill=(239, 59, 59, 12))
    ld.ellipse((720, 980, 1570, 1760), fill=(23, 178, 106, 12))
    light = light.filter(ImageFilter.GaussianBlur(140))
    image = Image.alpha_composite(image.convert("RGBA"), light).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")

    for left, top, sx, sy in ((40, 40, 1, 1), (1040, 40, -1, 1), (40, 1400, 1, -1), (1040, 1400, -1, -1)):
        draw.line((left, top, left + sx * 56, top), fill=(212, 178, 106, 115), width=2)
        draw.line((left, top, left, top + sy * 56), fill=(212, 178, 106, 115), width=2)

    topic = "PASSIVE QDII · RETURN & QUOTA" if group == "被动型" else "ACTIVE QDII · RETURN & QUOTA"
    # Pillow lacks text gradients: pale gold gives the same high-contrast title intent.
    title = f"{group}基金直销 / 代销额度"
    title_box = draw.textbbox((0, 0), title, font=font(49))
    draw.text(((width - (title_box[2] - title_box[0])) / 2, 120), topic, font=font(21), fill=(212, 178, 106, 255))
    draw.text(((width - (title_box[2] - title_box[0])) / 2, 164), title, font=font(49), fill=(242, 233, 210, 255))
    sub = "按近一年收益率排序 · 单位：元"
    sub_box = draw.textbbox((0, 0), sub, font=font(23))
    draw.text(((width - (sub_box[2] - sub_box[0])) / 2, 232), sub, font=font(23), fill=(138, 147, 166, 255))
    draw.line((480, 286, 600, 286), fill=(212, 178, 106, 175), width=1)

    grid_x, grid_y, gap = 52, 328, 14
    block_w, block_h = 235, 205
    for index, item in enumerate(rows[:16]):
        col, row = index % 4, index // 4
        x, y = grid_x + col * (block_w + gap), grid_y + row * (block_h + gap)
        ret = format_return(item["return_1y"])
        positive = not ret.startswith("-")
        if positive:
            fill, border, value_color = (55, 15, 25, 230), (239, 59, 59, 125), (255, 220, 220, 255)
        else:
            fill, border, value_color = (8, 51, 42, 230), (23, 178, 106, 125), (199, 243, 221, 255)
        rounded(draw, (x, y, x + block_w, y + block_h), 17, fill, border, 2 if index == 0 else 1)
        rank = f"#{index + 1:02d}"
        draw.text((x + 18, y + 17), rank, font=font(17), fill=(212, 178, 106, 255))
        label = ellipsize(draw, item["display"], font(22), block_w - 32)
        draw.text((x + 18, y + 45), label, font=font(22), fill=(231, 236, 243, 255))
        draw.text((x + 18, y + 76), item["code"], font=font(16), fill=(138, 147, 166, 255))
        pct_box = draw.textbbox((0, 0), ret, font=font(32))
        draw.text((x + (block_w - (pct_box[2] - pct_box[0])) / 2, y + 103), ret, font=font(32), fill=value_color)
        draw.line((x + 18, y + 149, x + block_w - 18, y + 149), fill=(255, 255, 255, 26), width=1)
        direct = compact_limit(item["direct_sales_limit"])
        agency = compact_limit(item["purchase_limit"])
        draw.text((x + 18, y + 161), "直销", font=font(15), fill=(212, 178, 106, 200))
        text_right(draw, x + block_w - 18, y + 160, direct, font(17), (242, 233, 210, 255))
        draw.text((x + 18, y + 183), "代销", font=font(15), fill=(138, 147, 166, 220))
        text_right(draw, x + block_w - 18, y + 182, agency, font(17), (199, 218, 255, 255))

    footer = f"QDII QUOTA RADAR · {date_text} · 直销仅取基金公司官网当前可验证数据"
    footer_box = draw.textbbox((0, 0), footer, font=font(16))
    draw.text(((width - (footer_box[2] - footer_box[0])) / 2, 1355), footer, font=font(16), fill=(138, 147, 166, 200))
    image.save(output, "PNG", optimize=True)


def _legacy_make_reference_table_card(rows: list[dict], group: str, date_text: str, output: Path):
    """直销 / 代销额度小红书卡片，沿用当前飞书推送 UI v2。"""
    width, pad, card_pad = 1080, 60, 40
    title_h, header_h, row_h = 180, 62, 94
    # 直销数据已验证的基金才会传入；高度随行数增长，不裁切基金信息。
    height = pad + title_h + 24 + header_h + len(rows) * row_h + 24 + pad
    image = Image.new("RGB", (width, height), "#F5F7FA")
    draw = ImageDraw.Draw(image, "RGBA")
    left, right = pad + card_pad, width - pad - card_pad
    direct_x, agency_x = 700, right

    rounded(draw, (pad, pad, width - pad, pad + title_h), 24, "#1A1A2E")
    title = f"QDII {group}基金直销 / 代销额度"
    title_font = feishu_font(48)
    title_box = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((width - (title_box[2] - title_box[0])) / 2, pad + 40), title,
              font=title_font, fill="#FFFFFF")
    subtitle = f"直销额度 · 代销额度 · {date_text.replace('.', '-') }"
    subtitle_box = draw.textbbox((0, 0), subtitle, font=feishu_font(28))
    draw.text(((width - (subtitle_box[2] - subtitle_box[0])) / 2, pad + 116), subtitle,
              font=feishu_font(28), fill="#8892A3")

    table_y = pad + title_h + 24
    table_h = header_h + len(rows) * row_h + 24
    rounded(draw, (pad, table_y, width - pad, table_y + table_h), 24, "#FFFFFF")
    header_font = feishu_font(25)
    draw.text((left, table_y + 18), "基金名称（基金代码）", font=header_font, fill="#64748B")
    text_right(draw, direct_x, table_y + 18, "直销额度", header_font, "#64748B")
    text_right(draw, agency_x, table_y + 18, "代销额度", header_font, "#64748B")

    for index, item in enumerate(rows):
        y = table_y + header_h + index * row_h
        if index:
            draw.line((left, y, right, y), fill="#E2E8F0", width=1)
        label = ellipsize(draw, item["display"], feishu_font(29), 450)
        draw.text((left, y + 17), label, font=feishu_font(29), fill="#334155")
        draw.text((left + 12, y + 54), f"（{item['code']}）", font=feishu_font(23), fill="#94A3B8")
        direct = compact_limit(item["direct_sales_limit"])
        agency = compact_limit(item["purchase_limit"])
        direct_color = "#EF4444" if direct == "暂停" else "#10B981" if direct != "—" else "#94A3B8"
        agency_color = "#EF4444" if agency == "暂停" else "#10B981" if agency != "—" else "#94A3B8"
        text_right(draw, direct_x, y + 28, direct, feishu_font(34), direct_color)
        text_right(draw, agency_x, y + 28, agency, feishu_font(34), agency_color)

    # 中部水印沿用飞书推送的低干扰样式。
    watermark = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    watermark_draw = ImageDraw.Draw(watermark)
    mark_font = feishu_font(76)
    mark = "HRunning"
    mark_box = watermark_draw.textbbox((0, 0), mark, font=mark_font)
    watermark_draw.text(((width - (mark_box[2] - mark_box[0])) / 2, int(height * .46)), mark,
                        font=mark_font, fill=(30, 30, 50, 52))
    image = Image.alpha_composite(image.convert("RGBA"), watermark.rotate(-22, resample=Image.Resampling.BICUBIC))
    image.convert("RGB").save(output, "PNG", optimize=True)


def make_reference_table_card(rows: list[dict], group: str, date_text: str, output: Path):
    """使用飞书推送同一生成器输出小红书直销 / 代销卡片。

    不再维护一份截图式复刻的排版：标题高度、圆角、字体、表格列距、
    排序规则和水印均直接继承 ``fund_monitor.output.image``。
    ``date_text`` 保留在调用签名中，以保持现有生成流程兼容。
    """
    del date_text
    generate_direct_sales(
        rows,
        str(output),
        title=f"QDII {group}基金直销 / 代销额度",
    )


def main():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    configured = config["passive_funds"] + config["active_funds"]
    funds = [fund for fund in configured if fund["code"] not in EXCLUDED_CODES]

    # UI v2 不展示近一年收益率，无需请求 RANKING 或在其失败时逐只回退
    # HTML；代销额度直接使用一次性全市场限购快照即可，避免无谓的慢请求。
    print(f"Fetching agency-limit snapshot for {len(funds)} configured funds …")
    market = eastmoney_jjjz.fetch_market_snapshot()
    print("Fetching one-year return snapshot …")
    ranking = eastmoney_ranking.fetch_market_snapshot()
    print(f"Fetching official direct-sales data for {len(funds)} supported funds …")
    direct = {item["code"]: item for item in fetch_official_limits(funds)}

    rows = []
    skipped = []
    for fund in funds:
        market_item = market.get(fund["code"], {})
        direct_item = direct.get(fund["code"], {})
        if direct_item.get("direct_sales_status") != "ok":
            skipped.append(fund["code"])
            continue
        rows.append({
            **fund,
            "return_1y": ranking.get(fund["code"], {}).get("return_1y", "—"),
            "purchase_limit": market_item.get("purchase_limit", "未获取"),
            "direct_sales_limit": direct_item.get("direct_sales_limit", "未获取"),
        })

    rows.sort(key=lambda item: item["code"])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_text = datetime.now().strftime("%Y.%m.%d")
    # The final deliverable is the active/passive UI v2 pair.  A row only enters
    # the card after its official direct-sales result has been verified above.
    outputs = []

    # Requested one-card summary for each configured category.
    groups = {
        "被动型": {item["code"] for item in config["passive_funds"]},
        "主动型": {item["code"] for item in config["active_funds"]},
    }
    group_outputs = []
    for label, codes in groups.items():
        grouped_rows = [item for item in rows if item["code"] in codes]
        output = OUTPUT_DIR / f"qdii-{label}-direct-sales-{datetime.now():%Y%m%d-%H%M%S}-v2.png"
        make_reference_table_card(grouped_rows, label, date_text, output)
        group_outputs.append(output)
    globals()["ACTIVE_FONT_SCHEME"] = "d"

    manifest = OUTPUT_DIR / f"qdii-direct-sales-{datetime.now():%Y%m%d}-manifest.json"
    manifest.write_text(json.dumps({
        "source": "config.json",
        "excluded_before_fetch": sorted(EXCLUDED_CODES),
        "skipped_after_fetch": skipped,
        "included": [item["code"] for item in rows],
        "outputs": [str(path) for path in outputs],
        "group_outputs": [str(path) for path in group_outputs],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Generated:")
    for output in outputs:
        print(output)
    print(f"Manifest: {manifest}")


if __name__ == "__main__":
    main()
