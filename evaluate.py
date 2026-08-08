#!/usr/bin/env python3
"""
evaluate.py - เทียบผลที่สกัดได้กับ ground truth แล้วรายงานความแม่นยำ

ใช้งาน:
    python evaluate.py
    python evaluate.py --gt tech-01.csv --extracted extracted.csv \
                       --aliases account_aliases_v3.csv
    python evaluate.py --report eval_report.csv

รายงาน 4 ส่วน
    1. ความครอบคลุม - ground truth กี่แถวถูกประเมินจริง (สำคัญที่สุด)
    2. ภาพรวม        - แม่นกี่เปอร์เซ็นต์
    3. แยกตาม trap   - พังที่กับดักไหน
    4. แยกตามบริษัท  - บริษัทไหนมีปัญหา

การเทียบใช้ concept ไม่ใช่ชื่อรายการ เพราะชื่อที่สกัดมาจะเพี้ยนเสมอ

ข้อควรระวัง (HANDOFF ข้อ 9.4): ถ้า ground truth 95 แถวแต่ประเมินแค่ 43 แถว
ตัวเลขความแม่นยำที่ได้ไม่ใช่ความแม่นยำจริง สคริปต์นี้จึงรายงานความครอบคลุม
ก่อนเสมอ และแสดง "ความแม่นยำถ่วงด้วยความครอบคลุม" กำกับไว้ด้วย
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

import fs_core as core

COLS = core.COLS

# สมการที่ใช้ตรวจว่าผลสกัดสอดคล้องกันเอง
EQUATIONS = core.EQUATIONS


def check_equations(lookup, companies, label=""):
    ok, bad, skip, _ = core.check_equations(lookup, companies)
    print(f"  {label}ผ่าน {ok} | ไม่ผ่าน {bad} | ข้าม {skip}")
    return ok, bad, skip


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gt", help="ground truth CSV")
    ap.add_argument("--extracted", help="ผลสกัด CSV")
    ap.add_argument("--aliases", help="ตารางเทียบคำศัพท์")
    ap.add_argument("--report", help="บันทึกผลเทียบรายช่องลง CSV")
    ap.add_argument("--show", type=int, default=30, help="แสดงรายการที่ผิดกี่แถว")
    args = ap.parse_args()

    gt_dir = core.default_gt_dir()
    gt_path = core.resolve(args.gt, (gt_dir / "tech-01.csv") if gt_dir else None,
                           "ground truth")
    alias_path = core.resolve(args.aliases, core.default_alias_path(), "alias map")
    ex_path = core.resolve(args.extracted,
                           core.default_out_dir() / "extracted.csv", "ผลสกัด")

    gt = core.read_gt(gt_path)
    ex = pd.read_csv(ex_path, encoding="utf-8-sig")
    ex["company"] = ex["company"].map(lambda v: "TRUE" if v is True else ("FALSE" if v is False else str(v).strip()))

    matcher = core.build_matcher(alias_path)
    gt["concept"] = core.gt_concepts(gt, matcher)

    # ---------- 1. ความครอบคลุม ----------
    unmapped = gt[gt.concept.isna()]
    n_total, n_mapped = len(gt), len(gt) - len(unmapped)
    coverage = n_mapped / n_total if n_total else 0
    print("=== ความครอบคลุม ===")
    print(f"  ground truth {n_total} แถว | map เป็น concept ได้ {n_mapped} "
          f"({coverage*100:.1f}%)")
    if len(unmapped):
        print(f"  ยังไม่ถูกประเมิน {len(unmapped)} แถว "
              f"(ต้องเพิ่มใน alias map ห้ามแก้ ground truth):")
        for comp, item in unmapped[["company", "item"]].itertuples(index=False):
            print(f"    - [{comp}] {item}")

    gt = gt.dropna(subset=["concept"])
    if gt.empty:
        print("\nไม่มีแถวที่ประเมินได้ หยุดการทำงาน")
        return

    # ---------- เตรียมผลสกัด ----------
    # ตรรกะเลือกแถวอยู่ที่ core.pick_rows ที่เดียว ห้ามเขียนซ้ำที่นี่
    #
    # ที่นี่ใช้ use_concept_eq=False เพราะ ground truth บันทึกชื่อรายการ
    # ตามที่งบเขียน จึงไม่รู้จัก concept ที่ลงท้าย _AFTER ซึ่งมาจากตำแหน่งบรรทัด
    # (ใช้เฉพาะตอนตรวจสมการ) — ความต่างนี้ตั้งใจ ไม่ใช่ความบังเอิญ
    ex = core.pick_rows(ex, keys=("company", "concept"), use_concept_eq=False)
    lut = {(r.company, r.concept): r for r in ex.itertuples()}

    recs = []
    for r in gt.itertuples():
        got = lut.get((r.company, r.concept))
        for col in COLS:
            want = getattr(r, col)
            have = getattr(got, col) if got is not None else None
            want_na = pd.isna(want)
            have_na = have is None or pd.isna(have)
            if want_na and have_na:
                status = "ok_empty"
            elif want_na != have_na:
                status = "miss" if got is None else (
                    "wrong_empty" if have_na else "extra_value")
            elif abs(float(want) - float(have)) < 0.005:
                status = "ok"
            else:
                status = "wrong"
            recs.append({"company": r.company, "concept": r.concept,
                         "item": r.item, "trap": r.trap, "col": col,
                         "status": status, "want": want, "have": have})

    d = pd.DataFrame(recs)
    d["ok"] = d.status.isin(["ok", "ok_empty"])
    good = int(d.ok.sum())
    acc = good / len(d)

    # ---------- 2. ภาพรวม ----------
    print("\n=== ภาพรวม ===")
    print(f"  ช่องที่เทียบ {len(d)} | ถูก {good} | แม่นยำ {acc*100:.1f}%")
    print(f"  แม่นยำถ่วงด้วยความครอบคลุม: {acc*coverage*100:.1f}% "
          f"(= {acc*100:.1f}% x {coverage*100:.1f}%)")
    print(d.status.value_counts().to_string())

    # ---------- 3. แยกตาม trap ----------
    print("\n=== แยกตาม trap ===")
    tt = d.groupby("trap")["ok"].agg(["sum", "count"])
    tt["pct"] = (tt["sum"] / tt["count"] * 100).round(1)
    print(tt.sort_values("pct").to_string())

    # ---------- 4. แยกตามบริษัท ----------
    print("\n=== แยกตามบริษัท ===")
    cc = d.groupby("company")["ok"].agg(["sum", "count"])
    cc["pct"] = (cc["sum"] / cc["count"] * 100).round(1)
    print(cc.sort_values("pct").to_string())

    # ---------- 5. สมการงบดุลจากผลสกัดล้วน ----------
    print("\n=== ตรวจสมการงบดุลจากผลสกัดล้วน (ไม่ดู ground truth) ===")
    lookup = {}
    for r in ex.itertuples():
        for c in COLS:
            v = getattr(r, c)
            if pd.notna(v):
                lookup[(r.company, r.concept, c)] = float(v)
    check_equations(lookup, sorted(ex.company.unique()))

    # ---------- รายการที่ผิด ----------
    bad = d[~d.ok]
    if len(bad):
        print(f"\n=== รายการที่ผิด ({len(bad)} ช่อง, แสดง {args.show} แรก) ===")
        print(bad.head(args.show)[["company", "concept", "col", "trap",
                                   "want", "have", "status"]]
              .to_string(index=False))

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        d.to_csv(args.report, index=False, encoding="utf-8-sig")
        print(f"\nบันทึกผลรายช่อง -> {args.report}")


if __name__ == "__main__":
    main()
