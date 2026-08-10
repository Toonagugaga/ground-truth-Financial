#!/usr/bin/env python3
"""
extract_fs.py - สกัดงบฐานะการเงินจาก PDF ไทย ออกมาเป็น CSV

ใช้งาน:
    python extract_fs.py                              # ใช้ path อัตโนมัติ
    python extract_fs.py --pdf-dir DIR --aliases account_aliases_v3.csv
    python extract_fs.py --pdf-dir file.pdf --company GABLE
    python extract_fs.py --debug-company BBIK         # ดูบรรทัดที่ map ไม่ติด

หลักการออกแบบ (รายละเอียดอยู่ใน fs_core.py):
  1. ใช้ pdftotext -layout เท่านั้น ห้ามใช้ pdfplumber
  2. แยกคอลัมน์ด้วยตำแหน่งตัวอักษร ไม่ใช่การนับ token
  3. กรองตำแหน่งที่โผล่น้อยกว่า 3 ครั้งก่อนจัดกลุ่ม กัน "คอลัมน์ปลอม"
  4. ตัดคอลัมน์หมายเหตุอัตโนมัติ แล้วเหลือ 4 คอลัมน์เสมอ
  5. ตัดป้ายกำกับที่ token ตัวแรกที่ตกในคอลัมน์ค่า ไม่ใช่ token ตัวแรกของบรรทัด
  6. รวมบรรทัดที่ชื่อรายการถูกตัดขึ้นบรรทัดใหม่
  7. เทียบ concept ด้วยโครงพยัญชนะ + difflib (เกณฑ์ 0.90)
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

import fs_core as core

# บริษัทที่มีอยู่ใน ground truth ปัจจุบัน
# ชื่อย่อบริษัทที่รู้จัก ใช้ตั้งชื่อ company ให้ตรงกันทุกไฟล์
# ถ้าไม่มีในนี้จะใช้ชื่อไฟล์แทน ซึ่งทำให้เทียบกับ ground truth ไม่ติด
KNOWN_COMPANIES = ["MFEC", "GABLE", "BBIK", "IIG",
                   "AIS", "CPF", "PTT", "PRG", "SAT", "TRUE",
                   "KBANK", "SCB", "CPALL", "HUMANICA", "STANLEY"]

# จำนวนบรรทัดก่อนหน้าที่จำไว้เผื่อชื่อรายการถูกตัดขึ้นบรรทัดใหม่
PREFIX_MEMORY = 3


def company_from_filename(path: Path, known=KNOWN_COMPANIES) -> str | None:
    """เดาชื่อย่อบริษัทจากชื่อไฟล์

    ต้องเทียบแบบมีขอบเขตคำ ไม่ใช่ substring เฉยๆ ไม่งั้น
    "Thai-Stanley_Q1_June69" จะถูกอ่านว่าเป็น AIS (จาก "th-AIS-tanley")
    """
    stem = path.stem.upper()
    parts = set(re.split(r"[^A-Z0-9]+", stem)) - {""}
    for c in known:
        cu = c.upper()
        if cu in parts:
            return c
        # รองรับชื่อที่ติดกับคำอื่นด้วยขีดหรือขีดล่าง เช่น FINANCIAL_STATEMENTS_MFEC
        if re.search(rf"(?<![A-Z0-9]){re.escape(cu)}(?![A-Z0-9])", stem):
            return c
    return None


def looks_like_prefix(line: str) -> bool:
    """บรรทัดนี้อาจเป็นท่อนหน้าของชื่อรายการที่ถูกตัดขึ้นบรรทัดใหม่หรือไม่

    ต้องไม่กรองบรรทัดที่มีตัวเลขทิ้ง เพราะชื่อรายการเองก็มีตัวเลขได้
        "หุ้นสามัญจำนวน 123,376,607 หุ้น"
        "  มูลค่าตราไว้หุ้นละ 0.50 บาท        61,688,304  ..."
    ท่อนหน้ามีเลขจำนวนหุ้นอยู่ ถ้ากรองทิ้งจะต่อบรรทัดไม่ได้เลย
    บรรทัดที่มีตัวเลข "ตกในคอลัมน์ค่า" ถูกคัดออกไปแล้วตั้งแต่ parse_line

    หัวข้อกลุ่ม ("สินทรัพย์หมุนเวียน") ก็ผ่านเงื่อนไขนี้ด้วย ซึ่งไม่เป็นไร
    เพราะการตัดสินใจจริงอยู่ที่คะแนน match ใน core.match_concept
    """
    return len(core.skeleton(line)) >= 2


MIN_LABEL_SKELETON = 3

# ยอดรวมของแต่ละกิจกรรมในงบกระแสเงินสด ที่บางบริษัทเขียนซ้ำสองครั้ง
CF_SUBTOTALS = {"CF_OPERATING", "CF_INVESTING", "CF_FINANCING"}

# concept ที่มีความหมายเฉพาะในงบกระแสเงินสด ถ้าเจอในงบอื่นแปลว่าจับคู่ผิด
#
# TRUE เขียน "ผลต่างจากการแปลงค่างบการเงิน" ทั้งในงบกำไรขาดทุน (เป็นรายการ
# กำไรขาดทุนเบ็ดเสร็จอื่น) และงบกระแสเงินสด (เป็นรายการปรับปรุงเงินสด)
# ข้อความเหมือนกันเป๊ะ ถ้าปล่อยให้ตัวจากงบกำไรขาดทุนชนะตอน dedup
# สมการงบกระแสเงินสดจะเอา 863 ไปบวกทั้งที่เป็นคนละเรื่อง
# ชื่อรายการเดียวกันที่มีความหมายคนละอย่างเมื่ออยู่ในงบส่วนของเจ้าของ
#
#   Thai-Stanley เขียน "เงินปันผลค้างจ่าย" ทั้งในงบฐานะการเงินและงบส่วนของเจ้าของ
#     งบฐานะการเงิน  1,306,436,427   = ยอดคงเหลือค้างจ่าย ณ สิ้นงวด
#     งบส่วนของเจ้าของ -1,302,625,000 = เงินปันผลที่ประกาศจ่าย ทำให้ส่วนของเจ้าของลดลง
#   คนละตัวเลข คนละความหมาย แต่ชื่อเหมือนกันเป๊ะ แยกด้วยชื่ออย่างเดียวไม่ได้
#   ต้องแยกด้วย "อยู่ในงบไหน" ซึ่งเป็นข้อมูลที่ตัวสกัดรู้อยู่แล้ว
#
# **ห้าม remap ไปเป็น SE_DIVIDEND** เพราะงบส่วนของเจ้าของหน้าเดียวกันมีสองบรรทัด
#     เงินปันผลจ่าย        (919,500,000)    = จ่ายจริงในงวด      -> SE_DIVIDEND
#     เงินปันผลค้างจ่าย 16 (1,302,625,000)  = ประกาศแล้วยังไม่จ่าย
# ถ้ายุบเป็น concept เดียวกัน dedup จะทิ้งไปหนึ่งแถวโดยไม่มีอะไรเตือน
# (ลองแล้วเกิดจริง เจอเพราะไล่ดูผลลัพธ์ ไม่ใช่เพราะเทสต์ฟ้อง)
#
# เจอบั๊กนี้จาก dashboard ไม่ใช่จากสมการหรือเฉลย เพราะ STANLEY ไม่มีเฉลยงบนี้
# และไม่มีสมการไหนใช้ concept นี้ -> การมองด้วยตายังจำเป็น
SE_REMAP = {"DIVIDEND_PAYABLE": "SE_DIVIDEND_DECLARED"}

CF_ONLY_CONCEPTS = set(CF_SUBTOTALS) | {
    "CASH_NET_CHANGE", "CASH_NET_CHANGE_BEFORE_FX", "CASH_BEGIN", "CASH_END",
    "CF_OPERATING_BEFORE_ITEMS", "CF_TRANSLATION", "CASH_FX_EFFECT",
    "CASH_RECLASS_HELD_FOR_SALE", "BANK_OVERDRAFT_BEGIN",
}


# งบฐานะการเงินอยู่ต้นเล่ม แต่งบกระแสเงินสดอยู่ท้าย บางเล่มถึงหน้า 15
# ถ้าตั้งไว้ต่ำเกินจะอ่านไม่ครบโดยไม่มี error (CPF หน้า 12-15 หายไปทั้งงบ)
MAX_PAGES = 20


def extract_pdf(pdf: Path, company: str, matcher, max_pages=MAX_PAGES, debug=False,
                want=("BS",)):
    rows = []
    total = core.n_pages(pdf)
    if total == 0:
        print(f"  !! อ่าน {pdf.name} ไม่ได้ (pdfinfo ไม่คืนจำนวนหน้า)")
        return rows

    # อ่านทุกหน้าครั้งเดียวแล้วคัดว่าหน้าไหนเป็นงบอะไร
    # ต้องคัดด้วยหัวเรื่องของหน้า ไม่ใช่แค่มีคำว่าสินทรัพย์/หนี้สิน
    # เพราะงบกระแสเงินสดมีชื่อรายการซ้ำกันเป๊ะๆ แต่ค่าเป็นผลต่างระหว่างงวด
    texts = {p: core.page_text(pdf, p) for p in range(1, min(total, max_pages) + 1)}
    kinds = {p: core.page_kind(t) for p, t in texts.items()}
    if not any(kinds.values()):
        # ทางถอย: ไฟล์นี้ไม่มีหัวเรื่องที่อ่านออก ใช้เกณฑ์หลวมแบบเดิม
        kinds = {p: core.page_kind(t, strict=False) for p, t in texts.items()}
        if any(kinds.values()):
            print(f"  ! {pdf.name}: หาหัวเรื่องของงบไม่เจอ ใช้เกณฑ์หลวม -> "
                  f"หน้า {[p for p, k in kinds.items() if k]}")

    pages = [(p, k) for p, k in kinds.items() if k in want]
    if debug:
        print(f"    หน้าที่เลือก: {pages}")

    # งบแสดงการเปลี่ยนแปลงส่วนของเจ้าของใช้ตัวอ่านคนละตัว เพราะเป็นตาราง 2 มิติ
    # หน้าแรก = งบรวม หน้าถัดมา = งบเฉพาะกิจการ (ลำดับมาตรฐานของงบไทย)
    se_pages = [p for p, k in kinds.items() if k == "SE"]
    # หน้าที่มีแต่คอลัมน์ยอดรวมลอยอยู่ ให้ผูกกับหน้างบที่อยู่ก่อนหน้ามัน
    spills = {}
    if "SE" in want:
        for p in se_pages:
            for q in range(p + 1, min(p + 4, max(texts) + 1)):
                if kinds.get(q) or q in se_pages:
                    break
                v = equity_spill_values(texts.get(q, ""))
                if v:
                    spills[p] = v
                    break
    if "SE" in want and se_pages:
        # จัดกลุ่มหน้าตามหัวเรื่อง (งบรวม / งบเฉพาะกิจการ)
        # ถ้าหน้าไหนไม่เขียนไว้ (IIG) ให้ตกกลับไปใช้ลำดับหน้าแบบเดิม
        sides = {p: equity_page_side(texts[p]) for p in se_pages}
        # ต้องระบุได้ครบทุกหน้า และต้องมีทั้งสองฝั่ง ไม่งั้นถอยไปใช้ลำดับหน้า
        # (BBIK ไม่เขียนหัวเรื่องฝั่งงบรวม ถ้าเชื่อครึ่งๆ จะทิ้งหน้างบรวมทั้งหน้า)
        ok = (all(sides.values())
              and {"con", "sep"} <= set(sides.values())) or len(se_pages) == 1
        if not ok:
            sides = {p: ("con" if i == 0 else "sep")
                     for i, p in enumerate(se_pages)}

        se_rows = []
        for side in ("con", "sep"):
            group = [p for p in se_pages if sides.get(p) == side]
            if not group:
                continue
            # กลุ่มเดียวอาจกินหลายหน้า ต้องต่อลำดับแถวข้ามหน้าก่อนตัดบล็อกปี
            seq = []
            for p in group[:-1]:
                extract_equity_page(pdf, company, matcher, p, texts[p],
                                    side == "con", collect=seq,
                                    spill=spills.get(p))
            se_rows += extract_equity_page(pdf, company, matcher, group[-1],
                                           texts[group[-1]], side == "con",
                                           collect=None,
                                           spill=spills.get(group[-1])) if len(group) == 1 else []
            if len(group) > 1:
                last = group[-1]
                extract_equity_page(pdf, company, matcher, last, texts[last],
                                    side == "con", collect=seq,
                                    spill=spills.get(last))
                se_rows += build_equity_rows(pdf, company, matcher,
                                             ",".join(map(str, group)),
                                             texts[group[0]],
                                             side == "con", seq)
        rows += merge_equity_rows(se_rows)

    for p, kind in pages:
        if kind == "SE":
            continue
        text = texts[p]
        unit = core.detect_unit(text)
        lines = text.split("\n")
        cols = core.find_columns(lines)
        if len(cols) < 2:
            continue
        cols = core.drop_note_column(cols, lines)

        prefixes: list[str] = []
        section = None
        # งบกระแสเงินสด: บรรทัดปรับปรุงที่โผล่ "หลัง" ยอดเงินสดเพิ่ม(ลด)สุทธิ
        # ยังไม่ถูกรวมอยู่ในยอดนั้น ต้องแยก concept ไม่งั้นสมการจะบวกซ้ำ
        # (ดูคำอธิบายที่ fs_core.EQUATIONS_CF)
        seen_net_change = False
        seen_subtotal: dict[str, int] = {}
        for ln in lines:
            if not ln.strip():
                prefixes.clear()
                continue

            parsed = core.parse_line(ln, cols)

            if parsed is None:
                # บรรทัดนี้เป็นหัวข้อกลุ่มหรือเปล่า (สินทรัพย์ไม่หมุนเวียน ฯลฯ)
                found = core.detect_section(ln)
                if found:
                    section = found
                    prefixes.clear()
                    continue
                # ไม่มีค่าในบรรทัดนี้ -> เก็บไว้เผื่อเป็นท่อนหน้าของชื่อถัดไป
                if looks_like_prefix(ln):
                    prefixes.append(core.clean_label(ln))
                    del prefixes[:-PREFIX_MEMORY]
                else:
                    prefixes.clear()
                continue

            label, vals = parsed
            if all(v is None for v in vals):
                prefixes.clear()
                continue

            # บรรทัดที่มีแต่ตัวเลข ไม่มีชื่อ และไม่มีท่อนหน้าค้างอยู่
            # = บรรทัดยอดรวมซ้ำที่งบไม่ใส่ชื่อ เช่นใต้ "การแบ่งปันกำไร" ของ PTT
            #     ส่วนที่เป็นของผู้ถือหุ้นของบริษัทฯ      46,730,678,342
            #     ส่วนที่เป็นของผู้มีส่วนได้เสียฯ         27,551,375,061
            #                                            74,282,053,403   <- บรรทัดนี้
            # ถ้าปล่อยไว้ label_candidates จะเอา "หัวข้อกลุ่ม" มาเป็นชื่อรายการแทน
            # แล้วได้ concept ผิดพร้อมคะแนน 1.00 ซึ่งดูน่าเชื่อถือทั้งที่มั่ว
            if not core.skeleton(label) and not prefixes:
                continue

            concept, score, used_label = core.match_concept(
                label, matcher, prefixes=prefixes, section=section
            )
            # ตัดแถวที่ป้ายกำกับสั้นเกินจะเป็นชื่อรายการ ตัดหลังต่อบรรทัดแล้ว
            if len(core.skeleton(used_label)) < MIN_LABEL_SKELETON:
                prefixes.clear()
                continue
            # concept_eq = concept ที่ใช้ "เฉพาะตอนตรวจสมการ"
            # แยกจาก concept เพราะตำแหน่งของบรรทัดไม่ได้เปลี่ยน "ว่ามันคืออะไร"
            # แค่เปลี่ยน "ว่ามันถูกรวมในยอดสุทธิแล้วหรือยัง"
            # ground truth บันทึกตามที่งบเขียนจึงรู้จักแค่ concept ธรรมดา
            # concept ของงบกระแสเงินสดที่ไปโผล่ในงบอื่น = จับคู่ผิด ปล่อยว่างดีกว่าเดา
            if kind != "CF" and concept in CF_ONLY_CONCEPTS:
                concept = None

            concept_eq = concept
            if kind == "CF" and concept in CF_SUBTOTALS:
                # บางบริษัทเขียนชื่อยอดรวมกิจกรรมซ้ำสองครั้งด้วยข้อความเดียวกัน
                #   เงินสดสุทธิใช้ไปในกิจกรรมดำเนินงาน   (235,135)  <- ก่อนจ่ายภาษี
                #     จ่ายภาษีเงินได้                     (1,229)
                #     รับคืนภาษีเงินได้                      709
                #   เงินสดสุทธิใช้ไปในกิจกรรมดำเนินงาน   (235,655)  <- ยอดสุทธิจริง
                # แยกได้ด้วยตำแหน่งอย่างเดียว ตัวหลังคือตัวที่สมการต้องใช้
                if concept in seen_subtotal:
                    prev_i = seen_subtotal[concept]
                    rows[prev_i]["concept"] = concept + "_BEFORE_ITEMS"
                    rows[prev_i]["concept_eq"] = concept + "_BEFORE_ITEMS"
                seen_subtotal[concept] = len(rows)
            if kind == "CF" and concept:
                if seen_net_change and concept in core.CF_ADJUSTMENTS:
                    concept_eq = concept + "_AFTER"
                elif concept == "CASH_NET_CHANGE":
                    seen_net_change = True

            if debug and concept is None:
                print(f"    [ไม่ติด {score:.2f}] {used_label[:70]}")

            vals = (list(vals) + [None] * 4)[:4]
            rows.append({
                "company": company,
                "source": pdf.name,
                "statement": kind,
                "unit": unit,
                # เก็บเป็น str เสมอ เพราะแถวงบส่วนของเจ้าของที่รวมจากสองหน้า
                # จะกลายเป็น "5,6" ถ้าปล่อยให้แถวปกติเป็น int คอลัมน์เดียวจะมี
                # สองชนิด -> pyarrow แปลงไม่ได้ Streamlit โยน ArrowTypeError
                # และผู้ใช้ข้อมูลปลายทางต้องมาเดาเองว่าช่องไหนเป็นชนิดอะไร
                "page": str(p),
                "section": section or "",
                "item_raw": label,
                "item_used": used_label,
                "concept": concept,
                "concept_eq": concept_eq,
                "match_score": round(score, 3),
                "con_cur": vals[0],
                "con_prev": vals[1],
                "sep_cur": vals[2],
                "sep_prev": vals[3],
            })
            prefixes.clear()

    return rows


# --------------------------------------------------------------------------
# งบแสดงการเปลี่ยนแปลงส่วนของเจ้าของ (ทางเลือก A: เก็บเฉพาะคอลัมน์ยอดรวม)
# --------------------------------------------------------------------------
#
# งบนี้เป็นตาราง 2 มิติ ไม่เหมือนอีกสามงบ
#     แถว    = รายการเคลื่อนไหว (ยอดต้นงวด, กำไร, เงินปันผล, ยอดปลายงวด)
#     คอลัมน์ = องค์ประกอบของส่วนของเจ้าของ (10+ คอลัมน์)
# และซ้ำสองบล็อกสำหรับสองปี
#
# โครงข้อมูลปลายทางมี 4 ช่อง จึงเก็บได้แค่คอลัมน์ขวาสุด 3 คอลัมน์
#     ...  ส่วนของผู้ถือหุ้นบริษัทฯ | ส่วนได้เสียที่ไม่มีอำนาจควบคุม | รวมทั้งสิ้น
# ซึ่งเป็นส่วนที่มีค่าที่สุด เพราะทำให้ตรวจข้ามงบได้ว่า
#     ยอดปลายงวดในงบนี้ = รวมส่วนของผู้ถือหุ้นในงบฐานะการเงิน
#
# การเก็บทั้งตารางเป็นงานคนละขนาด ต้องเปลี่ยนรูปข้อมูลเป็น long format
# และต้องประกอบชื่อคอลัมน์จากหัวตารางที่พาดแนวตั้ง 4 บรรทัด

# ตัดบล็อกปีด้วย "มกราคม" ไม่ใช่ "วันที่ 1" เพราะ skeleton ตัดตัวเลขทิ้ง
# "ณ วันที่ 1 มกราคม" กับ "ณ วันที่ 31 มีนาคม" จึงให้โครงขึ้นต้นเหมือนกัน
SE_BLOCK_START = core.skeleton("มกราคม")

# ปี พ.ศ. ในหัวบล็อก ใช้บอกว่าบล็อกไหนเป็นงวดปัจจุบัน
_YEAR = re.compile(r"\b(2[45]\d\d)\b")


def parse_equity_line(ln, total_col):
    """อ่านบรรทัดของงบแสดงการเปลี่ยนแปลงส่วนของเจ้าของ -> (ชื่อรายการ, [ยอดรวม])

    ใช้ "ตัวเลขตัวสุดท้ายของบรรทัด" เป็นยอดรวม ไม่ยึดพิกัดคอลัมน์ที่ตรวจเจอ

    เพราะ pdftotext จัดตำแหน่งสองบล็อกปีในตารางเดียวกันเหลื่อมกันได้
        บล็อก 2568  ... (19,294)   <- ตัวเลขจบที่ตำแหน่ง 193
        บล็อก 2569  ... 181,727    <- ตัวเลขจบที่ตำแหน่ง 188
    เกิน ±4 ที่ parse_line ยอมให้ ค่าของบล็อกก่อนจึงหายไปเงียบๆ
    ส่วนยอดรวมอยู่ขวาสุดของแถวเสมอตามโครงตาราง จึงใช้เป็นหลักได้แน่นอนกว่า

    total_col ใช้เป็นแค่ขอบเขตกันหลง — ตัวเลขต้องอยู่ไม่ไกลจากคอลัมน์ที่ตรวจเจอ
    """
    parsed = core.parse_line(ln, total_col)
    if parsed is not None and parsed[1][0] is not None:
        return parsed

    # ตรงคอลัมน์ไม่เจอค่า -> ลองใช้ "ตัวเลขตัวสุดท้ายของบรรทัด" เป็นคอลัมน์แทน
    # แต่ต้องอยู่ใกล้คอลัมน์ที่ตรวจเจอพอสมควร กันไปหยิบคอลัมน์อื่นมา
    hits = list(core.TOKEN.finditer(ln))
    if not hits or not total_col:
        return parsed
    end = hits[-1].end()
    if abs(end - total_col[0]) > 12:
        return parsed
    # เรียก parse_line ใหม่โดยยึดตำแหน่งจริงของตัวเลขตัวสุดท้าย
    # เพื่อให้ตรรกะการตัดชื่อรายการเหมือนเดิมทุกอย่าง
    return core.parse_line(ln, [end]) or parsed

# ป้ายกำกับในงบนี้ตามด้วยตัวเลข 10 กว่าช่อง ต้องตัดออกก่อนเทียบ
# ไม่งั้นตัวเลขพวกนั้นจะไปชนกับการเทียบแบบ "ตรงตัวเลขในชื่อ" (ดู fs_core.digits)
import re as _re
# ตัดตั้งแต่ "ตัวเลขที่มีคอมม่า" หรือ "ขีดกลางที่แปลว่าไม่มีค่า" เป็นต้นไป
# ห้ามตัดที่ตัวเลขเปล่า เพราะปี พ.ศ. ในชื่อแถว ("ณ วันที่ 31 มีนาคม 2569")
# เป็นตัวแยกว่าแถวนี้คือยอดต้นงวดหรือยอดปลายงวด
_SE_LABEL = _re.compile(r"\s+(?:\(?-?\d{1,3}(?:,\d{3})+\.?\d*\)?|-)(?:\s|$).*", _re.S)


def se_label(s: str) -> str:
    return _SE_LABEL.sub("", str(s)).strip() or str(s).strip()


# หัวเรื่องบอกว่าหน้านั้นเป็นงบรวมหรืองบเฉพาะกิจการ
_SK_CON = core.skeleton("งบการเงินรวม")
_SK_SEP = [core.skeleton("เฉพาะกิจการ"), core.skeleton("เฉพาะธนาคาร"),
           core.skeleton("เฉพาะบริษัท")]


def equity_spill_values(text, least=3, max_lines=24):
    """หน้าที่มีแต่ "คอลัมน์ยอดรวม" ลอยอยู่หน้าเดียว -> รายการตัวเลขตามลำดับแถว

    ตารางงบนี้กว้างมาก บางไฟล์คอลัมน์สุดท้ายล้นกรอบจนถูกดันไปอยู่หน้าถัดไป
    ทั้งหน้ามีแค่หัวคอลัมน์ "รวม" กับตัวเลขเรียงลงมา (IIG หน้า 8)

        (หน่วย: บาท)
        รวม
        680,593,011
        (4,800,000)
        ...
        220,811,040
        6              <- เลขหน้า ไม่ใช่ข้อมูล

    หน้าแบบนี้ has_table() = False (ตัวเลขน้อยเกินเกณฑ์) จึงไม่ถูกจัดเป็นงบใดๆ
    แล้วหายไปเงียบๆ ทั้งที่เป็นคอลัมน์ที่สำคัญที่สุดของตาราง

    คืน None ถ้าหน้านี้ไม่เข้าข่าย
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines or len(lines) > max_lines:
        return None
    if not any(core.skeleton(l).startswith(core.skeleton("รวม")) for l in lines):
        return None

    vals = []
    for l in lines:
        toks = list(core.TOKEN.finditer(l))
        # ต้องเป็นบรรทัดที่มีตัวเลขตัวเดียวล้วนๆ ไม่มีข้อความปน
        if len(toks) != 1 or core.skeleton(l):
            continue
        v = core.to_number(toks[0].group())
        if v is not None:
            vals.append(v)
    # ตัดเลขหน้าท้ายสุด (จำนวนเต็มสั้น ไม่มีคอมม่า)
    if vals and abs(vals[-1]) < 1000 and "," not in lines[-1]:
        vals = vals[:-1]
    return vals if len(vals) >= least else None


