#!/usr/bin/env python3
"""
make_gt_template.py - สร้างโครง ground truth ให้กรอกตัวเลขเอง

ใช้งาน:
    python make_gt_template.py --companies PTT CPF AIS --out tech-02_template.csv
    python make_gt_template.py --companies PTT --per-company 30

สิ่งที่สคริปต์นี้กรอกให้: company, unit, page, item, trap
สิ่งที่คุณต้องกรอกเอง: con_cur, con_prev, sep_cur, sep_prev

***สคริปต์นี้จงใจไม่กรอกตัวเลขให้***
ถ้าเอาตัวเลขจากตัวสกัดมาใส่ ground truth มันจะกลายเป็นการเอาโค้ดตรวจตัวเอง
ความแม่นยำที่วัดได้จะเป็น 100% เสมอและไม่มีความหมายอะไรเลย
(HANDOFF ข้อ 9.1: ห้ามแก้ ground truth ให้ตรงกับโค้ด)

ชื่อรายการที่กรอกให้มาจากสองทาง
  1. แถวที่ match ได้ 1.00 -> ใช้ข้อความจาก alias map ซึ่งสะกดถูก
  2. แถวที่ match ไม่ได้    -> ใส่ข้อความดิบที่เพี้ยนมาจาก PDF พร้อมเครื่องหมาย
                              (?) ให้คุณเปิด PDF แล้วพิมพ์ทับให้ถูก

คอลัมน์ trap เป็นการเดาจากรูปร่างของค่า ควรตรวจทานอีกรอบ เพราะ trap คือ
สิ่งที่บอกว่าแถวนั้นทดสอบอะไร ถ้าติดป้ายผิด รายงานแยกตาม trap จะชี้ผิดจุด
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

import fs_core as core
import extract_fs as ef

COLS = core.COLS

# เกณฑ์ตัดสิน trap เรียงตามลำดับความสำคัญ ตัวแรกที่เข้าเงื่อนไขชนะ
# อ้างอิงจากวิธีติดป้ายใน tech-01.csv
LONG_NAME_CHARS = 35
LONG_NUM_DIGITS = 9   # เช่น 2,646,553,095 ของ BBIK


def guess_trap(item: str, vals, unit: str) -> str:
    present = [v for v in vals if v is not None and pd.notna(v)]
    if not present:
        return "dash_empty"

    if item.startswith("รวม"):
        return "subtotal"
    # decimal มาก่อน parentheses เพราะหายากกว่ามาก (มีแค่บรรทัดกำไรต่อหุ้น)
    # ถ้าให้ parentheses ชนะ กำไรต่อหุ้นที่ติดลบจะถูกติดป้ายเป็น parentheses
    # แล้ว trap decimal จะไม่มีตัวอย่างเลยทั้งที่มีข้อมูลอยู่
    if any(v != int(v) for v in present):
        return "decimal"
    if any(v < 0 for v in present):
        return "parentheses"
    if len(item) > LONG_NAME_CHARS:
        return "long_name"
    # long_num วัดจาก "จำนวนหลักที่พิมพ์ในงบ" ไม่ใช่มูลค่าจริง
    # เพราะงบหน่วยพันบาทกับหน่วยบาทพิมพ์ตัวเลขยาวไม่เท่ากันที่มูลค่าเดียวกัน
    if any(len(str(abs(int(v)))) >= LONG_NUM_DIGITS for v in present):
        return "long_num"
    if any(v is None or pd.isna(v) for v in vals):
        return "dash_empty"
    if all(abs(v) < 1000 for v in present):
        return "small_num"
    return "normal"


def canonical_items(alias_csv):
    """โครงพยัญชนะ -> ข้อความ alias ที่สะกดถูก (เอาอันแรกที่เจอ)"""
    a = pd.read_csv(alias_csv, encoding="utf-8-sig").dropna(subset=["alias"])
    out = {}
    for r in a.itertuples():
        out.setdefault(core.skeleton(r.alias), str(r.alias).strip())
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--companies", nargs="+", required=True,
                    help="ชื่อย่อบริษัท ต้องเป็นคำที่อยู่ในชื่อไฟล์ PDF")
    ap.add_argument("--pdf-dir")
    ap.add_argument("--aliases")
    ap.add_argument("--out", default="gt_template.csv")
    ap.add_argument("--per-company", type=int, default=0,
                    help="จำกัดจำนวนแถวต่อบริษัท (0 = ทั้งหมด)")
    ap.add_argument("--mapped-only", action="store_true",
                    help="เอาเฉพาะแถวที่ match concept ได้")
    ap.add_argument("--statement", choices=["bs", "is", "cf", "se"], default="bs")
    args = ap.parse_args()
    want = {"bs": ("BS",), "is": ("IS",), "cf": ("CF",), "se": ("SE",)}[args.statement]

    alias_path = core.resolve(args.aliases, core.default_alias_path(), "alias map")
    pdf_dir = core.resolve(args.pdf_dir, core.default_pdf_dir(), "โฟลเดอร์ PDF")

    matcher = core.build_matcher(alias_path)
    canon = canonical_items(alias_path)
    known = [c.upper() for c in args.companies]

    # ชื่อบริษัทใน template ต้องตรงกับที่ extract_fs ใช้ ไม่งั้นเทียบไม่ติดสักช่อง
    # และจะแสดงผลเป็น "แม่นยำ 0.0%" ซึ่งดูเหมือนตัวสกัดพัง ทั้งที่แค่ชื่อไม่ตรง
    #   --companies Thai-Stanley  ->  เขียน "THAI-STANLEY"
    #   แต่ KNOWN_COMPANIES ใช้    ->  "STANLEY"
    unknown = [c for c in known if c not in {k.upper() for k in ef.KNOWN_COMPANIES}]
    if unknown:
        print(f"!! ชื่อบริษัทไม่อยู่ใน KNOWN_COMPANIES: {', '.join(unknown)}")
        print(f"   ชื่อที่ใช้ได้: {', '.join(ef.KNOWN_COMPANIES)}")
        print("   ถ้าใช้ชื่ออื่น ground truth จะเทียบกับผลสกัดไม่ติดเลย")
        return

    rows = []
    for f in sorted(Path(pdf_dir).glob("*.pdf")):
        comp = ef.company_from_filename(f, known)
        if comp is None:
            continue
        got = ef.extract_pdf(f, comp, matcher, want=want)
        print(f"{f.name:<45} {comp:<8} {len(got):>3} แถว")

        seen = set()
        for r in got:
            vals = [r[c] for c in COLS]
            if all(v is None or pd.isna(v) for v in vals):
                continue

            if r["concept"] and r["match_score"] >= 1.0:
                item = canon.get(core.skeleton(r["item_used"]), r["item_used"])
                needs_fix = ""
            elif args.mapped_only:
                continue
            else:
                item = r["item_used"]
                needs_fix = "(?) ตรวจการสะกดจาก PDF"

            # ต้องมี section ในกุญแจด้วย ไม่งั้นแถวที่ชื่อและตัวเลขในชื่อเหมือนกันเป๊ะ
            # แต่อยู่คนละหัวข้อจะถูกยุบเหลือแถวเดียว แล้ว ground truth จะขาดไปเงียบๆ
            #   ทุนจดทะเบียน        หุ้นสามัญ 76,625,000 หุ้น มูลค่าที่ตราไว้หุ้นละ 5 บาท
            #   ทุนที่ออกและชำระแล้ว หุ้นสามัญ 76,625,000 หุ้น มูลค่าที่ตราไว้หุ้นละ 5 บาท
            key = (comp, r.get("section", ""), core.skeleton(item), core.digits(item))
            if key in seen:
                continue
            seen.add(key)

            rows.append({
                "company": comp,
                "unit": r["unit"],
                "page": r["page"],
                # section = หัวข้อกลุ่มที่ครอบแถวนี้อยู่ ใช้แยกแถวที่ชื่อซ้ำกัน
                # ในหน้าเดียว โดยที่ item ยังบันทึกตามที่งบเขียนจริง
                "section": r.get("section", ""),
                "item": item,
                "con_cur": "", "con_prev": "", "sep_cur": "", "sep_prev": "",
                "trap": guess_trap(item, vals, r["unit"]),
                "_concept_hint": r["concept"] or "",
                "_note": needs_fix,
            })

    if not rows:
        print("ไม่พบข้อมูล ตรวจว่าชื่อย่อบริษัทตรงกับชื่อไฟล์ PDF ไหม")
        return

    df = pd.DataFrame(rows)
    if args.per_company:
        # คัดให้กระจายทุก trap ไม่ใช่เอาแต่แถวบนๆ ของหน้าแรก
        # และเอาแถวที่ match ได้ก่อน เพื่อให้คุณพิมพ์ชื่อรายการเองน้อยที่สุด
        df = df.sort_values("_note", kind="stable")
        n_traps = max(1, df.trap.nunique())
        per_trap = max(2, args.per_company // n_traps + 1)
        df = (df.groupby(["company", "trap"], group_keys=False, sort=False)
                .head(per_trap)
                .groupby("company", group_keys=False, sort=False)
                .head(args.per_company))

    df = df.sort_values(["company", "page"]).reset_index(drop=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False, encoding="utf-8-sig")

    print(f"\nสร้าง {len(df)} แถว -> {args.out}")
    print(df.pivot_table(index="trap", columns="company", values="item",
                         aggfunc="count", fill_value=0).to_string())
    n_fix = int((df._note != "").sum())
    if n_fix:
        print(f"\n{n_fix} แถวมีเครื่องหมาย (?) ในคอลัมน์ _note "
              f"ต้องเปิด PDF พิมพ์ชื่อรายการทับให้ถูก")
    print("\nขั้นตอนถัดไป")
    print("  1. เปิดไฟล์ กรอก con_cur / con_prev / sep_cur / sep_prev จาก PDF")
    print("     ช่องที่งบเขียน '-' ให้เว้นว่าง | ค่าติดลบ (ในวงเล็บ) ใส่เลขลบ")
    print("  2. ลบคอลัมน์ _concept_hint กับ _note ทิ้งเมื่อกรอกเสร็จ")
    print("  3. python validate_ground_truth.py --gt <ไฟล์นี้>")
    print("     ต้องผ่านสมการงบดุลก่อน ถึงจะเอาไปวัดผลได้")


if __name__ == "__main__":
    main()
