#!/usr/bin/env python3
"""
selfcheck.py - ทดสอบตรรกะข้อความล้วน ไม่ต้องใช้ PDF หรือ pdftotext

รันตัวนี้ก่อนเสมอ ใช้เวลาไม่กี่วินาที ถ้าตัวนี้ไม่ผ่าน ไม่ต้องเสียเวลารัน extract

ใช้งาน:
    python selfcheck.py

ตรวจ 3 อย่าง
    1. unit test ของ skeleton / clean_label / strip_suffix / parse_line
    2. alias map ครอบคลุม ground truth ครบ 95/95 แถวหรือยัง
    3. alias ที่ชนกัน (โครงพยัญชนะเดียวกันแต่ concept ต่างกัน)
"""
from __future__ import annotations

import sys
from collections import defaultdict

import pandas as pd

import fs_core as core

PASS, FAIL = [], []


def check(name, got, want):
    if got == want:
        PASS.append(name)
    else:
        FAIL.append(f"{name}\n      ได้  : {got!r}\n      ต้องได้: {want!r}")


# --------------------------------------------------------------------------
# 1. unit test
# --------------------------------------------------------------------------

def test_text():
    # skeleton ต้องทนข้อความเพี้ยนทุกแบบที่เจอจริง
    # หมายเหตุ: "า" (U+0E32) เป็นสระ ไม่ใช่พยัญชนะ จึงไม่อยู่ในโครง
    check("skeleton ปกติ", core.skeleton("ลูกหนี้การค้า"), "ลกหนกรค")
    check("skeleton IIG เพี้ยน", core.skeleton("ลกู หน้กีารคา้"), "ลกหนกรค")
    check("skeleton BBIK วรรณยุกต์เป็นเลข",
          core.skeleton("อืน8"), core.skeleton("อื่น"))
    check("skeleton ตัดตัวเลขในชื่อ",
          core.skeleton("หุ้นสามัญ 441,453,555 หุ้น มูลค่าหุ้นละ 1 บาท"),
          core.skeleton("หุ้นสามัญ หุ้น มูลค่าหุ้นละ บาท"))

    # clean_label ตัดเลขที่เกาะพยัญชนะ แต่ห้ามตัดเลขที่เป็นค่าจริง
    check("clean_label ตัดวรรณยุกต์เพี้ยน",
          core.clean_label("องค์ประกอบอืน8 ของส่วน"), "องค์ประกอบอืน ของส่วน")
    check("clean_label ไม่แตะเลขมีคอมม่า",
          core.clean_label("หุ้นสามัญ 441,453,555 หุ้น"),
          "หุ้นสามัญ 441,453,555 หุ้น")

    # strip_suffix ต้องตัดเฉพาะที่มีขีดคั่น
    check("strip_suffix - สุทธิ",
          core.strip_suffix("อุปกรณ์ - สุทธิ"), "อุปกรณ์")
    # ห้ามตัด "- หมุนเวียน" / "- ไม่หมุนเวียน" เพราะเป็นคนละรายการจริง
    # และมักอยู่ในหน้าเดียวกัน ถ้าตัดจะได้ concept เดียวกันแล้วหยิบผิดแถว
    check("strip_suffix ห้ามตัด - หมุนเวียน",
          core.strip_suffix("สินทรัพย์ที่เกิดจากสัญญา - หมุนเวียน"),
          "สินทรัพย์ที่เกิดจากสัญญา - หมุนเวียน")
    check("strip_suffix ห้ามตัด - ไม่หมุนเวียน",
          core.strip_suffix("รายได้รับล่วงหน้า - ไม่หมุนเวียน"),
          "รายได้รับล่วงหน้า - ไม่หมุนเวียน")
    check("digits ตัดคอมม่าและอักษร",
          core.digits("หุ้นสามัญ 707,500,000 หุ้น มูลค่าหุ้นละ 1 บาท"), "7075000001")
    check("digits แยกทุนจดทะเบียนกับทุนที่ออก",
          core.digits("หุ้นสามัญ 700,021,420 หุ้น มูลค่าหุ้นละ 1 บาท") !=
          core.digits("หุ้นสามัญ 707,500,000 หุ้น มูลค่าหุ้นละ 1 บาท"), True)
    # อันตราย: ห้ามตัดคำที่ไม่มีขีด ไม่งั้น "รวมสินทรัพย์หมุนเวียน"
    # จะกลายเป็น "รวมสินทรัพย์" = TOTAL_ASSETS
    check("strip_suffix ห้ามตัดถ้าไม่มีขีด",
          core.strip_suffix("รวมสินทรัพย์หมุนเวียน"), "รวมสินทรัพย์หมุนเวียน")
    check("strip_suffix ห้ามตัดถ้าไม่มีขีด (ไม่หมุนเวียน)",
          core.strip_suffix("รวมสินทรัพย์ไม่หมุนเวียน"),
          "รวมสินทรัพย์ไม่หมุนเวียน")