def equity_page_side(text):
    """หน้านี้เป็นงบรวมหรืองบเฉพาะกิจการ -> "con" / "sep" / None

    ต้องดูจากหัวเรื่อง ไม่ใช่ลำดับหน้า เพราะงบรวมของบางบริษัทกินสองหน้า
        GABLE MFEC PTT PRG SAT  [รวม, เฉพาะ]        2 หน้า
        AIS SCB                 [รวม, รวม, ?, ?]    4 หน้า
        CPF                     [รวม, รวม, ?]       3 หน้า
    ถ้าถือว่า "หน้าแรก = งบรวม ที่เหลือ = เฉพาะกิจการ" หน้าที่สองของงบรวม
    จะถูกอ่านเป็นงบเฉพาะกิจการแล้วทับค่ากันเอง
    """
    sk = core.skeleton("\n".join(text.split("\n")[:16]))
    if any(k in sk for k in _SK_SEP):
        return "sep"
    if _SK_CON in sk:
        return "con"
    return None


def extract_equity_page(pdf, company, matcher, page, text, is_consolidated,
                        collect=None, spill=None):
    """คืนแถวจากงบแสดงการเปลี่ยนแปลงส่วนของเจ้าของ 1 หน้า

    is_consolidated=True  -> ลงคอลัมน์ con_*   (งบรวม)
    is_consolidated=False -> ลงคอลัมน์ sep_*   (งบเฉพาะกิจการ)
    ในแต่ละหน้ามีสองบล็อกปี บล็อกแรก = ปีก่อน บล็อกหลัง = ปีปัจจุบัน

    collect = list สะสม (label, value) ข้ามหน้า สำหรับกลุ่มที่กินหลายหน้า
    """
    lines = text.split("\n")
    cols = core.find_columns(lines)
    if len(cols) < 3:
        return []

    # ใช้คอลัมน์ขวาสุดเป็น "ยอดรวม"
    #
    # เคยลองเลือกจาก "ค่าสูงสุด" ด้วยเหตุผลว่ายอดรวมต้องมากกว่าทุกองค์ประกอบ
    # แต่ผิด — IIG งบเฉพาะกิจการมีส่วนเกินมูลค่าหุ้น 749M มากกว่ายอดรวม 263M
    # เพราะกำไรสะสมติดลบหนัก และวิธีนั้นทำให้ SAT ที่เคยถูกกลับพัง
    #
    # ข้อจำกัด: บริษัทที่ไม่มีคอลัมน์ยอดรวมในงบนี้ (IIG, SCB) จะได้คอลัมน์ผิด
    # ซึ่งสมการ "ยอดปลายงวดตรงกับงบฐานะการเงิน" จับได้เอง — ไม่ได้เงียบหาย
    # การแก้ให้ถูกต้องต้องอ่านชื่อคอลัมน์จากหัวตารางที่พาดแนวตั้ง 4 บรรทัด
    # ซึ่งเป็นงานของทางเลือก B (เก็บทั้งตาราง)
    total_col = cols[-1:]

    seq = collect if collect is not None else []
    start_len = len(seq)
    prefixes: list[str] = []
    for ln in lines:
        parsed = parse_equity_line(ln, total_col)
        # ถ้ามีคอลัมน์ยอดรวมจากหน้าถัดไป ให้ยอมรับแถวที่คอลัมน์ในหน้านี้เป็นขีด
        # (เช่น "เงินปันผลจ่าย" ที่ทุกองค์ประกอบเป็น "-" แต่ยอดรวมมีค่า)
        # ไม่งั้นจำนวนแถวจะไม่ตรงกับจำนวนค่าที่ล้นไปหน้าถัดไป
        keep_empty = spill is not None
        if parsed is None or (parsed[1][0] is None and not keep_empty):
            # บรรทัดที่ไม่มีค่า อาจเป็นท่อนหน้าของชื่อที่ถูกตัดขึ้นบรรทัดใหม่
            # งบนี้ชื่อยาวและถูกตัดบ่อย เช่น MFEC
            #   "โอนสำรองสำหรับการป้องกันความเสี่ยง"
            #   "  ในกระแสเงินสดไปยังต้นทุนงานระหว่างทำ   8,253"
            if ln.strip() and looks_like_prefix(ln):
                prefixes.append(core.clean_label(ln))
                del prefixes[:-PREFIX_MEMORY]
            else:
                prefixes.clear()
            continue
        label, vals = parsed
        raw = se_label(label)
        _, _, label = core.match_concept(raw, matcher, prefixes=prefixes)
        prefixes.clear()
        # ตัวตัดเลขอ้างอิงหมายเหตุมองว่าปี พ.ศ. ท้ายชื่อเป็นเลขหมายเหตุแล้วตัดทิ้ง
        # แต่ปีคือสิ่งเดียวที่บอกว่าบล็อกนี้เป็นงวดไหน ต้องเก็บกลับมา
        m = _YEAR.search(raw)
        if m and not _YEAR.search(label):
            label = f"{label} {m.group(1)}"
        seq.append((label, vals[0]))

    # ถ้าคอลัมน์ยอดรวมล้นไปอยู่หน้าถัดไป ให้ใช้ค่าจากหน้านั้นแทนตามลำดับแถว
    # ต้องจำนวนค่าเท่ากับจำนวนแถวพอดี ไม่งั้นไม่กล้าใช้ (กันจับคู่เลื่อน)
    if spill is not None:
        n = len(seq) - start_len
        if len(spill) == n:
            for i in range(n):
                lbl = seq[start_len + i][0]
                seq[start_len + i] = (lbl, spill[i])
        else:
            print(f"  ! {pdf.name} p{page}: คอลัมน์ยอดรวมหน้าถัดไปมี {len(spill)} ค่า "
                  f"แต่ตารางมี {n} แถว ไม่ตรงกัน จึงไม่ใช้")

    if collect is not None:
        return []          # ยังสะสมอยู่ รอหน้าถัดไปของกลุ่มเดียวกัน
    return build_equity_rows(pdf, company, matcher, page, text,
                             is_consolidated, seq)


