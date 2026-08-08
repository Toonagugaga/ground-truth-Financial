#!/usr/bin/env python3
"""
ocr_reader.py - อ่าน PDF ที่เป็นภาพสแกน ให้ออกมาเป็น "text layer" แบบเดียวกับ
                pdftotext -layout เพื่อให้ตัวสกัดเดิมใช้ได้โดยไม่ต้องเขียนใหม่

แนวคิดหลัก
--------------------------------------------------------------------------
ทั้งระบบ (find_columns, parse_line, se_matrix) ตัดสินตำแหน่งค่าจาก
"พิกัดตัวอักษรในบรรทัด" ไม่ใช่จากโครงสร้างตาราง ดังนั้นถ้า OCR คืนข้อความ
ที่รักษาระยะห่างตามพิกัดจริงได้ ตัวสกัดทั้งหมดจะทำงานต่อได้ทันที
โดยไม่ต้องแตะโค้ดเดิมสักบรรทัด

    PDF สแกน -> ภาพ 300dpi -> tesseract (คืนกล่องคำพร้อมพิกัด)
             -> จัดคำกลับเป็นบรรทัดข้อความความกว้างคงที่ -> ป้อนเข้าตัวสกัดเดิม

ทำไมต้องใช้ image_to_data ไม่ใช่ image_to_string
--------------------------------------------------------------------------
image_to_string คืนข้อความล้วน ซึ่ง "ทำลายพิกัด" ไปแล้ว ช่องว่างที่ได้เป็นการ
เดาของ tesseract เอง ตัวเลขคนละคอลัมน์อาจถูกวางชิดกันจนแยกไม่ออก
image_to_data คืนกล่อง (left, top, width, height) ของทุกคำ เราจึงคำนวณ
ตำแหน่งคอลัมน์เองได้ตรงกับที่ pdftotext -layout ทำ

ข้อควรระวังที่ต่างจาก text layer ปกติ
--------------------------------------------------------------------------
1. OCR อ่านตัวเลขผิดได้ ซึ่งอันตรายกว่าข้อความเพี้ยนมาก
   ข้อความเพี้ยน -> โครงพยัญชนะยังกู้ได้  |  ตัวเลขผิด -> กู้ไม่ได้เลย
   จึงต้องพึ่งสมการตรวจสอบ (crosscheck / se_matrix.check_rows) มากกว่าเดิม
2. "1" กับ "l", "0" กับ "O", "," กับ "." สลับกันได้ง่าย
   -> normalise_digits() แก้เฉพาะกรณีที่แน่ใจ และเฉพาะใน token ที่เป็นตัวเลข
3. บรรทัดที่เอียงเล็กน้อยทำให้ y ไม่ตรงกัน -> จัดกลุ่มบรรทัดด้วยช่วง ไม่ใช่ค่าเป๊ะ

วิธีรันบน Kaggle (มี tesseract-ocr-tha ติดตั้งมาแล้ว)
--------------------------------------------------------------------------
    !apt-get -qq install -y tesseract-ocr-tha poppler-utils
    !pip -q install pytesseract pdf2image

    python ocr_reader.py --pdf CP-ALL-Signed-FS-TH-Q126.pdf --out cpall_ocr/
    python ocr_reader.py --compare FINANCIAL_STATEMENTS_Mfec.pdf --gt tech-01.csv

โหมด --compare สำคัญที่สุด
--------------------------------------------------------------------------
รัน OCR ทับไฟล์ที่ "มี text layer อยู่แล้วและมีเฉลย 100%" แล้ววัดว่าความแม่นยำ
ตกเหลือเท่าไร นี่คือตัวเลขที่ตอบคำถามว่า "OCR แย่ลงกว่าเดิมแค่ไหน"
ซึ่งวัดไม่ได้เลยถ้าเอา OCR ไปใช้กับไฟล์ที่ไม่มีเฉลย (CP ALL / Humanica)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# ค่าตั้งต้น
# --------------------------------------------------------------------------

DPI = 300              # ต่ำกว่านี้ตัวเลขไทยเริ่มอ่านผิด สูงกว่านี้ช้าขึ้นมากโดยไม่ได้ดีขึ้น
LANG = "tha+eng"       # ต้องมี eng ด้วย ไม่งั้นตัวเลขอารบิกกับ (,) จะเพี้ยน
PSM = 6                # 6 = ถือว่าทั้งหน้าเป็นบล็อกข้อความเดียวที่จัดเรียงสม่ำเสมอ
                       # เหมาะกับงบการเงินซึ่งเป็นตารางเต็มหน้า
MIN_CONF = 30          # ทิ้งคำที่ tesseract เองไม่มั่นใจ
LINE_TOL_RATIO = 0.6   # คำที่ศูนย์กลางแนวตั้งห่างกันไม่เกิน 0.6 เท่าของความสูงตัวอักษร
                       # ถือว่าอยู่บรรทัดเดียวกัน (กันบรรทัดเอียง)


def need(mod: str, hint: str):
    """import แบบบอกวิธีติดตั้งเมื่อไม่มี

    ต้องใช้ importlib.import_module ไม่ใช่ __import__ เพราะ __import__("PIL.Image")
    คืน "PIL" (แพ็กเกจชั้นบนสุด) ไม่ใช่โมดูลย่อย และ PIL ไม่ได้โหลด Image
    ให้อัตโนมัติ -> AttributeError: module 'PIL' has no attribute 'Image'
    """
    import importlib
    try:
        return importlib.import_module(mod)
    except ImportError:
        sys.exit(f"ต้องติดตั้งก่อน: {hint}")


# --------------------------------------------------------------------------
# 1. PDF -> ภาพ
# --------------------------------------------------------------------------

def page_images(pdf: Path, first: int, last: int, dpi: int = DPI):
    """แปลงหน้าที่ต้องการเป็นภาพ ใช้ pdftoppm ตรงๆ ไม่พึ่ง pdf2image

    pdftoppm มากับ poppler-utils ซึ่งเป็นแพ็กเกจเดียวกับ pdftotext ที่เราใช้อยู่แล้ว
    จึงไม่เพิ่ม dependency ใหม่
    """
    Image = need("PIL.Image", "pip install pillow")
    import tempfile
    out = []
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png",
                        "-f", str(first), "-l", str(last),
                        str(pdf), f"{td}/p"], check=True)
        # ต้องเรียงด้วย "ตัวเลขหน้า" ไม่ใช่เรียงตามชื่อไฟล์
        # pdftoppm เติมศูนย์นำหน้าตามจำนวนหลักของหน้าสุดท้าย ถ้าเรียงแบบข้อความ
        # p-10.png จะมาก่อน p-2.png แล้วหน้าทั้งหมดจะสลับกันโดยไม่มีอะไรเตือน
        def page_no(p: Path) -> int:
            m = re.search(r"(\d+)", p.stem)
            return int(m.group(1)) if m else 0
        files = sorted(Path(td).glob("p-*.png"), key=page_no)
        for f in files:
            # .copy() จำเป็น เพราะโฟลเดอร์ชั่วคราวจะถูกลบเมื่อออกจาก with
            out.append((page_no(f), Image.open(f).copy()))
    return out


# --------------------------------------------------------------------------
# 2. ภาพ -> กล่องคำ -> ข้อความความกว้างคงที่
# --------------------------------------------------------------------------

_DIGIT_FIX = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1"})


def normalise_digits(tok: str) -> str:
    """แก้ตัวอักษรที่ OCR มักสับสนกับตัวเลข **เฉพาะ token ที่เป็นตัวเลขชัดเจน**

    เงื่อนไขเข้มไว้ก่อน: ต้องมีตัวเลขจริงอย่างน้อยครึ่งหนึ่งของความยาว
    ไม่งั้นคำไทยที่มีตัว l หรือ O ปนจะถูกแก้มั่ว
    """
    core_chars = re.sub(r"[(),.\-\s]", "", tok)
    if not core_chars:
        return tok
    digits = sum(ch.isdigit() for ch in core_chars)
    if digits / len(core_chars) < 0.5:
        return tok
    if re.search(r"[ก-๛]", tok):     # มีอักษรไทยปน = ไม่ใช่ตัวเลขล้วน
        return tok
    return tok.translate(_DIGIT_FIX)


def ocr_page_layout(img, lang: str = LANG, psm: int = PSM) -> str:
    """OCR หนึ่งหน้า แล้วประกอบกลับเป็นข้อความที่รักษาพิกัดแนวนอน

    ผลลัพธ์มีหน้าตาเหมือน pdftotext -layout คือใช้ช่องว่างดันคำไปยังคอลัมน์
    ที่ตรงกับตำแหน่งจริงในภาพ
    """
    pytesseract = need("pytesseract", "pip install pytesseract")
    cfg = f"--psm {psm}"
    data = pytesseract.image_to_data(img, lang=lang, config=cfg,
                                     output_type=pytesseract.Output.DICT)

    words = []
    for i, txt in enumerate(data["text"]):
        t = (txt or "").strip()
        if not t:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < MIN_CONF:
            continue
        words.append({
            "text": normalise_digits(t),
            "x": data["left"][i], "y": data["top"][i],
            "w": data["width"][i], "h": data["height"][i],
        })
    if not words:
        return ""

    # ความกว้างของตัวอักษรหนึ่งตัว ใช้แปลงพิกัดพิกเซล -> ตำแหน่งคอลัมน์
    #
    # **ต้องคิดจาก token ที่เป็นตัวเลขเท่านั้น** ห้ามเอาคำไทยมาเฉลี่ยด้วย
    # เพราะสระบน/ล่างและวรรณยุกต์ของไทยไม่กินความกว้างแนวนอน แต่ถูกนับใน len()
    #     "เงินสดและรายการเทียบเท่าเงินสด"  len = 29 แต่กว้างเท่าอักษรจริงราว 20 ตัว
    # ถ้าเอามาเฉลี่ยรวมกัน char_w จะเล็กเกินจริง -> x/char_w ใหญ่เกิน ->
    # ทุกอย่างถูกดันไปทางขวาไม่เท่ากัน -> คอลัมน์เพี้ยนทั้งหน้า
    # ตัวเลขเป็นสิ่งเดียวที่ต้องตรงคอลัมน์ จึงต้องยึดความกว้างของตัวเลขเป็นหลัก
    num_w = [w["w"] / len(w["text"]) for w in words
             if re.fullmatch(r"[\d,.()\-]+", w["text"]) and w["text"]]
    src = num_w or [w["w"] / max(len(w["text"]), 1) for w in words]
    src.sort()
    char_w = src[len(src) // 2] or 1

    # จัดคำเข้าบรรทัดด้วยศูนย์กลางแนวตั้ง เผื่อหน้าเอียงเล็กน้อย
    heights = sorted(w["h"] for w in words)
    line_tol = heights[len(heights) // 2] * LINE_TOL_RATIO
    words.sort(key=lambda w: (w["y"] + w["h"] / 2))

    lines, cur, cur_mid = [], [], None
    for w in words:
        mid = w["y"] + w["h"] / 2
        if cur_mid is None or abs(mid - cur_mid) <= line_tol:
            cur.append(w)
            cur_mid = mid if cur_mid is None else (cur_mid + mid) / 2
        else:
            lines.append(cur)
            cur, cur_mid = [w], mid
    if cur:
        lines.append(cur)

    # ระยะห่างที่ถือว่า "ติดกัน" = ส่วนหนึ่งของความกว้างตัวอักษร
    # tesseract หั่นข้อความไทยเป็นชิ้นเล็กๆ หลายชิ้นในคำเดียว เพราะไม่มีช่องว่าง
    # ระหว่างคำ ถ้าใส่ช่องว่างคั่นทุกชิ้น ชื่อรายการจะยาวขึ้นมากจนล้นไปทับ
    # คอลัมน์ตัวเลข แล้ว parse_line จะตัดชื่อผิดที่
    #     จริง   ประมาณการหนี้สินค่าเสียหายจากการฟ้องร้อง      22,567
    #     OCR    ปรมมาณา กร หน ้ ส ` น` ค า เส ` ห ย ย จาก กา ฟ้อง ร ร ้ อ ง 22,567
    # ต้องเชื่อมชิ้นที่อยู่ติดกันจริงๆ เข้าด้วยกันโดยไม่ใส่ช่องว่าง
    JOIN_GAP = 0.5

    out = []
    for ln in lines:
        ln.sort(key=lambda w: w["x"])
        buf = ""
        prev_right = None
        for w in ln:
            gap_px = None if prev_right is None else w["x"] - prev_right
            if gap_px is not None and gap_px < JOIN_GAP * char_w:
                buf += w["text"]            # ติดกัน = ชิ้นส่วนของคำเดียวกัน
            else:
                col = int(round(w["x"] / char_w))
                if col < len(buf):
                    col = len(buf) + 1      # ชนกันแล้ว เว้นอย่างน้อยหนึ่งช่อง
                buf += " " * (col - len(buf)) + w["text"]
            prev_right = w["x"] + w["w"]
        out.append(buf.rstrip())
    return "\n".join(out)


def ocr_pdf(pdf: Path, first: int = 1, last: int = 20,
            lang: str = LANG, psm: int = PSM) -> list[str]:
    """คืนข้อความทีละหน้า ในรูปแบบเดียวกับ pdftotext -layout"""
    pages = []
    for pno, img in page_images(pdf, first, last):
        pages.append(ocr_page_layout(img, lang=lang, psm=psm))
    return pages


# --------------------------------------------------------------------------
# 3. ต่อเข้ากับตัวสกัดเดิม
# --------------------------------------------------------------------------

def write_text_layer(pdf: Path, outdir: Path, first: int, last: int,
                     lang: str, psm: int) -> Path:
    """เขียนผล OCR เป็นไฟล์ .txt ทีละหน้า + ไฟล์รวมที่คั่นด้วย \\f

    ไฟล์รวมมีรูปแบบเหมือน `pdftotext -layout file.pdf -` ทุกประการ
    จึงเอาไปป้อน page_kind / find_columns / parse_line ได้ตรงๆ
    """
    # บน Kaggle โฟลเดอร์ /kaggle/input เป็นแบบอ่านอย่างเดียว ถ้าเผลอชี้ --out
    # ไปที่นั่นจะพังตอนเขียนไฟล์ ซึ่งเป็นตอนที่ OCR ทำงานเสร็จไปหมดแล้ว
    # เสียเวลาเปล่า จึงต้องเช็คก่อนเริ่ม ไม่ใช่ตอนจบ
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        probe = outdir / ".write_test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        sys.exit(f"เขียนลง {outdir} ไม่ได้ ({e})\n"
                 f"บน Kaggle ให้ใช้ --out /kaggle/working/<ชื่อโฟลเดอร์>")

    pages = ocr_pdf(pdf, first, last, lang, psm)
    for i, t in enumerate(pages, first):
        (outdir / f"page-{i:02d}.txt").write_text(t, encoding="utf-8")
    joined = outdir / f"{pdf.stem}.layout.txt"
    joined.write_text("\f".join(pages), encoding="utf-8")
    print(f"เขียน {len(pages)} หน้า -> {joined}")
    return joined


def classify(pages: list[str]):
    """บอกว่าหน้าไหนเป็นงบอะไร ใช้ตัวจำแนกเดิมของระบบ"""
    import fs_core as core
    for i, t in enumerate(pages, 1):
        k = core.page_kind(t)
        n_num = len(re.findall(r"\d{1,3}(?:,\d{3})+", t))
        print(f"  หน้า {i:>2}: {str(k):<5} | ตัวเลขที่มีลูกน้ำ {n_num:>4} ตัว "
              f"| อักขระ {len(t):>6}")


# --------------------------------------------------------------------------
# 4. โหมดวัดผล: OCR แย่ลงกว่า text layer แค่ไหน
# --------------------------------------------------------------------------

def compare(pdf: Path, first: int, last: int, lang: str, psm: int):
    """เทียบผล OCR กับ text layer จริงของไฟล์เดียวกัน ทีละหน้า

    วัดสองอย่างที่ต่างกันและสำคัญคนละแบบ
      1. ตัวเลขที่ text layer มี แต่ OCR อ่านไม่ได้        -> ข้อมูลหาย
      2. ตัวเลขที่ OCR อ่านได้ แต่ text layer ไม่มี         -> ข้อมูลปลอม (แย่กว่า)
    ตัวเลขปลอมอันตรายกว่ามาก เพราะมันดูสมเหตุสมผลและไม่มีอะไรบอกว่าผิด
    """
    ocr_pages = ocr_pdf(pdf, first, last, lang, psm)
    raw = subprocess.run(["pdftotext", "-layout", "-f", str(first),
                          "-l", str(last), str(pdf), "-"],
                         capture_output=True, text=True).stdout
    real_pages = raw.split("\f")

    NUM = re.compile(r"\(?-?\d{1,3}(?:,\d{3})+\)?")
    tot_real = tot_hit = tot_extra = 0
    print(f"{'หน้า':>5} {'ในไฟล์':>8} {'OCR อ่านตรง':>12} {'อ่านไม่ได้':>11} "
          f"{'OCR สร้างเกิน':>14} {'%':>7}")
    for i, (o, r) in enumerate(zip(ocr_pages, real_pages), first):
        want = NUM.findall(r)
        got = NUM.findall(o)
        from collections import Counter
        cw, cg = Counter(want), Counter(got)
        hit = sum((cw & cg).values())
        extra = sum((cg - cw).values())
        tot_real += len(want); tot_hit += hit; tot_extra += extra
        pct = hit / len(want) * 100 if want else 100.0
        print(f"{i:>5} {len(want):>8} {hit:>12} {len(want) - hit:>11} "
              f"{extra:>14} {pct:>6.1f}%")
    print("-" * 62)
    pct = tot_hit / tot_real * 100 if tot_real else 0
    print(f"{'รวม':>5} {tot_real:>8} {tot_hit:>12} {tot_real - tot_hit:>11} "
          f"{tot_extra:>14} {pct:>6.1f}%")
    print()
    print("อ่านผลอย่างไร")
    print("  'อ่านไม่ได้'  = ตัวเลขหายไป สมการตรวจสอบจะจับได้ (ยอดไม่ลงตัว)")
    print("  'สร้างเกิน'   = ตัวเลขที่ไม่มีในงบ อันตรายกว่ามาก เพราะดูสมเหตุสมผล")
    print("                 และอาจไปแทนที่ค่าจริงในคอลัมน์เดียวกัน")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", help="ไฟล์ที่ต้องการ OCR")
    ap.add_argument("--compare", help="ไฟล์ที่มี text layer อยู่แล้ว ใช้วัดว่า OCR แย่ลงแค่ไหน")
    ap.add_argument("--out", default="ocr_out", help="โฟลเดอร์ผลลัพธ์")
    ap.add_argument("--first", type=int, default=1)
    ap.add_argument("--last", type=int, default=20)
    ap.add_argument("--lang", default=LANG)
    ap.add_argument("--psm", type=int, default=PSM)
    ap.add_argument("--classify", action="store_true",
                    help="บอกว่าหน้าไหนเป็นงบอะไร หลัง OCR เสร็จ")
    ap.add_argument("--dump", type=int, metavar="PAGE",
                    help="พิมพ์ layout ที่ประกอบได้ของหน้านั้นเทียบกับ text layer จริง "
                         "ใช้ดูว่าคอลัมน์เพี้ยนตรงไหน")
    args = ap.parse_args()

    if args.dump:
        # เครื่องมือวินิจฉัยที่สำคัญที่สุด: ต้องเห็นด้วยตาว่า OCR ประกอบ layout
        # ออกมาหน้าตาอย่างไร ตัวเลขความแม่นยำบอกแค่ว่า "ผิด" ไม่ได้บอกว่าผิดยังไง
        src = Path(args.pdf or args.compare)
        pages = ocr_pdf(src, args.dump, args.dump, args.lang, args.psm)
        print("=" * 100)
        print(f"OCR ประกอบได้ (หน้า {args.dump})")
        print("=" * 100)
        print(pages[0][:4000])
        real = subprocess.run(["pdftotext", "-layout", "-f", str(args.dump),
                               "-l", str(args.dump), str(src), "-"],
                              capture_output=True, text=True).stdout
        if real.strip():
            print()
            print("=" * 100)
            print("text layer จริง (ของจริงที่ควรได้)")
            print("=" * 100)
            print(real[:4000])
        return

    if args.compare:
        compare(Path(args.compare), args.first, args.last, args.lang, args.psm)
        return
    if not args.pdf:
        ap.error("ต้องระบุ --pdf หรือ --compare อย่างใดอย่างหนึ่ง")

    pdf = Path(args.pdf)
    joined = write_text_layer(pdf, Path(args.out), args.first, args.last,
                              args.lang, args.psm)
    if args.classify:
        print("\n=== หน้าไหนเป็นงบอะไร ===")
        classify(joined.read_text(encoding="utf-8").split("\f"))


if __name__ == "__main__":
    main()
