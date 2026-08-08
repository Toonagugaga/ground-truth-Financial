#!/usr/bin/env python3
"""
ocr_extract.py - ป้อนผล OCR เข้าตัวสกัดเดิม แล้วได้ CSV หน้าตาเหมือน extracted.csv

ทำไมต้องมีไฟล์นี้
--------------------------------------------------------------------------
extract_fs.extract_pdf() รับ "path ของ PDF" แล้วเรียก core.page_text() ซึ่งไป
เรียก pdftotext เอง ถ้าไฟล์เป็นภาพสแกน pdftotext จะคืนข้อความว่าง ตัวสกัดจึง
ได้ 0 แถวโดยไม่มี error

ทางแก้ที่ "ไม่ต้องแก้โค้ดเดิมสักบรรทัด" คือสลับตัวอ่านหน้าชั่วคราว
    core.page_text  -> อ่านจากไฟล์ผล OCR แทนการเรียก pdftotext
    core.n_pages    -> นับจำนวนหน้าจากผล OCR แทนการเรียก pdfinfo
ที่เหลือทั้งหมด (page_kind, find_columns, parse_line, ตัวอ่าน SE, การเติมหน่วย,
การเตือน concept ซ้ำ) ทำงานเหมือนเดิมทุกอย่าง

นี่คือเหตุผลที่ ocr_reader.py ต้องคืนข้อความที่ "รักษาพิกัดแนวนอน"
ถ้าคืนข้อความล้วน ตัวสกัดจะแยกคอลัมน์ไม่ได้เลย

ข้อควรระวังที่ต่างจากไฟล์ที่มี text layer
--------------------------------------------------------------------------
ผลที่ได้จากไฟล์นี้ **ไม่มี ground truth** สิ่งเดียวที่ตรวจได้คือสมการในตัวมันเอง
และสมการก็จับได้แค่ "ไม่ขัดกันเอง" ไม่ได้แปลว่าถูก
(บทเรียนจาก se_matrix: คอลัมน์เลื่อนทั้งคอลัมน์ สมการยังผ่าน 428/428)
จึงต้องติดป้าย reader=ocr ไว้ทุกแถว เพื่อไม่ให้ปนกับข้อมูลที่วัดแล้ว

วิธีใช้บน Kaggle
--------------------------------------------------------------------------
    # 1) ทำ text layer จากภาพก่อน
    !python ocr_reader.py --pdf CP-ALL-Signed-FS-TH-Q126.pdf \
            --out /kaggle/working/cpall --first 1 --last 20 --classify

    # 2) ป้อนเข้าตัวสกัดเดิม
    !python ocr_extract.py --layout /kaggle/working/cpall/CP-ALL-Signed-FS-TH-Q126.layout.txt \
            --company CPALL --statement all --out cpall_extracted.csv

    # 3) ตรวจด้วยสมการ (ขั้นที่ห้ามข้าม)
    !python crosscheck.py --extracted cpall_extracted.csv
"""
from __future__ import annotations

import argparse
import difflib
from pathlib import Path

import pandas as pd

import fs_core as core
import extract_fs as ef

WANT = {"bs": ("BS",), "is": ("IS",), "cf": ("CF",), "se": ("SE",),
        "all": ("BS", "IS", "CF", "SE")}

# --------------------------------------------------------------------------
# จำแนกหน้าแบบทนความผิดพลาดของ OCR
# --------------------------------------------------------------------------
# ระบบหลักจำแนกหน้าจาก "หัวเรื่องของงบ" ซึ่งแม่นมากกับ text layer
# แต่พังทันทีเมื่อ OCR อ่านหัวเรื่องพลาดแม้แต่ตัวเดียว
#     จริง  งบฐานะการเงิน (ต่อ)     -> โครง งบฐนกรงนตอ
#     OCR   งง ฐาน ะกาง ร` น (ต` อ ) -> โครง งงฐนกงรนตอ     <- ไม่ตรง หน้าถูกทิ้งทั้งหน้า
# ผลคือ MFEC หน้า 2 (ด้านหนี้สินและส่วนของผู้ถือหุ้น) หายไปทั้งหน้า
#
# จึงต้องมีชั้นสำรอง แต่ **ห้ามไปแก้ fs_core** เพราะตัวนั้นผ่านเฉลย 14 ชุด
# ที่ 100% อยู่แล้ว การผ่อนเกณฑ์ที่นั่นอาจทำให้หน้าหมายเหตุหลุดเข้ามา
# ชั้นสำรองจึงอยู่เฉพาะเส้นทางของ OCR เท่านั้น