def _block_year(block):
    """ปี พ.ศ. ที่เขียนในหัวบล็อก เช่น "ยอดคงเหลือ ณ วันที่ 1 มกราคม 2569" -> 2569"""
    for label, _ in block[:1]:
        m = _YEAR.search(str(label))
        if m:
            return int(m.group(1))
    return None


def build_equity_rows(pdf, company, matcher, page, text, is_consolidated, seq):
    """แปลงลำดับ (ชื่อรายการ, ยอดรวม) ที่สะสมมาเป็นแถวผลลัพธ์

    ตัดเป็นบล็อกปีตรงบรรทัด "ยอดคงเหลือ ณ วันที่ 1 มกราคม"
    บล็อกสุดท้าย = งวดปัจจุบัน บล็อกก่อนหน้า = งวดเดียวกันปีก่อน
    """
    blocks, cur, years = [], [], []
    for label, v in seq:
        if SE_BLOCK_START in core.skeleton(label):
            if cur:
                blocks.append(cur)
                years.append(_block_year(cur))
            cur = []
        cur.append((label, v))
    if cur:
        blocks.append(cur)
        years.append(_block_year(cur))
    if not blocks:
        return []

    # เลือกบล็อกงวดปัจจุบันจาก "ปีที่เขียนในหัวบล็อก" ไม่ใช่จากลำดับ
    # SCB วางหน้าปีใหม่ไว้ก่อนปีเก่า (p9 = 2569, p10 = 2568) กลับกับบริษัทอื่น
    # ถ้าถือว่าบล็อกสุดท้ายคือปีปัจจุบันจะได้ยอดของปีก่อนมาแทน
    order = sorted(range(len(blocks)), key=lambda i: (years[i] or 0))
    cur_block = blocks[order[-1]]
    prev_block = blocks[order[-2]] if len(blocks) >= 2 else []
    prev_by_concept, prev_by_label = {}, {}
    for label, v in prev_block:
        cc, _, _ = core.match_concept(label, matcher)
        if cc:
            prev_by_concept.setdefault(cc, v)
        prev_by_label.setdefault(core.skeleton(label), (label, v))

    # ต้องสร้างแถวจาก "ทั้งสองบล็อก" ไม่ใช่แค่บล็อกปัจจุบัน
    # เพราะบางรายการมีเฉพาะปีก่อน เช่น GABLE จ่ายโดยใช้หุ้นเป็นเกณฑ์ปี 2568
    # แต่ปี 2569 ไม่ได้จ่าย ถ้าไล่แค่บล็อกปัจจุบันจะไม่มีแถวให้เติมค่าปีก่อนเลย
    seen_cur = {core.skeleton(l) for l, _ in cur_block}
    only_prev = [(l, None) for sk, (l, _) in prev_by_label.items()
                 if sk not in seen_cur]

    rows = []
    for label, v in list(cur_block) + only_prev:
        concept, score, used = core.match_concept(label, matcher)
        if len(core.skeleton(used)) < MIN_LABEL_SKELETON:
            continue
        concept = SE_REMAP.get(concept, concept)
        pv = prev_by_concept.get(concept) if concept else None
        if pv is None:
            hit = prev_by_label.get(core.skeleton(label))
            pv = hit[1] if hit else None
        vals = [v, pv, None, None] if is_consolidated else [None, None, v, pv]
        rows.append({
            "company": company, "source": pdf.name, "statement": "SE",
            "unit": core.detect_unit(text), "page": str(page),
            "section": "งบรวม" if is_consolidated else "งบเฉพาะกิจการ",
            "item_raw": label, "item_used": used,
            "concept": concept, "concept_eq": concept,
            "match_score": round(score, 3),
            "con_cur": vals[0], "con_prev": vals[1],
            "sep_cur": vals[2], "sep_prev": vals[3],
        })
    return rows


