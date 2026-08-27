from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/cards/xiaohongshu-direct-sales"
CHINESE = "/System/Library/Fonts/Supplemental/Songti.ttc"
CORM = "/Users/shareit/Library/Fonts/CormorantGaramond[wght].ttf"
DM = "/Users/shareit/Library/Fonts/DMSerifDisplay-Regular.ttf"
def f(path, size): return ImageFont.truetype(path, size)
def center(d, text, y, font, fill):
    b=d.textbbox((0,0),text,font=font); d.text(((1080-(b[2]-b[0]))/2,y),text,font=font,fill=fill)
def main():
    returns = {
        "主动型": [94.75,88.91,86,85.56,84.15,80.24,67.59,55.33,53.96,43.29,41.43,33.49,32.13,21.55,17.46,7.18],
        "被动型": [18.48,18.23,17.54,17.39,17.22,17.18,17.10,17.06,16.91,16.88,16.87,16.69,16.60,16.39,16.19,15.32],
    }
    for group in ("主动型", "被动型"):
        image=Image.open(OUT/f"qdii-{group}-direct-sales-20260827-html-scheme.png").convert("RGBA")
        d=ImageDraw.Draw(image,"RGBA")
        d.rectangle((45,85,1035,326),fill=(5,6,11,245))
        center(d,"ACTIVE QDII · RETURN & QUOTA" if group=="主动型" else "PASSIVE QDII · RETURN & QUOTA",112,f(CORM,26),"#d4b26a")
        center(d,f"QDII {group}基金直销 / 代销额度",156,f(CHINESE,52),"#f2e9d2")
        center(d,"2026 — 08 — 27",228,f(DM,25),"#f2e9d2")
        d.line((480,282,600,282),fill="#d4b26a",width=1)
        d.rectangle((58,330,1022,389),fill=(5,6,11,255))
        labels=[(82,"基金名称",False),(620,"近一年收益率",True),(840,"直销额度",True),(998,"代销额度",True)]
        for x,text,right in labels:
            ft=f(CHINESE,19); b=d.textbbox((0,0),text,font=ft); d.text((x-(b[2]-b[0]) if right else x,340),text,font=ft,fill="#d4b26a")
        d.line((76,374,1004,374),fill="#d4b26a",width=1)
        # Normalize archived same-day return values to two decimals before final export.
        for i, value in enumerate(returns[group]):
            y = 403 + i * 60
            d.rectangle((505, y - 2, 628, y + 34), fill=(5,6,11,255))
            d.line((505, y + 33, 628, y + 33), fill="#1C202A", width=1)
            label = f"{value:.2f}%"
            ft = f(DM,23)
            box = d.textbbox((0,0),label,font=ft)
            d.text((620-(box[2]-box[0]), y), label, font=ft, fill="#ef3b3b")
        # The current source card already contains the HRunning watermark.
        # Add the footer here so legacy source snapshots also receive it.
        disclaimer="免责声明：数据仅供参考，不构成投资建议；额度以基金公司及销售机构实时规则为准"
        ft=f(CHINESE,13); box=ImageDraw.Draw(image).textbbox((0,0),disclaimer,font=ft)
        ImageDraw.Draw(image).text(((1080-(box[2]-box[0]))/2,1372),disclaimer,font=ft,fill=(138,147,166,210))
        image.convert("RGB").save(OUT/f"qdii-{group}-direct-sales-20260827-final.png","PNG")
if __name__=="__main__": main()