TITLES = [("SE", core.SE_TITLE), ("CF", core.CF_TITLE),
          ("BS", core.BS_TITLE), ("IS", core.IS_TITLE)]

# เครื่องหมายจากเนื้อหา ใช้เมื่อหัวเรื่องอ่านไม่ออกเลย
# เรียงจากเฉพาะเจาะจงไปทั่วไป เพราะงบกระแสเงินสดมีชื่อรายการซ้ำกับงบฐานะการเงิน
CONTENT_MARKS = [
    ("CF", ["กระแสเงินสดจากกิจกรรม", "เงินสดสุทธิได้มาจาก", "เงินสดสุทธิใช้ไปใน"]),
    ("SE", ["ยอดคงเหลือ ณ วันที่", "ยอดยกมา"]),
    ("IS", ["รายได้จากการขาย", "ต้นทุนขาย", "กำไรขั้นต้น", "กำไรต่อหุ้น"]),
    ("BS", ["รวมสินทรัพย์", "รวมหนี้สิน", "สินทรัพย์หมุนเวียน",
            "หนี้สินและส่วนของผู้ถือหุ้น", "หนี้สินและส่วนของเจ้าของ"]),
]

FUZZY_CUTOFF = 0.75

# เก็บตัวจำแนกเดิมไว้ตั้งแต่ตอน import **ก่อน** ที่ใครจะไปสลับ core.page_kind
#
# ห้ามเรียก core.page_kind ตรงๆ ใน page_kind_ocr เด็ดขาด เพราะตอนรันจริงเรา
# สลับ core.page_kind ให้ชี้มาที่ page_kind_ocr เอง ถ้าข้างในเรียกกลับไปที่
# core.page_kind อีก จะกลายเป็นเรียกตัวเองวนไม่รู้จบ -> RecursionError
# การเก็บอ้างอิงไว้ล่วงหน้าทำให้ชั้น "เข้ม" กับชั้น "สำรอง" แยกออกจากกันจริง
_STRICT_PAGE_KIND = core.page_kind


def page_kind_ocr(text: str) -> tuple[str | None, str]:
    """คืน (ชนิดของงบ, วิธีที่ใช้ตัดสิน) - ไล่จากเข้มไปหลวม เพื่อให้ตรวจสอบย้อนได้

    ต้องรายงานด้วยว่าตัดสินด้วยวิธีไหน ไม่ใช่คืนแค่คำตอบ
    เพราะ "จำแนกได้ด้วยหัวเรื่อง" กับ "เดาจากเนื้อหา" เชื่อถือได้ไม่เท่ากัน
    """
    k = _STRICT_PAGE_KIND(text)
    if k:
        return k, "หัวเรื่อง"

    if not core.has_table(text):
        return None, "-"

    head = core.skeleton("\n".join(text.split("\n")[:core.HEADER_LINES]))
    if core.NOTES_MARK in head:
        return None, "หน้าหมายเหตุ"

    # เทียบหัวเรื่องแบบคล้ายกัน โดยเลื่อนหน้าต่างตามความยาวของหัวเรื่องแต่ละอัน
    best = (0.0, None)
    for kind, title in TITLES:
        n = len(title)
        for i in range(0, max(1, len(head) - n + 1)):
            r = difflib.SequenceMatcher(None, title, head[i:i + n]).ratio()
            if r > best[0]:
                best = (r, kind)
    if best[0] >= FUZZY_CUTOFF:
        return best[1], f"หัวเรื่องคล้าย {best[0]:.2f}"

    sk = core.skeleton(text)
    for kind, marks in CONTENT_MARKS:
        if any(core.skeleton(m) in sk for m in marks):
            return kind, "เนื้อหา"
    return None, "-"