def test_skeleton_constants():
    """ค่าคงที่ที่เป็น "โครงพยัญชนะ" ต้องไม่มีสระหรืออักขระอื่นปนเลย

    บั๊กจริงที่เคยเกิด: NOTES_MARK ถูกพิมพ์ด้วยมือเป็น "หมายหตปรกอบ"
    ซึ่งมี "า" ค้างอยู่ skeleton() ตัดสระทิ้งหมด สตริงนั้นจึงไม่มีวันตรงกับอะไร
    -> ตัวกันหน้าหมายเหตุเป็น dead code มาตลอดโดยไม่มีใครรู้
    เทสต์นี้กันไม่ให้เกิดซ้ำกับค่าคงที่ตัวอื่น
    """
    pairs = [
        ("BS_TITLE", core.BS_TITLE, "งบฐานะการเงิน"),
        ("IS_TITLE", core.IS_TITLE, "งบกำไรขาดทุน"),
        ("CF_TITLE", core.CF_TITLE, "งบกระแสเงินสด"),
        ("SE_TITLE", core.SE_TITLE, "การเปลี่ยนแปลงส่วนของ"),
        ("NOTES_MARK", core.NOTES_MARK, "หมายเหตุประกอบ"),
    ]
    for name, val, phrase in pairs:
        check(f"{name} เป็นโครงพยัญชนะล้วน", core.skeleton(val), val)
        check(f"{name} ตรงกับ skeleton ของวลีต้นทาง", val, core.skeleton(phrase))
    for tail in core._TAIL_SKELETONS:
        check("_TAIL_SKELETONS เป็นโครงพยัญชนะล้วน", core.skeleton(tail), tail)


def test_equation_lut_single_source():
    """ตัวสร้าง lut สำหรับตรวจสมการต้องมีที่เดียว และต้องเลือกงบหลักก่อนเสมอ

    บั๊กจริงที่เคยเกิด: dashboard เขียน dedup ของตัวเองที่ไม่มีตรรกะ "งบหลักชนะ"
    ผลคือ concept ที่โผล่หลายงบได้ค่าจากงบที่มาทีหลังแบบสุ่ม
    มีคีย์ค่าไม่ตรงกัน 519 จุด และรายงานสมการไม่ผ่าน 1 ข้อ
    ทั้งที่ crosscheck.py บอกว่าผ่านหมด
    """
    import pandas as pd
    rows = [
        # concept เดียวกัน อยู่สองงบ ค่าไม่เหมือนกัน
        dict(company="X", statement="BS", concept="A", concept_eq="A",
             match_score=1.0, con_cur=100.0, con_prev=None,
             sep_cur=None, sep_prev=None),
        dict(company="X", statement="SE", concept="A", concept_eq="A",
             match_score=1.0, con_cur=-7.0, con_prev=None,
             sep_cur=None, sep_prev=None),
        # คะแนนเท่ากัน ต่างกันที่จำนวนช่องที่มีค่า -> ตัวที่ครบกว่าต้องชนะ
        dict(company="X", statement="BS", concept="B", concept_eq="B",
             match_score=1.0, con_cur=None, con_prev=None,
             sep_cur=None, sep_prev=4.0),
        dict(company="X", statement="BS", concept="B", concept_eq="B",
             match_score=1.0, con_cur=50.0, con_prev=50.0,
             sep_cur=50.0, sep_prev=50.0),
    ]
    lut, _ = core.build_equation_lut(pd.DataFrame(rows))
    check("งบหลักชนะงบส่วนของเจ้าของ", lut.get(("X", "A", "con_cur")), 100.0)
    check("แถวที่มีค่าครบกว่าชนะ", lut.get(("X", "B", "con_cur")), 50.0)

    # สลับลำดับแถวแล้วผลต้องเหมือนเดิม (ไม่ขึ้นกับลำดับในไฟล์)
    lut2, _ = core.build_equation_lut(pd.DataFrame(rows[::-1]))
    check("ผลไม่ขึ้นกับลำดับแถว", lut2, lut)


