#!/usr/bin/env python3
"""
crosscheck.py - ตรวจผลสกัดด้วยสมการงบดุล โดยไม่ใช้ ground truth เลย

ใช้งาน:
    python extract_fs.py --all-companies --out all.csv
    python crosscheck.py --extracted all.csv

ทำไมต้องมี: ground truth มีแค่ 4 บริษัท ถ้าวัดผลจาก 4 บริษัทนั้นอย่างเดียว
จะแยกไม่ออกว่าโค้ด "ทำงานได้จริง" หรือแค่ "ถูกปรับจนพอดีกับ 4 บริษัทนี้"

สมการงบดุลเป็นตัวตรวจที่ไม่ต้องมีเฉลย ใช้กับบริษัทไหนก็ได้
ถ้าบริษัทที่ไม่เคยใช้ตอนพัฒนายังผ่านสมการ แปลว่าโค้ดน่าจะทั่วไปจริง

ข้อจำกัด: สมการงบดุลจับได้แค่ความไม่สอดคล้องกันเอง จับไม่ได้ถ้าค่าผิดทั้งคอลัมน์
ไปในทางเดียวกัน (เช่น หยิบมาจากงบกระแสเงินสดทั้งหน้า) จึงใช้แทน ground truth
ไม่ได้ ใช้เป็นตัวเสริมเท่านั้น
"""
from __future__ import annotations

import argparse

import pandas as pd

import fs_core as core

COLS = core.COLS

EQUATIONS = core.EQUATIONS


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extracted", default="all.csv")
    ap.add_argument("--tol", type=float, default=1.0, help="ผลต่างที่ยอมรับได้")
    args = ap.parse_args()

    ex = pd.read_csv(args.extracted, encoding="utf-8-sig")
    # ตรรกะการเลือกแถวอยู่ที่ fs_core ที่เดียว ห้ามเขียนซ้ำที่นี่
    lut, ex = core.build_equation_lut(ex)

    tot_ok = tot_bad = tot_skip = 0
    print(f"{'บริษัท':<38} {'ผ่าน':>5} {'ผิด':>5} {'ข้าม':>5}  แถวที่ map ได้")
    for comp in sorted(ex.company.unique()):
        ok, bad, skip, fails = core.check_equations(
            lut, [comp], EQUATIONS, tol=args.tol, verbose=False)
        n = int((ex.company == comp).sum())
        print(f"{comp:<38} {ok:>5} {bad:>5} {skip:>5}  {n}")
        for m in fails:
            print(f"    x {m}")
        tot_ok += ok; tot_bad += bad; tot_skip += skip

    print(f"\nรวม: ผ่าน {tot_ok} | ผิด {tot_bad} | ข้าม {tot_skip}")
    print("ข้าม = ยังไม่มี concept ที่ต้องใช้ในสมการ (มักเป็นงบธนาคาร "
          "หรือไฟล์ที่ต้อง OCR) ไม่ใช่ข้อผิดพลาด")


if __name__ == "__main__":
    main()