def install_layout_reader(pages: list[str]):
    """สลับ core.page_text / core.n_pages ให้อ่านจากผลของ OCR

    คืนฟังก์ชันสำหรับคืนค่าเดิม เผื่อต้องรันสลับกับ PDF ปกติในเซสชันเดียวกัน
    """
    orig_text, orig_n = core.page_text, core.n_pages

    def page_text(pdf, page):          # เพิกเฉยกับ pdf ทั้งหมด อ่านจากผล OCR
        return pages[page - 1] if 1 <= page <= len(pages) else ""

    def n_pages(pdf):
        return len(pages)

    core.page_text = page_text
    core.n_pages = n_pages

    def restore():
        core.page_text, core.n_pages = orig_text, orig_n
    return restore


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--layout", required=True,
                    help="ไฟล์ .layout.txt ที่ ocr_reader.py สร้าง (หน้าคั่นด้วย \\f)")
    ap.add_argument("--company", required=True,
                    help="ชื่อย่อบริษัท ต้องอยู่ใน extract_fs.KNOWN_COMPANIES")
    ap.add_argument("--statement", choices=list(WANT), default="all")
    ap.add_argument("--strict-pages", action="store_true",
                    help="ใช้เฉพาะหน้าที่จำแนกได้จากหัวเรื่องตรงตัว "
                         "ได้ข้อมูลน้อยลงแต่ค่าผิดน้อยกว่ามาก")
    ap.add_argument("--unit", choices=["baht", "thousand", "million"],
                    help="ระบุหน่วยเงินเอง เมื่อ OCR อ่านบรรทัด (หน่วย: พันบาท) ไม่ออก "
                         "ผิดหน่วย = ผิด 1,000 เท่า ระบบจึงไม่เดาให้")
    ap.add_argument("--aliases")
    ap.add_argument("--out", default="ocr_extracted.csv")
    args = ap.parse_args()

    comp = args.company.upper()
    known = {c.upper() for c in ef.KNOWN_COMPANIES}
    if comp not in known:
        # บทเรียนจากบั๊ก THAI-STANLEY: ชื่อไม่ตรง = เทียบกับเฉลยไม่ติดสักช่อง
        # แล้วแสดงผลเป็น 0.0% ซึ่งดูเหมือนตัวสกัดพัง ทั้งที่แค่ชื่อผิด
        raise SystemExit(f"!! '{comp}' ไม่อยู่ใน KNOWN_COMPANIES\n"
                         f"   ใช้ได้: {', '.join(ef.KNOWN_COMPANIES)}")

    layout = Path(args.layout)
    if not layout.exists():
        raise SystemExit(f"!! ไม่พบ {layout}\n"
                         f"   ต้องรัน ocr_reader.py --pdf ... --out <โฟลเดอร์> ก่อน")
    pages = layout.read_text(encoding="utf-8").split("\f")
    print(f"อ่านผล OCR {len(pages)} หน้า จาก {layout.name}")

    alias_path = core.resolve(args.aliases, core.default_alias_path(), "alias map")
    matcher = core.build_matcher(alias_path)
    print(f"alias map: {Path(alias_path).name} ({len(matcher)} รายการ)")

    # จำแนกหน้าด้วยตัวที่ทน OCR แล้วสลับให้ extract_pdf ใช้ผลนี้
    verdict = {p: page_kind_ocr(t) for p, t in enumerate(pages, 1)}
    print("\n=== จำแนกหน้า ===")
    for p, (k, how) in verdict.items():
        if k:
            print(f"  หน้า {p:>2}: {k:<3} ({how})")
    found = {p: k for p, (k, _) in verdict.items() if k}
    if not found:
        print("  ไม่มีเลย")
        print("\n!! OCR อ่านหัวเรื่องของงบไม่ออก และเนื้อหาก็ไม่พอให้เดา")
        print("   ลองปรับ --psm ของ ocr_reader.py (4 หรือ 11) แล้วทำใหม่")
        print("   อย่าเพิ่งไปแก้ที่อื่น ถ้าจำแนกหน้าไม่ได้ ขั้นต่อไปไม่มีความหมาย")
        return
    weak = [p for p, (k, how) in verdict.items() if k and how != "หัวเรื่อง"]
    if weak:
        print(f"  ({len(weak)} หน้าตัดสินด้วยวิธีสำรอง เชื่อถือได้น้อยกว่า: {weak})")

    restore = install_layout_reader(pages)
    orig_kind = core.page_kind
    if args.strict_pages:
        print("  (โหมดเข้มงวด: ใช้เฉพาะหน้าที่จำแนกจากหัวเรื่องตรงตัว)")
        core.page_kind = lambda t, strict=True: (
            page_kind_ocr(t)[0] if page_kind_ocr(t)[1] == "หัวเรื่อง" else None)
    else:
        core.page_kind = lambda t, strict=True: page_kind_ocr(t)[0]
    try:
        rows = ef.extract_pdf(Path(layout.stem), comp, matcher,
                              max_pages=len(pages), want=WANT[args.statement])
    finally:
        core.page_kind = orig_kind
        restore()

    if not rows:
        print("!! จำแนกหน้าได้ แต่สกัดไม่ได้สักแถว "
              "= อ่านตัวเลขหรือแยกคอลัมน์ไม่ได้ ลองเพิ่ม --dpi ตอน OCR")
        return

    df = pd.DataFrame(rows)
    # ติดป้ายว่ามาจาก OCR ทุกแถว ห้ามให้ปนกับข้อมูลที่วัดความแม่นยำแล้ว
    # ปลายทาง (dashboard/การเทียบข้ามบริษัท) ต้องแยกแสดงได้ว่าอันไหนเชื่อได้แค่ไหน
    df["reader"] = "ocr"
    df["source"] = layout.stem.replace(".layout", "")

    # บันทึกว่าแถวนี้มาจากหน้าที่จำแนกด้วยวิธีไหน
    #
    # หน้าที่ตัดสินด้วย "หัวเรื่องคล้าย" หรือ "เนื้อหา" เชื่อถือได้น้อยกว่าชัดเจน
    # วัดกับ MFEC แล้วพบว่า
    #     ใช้เฉพาะหัวเรื่องตรงตัว   แม่นยำ 50.0%  ค่าผิด 0 ช่อง
    #     เปิดวิธีสำรองด้วย         แม่นยำ 63.1%  ค่าผิด 5 ช่อง
    # ได้ข้อมูลมากขึ้นแต่แลกมาด้วยค่าผิดซึ่ง "ตรวจไม่ได้" ถ้าไม่มีเฉลย
    # จึงไม่เลือกแทนผู้ใช้ แต่ติดป้ายไว้ให้กรองเองได้ที่ปลายทาง
    how_of = {p: how for p, (k, how) in verdict.items() if k}
    df["page_how"] = [how_of.get(int(str(p).split(",")[0]), "?")
                      if str(p).split(",")[0].isdigit() else "?"
                      for p in df.page]
    weak_rows = int((df.page_how != "หัวเรื่อง").sum())
    if weak_rows:
        print(f"  {weak_rows} แถวมาจากหน้าที่จำแนกด้วยวิธีสำรอง (คอลัมน์ page_how)")

    if args.unit:
        df["unit"] = args.unit
    else:
        known_unit = df.loc[df.unit != "unknown", "unit"]
        if len(known_unit):
            df["unit"] = df["unit"].replace("unknown", known_unit.mode().iat[0])
    if (df.unit == "unknown").any():
        print("\n!! หน่วยเงินไม่ทราบ OCR อ่านบรรทัด (หน่วย: ...) ไม่ออก")
        print("   ระบุเองด้วย --unit thousand (หรือ baht / million)")
        print("   ผิดหน่วย = ผิด 1,000 เท่าแบบเงียบๆ ระบบจึงไม่เดาให้")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"\nบันทึก {len(df)} แถว -> {args.out}")
    print(f"  map concept ได้ {int(df.concept.notna().sum())} แถว "
          f"({df.concept.notna().mean() * 100:.0f}%)")
    print(f"  หน่วยเงิน: {sorted(set(df.unit))}")
    print("\nขั้นต่อไป (ห้ามข้าม): python crosscheck.py --extracted " + args.out)
    print("  ถ้าสมการงบดุลไม่ผ่าน = OCR อ่านตัวเลขผิด ห้ามเอาข้อมูลไปใช้ต่อ")


if __name__ == "__main__":
    main()
