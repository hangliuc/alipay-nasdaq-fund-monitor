#!/usr/bin/env python3
"""Create a compact font comparison sheet for the QDII card style."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/cards/xiaohongshu-direct-sales/font-options.png"

OPTIONS = [
    ("A · 推荐", "Noto Serif SC + DM Serif", "/Users/shareit/Library/Fonts/NotoSerifSC[wght].ttf", "/Users/shareit/Library/Fonts/DMSerifDisplay-Regular.ttf", "杂志感最强，适合当前深色金卡"),
    ("B · 清爽", "PingFang SC + DM Serif", "/System/Library/AssetsV2/com_apple_MobileAsset_Font7/3419f2a427639ad8c8e139149a287865a90fa17e.asset/AssetData/PingFang.ttc", "/Users/shareit/Library/Fonts/DMSerifDisplay-Regular.ttf", "小字号最清晰，数据阅读压力最低"),
    ("C · 典雅", "Songti SC + DM Serif", "/System/Library/Fonts/Supplemental/Songti.ttc", "/Users/shareit/Library/Fonts/DMSerifDisplay-Regular.ttf", "传统财经刊物气质，正文稍显柔和"),
    ("D · 现代", "STHeiti + DM Serif", "/System/Library/Fonts/STHeiti Medium.ttc", "/Users/shareit/Library/Fonts/DMSerifDisplay-Regular.ttf", "当前中文风格升级版，稳妥耐看"),
]

def f(path, size):
    return ImageFont.truetype(path, size)

def card(img, x, y, option):
    tag, pairing, chinese, number, note = option
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x, y, x + 500, y + 650), 20, fill="#080b14", outline="#d4b26a", width=2)
    cn_title, en, meta, value, body = f(chinese, 20), f(number, 21), f(chinese, 16), f(number, 48), f(chinese, 17)
    d.text((x + 28, y + 28), tag, font=cn_title, fill="#d4b26a")
    d.text((x + 28, y + 66), pairing, font=en, fill="#f2e9d2")
    d.text((x + 28, y + 128), "纳斯达克 QDII 基金", font=f(chinese, 30), fill="#f2e9d2")
    d.line((x + 28, y + 180, x + 472, y + 180), fill="#d4b26a", width=1)
    d.text((x + 28, y + 208), "近一年收益率", font=meta, fill="#d4b26a")
    d.text((x + 28, y + 238), "18.48%", font=value, fill="#ef3b3b")
    d.text((x + 28, y + 318), "国泰纳斯达克100", font=f(chinese, 21), fill="#f2e9d2")
    d.text((x + 28, y + 360), "直销额度", font=meta, fill="#d4b26a")
    d.text((x + 345, y + 360), "50", font=f(number, 26), fill="#f2e9d2")
    d.text((x + 28, y + 405), "代销额度", font=meta, fill="#d4b26a")
    d.text((x + 345, y + 405), "50", font=f(number, 26), fill="#f2e9d2")
    d.line((x + 28, y + 468, x + 472, y + 468), fill="#242b3b", width=1)
    d.multiline_text((x + 28, y + 500), note, font=body, fill="#aab3c5", spacing=7)

def main():
    sheet = Image.new("RGB", (1080, 1440), "#05060b")
    for i, option in enumerate(OPTIONS):
        card(sheet, 40 + (i % 2) * 500, 60 + (i // 2) * 670, option)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(OUT, "PNG", optimize=True)

if __name__ == "__main__":
    main()
