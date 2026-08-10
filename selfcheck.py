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
from pathlib import Path

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
    # ชื่อบริษัทที่มีช่องว่างหัวท้ายต้องไม่ทำให้ตัวตรวจสมการบอดเงียบๆ
    #
    # pick_rows ทำ .strip() ให้ชื่อบริษัทตอนสร้างตารางค้นหา แต่ผู้เรียก
    # check_equations ส่งชื่อดิบเข้ามา คีย์จึงไม่ตรงกันสักตัว ผลคือ
    #   ผ่าน 0 | ผิด 0 | ข้าม 32
    # ซึ่งหน้าตาเหมือน "ตรวจแล้วไม่เจอปัญหา" ทั้งที่แปลว่า "ตรวจไม่ได้เลย"
    #
    # เจอจากไฟล์ชื่อ "FINANCIAL_STATEMENTS_SRS .pdf" (มีช่องว่างก่อนนามสกุล)
    # ซึ่งชื่อบริษัทถูกเดาจากชื่อไฟล์ -> ติดช่องว่างมาด้วย
    _row = {"company": "  ACME ", "concept": "TOTAL_ASSETS",
            "concept_eq": "TOTAL_ASSETS", "statement": "BS", "item": "x",
            "match_score": 1.0, "con_cur": 3.0, "con_prev": None,
            "sep_cur": None, "sep_prev": None}
    _rows = [dict(_row),
             dict(_row, concept="TOTAL_CURRENT_ASSETS",
                  concept_eq="TOTAL_CURRENT_ASSETS", con_cur=1.0),
             dict(_row, concept="TOTAL_NONCURRENT_ASSETS",
                  concept_eq="TOTAL_NONCURRENT_ASSETS", con_cur=2.0)]
    _lut, _ = core.build_equation_lut(pd.DataFrame(_rows))
    _ok, _bad, _skip, _ = core.check_equations(
        _lut, ["  ACME "], core.EQUATIONS_BS, verbose=False)
    check("ชื่อบริษัทมีช่องว่างหัวท้ายแล้วสมการยังรันได้", _ok > 0, True)
    check("ชื่อบริษัทมีช่องว่างหัวท้ายแล้วสมการไม่ผิด", _bad, 0)

    # ต้องบอกได้ว่าเครื่องนี้ขาดโปรแกรมอะไร ก่อนจะไปโทษว่าไฟล์เป็น PDF สแกน
    #
    # บั๊กจริง: รัน dashboard บน Windows ที่ไม่ได้ติดตั้ง poppler
    # subprocess โยน FileNotFoundError [WinError 2] ออกมา แล้วหน้าจอสรุปว่า
    # "อ่านไม่ได้เลยสักบรรทัด อาจเป็น PDF สแกน" ทั้งที่ไฟล์นั้นมี text layer ปกติ
    # คือวินิจฉัยผิดสาเหตุ แล้วชี้ให้ผู้ใช้ไปแก้ผิดจุด
    check("missing_poppler คืน list", isinstance(core.missing_poppler(), list), True)
    check("เครื่องนี้มี poppler ครบ", core.missing_poppler(), [])

    # page_text / n_pages ต้องคืนชนิดข้อมูลที่สัญญาไว้เสมอ ไม่ว่า subprocess
    # จะทำอะไรก็ตาม
    #
    # บั๊กจริงบน Windows: subprocess.run(...).stdout คืน None ทำให้
    # "".join(...) ระเบิดเป็น TypeError กลางหน้าจอผู้ใช้ แทนที่จะได้ข้อความ
    # บอกว่าอ่านไฟล์ไม่ได้ ซึ่งเป็นสิ่งที่ระบบควรบอก
    # บน Linux stdout เป็น "" อยู่แล้ว เทสต์ที่เรียกเฉยๆ จึงผ่านโดยไม่ได้ทดสอบอะไร
    # ต้องจำลองสภาพของ Windows ให้ subprocess คืน stdout=None ตรงๆ
    # ไม่งั้นเป็นเทสต์ที่ให้ความมั่นใจปลอม
    import subprocess as _sp
    import types as _types

    _orig_run = _sp.run
    try:
        _sp.run = lambda *a, **k: _types.SimpleNamespace(
            stdout=None, stderr=None, returncode=0)
        _t = core.page_text(Path("x.pdf"), 1)
        _n = core.n_pages(Path("x.pdf"))
    finally:
        _sp.run = _orig_run
    check("page_text คืน str แม้ subprocess ให้ stdout=None", isinstance(_t, str), True)
    check("n_pages คืน int แม้ subprocess ให้ stdout=None", isinstance(_n, int), True)

    # ต้องระบุ encoding="utf-8" ตอนอ่านผลจากโปรแกรมภายนอกเสมอ ห้ามพึ่ง locale
    #
    # บั๊กจริงบน Windows ภาษาไทย: pdftotext ส่งออกมาเป็น UTF-8 แต่ subprocess
    # ที่ไม่ระบุ encoding จะ decode ด้วย locale ของเครื่อง (cp874) ผลคือ
    #     "บริษัท สิริซอฟต์ จำกัด"  ->  "เธ?เธฃเธดเธฉเธ—เธฑ เธชเธดเธฃเธด..."
    # ข้อความเพี้ยนหมด page_kind หาหัวเรื่องงบไม่เจอ สกัดได้ 0 แถว
    #
    # ร้ายที่สุดคือ mojibake ของ cp874 ให้ "พยัญชนะไทย" ออกมามากกว่าข้อความจริง
    # (1,263 ตัว เทียบกับของจริง 527 ตัว) ตัวตรวจว่าเป็น PDF สแกนจึงบอกว่า
    # "ไฟล์นี้มี text layer ปกติ" ทั้งที่อ่านออกมาเป็นขยะทั้งหน้า
    #
    # จำลองด้วยการให้ subprocess ปลอม decode ตาม encoding ที่ถูกส่งเข้ามาจริง
    # ถ้าไม่ส่ง encoding มาก็ตกไปใช้ cp874 เหมือนเครื่อง Windows
    _thai = "ลูกหนี้การค้าและลูกหนี้หมุนเวียนอื่น"

    def _fake_run(cmd, capture_output=None, text=None, encoding=None, errors=None):
        enc = encoding or "cp874"
        return _types.SimpleNamespace(
            stdout=_thai.encode("utf-8").decode(enc, errors="replace"),
            stderr="", returncode=0)

    _orig_run = _sp.run
    try:
        _sp.run = _fake_run
        _got = core._run_text(["pdftotext"])
    finally:
        _sp.run = _orig_run
    check("อ่านผลโปรแกรมภายนอกเป็น utf-8 ไม่ตาม locale ของเครื่อง", _got, _thai)

    # เทสต์ข้างบนจับได้แค่ _run_text ตัวเดียว แต่บั๊กนี้อยู่ทุกที่ที่เรียก
    # subprocess แล้วอ่านข้อความกลับมา ต้องจับทั้งคลาสของบั๊ก ไม่ใช่จุดเดียว
    # (บทเรียนจากข้อ 27.2 — เทสต์ที่จับได้แค่วิธีเขียนแบบหนึ่ง กันบั๊กเดิม
    #  กลับมาในรูปแบบใหม่ไม่ได้)
    from pathlib import Path as _P

    def _calls(src, needle="subprocess.run("):
        """ตัดข้อความของการเรียกฟังก์ชันออกมาโดยนับวงเล็บให้สมดุล

        ใช้ regex ไม่ได้ เพราะการเรียกจริงคร่อมหลายบรรทัดและมีวงเล็บซ้อน
        (รอบแรกเขียนด้วย regex แล้วจับ ocr_reader.py ไม่เจอทั้งสองจุด
         = เทสต์ที่บอกว่าผ่านทั้งที่บั๊กยังอยู่)
        """
        out, i = [], src.find(needle)
        while i != -1:
            j, depth = i + len(needle) - 1, 0
            while j < len(src):
                if src[j] == "(":
                    depth += 1
                elif src[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out.append((i, src[i:j + 1]))
            i = src.find(needle, j + 1)
        return out

    _here = _P(__file__).resolve().parent
    _bad_enc = []
    for _f in sorted(_here.glob("*.py")):
        if _f.name == "selfcheck.py":
            continue
        _src = _f.read_text(encoding="utf-8")
        for _pos, _call in _calls(_src):
            if "text=True" in _call and "encoding=" not in _call:
                _bad_enc.append(f"{_f.name}:{_src[:_pos].count(chr(10)) + 1}")
    check("ไม่มี subprocess.run(text=True) ที่ไม่ระบุ encoding", _bad_enc, [])

    # คอลัมน์เดียวต้องมีชนิดข้อมูลเดียว
    #
    # บั๊กจริง: extract_fs ใส่ page เป็น int ปกติ แต่แถวงบส่วนของเจ้าของที่ถูก
    # รวมจากสองหน้าจะกลายเป็น str "5,6" ผลคือคอลัมน์เดียวมีทั้ง int และ str
    # pyarrow แปลงไม่ได้ Streamlit โยน ArrowTypeError ขึ้น log ทุกครั้งที่แสดงตาราง
    #
    # ที่สำคัญกว่าคือมันเป็นสัญญาณว่า "หน้า" มีสองความหมายปนกันในคอลัมน์เดียว
    # ซึ่งเป็นเรื่องที่ผู้ใช้ข้อมูลปลายทางต้องมาเดาเอง
    # ต้องมีทั้งแถวที่ถูกรวมและแถวที่ไม่ถูกรวมอยู่ในชุดเดียวกัน
    # ถ้าทดสอบแต่แถวที่ถูกรวม เทสต์จะผ่านทั้งที่บั๊กยังอยู่ (เขียนผิดมาแล้วรอบหนึ่ง)
    import extract_fs as ef

    def _row(concept, page, val):
        return {"concept": concept, "item_used": "ก", "page": page,
                "section": "", **{c: val for c in core.COLS}}

    _merged = ef.merge_equity_rows([
        _row("X", 5, None), _row("X", 6, 1.0),      # สองแถวนี้จะถูกรวม -> "5,6"
        _row("Y", 7, 2.0),                          # แถวนี้ไม่ถูกรวม -> ยังเป็น int
    ])
    check("page ในชุดเดียวกันต้องเป็นชนิดเดียว",
          sorted({type(r["page"]).__name__ for r in _merged}), ["str"])

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

    # ช่องโหว่ของเทสต์ข้างบน: มันจับแค่ drop_duplicates
    # แต่ตารางค้นหาสำหรับสมการสร้างด้วย for-loop ธรรมดาก็ได้ ซึ่งไม่มี
    # ตรรกะเลือกแถวเลย กลายเป็น "ตัวสุดท้ายที่เจอชนะ"
    #
    # เจอจริงใน evaluate.py — สร้าง lookup[(company, concept, col)] เอง
    # ผลคือรายงานสมการไม่ผ่าน 26 ข้อ ขณะที่ crosscheck.py บอกผ่านหมด
    # ทั้งสองตัวรันสมการชุดเดียวกันบนไฟล์เดียวกัน (ข้าม 149 เท่ากันเป๊ะ)
    # ต่างกันแค่วิธีสร้างตารางค้นหา
    # ยกเว้นได้ด้วยการเขียน "# lut-ok:" พร้อมเหตุผลไว้ท้ายบรรทัด
    # กรณีที่ยกเว้นได้จริงคือตารางที่สร้างจาก ground truth ไม่ใช่จากผลสกัด
    # เพราะ ground truth มีแถวเดียวต่อ concept ต่อบริษัทอยู่แล้วโดยนิยาม
    # จึงไม่มีอะไรให้ "เลือกว่าแถวไหนชนะ"
    pat2 = re.compile(r'\[\s*\(\s*r\.company\s*,\s*r\.concept')
    offenders2 = []
    for f in sorted(here.glob("*.py")):
        if f.name in ("fs_core.py", "selfcheck.py"):
            continue
        for i, ln in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if (pat2.search(ln) and not ln.strip().startswith("#")
                    and "lut-ok:" not in ln):
                offenders2.append(f"{f.name}:{i}")
    check("ไม่มีการสร้างตารางค้นหาสมการเองนอก fs_core", offenders2, [])


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