def test_no_duplicate_row_selection_logic():
    """ตรรกะ "เลือกแถวไหนชนะ" ต้องอยู่ใน fs_core ที่เดียวเท่านั้น

    บั๊กจริงที่เคยเกิด: crosscheck / dashboard / evaluate เขียน dedup ของตัวเอง
    แล้วเดินห่างกัน จนผลไม่ตรงกัน 519 คีย์ และรายงานสมการไม่ผ่านคนละจำนวน
    เทสต์นี้จับตั้งแต่ตอนเขียนโค้ด ไม่ต้องรอให้ตัวเลขขัดกันแล้วค่อยมาไล่หา

    จับเฉพาะการ "เลือกแถวชนะด้วยคีย์ที่มี company" เท่านั้น
    การใช้ drop_duplicates เพื่อเทียบว่าค่าซ้ำกันไหม (extract_fs ตอนเตือน
    concept ซ้ำ) เป็นคนละเรื่อง ไม่ควรถูกจับ
    """
    import re
    from pathlib import Path
    here = Path(__file__).resolve().parent
    pat = re.compile(r'drop_duplicates\s*\(\s*\[?\s*["\']company')
    offenders = []
    for f in sorted(here.glob("*.py")):
        if f.name in ("fs_core.py", "selfcheck.py"):
            continue
        for i, ln in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(ln) and not ln.strip().startswith("#"):
                offenders.append(f"{f.name}:{i}")
    check("ไม่มีการเลือกแถวชนะนอก fs_core", offenders, [])


def test_row_selection_consumers_agree():
    """ผู้ใช้ผลสกัดทุกรายต้องได้ "ค่า" ตรงกัน แม้ชื่อ concept จะตั้งใจให้ต่าง

    evaluate ใช้ concept (ตามที่งบเขียน) crosscheck ใช้ concept_eq (ตามตำแหน่ง)
    ชุดแถวที่ชนะจึงไม่จำเป็นต้องเหมือนกัน เพราะคีย์การจัดกลุ่มต่างกัน
    แต่คีย์ไหนที่ทั้งคู่ผลิตออกมาได้ ค่าต้องตรงกันเสมอ
    ถ้าไม่ตรง แปลว่าเกณฑ์ตัดสินเดินห่างกันแล้ว (เคยต่างกัน 519 คีย์)

    หมายเหตุ: เทสต์เวอร์ชันแรกของผมเทียบ "ชุดแถวที่ชนะ" ซึ่งผิด
    เพราะสองโหมดจัดกลุ่มด้วยคีย์คนละตัว แถวที่ชนะย่อมต่างกันเป็นธรรมดา
    """
    import pandas as pd
    from pathlib import Path
    p = Path(__file__).resolve().parent / "all.csv"
    if not p.exists():
        return
    ex = pd.read_csv(p, encoding="utf-8-sig")

    def lut_of(**kw):
        d = core.pick_rows(ex, **kw)
        return {(r.company, r.concept, c): float(getattr(r, c))
                for r in d.itertuples() for c in core.COLS
                if pd.notna(getattr(r, c))}

    a = lut_of(use_concept_eq=True, se_last=True)   # crosscheck
    b = lut_of(use_concept_eq=False)                # evaluate
    bad = [k for k in set(a) & set(b) if abs(a[k] - b[k]) > 0.5]
    check("ค่าที่สองโหมดเลือกมาต้องตรงกัน", bad, [])


def test_parse_line():
    """ตัดป้ายกำกับที่ token แรกที่ตกในคอลัมน์ค่า ไม่ใช่ token แรกของบรรทัด"""
    # จำลองบรรทัดที่มีตัวเลขอยู่ในชื่อรายการ (HANDOFF ข้อ 7.2)
    #                0         1         2         3         4         5
    #                0123456789012345678901234567890123456789012345678901234
    ln = "หุ้นสามัญ 441,453,555 หุ้น มูลค่าหุ้นละ 1 บาท        441,454     441,454"
    cols = [len(ln) - len("     441,454"), len(ln)]
    got = core.parse_line(ln, cols)
    check("parse_line เก็บชื่อเต็มแม้มีเลขในชื่อ",
          got is not None and "มูลค่าหุ้นละ" in got[0], True)
    check("parse_line ได้ค่าถูก", got and got[1][-1], 441454.0)

    # วรรณยุกต์เพี้ยนเป็นเลขกลางชื่อ ต้องไม่ตัดชื่อทิ้ง (HANDOFF บั๊ก #4)
    ln2 = "องค์ประกอบอืน8 ของส่วนของผู้ถือหุ้น                (2,036,264)  (2,481,614)"
    cols2 = [ln2.index("(2,036,264)") + len("(2,036,264)"), len(ln2)]
    got2 = core.parse_line(ln2, cols2)
    check("parse_line ไม่ตัดชื่อที่ตำแหน่งวรรณยุกต์เพี้ยน",
          got2 is not None and "ผู้ถือหุ้น" in got2[0], True)
    check("parse_line อ่านวงเล็บเป็นค่าติดลบ",
          got2 and got2[1][0], -2036264.0)

    # แถวหัวตารางที่ค่าทุกช่องเป็นปี ต้องถูกกรองทิ้ง
    ln3 = "หมายเหตุ                                              2569        2568"
    cols3 = [ln3.index("2569") + 4, len(ln3)]
    check("parse_line กรองแถวหัวตาราง", core.parse_line(ln3, cols3), None)


