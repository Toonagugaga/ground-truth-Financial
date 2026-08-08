#!/usr/bin/env python3
"""
evaluate_se_matrix.py - วัดผลตัวอ่าน SE แบบเต็มตารางกับ ground truth

ทำไมเฉลยชุดนี้ถึงเล็กแค่ 133 ช่องแต่พอ
--------------------------------------------------------------------------
สมการ "ยอดต้นงวด + รายการเคลื่อนไหว = ยอดปลายงวด" ใน se_matrix.py ตรวจได้
เฉพาะความสอดคล้องภายใน ถ้าคอลัมน์ทั้งคอลัมน์เลื่อนไปหนึ่งช่อง ทั้งยอดต้นงวด
รายการเคลื่อนไหว และยอดปลายงวดจะเลื่อนพร้อมกันหมด สมการก็ยังผ่านสวยงาม
(พิสูจน์แล้วตอนเจอบั๊กนับคอลัมน์เกิน 4 หน้า ซึ่งสมการผ่าน 100% ตลอด)

เฉลยชุดนี้จึงออกแบบมาเพื่อ "จับคอลัมน์เลื่อน" โดยเฉพาะ
  - ตำแหน่งคอลัมน์คำนวณทีละหน้า ถ้าหน้าไหนเลื่อน มันเลื่อนทั้งหน้า
  - จึงกรอกแค่ "แถวยอดคงเหลือต้นงวด" แถวเดียวต่อหน้า แต่ต้องครบทุกคอลัมน์
  - ส่วนช่องอื่นในหน้านั้นมีสมการตามแถวคุมอยู่แล้ว

การทดสอบพลังในการตรวจจับ (--power) จำลองว่าถ้าเลื่อนคอลัมน์ไป 1 ช่อง
เฉลยชุดนี้จะจับได้กี่หน้า ถ้าไม่ใช่ 100% แปลว่าเฉลยยังไม่มีพลังพอ
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

GT_DEFAULT = "tech-14_se_matrix_template.csv"
EX_DEFAULT = "se_m.csv"
TOL = 1.0


def same(a, b) -> bool:
    if pd.isna(a) and pd.isna(b):
        return True
    if pd.isna(a) or pd.isna(b):
        return False
    return abs(float(a) - float(b)) <= TOL


def opening_rows(ex: pd.DataFrame) -> pd.DataFrame:
    """แถวยอดคงเหลือแถวแรกของแต่ละหน้า = แถวที่ ground truth บันทึกไว้"""
    b = ex[ex.kind == "balance"].copy()
    b["minrow"] = b.groupby(["company", "page"]).row_index.transform("min")
    return b[b.row_index == b["minrow"]]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gt", default=GT_DEFAULT)
    ap.add_argument("--extracted", default=EX_DEFAULT)
    ap.add_argument("--power", action="store_true",
                    help="ทดสอบว่าเฉลยชุดนี้จับคอลัมน์เลื่อนได้จริงไหม")
    args = ap.parse_args()

    gt = pd.read_csv(args.gt, encoding="utf-8-sig")
    ex = pd.read_csv(args.extracted, encoding="utf-8-sig")

    m = gt.merge(opening_rows(ex)[["company", "page", "col_index", "value"]],
                 on=["company", "page", "col_index"], how="left",
                 suffixes=("_gt", "_ex"))
    m["ok"] = [same(a, b) for a, b in zip(m.value_gt, m.value_ex)]

    n, ok = len(m), int(m.ok.sum())
    print("=== ความแม่นยำของตัวอ่าน SE เต็มตาราง ===")
    print(f"  เทียบ {n} ช่อง | ถูก {ok} | แม่นยำ {ok / n * 100:.1f}%")
    print()
    t = m.groupby(["company", "page"]).ok.agg(ถูก="sum", ทั้งหมด="size")
    print(t.to_string())

    bad = m[~m.ok]
    if len(bad):
        print("\n=== ช่องที่ไม่ตรง ===")
        cols = ["company", "page", "col_index", "value_gt", "value_ex"]
        if "หัวคอลัมน์ที่ตัวสกัดอ่านได้" in bad.columns:
            cols.insert(3, "หัวคอลัมน์ที่ตัวสกัดอ่านได้")
        print(bad[cols].to_string(index=False))
        # ถ้าทั้งหน้าผิดหมด แปลว่าคอลัมน์เลื่อน ไม่ใช่อ่านเลขผิดทีละช่อง
        for (co, pg), g in bad.groupby(["company", "page"]):
            total = int(t.loc[(co, pg), "ทั้งหมด"])
            if len(g) == total:
                print(f"  !! {co} หน้า {pg} ผิดทั้งหน้า ({total}/{total}) "
                      f"= คอลัมน์เลื่อน ไม่ใช่อ่านเลขผิดรายช่อง")

    if args.power:
        print("\n=== ทดสอบพลังในการตรวจจับ (จำลองคอลัมน์เลื่อน 1 ช่อง) ===")
        caught = tot = 0
        for (co, pg), g in gt.groupby(["company", "page"]):
            g = g.sort_values("col_index")
            tot += 1
            diff = sum(1 for a, b in zip(g.value, g.value.shift(1))
                       if not same(a, b))
            if diff:
                caught += 1
            print(f"  {co:<6} หน้า {pg:>2}: จะพบไม่ตรง {diff}/{len(g)} ช่อง")
        print(f"  จับได้ {caught}/{tot} หน้า"
              + ("" if caught == tot else "  << เฉลยยังมีพลังไม่พอ"))


if __name__ == "__main__":
    main()