HEADER_LOOKBACK = 10
HEADER_TOL = 14
_WORD_GROUP = re.compile(r"\S(?:.*?\S)?(?=\s{2,}|$)")


def equity_column_names(lines, cols, first_data,
                        look=HEADER_LOOKBACK, tol=HEADER_TOL):
    """ประกอบชื่อคอลัมน์ของงบแสดงการเปลี่ยนแปลงส่วนของเจ้าของ

    หัวตารางงบนี้พาดแนวตั้ง 3-4 บรรทัด ชื่อคอลัมน์เดียวถูกตัดเป็นท่อนๆ

        บรรทัด  9 |            ส่วนเกินทุน        ส่วนเกินทุน
        บรรทัด 10 |         จากการรวมธุรกิจ   จากการเปลี่ยนแปลง
        บรรทัด 11 |  ทุนเรือนหุ้น  ภายใต้การควบคุม   สัดส่วนการถือหุ้น
        บรรทัด 12 | ที่ออกและชำระแล้ว   เดียวกัน      ในบริษัทย่อย

    วิธีประกอบ: จับกลุ่มคำในแต่ละบรรทัด (คั่นด้วยช่องว่าง 2 ช่องขึ้นไป)
    แล้วผูกเข้ากับคอลัมน์ที่ "ขอบขวา" ใกล้ที่สุด เพราะหัวตารางจัดชิดขวา
    ตามคอลัมน์ตัวเลขเหมือนกัน จากนั้นต่อท่อนจากบนลงล่าง

    ยังมีสิ่งเจือปน: หัวข้อที่ครอบหลายคอลัมน์ ("งบการเงินรวม",
    "องค์ประกอบอื่นของส่วนของผู้ถือหุ้น") จะติดมากับคอลัมน์ที่มันจบพอดี
    ต้องกรองออกตอนแปลงเป็น concept

    ใช้สำหรับทางเลือก B (เก็บทั้งตาราง) — ยังไม่ถูกเรียกใช้ในสายหลัก
    """
    parts = {col: [] for col in cols}
    for i in range(max(0, first_data - look), first_data):
        for m in _WORD_GROUP.finditer(lines[i]):
            g = m.group().strip()
            if not g or not core.skeleton(g):
                continue
            best = min(cols, key=lambda x: abs(m.end() - x))
            if abs(m.end() - best) <= tol:
                parts[best].append(g)
    return {col: " ".join(v) for col, v in parts.items()}


