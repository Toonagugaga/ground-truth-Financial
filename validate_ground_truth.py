#!/usr/bin/env python3
"""
validate_ground_truth.py - ตรวจว่า ground truth พิมพ์ถูกไหม

ใช้งาน:
    python validate_ground_truth.py
    python validate_ground_truth.py --gt tech-01.csv --aliases account_aliases_v3.csv

ตรวจ 4 อย่าง
    1. โครงสร้างคอลัมน์ครบไหม
    2. ความครอบคลุมกับดัก (trap) - กับดักไหนยังไม่มีตัวอย่าง
    3. สมการงบดุล - บวกแล้วตรงไหม (จับเลขที่พิมพ์ผิด)
    4. รายการที่ยังไม่มีใน alias map

***ห้ามแก้ ground truth ให้ตรงกับโค้ด***
ground truth ต้องบันทึกสิ่งที่เอกสารเขียนจริง ถ้าคำไม่ตรง
ให้เพิ่มบรรทัดใหม่ใน account_aliases_v3.csv แทน
"""
from __future__ import annotations

import argparse

import pandas as pd

import fs_core as core

COLS = core.COLS

REQUIRED_COLS = ["company", "unit", "page", "item"] + COLS + ["trap"]

# trap = "แถวนี้ทดสอบความยากแบบไหนในการอ่าน PDF" ไม่ใช่ประเภทรายการบัญชี
#   same_name = ชื่อรายการซ้ำกันเป๊ะในหน้าเดียว แยกได้ด้วยหัวข้อกลุ่มอย่างเดียว
#               (การแบ่งปันกำไร vs การแบ่งปันกำไรเบ็ดเสร็จรวม,
#                ต้นทุนงานบริการจ่ายล่วงหน้า ใต้หมุนเวียน vs ไม่หมุนเวียน)
ALL_TRAPS = {"normal", "parentheses", "dash_empty", "subtotal", "note_col",
             "long_name", "decimal", "small_num", "long_num", "thai_digit",
             "same_name"}

EQUATIONS = core.EQUATIONS


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gt")
    ap.add_argument("--aliases")
    args = ap.parse_args()

    gt_dir = core.default_gt_dir()
    gt_path = core.resolve(args.gt, (gt_dir / "tech-01.csv") if gt_dir else None,
                           "ground truth")
    alias_path = core.resolve(args.aliases, core.default_alias_path(), "alias map")

    df = core.read_gt(gt_path)

    # ---------- 1. โครงสร้าง ----------
    missing_cols = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing_cols:
        print(f"!! ขาดคอลัมน์: {missing_cols}")

    matcher = core.build_matcher(alias_path)
    df["concept"] = core.gt_concepts(df, matcher)

    print(f"แถวทั้งหมด {len(df)} | บริษัท {df.company.nunique()}: "
          f"{list(df.company.unique())}")
    print(f"ค่าที่กรอกจริง {int(df[COLS].notna().sum().sum())} จาก {len(df)*4} ช่อง")
    if "unit" in df.columns:
        u = df.groupby("company")["unit"].agg(lambda s: s.unique()[0])
        print(f"หน่วย: {dict(u)}")
        mixed = df.groupby("company")["unit"].nunique()
        if (mixed > 1).any():
            print(f"!! บริษัทที่มีหน่วยปนกัน: {list(mixed[mixed>1].index)}")
    print()

    # ---------- 2. ความครอบคลุมกับดัก ----------
    print("=== ความครอบคลุมกับดัก ===")
    print(df.pivot_table(index="trap", columns="company", values="item",
                         aggfunc="count", fill_value=0).to_string())
    miss = ALL_TRAPS - set(df.trap.unique())
    print(f"\nกับดักที่ยังไม่มีเลย: {sorted(miss) if miss else 'ครบ'}\n")

    # ---------- 3. สมการงบดุล ----------
    print("=== ตรวจเลขคณิต (ผ่าน concept) ===")
    # งบกระแสเงินสด: บรรทัดปรับปรุงที่อยู่หลังยอดสุทธิต้องแยก concept
    # ไม่งั้นสมการจะบวกซ้ำ (ดู fs_core.EQUATIONS_CF)
    eq = df.copy().reset_index(drop=True)
    eq["concept"] = core.apply_cf_position(eq)

    lookup = {}
    for r in eq.itertuples():
        if isinstance(r.concept, str):
            for c in COLS:
                v = getattr(r, c)
                if pd.notna(v):
                    # lut-ok: สร้างจาก ground truth ไม่ใช่ผลสกัด
                    # เฉลยมีแถวเดียวต่อ concept ต่อบริษัทอยู่แล้วโดยนิยาม
                    # (ข้อ 1 ของไฟล์นี้ตรวจข้อนั้นอยู่) จึงไม่มีอะไรให้เลือก
                    lookup[(r.company, r.concept, c)] = float(v)  # lut-ok:

    ok, bad, skip, _ = core.check_equations(
        lookup, list(eq.company.unique()), EQUATIONS)
    print(f"  ผ่าน {ok} | ไม่ผ่าน {bad} | ข้าม {skip}")


    # ---------- 4. รายการที่ยังไม่มีใน alias map ----------
    unmapped = df[df.concept.isna()]
    print(f"\n=== รายการที่ยังไม่มีใน alias map ({len(unmapped)} แถว) ===")
    if len(unmapped) == 0:
        print("  ครบทุกแถว")
    else:
        print("  (ไม่ใช่ข้อผิดพลาด แค่ยังไม่ถูกใช้ตรวจเลขคณิตและไม่ถูกประเมิน)")
        for comp, it in unmapped[["company", "item"]].itertuples(index=False):
            print(f"    - [{comp}] {it}")

    # ---------- 5. concept ที่ซ้ำภายในบริษัทเดียวกัน ----------
    dup = (df.dropna(subset=["concept"])
             .groupby(["company", "concept"]).size())
    dup = dup[dup > 1]
    if len(dup):
        print(f"\n!! concept ที่ซ้ำในบริษัทเดียวกัน ({len(dup)} รายการ)")
        print("   ตัวสกัดเก็บได้ concept ละ 1 แถวต่อบริษัท แถวที่ซ้ำจะถูกนับผิด")
        print(dup.to_string())


if __name__ == "__main__":
    main()