def test_to_number():
    check("to_number วงเล็บ = ติดลบ", core.to_number("(2,036,264)"), -2036264.0)
    check("to_number ขีด = ไม่มีค่า", core.to_number("-"), None)
    check("to_number ทศนิยม", core.to_number("0.50"), 0.5)


# --------------------------------------------------------------------------
# 2 + 3. ตรวจ alias map กับ ground truth
# --------------------------------------------------------------------------

def test_alias_coverage():
    gt_dir = core.default_gt_dir()
    if gt_dir is None:
        print("\n!! หา ground truth ไม่เจอ ข้ามการตรวจความครอบคลุม")
        return

    alias_path = core.default_alias_path()
    if alias_path is None:
        print("\n!! หา alias map ไม่เจอ")
        return

    # alias ที่โครงพยัญชนะชนกันแต่ concept ต่างกัน = ระเบิดเวลา
    a = pd.read_csv(alias_path, encoding="utf-8-sig").dropna(subset=["alias", "concept"])
    by_skel = defaultdict(set)
    for r in a.itertuples():
        # alias ที่มีตัวเลขในชื่อถูกแยกด้วยตัวเลขด้วย จึงไม่ถือว่าชนกัน
        by_skel[(core.skeleton(r.alias), core.digits(r.alias))].add(
            str(r.concept).strip())
    clashes = {k: v for k, v in by_skel.items() if len(v) > 1}

    print(f"\n=== alias map: {alias_path.name} ===")
    print(f"  {len(a)} บรรทัด | {a.concept.nunique()} concept "
          f"| {len(by_skel)} โครงพยัญชนะไม่ซ้ำ")
    if clashes:
        print(f"  !! โครงพยัญชนะชนกัน {len(clashes)} รายการ:")
        for k, v in clashes.items():
            print(f"     {k} -> {sorted(v)}")
    else:
        print("  ไม่มีโครงพยัญชนะชนกัน")

    matcher = core.build_matcher(alias_path)
    gt = pd.read_csv(gt_dir / "tech-01.csv", encoding="utf-8-sig")
    gt.columns = [c.strip() for c in gt.columns]
    gt["concept"] = core.gt_concepts(gt, matcher)

    n_missing = int(gt.concept.isna().sum())
    print(f"\n=== ความครอบคลุม ground truth ===")
    print(f"  {len(gt)-n_missing}/{len(gt)} แถว map เป็น concept ได้")
    if n_missing:
        print(f"  ยังขาด {n_missing} แถว:")
        for comp, item in gt[gt.concept.isna()][["company", "item"]].itertuples(index=False):
            print(f"    - [{comp}] {item}")

    # concept ซ้ำในบริษัทเดียวกัน -> ตัวสกัดเก็บได้แค่แถวเดียว จะนับผิด
    dup = gt.dropna(subset=["concept"]).groupby(["company", "concept"]).size()
    dup = dup[dup > 1]
    if len(dup):
        print(f"\n  !! concept ซ้ำในบริษัทเดียวกัน {len(dup)} รายการ:")
        print(dup.to_string())
    else:
        print("  ไม่มี concept ซ้ำในบริษัทเดียวกัน")

    return n_missing == 0 and not clashes and len(dup) == 0


def main():
    test_text()
    test_to_number()
    test_parse_line()
    test_skeleton_constants()
    test_equation_lut_single_source()
    test_no_duplicate_row_selection_logic()
    test_row_selection_consumers_agree()

    print(f"=== unit test ===")
    print(f"  ผ่าน {len(PASS)} | ไม่ผ่าน {len(FAIL)}")
    for f in FAIL:
        print(f"  x {f}")

    coverage_ok = test_alias_coverage()

    print()
    if FAIL:
        print("สรุป: unit test ไม่ผ่าน ต้องแก้ fs_core.py ก่อน")
        sys.exit(1)
    if coverage_ok is False:
        print("สรุป: unit test ผ่านหมด แต่ alias map ยังไม่ครอบคลุม ground truth")
        sys.exit(2)
    print("สรุป: ผ่านทั้งหมด รัน extract_fs.py ต่อได้")


if __name__ == "__main__":
    main()