def merge_equity_rows(rows):
    """รวมแถวจากหน้างบรวมกับหน้างบเฉพาะกิจการที่เป็นรายการเดียวกัน

    งบนี้แยกเป็นคนละหน้า (หน้าหนึ่งงบรวม อีกหน้างบเฉพาะกิจการ) ตัวอ่านจึงได้
    สองแถวที่มีค่าคนละครึ่ง ถ้าปล่อยไว้ ขั้นตอน dedup ปลายทางจะเก็บได้แถวเดียว
    แล้วอีกครึ่งหายไปเงียบๆ — ต้องรวมเป็นแถวเดียวให้ครบ 4 ช่องก่อน
    """
    out, by_key = [], {}
    for r in rows:
        key = r["concept"] or ("~" + core.skeleton(r["item_used"]))
        if key not in by_key:
            by_key[key] = r
            out.append(r)
            continue
        keep = by_key[key]
        for c in core.COLS:
            if keep[c] is None and r[c] is not None:
                keep[c] = r[c]
        keep["page"] = f'{keep["page"]},{r["page"]}'
        keep["section"] = "งบรวม+งบเฉพาะกิจการ"

    # บังคับให้ทั้งชุดเป็น str ตรงนี้ด้วย ไม่ใช่หวังว่าผู้เรียกจะใส่มาถูกชนิด
    # แถวที่ถูกรวมกลายเป็น "5,6" ส่วนแถวที่ไม่ถูกรวมยังเป็น int
    # ปล่อยไว้คอลัมน์เดียวจะมีสองชนิด ซึ่งพังตอนแสดงผลและทำให้ปลายทางต้องเดาเอง
    for r in out:
        r["page"] = str(r["page"])
    return out


