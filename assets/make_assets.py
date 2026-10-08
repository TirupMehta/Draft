"""Generate Draft v3 art assets: icon.ico, splash.png, tray.png, version.txt."""
import os

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = BASE  # icon.ico, splash.png, tray.png, version.txt live next to this script

ESPRESSO = (20, 15, 13)
GOLD = (227, 183, 107)
GOLD_DEEP = (190, 140, 70)
BLUSH = (201, 138, 125)
IVORY = (245, 237, 227)

FONTS = "C:\\Windows\\Fonts"


def _font(name, size):
    from PIL import ImageFont
    for cand in (os.path.join(FONTS, name), name):
        try:
            return ImageFont.truetype(cand, size)
        except Exception:
            continue
    return ImageFont.load_default()


def rounded(canvas_draw, box, radius, **kw):
    canvas_draw.rounded_rectangle(box, radius=radius, **kw)


def make_icon():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rounded(d, [4, 4, 252, 252], 60, fill=ESPRESSO + (255,))
    # gold ring
    rounded(d, [10, 10, 246, 246], 54, outline=GOLD + (255,), width=6)
    # soft blush glow behind letter
    glow = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([78, 60, 178, 150], fill=BLUSH + (70,))
    from PIL import ImageFilter
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(18)))
    d = ImageDraw.Draw(img)
    f = _font("georgia.ttf", 150)
    d.text((128, 138), "D", fill=GOLD + (255,), font=f, anchor="mm")
    # tiny sound-wave ticks under the D
    for i, h in enumerate((10, 18, 26, 18, 10)):
        x = 96 + i * 16
        d.rounded_rectangle([x, 208 - h // 2, x + 6, 208 + h // 2], 3,
                            fill=IVORY + (230,))
    img.save(os.path.join(OUT, "icon.ico"),
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                    (64, 64), (128, 128), (256, 256)])
    img.resize((64, 64)).save(os.path.join(OUT, "tray.png"))
    print("icon.ico + tray.png done")


def make_splash():
    from PIL import Image, ImageDraw
    W, H = 640, 420
    img = Image.new("RGB", (W, H), ESPRESSO)
    d = ImageDraw.Draw(img)
    # soft vignette glow up top (1px steps so no banding)
    for y in range(0, 220):
        a = int(14 * (1 - y / 220))
        d.line([(0, y), (W, y)], fill=(32 + a, 24 + a // 2, 18))
    rounded(d, [14, 14, W - 14, H - 14], 26, outline=GOLD, width=2)
    f_big = _font("georgia.ttf", 120)
    d.text((W / 2, 168), "Draft", fill=GOLD, font=f_big, anchor="mm")
    f_sub = _font("segoeui.ttf", 24)
    d.text((W / 2, 262), "hold  ·  speak  ·  done", fill=IVORY, font=f_sub,
           anchor="mm")
    f_small = _font("segoeuil.ttf", 18)
    d.text((W / 2, 300), "local  ·  private  ·  instant", fill=(150, 135, 115),
           font=f_small, anchor="mm")
    # wave ticks
    for i, h in enumerate((12, 22, 34, 46, 34, 22, 12)):
        x = W / 2 - 66 + i * 20
        d.rounded_rectangle([x, 348 - h / 2, x + 9, 348 + h / 2], 4,
                            fill=BLUSH)
    img.save(os.path.join(OUT, "splash.png"))
    print("splash.png done")


def make_version():
    txt = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=(3,0,0,0), prodvers=(3,0,0,0), mask=0x3f,
    flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Draft'),
    StringStruct('FileDescription', 'Draft - fastest local speech to text'),
    StringStruct('FileVersion', '3.0.0'),
    StringStruct('InternalName', 'Draft'),
    StringStruct('LegalCopyright', 'Draft'),
    StringStruct('OriginalFilename', 'Draft.exe'),
    StringStruct('ProductName', 'Draft'),
    StringStruct('ProductVersion', '3.0.0')])]),
  VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"""
    with open(os.path.join(OUT, "version.txt"), "w") as fh:
        fh.write(txt)
    print("version.txt done")


if __name__ == "__main__":
    make_icon()
    make_splash()
    make_version()