def warn_duplicate_concepts(df):
    """เตือนเมื่อสองแถวใน "งบเดียวกันของบริษัทเดียวกัน" ชี้ concept เดียวกัน
    แต่ค่าไม่เท่ากัน

    ปลายทางเก็บได้ concept ละแถวเดียวต่อบริษัท ถ้าซ้ำแล้วค่าต่างกัน แปลว่า
    "จะหยิบอันหนึ่งมา และมีโอกาสหยิบผิด" โดยไม่มี error ใดๆ
    เป็นความผิดพลาดแบบเดียวกับที่ทำให้ IIG ลูกหนี้ฯ ได้ 155M แทน 29.9M
    และ GABLE ทุนเรือนหุ้นได้ 700,021 แทน 707,500

    เตือนอย่างเดียว ไม่หยุดการทำงาน เพราะบางกรณีค่าซ้ำกันจริงและไม่เสียหาย
    """
    if df.empty or "concept" not in df:
        return
    d = df.dropna(subset=["concept"])
    keys = ["company", "statement", "concept"]
    hits = []
    for key, g in d.groupby(keys):
        if len(g) < 2:
            continue
        vals = g[core.COLS].round(4).drop_duplicates()
        if len(vals) > 1:
            hits.append((key, g))
    if not hits:
        return
    print(f"\n!! {len(hits)} concept ถูกชี้จากหลายแถวและค่าไม่ตรงกัน "
          f"(ตัวสกัดจะเก็บได้แถวเดียว มีโอกาสหยิบผิด)")
    for (comp, stmt, concept), g in hits[:10]:
        print(f"  {comp} [{stmt}] {concept}")
        for r in g.itertuples():
            v = r.con_cur
            print(f"      หน้า {r.page} | {str(r.item_used)[:52]:<52} "
                  f"| con_cur={v}")
    if len(hits) > 10:
        print(f"  ... อีก {len(hits)-10} concept")
    print("  วิธีแก้: แยกเป็นคนละ concept ใน alias map "
          "(ดูวิธีทำที่ CONTRACT_ASSETS_CURRENT / SHARE_CAPITAL_DESC)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf-dir", help="โฟลเดอร์ PDF หรือไฟล์ PDF เดียว")
    ap.add_argument("--aliases", help="ไฟล์ตารางเทียบคำศัพท์")
    ap.add_argument("--out", help="ไฟล์ CSV ผลลัพธ์")
    ap.add_argument("--company", help="บังคับชื่อบริษัท (ใช้กับไฟล์เดียว)")
    ap.add_argument("--all-companies", action="store_true",
                    help="สกัดทุกไฟล์ ไม่กรองเฉพาะบริษัทใน ground truth")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES)
    ap.add_argument("--statement", choices=["bs", "is", "cf", "se", "all"], default="bs",
                    help="bs = งบฐานะการเงิน (ค่าตั้งต้น), is = งบกำไรขาดทุน, "
                         "cf = งบกระแสเงินสด, all = ทั้งหมด")
    ap.add_argument("--debug-company", help="พิมพ์บรรทัดที่ map ไม่ติดของบริษัทนี้")
    args = ap.parse_args()

    alias_path = core.resolve(args.aliases, core.default_alias_path(), "alias map")
    pdf_target = core.resolve(args.pdf_dir, core.default_pdf_dir(), "โฟลเดอร์ PDF")
    out_csv = Path(args.out) if args.out else core.default_out_dir() / "extracted.csv"

    matcher = core.build_matcher(alias_path)
    if not matcher:
        print(f"!! ไม่พบ {alias_path} จะไม่มีการ map concept")
    else:
        print(f"alias map: {alias_path.name} ({len(matcher)} รายการ, "
              f"{len(matcher.concepts)} concept)")

    want = {"bs": ("BS",), "is": ("IS",), "cf": ("CF",), "se": ("SE",),
            "all": ("BS", "IS", "CF", "SE")}[args.statement]
    files = sorted(pdf_target.glob("*.pdf")) if pdf_target.is_dir() else [pdf_target]

    all_rows = []
    for f in files:
        comp = args.company or company_from_filename(f)
        if comp is None:
            if not args.all_companies:
                continue
            comp = f.stem.upper()

        debug = args.debug_company and comp == args.debug_company.upper()
        if debug:
            print(f"\n--- debug {comp} ---")
        rows = extract_pdf(f, comp, matcher, max_pages=args.max_pages,
                           debug=debug, want=want)
        mapped = sum(1 for r in rows if r["concept"])
        kinds = "+".join(sorted({r["statement"] for r in rows})) or "-"
        print(f"{f.name:<45} {comp:<8} {len(rows):>3} แถว | map ได้ {mapped:>3}"
              f" | {kinds}")
        all_rows += rows

    if not all_rows:
        print("ไม่พบข้อมูล หรือชื่อไฟล์ไม่ตรงกับบริษัทที่ค้นหา")
        return

    df = pd.DataFrame(all_rows)

    # หน่วยเงินพิมพ์ไว้แค่บางหน้า (มักเป็นหน้าแรกของแต่ละงบ) หน้าที่เหลือจะได้
    # "unknown" ทั้งที่เป็นไฟล์เดียวกัน ต้องเติมจากหน้าที่อ่านเจอ
    # ผิดหน่วยคือผิด 1,000 เท่า จึงห้ามปล่อยให้ unknown หลุดไปถึงปลายทาง
    known = (df[df.unit != "unknown"].groupby("company")["unit"]
               .agg(lambda s: s.mode().iat[0]))
    df["unit"] = [known.get(c, u) if u == "unknown" else u
                  for c, u in zip(df.company, df.unit)]

    # บริษัทที่ยังเหลือ unknown หลังเติมแล้ว = ทั้งเอกสารไม่เคยเขียนหน่วยไว้เลย
    # (IIG เป็นแบบนี้) ห้ามเดาแทนผู้ใช้ เพราะเดาผิดคือผิด 1,000 เท่าแบบเงียบๆ
    # แต่ต้องเตือนให้เห็น ไม่ใช่ปล่อย unknown ไหลไปถึงหน้าจอเปรียบเทียบบริษัท
    still = sorted(df[df.unit == "unknown"].company.unique())
    if still:
        print(f"\n!! หน่วยเงินไม่ทราบ: {', '.join(still)}")
        print("   เอกสารไม่ได้เขียนหน่วยไว้เลย ระบบไม่เดาให้")
        print("   ต้องระบุเองก่อนนำไปเทียบข้ามบริษัท ไม่งั้นจะเทียบผิด 1,000 เท่า")

    warn_duplicate_concepts(df)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\nบันทึก {len(df)} แถว -> {out_csv}")


if __name__ == "__main__":
    main()
