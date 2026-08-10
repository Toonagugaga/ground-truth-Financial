#!/usr/bin/env python3
"""
fs_core.py - โมดูลกลางของโปรเจกต์สกัดงบการเงินไทย

ทุกสคริปต์ (extract_fs / evaluate / validate_ground_truth / inspect_risk)
เรียกใช้ฟังก์ชันจากที่นี่ เพื่อให้ตรรกะการเทียบชื่อรายการเหมือนกันทุกที่
ถ้าโค้ดเทียบชื่อของ extract กับ evaluate ไม่ตรงกัน ตัวเลขความแม่นยำจะไม่มีความหมาย

หลักการสำคัญที่ห้ามละเมิด (จาก HANDOFF.md ข้อ 9)
  1. ห้ามแก้ ground truth ให้ตรงกับโค้ด
  2. ground truth ต้องมีแถว subtotal เสมอ เพื่อให้ตรวจด้วยสมการงบดุลได้
  3. วัดผลแยกตาม trap ไม่ใช่แค่ตัวเลขรวม
  4. ระวังการรายงานความแม่นยำจากกลุ่มตัวอย่างที่ไม่ครบ
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------
# ค่าคงที่
# --------------------------------------------------------------------------

COLS = ["con_cur", "con_prev", "sep_cur", "sep_prev"]

# สมการที่ต้องเป็นจริงเสมอในงบที่ถูกต้อง ใช้ตรวจทั้ง ground truth และผลสกัด
# โดยไม่ต้องมีเฉลย  (ชื่อ, [ตัวตั้ง...], ผลรวม)
EQUATIONS_BS = [
    ("สินทรัพย์รวม",
     ["TOTAL_CURRENT_ASSETS", "TOTAL_NONCURRENT_ASSETS"], "TOTAL_ASSETS"),
    ("งบดุลสมดุล",
     ["TOTAL_LIABILITIES", "TOTAL_EQUITY"], "TOTAL_LIAB_EQUITY"),
]

EQUATIONS_IS = [
    ("การแบ่งปันกำไร",
     ["PROFIT_ATTRIB_PARENT", "PROFIT_ATTRIB_NCI"], "NET_PROFIT"),
    ("การแบ่งปันกำไรเบ็ดเสร็จ",
     ["TCI_ATTRIB_PARENT", "TCI_ATTRIB_NCI"], "TOTAL_COMPREHENSIVE_INCOME"),
    ("กำไรเบ็ดเสร็จรวม",
     ["NET_PROFIT", "OCI_TOTAL"], "TOTAL_COMPREHENSIVE_INCOME"),
]

# งบกระแสเงินสด — "?" นำหน้า = ตัวตั้งที่ไม่จำเป็นต้องมี ถ้าไม่มีให้ถือเป็น 0
# บางบริษัทไม่มีบรรทัดผลกระทบอัตราแลกเปลี่ยนหรือผลต่างจากการแปลงค่างบ
# ถ้าบังคับว่าต้องมีครบ สมการจะถูกข้ามทั้งที่ตรวจได้
#
# งบไทยวางบรรทัดปรับปรุง (ผลกระทบอัตราแลกเปลี่ยน / ผลต่างจากการแปลงค่างบ)
# ไว้คนละที่กัน และตำแหน่งเป็นตัวบอกว่ามันถูกรวมอยู่ในยอดสุทธิแล้วหรือยัง
#
#   MFEC, GABLE            BBIK, PTT, AIS
#   ...สุทธิ 3 กิจกรรม     ...สุทธิ 3 กิจกรรม
#   เงินสดเพิ่ม(ลด)สุทธิ   ผลกระทบอัตราแลกเปลี่ยน   <- อยู่ "ก่อน" = รวมอยู่ในยอดสุทธิ
#   ผลกระทบอัตราแลกเปลี่ยน  เงินสดเพิ่ม(ลด)สุทธิ
#   เงินสดต้นงวด           เงินสดต้นงวด
#   เงินสดปลายงวด          เงินสดปลายงวด
#           ^ อยู่ "หลัง" = ยังไม่ถูกรวม ต้องบวกเพิ่มตอนหายอดปลายงวด
#
# ถ้าไม่แยกตามตำแหน่ง จะบวกซ้ำ — PTT ได้ยอดปลายงวดเกินไป 8,856,395,262
# ซึ่งเท่ากับผลกระทบอัตราแลกเปลี่ยนพอดี
# ตัวสกัดจึงเติมท้าย "_AFTER" ให้ concept ที่โผล่หลังบรรทัดยอดสุทธิ
CF_ADJUSTMENTS = ["CASH_FX_EFFECT", "CF_TRANSLATION", "CASH_RECLASS_HELD_FOR_SALE",
                  "BANK_OVERDRAFT_BEGIN"]

EQUATIONS_CF = [
    ("เงินสดปลายงวด",
     ["CASH_BEGIN", "CASH_NET_CHANGE"] + [f"?{a}_AFTER" for a in CF_ADJUSTMENTS],
     "CASH_END"),
    ("เงินสดสุทธิรวม 3 กิจกรรม",
     ["CF_OPERATING", "CF_INVESTING", "CF_FINANCING"]
     + [f"?{a}" for a in CF_ADJUSTMENTS],
     "CASH_NET_CHANGE"),
]

# งบแสดงการเปลี่ยนแปลงส่วนของเจ้าของ — และการตรวจ "ข้ามงบ"
#
# สองสมการล่างนี้เป็นการตรวจที่แข็งแรงที่สุดที่มี เพราะเป็นการเทียบตัวเลข
# ที่มาจากคนละหน้าคนละตารางของเอกสาร ถ้าตัวสกัดอ่านหน้าใดหน้าหนึ่งผิด
# มันจะไม่มีทางบังเอิญตรงกัน (สมการภายในงบเดียวกันยังพลาดได้ถ้าดูดมาผิดทั้งตาราง
# ซึ่งเคยเกิดกับ GABLE ที่ดูดค่าจากงบกระแสเงินสดมาใส่งบฐานะการเงิน)
#
# ***ใช้ได้เฉพาะคอลัมน์งวดปัจจุบัน*** เพราะคำว่า "งวดก่อน" ในแต่ละงบไม่ใช่วันเดียวกัน
#     งบฐานะการเงิน    prev = 31 ธ.ค. 2568  (สิ้นปีก่อน)
#     งบกำไรขาดทุน/กระแสเงินสด prev = ไตรมาส 1/2568 (งวดเดียวกันปีก่อน)
# ถ้าเอา prev มาเทียบข้ามงบจะไม่ตรงเป็นธรรมดา ไม่ใช่ตัวสกัดผิด
CUR_ONLY = ["con_cur", "sep_cur"]

EQUATIONS_SE = [
    ("ยอดปลายงวดตรงกับงบฐานะการเงิน", ["SE_CLOSING"], "TOTAL_EQUITY", CUR_ONLY),
    # บางบริษัท (CPF, IIG) นิยาม "เงินสด" ในงบกระแสเงินสดว่าหักเงินเบิกเกินบัญชีแล้ว
    # จึงไม่เท่ากับงบฐานะการเงินโดยตั้งใจ และงบเขียนกระทบยอดไว้ให้เองในหมายเหตุ
    #     เงินสดและรายการเทียบเท่าเงินสด   22,933,621   <- ตรงกับงบฐานะการเงิน
    #     เงินเบิกเกินบัญชี                  (416,206)
    #     สุทธิ                            22,517,415   <- ยอดปลายงวดในงบกระแสเงินสด
    # ใส่บรรทัดกระทบยอดเป็นตัวตั้งเสริม จะได้ไม่รายงานว่าผิดทั้งที่ถูกตามงบ
    ("เงินสดปลายงวดตรงกับงบฐานะการเงิน",
     ["CASH", "?BANK_OVERDRAFT_IN_CASH"], "CASH_END", CUR_ONLY),
]

EQUATIONS = EQUATIONS_BS + EQUATIONS_IS + EQUATIONS_CF + EQUATIONS_SE


def build_equation_lut(ex):
    """สร้างตารางค้นหา (บริษัท, concept, คอลัมน์) -> ค่า สำหรับตรวจสมการ

    **ต้องมีที่เดียวในระบบ** ห้ามให้ใครเขียนซ้ำ
    เพราะการเลือกว่า "concept เดียวกันที่โผล่หลายงบ จะเอาค่าจากงบไหน"
    เป็นการตัดสินใจที่ผิดแล้วเงียบ ไม่มี error ให้เห็น

    เคยเกิดจริง: dashboard เขียน dedup ของตัวเองที่ไม่มีตรรกะนี้ ผลคือ
    SCB DERIVATIVE_ASSETS ได้ -4,077,029 (ผลต่างจากงบกระแสเงินสด)
    แทนที่จะเป็น 51,697,961 (ยอดคงเหลือจากงบฐานะการเงิน)
    มีคีย์ที่ค่าไม่ตรงกันถึง 519 จุด และรายงานว่ามีสมการไม่ผ่าน 1 ข้อ
    ทั้งที่ crosscheck.py บอกว่าผ่านหมด — เป็นบั๊กชนิดเดียวกับที่ GABLE เคยเจอ
    (ดูดผลต่างจากงบกระแสเงินสดมาใส่ยอดคงเหลือ) แต่ย้ายมาเกิดที่ชั้นแสดงผล

    ลำดับการตัดสินเมื่อ concept ซ้ำ
      1. งบหลักชนะงบแสดงการเปลี่ยนแปลงส่วนของเจ้าของ
         (ค่าในงบนั้นเป็นคอลัมน์ยอดรวม ไม่ใช่ตัวที่สมการงบกำไรขาดทุนต้องการ)
      2. คะแนนจับคู่สูงกว่าชนะ
      3. แถวที่มีค่าครบกว่าชนะ  <- กันไม่ให้ผลขึ้นกับลำดับแถวในไฟล์
    """
    ex = pick_rows(ex, use_concept_eq=True, se_last=True)
    lut = {(r.company, r.concept, c): float(getattr(r, c))
           for r in ex.itertuples() for c in COLS if pd.notna(getattr(r, c))}
    return lut, ex


def pick_rows(ex, keys=("company", "concept"), use_concept_eq=False,
              se_last=False):
    """เลือกแถวเดียวต่อคีย์ที่กำหนด — **ตรรกะการเลือกแถวมีที่เดียวคือฟังก์ชันนี้**

    ผู้ใช้ผลสกัดมี 3 ราย และต้องการต่างกันนิดเดียว จึงทำเป็นพารามิเตอร์
    ไม่ใช่ให้แต่ละรายเขียนเอง (ซึ่งเคยทำแล้วเดินห่างกันจนผลไม่ตรงกัน 519 คีย์)

        crosscheck / dashboard แท็บสมการ  keys=(company, concept)
                                          use_concept_eq=True  se_last=True
        evaluate                          keys=(company, concept)
                                          use_concept_eq=False   <- เฉลยบันทึกตามที่
                                          งบเขียน ไม่รู้จัก _AFTER ที่มาจากตำแหน่ง
        dashboard แท็บดูข้อมูล            keys=(company, statement, concept)
                                          เพราะอยากดูแยกงบ

    เกณฑ์ตัดสินเมื่อคีย์ซ้ำ (เหมือนกันทุกราย)
      1. งบหลักชนะงบส่วนของเจ้าของ (เฉพาะเมื่อ se_last=True)
      2. คะแนนจับคู่สูงกว่าชนะ
      3. แถวที่มีค่าครบกว่าชนะ  <- กันไม่ให้ผลขึ้นกับลำดับแถวในไฟล์
    """
    ex = ex.dropna(subset=["concept"]).copy()
    ex["company"] = ex["company"].map(
        lambda v: "TRUE" if v is True else ("FALSE" if v is False else str(v).strip()))
    if use_concept_eq and "concept_eq" in ex.columns:
        ex["concept"] = ex["concept_eq"].fillna(ex["concept"])

    by, asc = [], []
    if se_last:
        ex["_se_last"] = (ex["statement"] == "SE").astype(int)
        by.append("_se_last"); asc.append(True)
    if "match_score" in ex.columns:
        by.append("match_score"); asc.append(False)
    ex["_n_vals"] = ex[COLS].notna().sum(axis=1)
    by.append("_n_vals"); asc.append(False)

    return (ex.sort_values(by, ascending=asc, kind="mergesort")
              .drop_duplicates(list(keys)))


def check_equations(lookup, companies, equations=None, tol=1.0, verbose=True):
    """ตรวจสมการจากตาราง {(company, concept, col): value}

    ตัวตั้งที่ขึ้นต้นด้วย "?" = ไม่จำเป็นต้องมี ถ้าไม่มีถือเป็น 0
    ตัวตั้งปกติหรือผลรวมที่หายไป -> ข้ามสมการนั้น (ไม่นับว่าผิด)
    """
    equations = EQUATIONS if equations is None else equations
    ok = bad = skip = 0
    fails = []
    # ต้องล้างชื่อบริษัทแบบเดียวกับ pick_rows ไม่งั้นคีย์ไม่ตรงกันสักตัว
    # แล้วจะได้ "ผ่าน 0 | ผิด 0 | ข้าม ทั้งหมด" ซึ่งหน้าตาเหมือนไม่มีปัญหา
    # ทั้งที่แปลว่าตัวตรวจบอดสนิท (เจอกับไฟล์ที่ชื่อบริษัทมีช่องว่างติดมา)
    companies = [str(c).strip() for c in companies]
    for comp in companies:
        for col in COLS:
            for eq in equations:
                # สมการบางข้อใช้ได้เฉพาะบางคอลัมน์ (ดู CUR_ONLY)
                name, parts, total = eq[0], eq[1], eq[2]
                only = eq[3] if len(eq) > 3 else None
                if only and col not in only:
                    continue
                tv = lookup.get((comp, total, col))
                if tv is None:
                    skip += 1
                    continue
                vals, missing = [], False
                for p in parts:
                    opt = p.startswith("?")
                    v = lookup.get((comp, p.lstrip("?"), col))
                    if v is None:
                        if opt:
                            continue
                        missing = True
                        break
                    vals.append(v)
                if missing:
                    skip += 1
                    continue
                if abs(sum(vals) - tv) < tol:
                    ok += 1
                else:
                    bad += 1
                    msg = f"{comp} [{col}] {name}: {sum(vals):,.0f} != {tv:,.0f}"
                    fails.append(msg)
                    if verbose:
                        print(f"  x {msg}")
    return ok, bad, skip, fails

THAI_CONS = re.compile(r"[ก-ฮ]")

# token ที่เป็นค่าตัวเลข หรือขีดกลางที่แปลว่า "ไม่มีค่า"
TOKEN = re.compile(
    r"\(?-?[\d,]+\.?\d*\)?"
    r"|(?<![฀-๿\w])-(?![\d฀-๿])"
)

UNIT_PAT = re.compile(r"หน\s*่?\s*วย\s*[:：]?\s*(พัน|ล้าน)?\s*บาท")

# คำต่อท้ายที่ไม่เปลี่ยนความหมายของ concept มีแค่ "- สุทธิ" (โครง = สทธ)
#
# เดิมเคยตัด "- หมุนเวียน" กับ "- ไม่หมุนเวียน" ด้วย แต่ผิด เพราะงบแยกสองตัวนี้
# เป็นคนละรายการจริงและมักอยู่ในหน้าเดียวกัน
#     "ลูกหนี้ตามสัญญาให้บริการที่ยังไม่ได้เรียกเก็บ - หมุนเวียน"    155,445,665
#     "ลูกหนี้ตามสัญญาให้บริการที่ยังไม่ได้เรียกเก็บ - ไม่หมุนเวียน"  29,893,412
# ถ้าตัดทิ้งทั้งคู่จะได้ concept เดียวกัน แล้วตัวสกัดเก็บได้แถวเดียว = หยิบผิด
# ตอนนี้แยกเป็นคนละ concept ใน alias map แทน
#
# และห้ามตัดด้วย regex ที่ท้ายสตริงเฉยๆ เพราะ "รวมสินทรัพย์หมุนเวียน" จะกลายเป็น
# "รวมสินทรัพย์" = TOTAL_ASSETS ซึ่งผิดคนละเรื่อง ต้องมีขีดคั่นเสมอ
_TAIL_SKELETONS = {"สทธ", "สทธจกภษ"}   # "- สุทธิ", "- สุทธิจากภาษี"

# เลขอ้างอิงหมายเหตุประกอบงบที่ห้อยท้ายชื่อรายการ
#   "หุ้นกู้ 6, 10"          -> "หุ้นกู้"
#   "เงินปันผลรับ 4 และ 5.3" -> "เงินปันผลรับ"
# ปกติ skeleton ตัดตัวเลขทิ้งอยู่แล้ว แต่คำว่า "และ" เป็นอักษรไทย จึงติดมาด้วย
# และทำให้โครงพยัญชนะเพี้ยนจนเทียบไม่ติด
_NOTE_REF = re.compile(
    r"[\s.]+\d+(?:\.\d+)?(?:\s*(?:,|และ)\s*\d+(?:\.\d+)?)*\s*$"
)


def strip_note_ref(label: str) -> str:
    return _NOTE_REF.sub("", str(label)).strip()


# วงเล็บที่ใส่ข้อมูลของ "งวดก่อน" ไว้ในชื่อรายการ ไม่ใช่ส่วนหนึ่งของชื่อ
#   ทุนออกจำหน่ายและชำระเต็มมูลค่าแล้ว หุ้นสามัญ 763,384,513 หุ้น มูลค่าหุ้นละ 1 บาท
#     (31 ธันวาคม 2568: หุ้นสามัญ 763,324,951 หุ้น มูลค่าหุ้นละ 1 บาท)
# ถ้าไม่ตัดทิ้ง ชื่อจะยาวเป็นสองเท่าและ digits() จะได้เลขของสองงวดปนกัน
# ตัดเฉพาะวงเล็บที่มีชื่อเดือนไทยหรือปี พ.ศ./ค.ศ. อยู่ข้างใน เพื่อไม่ให้ไปโดน
# ชื่อจริงอย่าง "กำไร (ขาดทุน) ต่อหุ้น"
_THAI_MONTHS = ("มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
                "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม")
_PAREN = re.compile(r"\([^()]*\)?")


def strip_period_paren(label: str) -> str:
    def drop(m):
        inner = m.group(0)
        sk = skeleton(inner)
        if any(skeleton(mo) in sk for mo in _THAI_MONTHS):
            return " "
        if re.search(r"(?:25|19|20)\d\d", inner):
            return " "
        return inner
    return " ".join(_PAREN.sub(drop, str(label)).split())
_DASH_SPLIT = re.compile(r"\s+[-–—]\s+")


# --------------------------------------------------------------------------
# การจัดการข้อความไทยที่เพี้ยนจาก PDF
# --------------------------------------------------------------------------

def skeleton(s) -> str:
    """เหลือเฉพาะพยัญชนะไทย

    ข้อความไทยจาก PDF เพี้ยนเสมอ และเพี้ยนไม่เหมือนกันตามผู้ผลิตไฟล์
        GABLE "จำกัด"   -> "จาํกดั"      สระแยกออก
        MFEC  "มหาชน"   -> "มหำชน"      ฟอนต์ map สระผิด
        BBIK  "อื่น"    -> "อืน8"        วรรณยุกต์กลายเป็นเลข
        IIG   "ลูกหนี้" -> "ลกู หน้ี"    สลับตำแหน่ง + แทรกช่องว่าง
    แต่พยัญชนะไม่เคยเพี้ยน จึงตัดสระ วรรณยุกต์ ตัวเลข ช่องว่าง ทิ้งก่อนเทียบ
        "ลูกหนี้การค้า"  -> "ลกหนการคา"
        "ลกู หน้กีารคา้" -> "ลกหนการคา"   ตรงกัน
    """
    return "".join(THAI_CONS.findall(str(s)))


def digits(s) -> str:
    """ตัวเลขทั้งหมดในข้อความ ต่อกันเป็นสตริงเดียว (ตัดคอมม่าออก)

    ใช้แยกรายการที่ชื่อเหมือนกันเป๊ะแต่ตัวเลขในชื่อต่างกัน ซึ่ง skeleton
    แยกไม่ได้เพราะมันตัดตัวเลขทิ้งหมด กรณีจริงคือทุนเรือนหุ้นของ GABLE

        ทุนจดทะเบียน
          หุ้นสามัญ 707,500,000 หุ้น มูลค่าหุ้นละ 1 บาท    707,500   <- ground truth เก็บบรรทัดนี้
        ทุนออกจำหน่ายและชำระเต็มมูลค่าแล้ว
          หุ้นสามัญ 700,021,420 หุ้น มูลค่าหุ้นละ 1 บาท    700,021

    ชื่อรายการเหมือนกันทุกพยัญชนะ ต่างกันแค่จำนวนหุ้น
    """
    return re.sub(r"[^0-9]", "", str(s))


def clean_label(s: str) -> str:
    """เก็บข้อความดิบไว้ให้คนอ่าน แต่ตัดตัวเลขที่ติดพยัญชนะทิ้ง

    ตัวเลขที่เกาะพยัญชนะไทยคือวรรณยุกต์ที่เพี้ยน ("อืน8" = "อื่น")
    ไม่ใช่ค่าตัวเลขจริง ถ้าไม่ตัดจะกลายเป็นข้อมูลปลอม (HANDOFF บั๊ก #4)
    """
    s = re.sub(r"(?<=[ก-ฮ])[0-9](?![\d,])", "", str(s))
    return " ".join(s.split())


def strip_suffix(label: str) -> str:
    """ตัดคำต่อท้าย "- สุทธิ / - หมุนเวียน / - ไม่หมุนเวียน" ออก

    ใช้เป็น "ตัวเลือกสำรอง" เท่านั้น ไม่ได้แทนที่ชื่อเดิม
    เพราะบางงบแยก concept หมุนเวียน/ไม่หมุนเวียน จริงๆ เช่น
        "รายได้รับล่วงหน้า - ไม่หมุนเวียน"  DEFERRED_REVENUE
        "รายได้รับล่วงหน้าส่วนที่จะรับรู้เป็นรายได้ภายในหนึ่งปี"
                                            DEFERRED_REVENUE_CURRENT
    ถ้าตัดทิ้งเลยจะชนกัน
    """
    parts = _DASH_SPLIT.split(str(label))
    if len(parts) > 1 and skeleton(parts[-1]) in _TAIL_SKELETONS:
        return " ".join(p.strip() for p in parts[:-1]).strip()
    # บางงบไม่ใส่ขีดคั่น เขียนติดกันเป็น "สินค้าคงเหลือ สุทธิ" (Thai-Stanley)
    # ทั้งที่บริษัทอื่นเขียน "สินค้าคงเหลือ - สุทธิ" ซึ่งเป็นรายการเดียวกัน
    # ตัดได้เฉพาะ "สุทธิ" เท่านั้น ห้ามขยายไปถึง "หมุนเวียน/ไม่หมุนเวียน"
    # เพราะสองคำนั้นแยก concept กันจริง (ดูคำอธิบายด้านบน)
    words = str(label).split()
    if len(words) > 1 and skeleton(words[-1]) in _TAIL_SKELETONS:
        return " ".join(words[:-1]).strip()
    return label


def label_candidates(label: str, prefixes=None, section=None) -> list[str]:
    """รูปแบบป้ายกำกับที่จะลองเทียบ **เรียงจากเจาะจงที่สุดไปทั่วไปที่สุด**

    ลำดับสำคัญมาก เพราะการเทียบแบบตรงตัวจะหยุดที่ตัวแรกที่เจอ
    ถ้าเอาชื่อเปล่าขึ้นก่อน รายการที่ต้องอาศัยบริบทจะถูกตัดสินผิดทันที

        ส่วนของหนี้สินระยะยาวที่ถึงกำหนดชำระภายในหนึ่งปี   <- หัวข้อย่อย
         - หนี้สินตามสัญญาเช่า          10,656,223         <- ตัวนี้คือ "ส่วนที่ครบใน 1 ปี"
        ...
        หนี้สินไม่หมุนเวียน
          หนี้สินตามสัญญาเช่า          49,375,690         <- ตัวนี้คือตัวไม่หมุนเวียน

    ชื่อเปล่าของสองบรรทัดนี้เหมือนกัน ถ้าตัดสินจากชื่อเปล่าก่อน
    ทั้งคู่จะได้ concept เดียวกันแล้วหยิบผิด

    ปลอดภัยเพราะรูปแบบที่ต่อกันแล้วจะ "ตรงตัว" ได้ก็ต่อเมื่อมีคนใส่ไว้ใน
    alias map เองเท่านั้น ถ้าไม่มีก็ตกมาใช้ชื่อเปล่าตามปกติ

    prefixes = บรรทัดก่อนหน้าที่ไม่มีค่าตัวเลข (อาจเป็นท่อนหน้าของชื่อที่ถูกตัด)
    section  = หัวข้อกลุ่มที่ครอบอยู่ (สินทรัพย์ไม่หมุนเวียน ฯลฯ)
    """
    out, seen = [], set()

    def add(x):
        x = " ".join(str(x).split())
        if x and x not in seen:
            seen.add(x)
            out.append(x)

    pre = list(prefixes or [])
    joins = []
    # PRG ตัดชื่อรายการเดียวข้าม 4 บรรทัด จึงต้องลองต่อย้อนหลังถึง 3 บรรทัด
    if len(pre) >= 3:
        joins.append(" ".join(pre[-3:] + [label]))
    if len(pre) >= 2:
        joins.append(" ".join(pre[-2:] + [label]))
    for p in reversed(pre):
        joins.append(f"{p} {label}")
    joins.append(label)

    # เจาะจงสุด: มีหัวข้อกลุ่มนำหน้าด้วย
    if section:
        for j in joins:
            add(f"{section} {j}")
    for j in joins:
        add(j)
        add(strip_suffix(j))
        add(strip_note_ref(j))
        add(strip_suffix(strip_note_ref(j)))
        jp = strip_period_paren(j)
        if jp != j:
            add(jp)
            add(strip_suffix(jp))
            add(strip_note_ref(jp))
    return out


# --------------------------------------------------------------------------
# การอ่าน PDF
# --------------------------------------------------------------------------

# โปรแกรมภายนอกที่ทั้งระบบพึ่งอยู่ ถ้าไม่มีจะอ่าน PDF ไม่ได้เลยสักไฟล์
POPPLER_BINS = ("pdftotext", "pdfinfo")

POPPLER_HOWTO = {
    "windows": "ดาวน์โหลด poppler จาก github.com/oschwartz10612/poppler-windows "
               "แตกไฟล์แล้วเพิ่มโฟลเดอร์ bin เข้า PATH",
    "darwin": "brew install poppler",
    "linux": "sudo apt install poppler-utils",
}


def missing_poppler() -> list[str]:
    """คืนรายชื่อโปรแกรมของ poppler ที่หาไม่เจอในเครื่องนี้

    ต้องตรวจก่อนเรียกใช้เสมอ ไม่ใช่รอให้ subprocess โยน error ออกมา

    บั๊กจริง: บน Windows ที่ไม่ได้ติดตั้ง poppler subprocess โยน
    FileNotFoundError [WinError 2] ซึ่งผู้เรียกดักไว้แล้วสรุปว่า
    "อ่านไม่ได้ อาจเป็น PDF สแกน" ทั้งที่ไฟล์นั้นมี text layer ปกติ
    ผลคือชี้ให้ผู้ใช้ไปแก้ผิดจุด — ระบบวินิจฉัยสาเหตุผิด อันตรายกว่าไม่วินิจฉัย
    """
    import shutil
    return [b for b in POPPLER_BINS if shutil.which(b) is None]


def poppler_howto() -> str:
    """คำสั่งติดตั้ง poppler ให้ตรงกับระบบปฏิบัติการที่กำลังรันอยู่"""
    import sys
    key = ("windows" if sys.platform.startswith("win")
           else "darwin" if sys.platform == "darwin" else "linux")
    return POPPLER_HOWTO[key]


def page_text(pdf: Path, page: int) -> str:
    """อ่านข้อความหน้าเดียว

    ต้องใช้ pdftotext -layout เท่านั้น
    pdfplumber แทรกช่องว่างกลางตัวเลข ("704,121" -> "7 04,121") ทำให้ค่าผิด
    โดยไม่มี error  ทดสอบแล้ว pdfplumber 3/15 vs pdftotext 15/15
    และห้ามถอด -layout ออก เพราะจะเสียการเรียงคอลัมน์
    """
    return _run_text(
        ["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(pdf), "-"])


def _run_text(cmd) -> str:
    """เรียกโปรแกรมภายนอกแล้วคืนข้อความเสมอ ไม่มีทางคืน None

    บั๊กจริงบน Windows: subprocess.run(...).stdout คืน None ทำให้
    "".join(...) ที่ผู้เรียกเขียนไว้ระเบิดเป็น TypeError กลางหน้าจอผู้ใช้
    แทนที่ระบบจะบอกว่า "อ่านไฟล์นี้ไม่ได้" ซึ่งเป็นสิ่งที่ควรบอก

    ฟังก์ชันที่สัญญาว่าคืน str ต้องคืน str เสมอ ไม่ว่าข้างล่างจะทำอะไร
    ไม่งั้นผู้เรียกทุกรายต้องเขียนโค้ดกันพังเอง ซึ่งจะลืมสักที่จนได้
    """
    try:
        # ต้องระบุ encoding="utf-8" เสมอ ห้ามพึ่ง locale ของเครื่อง
        #
        # poppler ส่งข้อความออกมาเป็น UTF-8 เสมอ แต่ subprocess ที่ไม่ระบุ
        # encoding จะ decode ตาม locale ซึ่งบน Windows ภาษาไทยคือ cp874
        # ผลคือข้อความเพี้ยนทั้งไฟล์ หาหัวเรื่องงบไม่เจอ สกัดได้ 0 แถว
        # และ mojibake ของ cp874 ยังให้ "พยัญชนะไทย" มากกว่าข้อความจริงเสียอีก
        # ทำให้ตัวตรวจ PDF สแกนสรุปว่า "ไฟล์นี้มี text layer ปกติ"
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        # หาโปรแกรมไม่เจอ หรือเรียกไม่ได้ — ผู้เรียกควรตรวจ missing_poppler() ก่อน
        return ""
    return r.stdout if isinstance(r.stdout, str) else ""


def n_pages(pdf: Path) -> int:
    m = re.search(r"Pages:\s+(\d+)", _run_text(["pdfinfo", str(pdf)]))
    return int(m.group(1)) if m else 0


# บางงบไม่เขียนคำว่า "หน่วย" เลย เขียนแค่ "(พันบาท)" ต่อท้ายหัวตาราง
# เช่น KBank "งบการเงินรวม (พันบาท)" ถ้าจับไม่ได้จะได้ unit=unknown
# ซึ่งอันตรายเพราะผิดหน่วย = ผิด 1,000 เท่า
UNIT_BARE = re.compile(r"[(（]\s*(พัน|ล้าน)?\s*บาท\s*[)）]")


# บางงบไม่เขียนคำว่า "หน่วย" และไม่ใส่วงเล็บ แต่พิมพ์หน่วยเป็นคำโดด
# ซ้ำกันในแถวหัวคอลัมน์ ทีละคอลัมน์ เช่น
#   TRUE          หมายเหตุ    พันบาท    พันบาท    พันบาท    พันบาท
#   Thai-Stanley  หมายเหตุ      บาท       บาท       บาท       บาท
# ต้องบังคับให้เจอ "อย่างน้อย 2 ครั้งในบรรทัดเดียว" และห้ามมีตัวเลขนำหน้า
# ไม่งั้นจะไปจับชื่อรายการอย่าง "มูลค่าที่ตราไว้หุ้นละ 5 บาท" แล้วสรุปหน่วยผิด
UNIT_COLUMN = re.compile(r"(?<![\d.,])\s(พัน|ล้าน)?บาท(?=\s|$)")


def detect_unit(text: str) -> str:
    """อ่านหน่วยเงินจากหัวงบ - ผิดแล้วผิด 1,000 เท่า

    MFEC, GABLE = พันบาท | BBIK, IIG = บาท
    """
    t = text.replace("ํ", "")
    m = UNIT_PAT.search(t) or UNIT_BARE.search(t)
    if m:
        return {"พัน": "thousand", "ล้าน": "million", None: "baht"}[m.group(1)]

    for ln in t.split("\n")[:HEADER_LINES * 5]:
        hits = UNIT_COLUMN.findall(ln)
        if len(hits) >= 2 and len(set(hits)) == 1:
            return {"พัน": "thousand", "ล้าน": "million", "": "baht"}[hits[0]]
    return "unknown"


# โครงพยัญชนะของหัวเรื่องแต่ละงบ ใช้คัดว่าหน้านั้นเป็นงบอะไร
#   "งบฐานะการเงิน" / "งบแสดงฐานะการเงิน"   -> มี "งบฐนกรงน" เหมือนกัน
#   "งบกำไรขาดทุน" / "งบกำไรขาดทุนเบ็ดเสร็จ" -> มี "งบกรขดทน" เหมือนกัน
# ทนการเพี้ยนแบบ MFEC ("งบฐำนะกำรเงิน") เพราะ ำ (U+0E33) กับ า (U+0E32)
# ไม่ใช่พยัญชนะ จึงหายไปทั้งคู่
BS_TITLE = "งบฐนกรงน"
IS_TITLE = "งบกรขดทน"
CF_TITLE = "งบกรสงนสด"   # "งบกระแสเงินสด"
# "งบการเปลี่ยนแปลงส่วนของผู้ถือหุ้น" / "งบแสดงการเปลี่ยนแปลงส่วนของเจ้าของ"
# ทั้งสองสำนวนมี "กรปลยนปลงสวนของ" เหมือนกัน
SE_TITLE = "กรปลยนปลงสวนของ"

# หน้าหมายเหตุประกอบงบมีตารางและอ้างชื่องบด้วย ต้องกันไว้ ไม่ใช่ตัวงบ
# ห้ามพิมพ์โครงพยัญชนะด้วยมือเด็ดขาด ให้เรียก skeleton() เสมอ
# ของเดิมเขียน "หมายหตปรกอบ" ซึ่งมี "า" ค้างอยู่ (ควรเป็น "หมยหตปรกอบ")
# skeleton() ตัดสระทิ้งหมด สตริงที่มีสระจึงไม่มีวันตรงกับอะไรเลย
# ผลคือตัวกันหน้าหมายเหตุเป็น dead code มาตลอด ไม่เคยทำงานสักครั้ง
# สิ่งเดียวที่กันไว้จริงคือ MAX_PAGES ซึ่งไม่ได้ตั้งใจให้ทำหน้าที่นี้
NOTES_MARK = skeleton("หมายเหตุประกอบ")

# จำนวนบรรทัดหัวหน้าที่ใช้หาชื่องบ ต้องจำกัด ไม่งั้นเนื้อหากลางหน้าจะหลอกได้
HEADER_LINES = 12


def has_table(text: str, least=15) -> bool:
    return len(re.findall(r"\d{1,3}(?:,\d{3})+", text)) >= least


def page_kind(text: str, strict: bool = True):
    """หน้านี้เป็นงบอะไร -> "BS" (ฐานะการเงิน) / "IS" (กำไรขาดทุน) / None

    ต้องดูจาก "หัวเรื่องของหน้า" ไม่ใช่คำที่โผล่ที่ไหนก็ได้ในหน้า
    เพราะงบกระแสเงินสดและงบแสดงการเปลี่ยนแปลงส่วนของเจ้าของ มีชื่อรายการ
    ซ้ำกับงบฐานะการเงินเป๊ะๆ แต่ค่าเป็น "ผลต่างระหว่างงวด" ไม่ใช่ยอดคงเหลือ
    ถ้าเก็บมาด้วยจะได้ค่าที่หน้าตาสมเหตุสมผลแต่ผิดคนละความหมาย
    (GABLE INVENTORY ได้ -24,216 แทนที่จะเป็น 37,379 มาจากสาเหตุนี้)

    strict=False คือเกณฑ์หลวมแบบเดิม ใช้เป็นทางถอยเมื่อหาหัวเรื่องไม่เจอทั้งไฟล์
    """
    if not has_table(text):
        return None
    head = skeleton("\n".join(text.split("\n")[:HEADER_LINES]))
    if NOTES_MARK in head:
        return None
    if SE_TITLE in head:
        return "SE"
    if CF_TITLE in head:
        return "CF"
    if BS_TITLE in head:
        return "BS"
    if IS_TITLE in head:
        return "IS"
    if not strict:
        sk = skeleton(text)
        if "สนทรพย" in sk or "หนสน" in sk:
            return "BS"
    return None


def is_balance_sheet_page(text: str, strict: bool = True) -> bool:
    return page_kind(text, strict) == "BS"


# --------------------------------------------------------------------------
# การแยกคอลัมน์ตัวเลข
# --------------------------------------------------------------------------

def to_number(tok: str):
    tok = str(tok).strip()
    if tok in {"-", ""}:
        return None
    neg = tok.startswith("(") and tok.endswith(")")
    body = tok.strip("()").replace(",", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", body):
        return None
    v = float(body)
    return -v if neg else v


def find_columns(lines) -> list[int]:
    """หาตำแหน่ง x ของคอลัมน์ตัวเลข จากจุดสิ้นสุดของ token

    ต้องแยกคอลัมน์ด้วยตำแหน่งตัวอักษร ไม่ใช่การนับ token
    เพราะช่องที่งบเขียน "-" ทำให้จำนวน token ไม่คงที่ ถ้านับ token ค่าจะเลื่อน
    ตัวเลขในงบชิดขวาเสมอ จึงใช้ "จุดสิ้นสุด" ของ token เป็นหลัก
    """
    ends = Counter()
    for ln in lines:
        for m in TOKEN.finditer(ln):
            if to_number(m.group()) is not None or m.group().strip() == "-":
                ends[m.end()] += 1
    if not ends:
        return []

    # ตัดตำแหน่งที่โผล่น้อยกว่า 3 ครั้งทิ้งก่อนจัดกลุ่ม
    # ไม่งั้นจะเกิดคอลัมน์ปลอมและค่าเลื่อนทั้งหน้า (HANDOFF บั๊ก #3)
    # บั๊กนี้ให้ค่าที่ "หน้าตาถูกทุกตัว" แค่ไปอยู่ผิดคอลัมน์ ถ้าไม่มีสมการงบดุล
    # คอยตรวจจะไม่มีวันรู้
    ends = {p: c for p, c in ends.items() if c >= 3}
    if not ends:
        return []

    # รวมตำแหน่งที่ห่างกันไม่เกิน 3 ตัวอักษรเป็นคอลัมน์เดียวกัน
    groups, cur = [], []
    for pos in sorted(ends):
        if cur and pos - cur[-1] > 6:
            groups.append(cur)
            cur = []
        cur.append(pos)
    groups.append(cur)

    # ใช้ "ตำแหน่งที่พบบ่อยที่สุด" ในกลุ่มเป็นตัวแทน ไม่ใช่ตำแหน่งขวาสุด
    #
    # ตัวเลขบางหน้าจบไม่ตรงกันเป๊ะ เช่น PTT งบกำไรขาดทุนหน้า 5
    #     ตำแหน่งจบ 103 (9 ครั้ง), 106 (2), 107 (3), 108 (3)
    # ถ้าใช้ขวาสุด (108) แถวส่วนใหญ่จะห่างเกินเกณฑ์ที่ parse_line ยอมรับ
    # แล้วค่าจะหายไปติดกับชื่อรายการแทน
    scored = [(sum(ends[p] for p in g), max(g, key=lambda p: (ends[p], p)))
              for g in groups]
    top = max(s for s, _ in scored)
    return sorted(pos for s, pos in scored if s >= top * 0.25)


def _column_tokens(col, lines):
    out = []
    for ln in lines:
        for m in TOKEN.finditer(ln):
            if abs(m.end() - col) <= 3:
                out.append(m.group().strip())
    return out


def drop_note_column(cols, lines) -> list[int]:
    """คัดให้เหลือเฉพาะคอลัมน์ที่เป็น "จำนวนเงิน" จริง

    ต้องตัดสองอย่างทิ้ง
      1. คอลัมน์หมายเหตุ (ซ้ายสุด ค่าเป็นเลขสั้นไม่มีคอมม่า)
      2. คอลัมน์ "% เปลี่ยนแปลง" ที่งบธนาคารชอบแทรกไว้

    งบ KBank มี 6 คอลัมน์: รวม-ปัจจุบัน / รวม-ก่อน / %เปลี่ยน /
    เฉพาะธนาคาร-ปัจจุบัน / เฉพาะธนาคาร-ก่อน / %เปลี่ยน
    ถ้าเอา 4 คอลัมน์ขวาสุดตรงๆ จะได้ %เปลี่ยน มาเป็นค่าเงิน แล้วผิดทั้งหน้า
    (สมการงบดุลออกมาเป็น "2 != 1" เพราะบวกเปอร์เซ็นต์กัน)

    จุดสังเกต: คอลัมน์จำนวนเงินในงบพวกนี้เกือบทุกค่ามีคอมม่าคั่นหลักพัน
    ส่วนคอลัมน์เปอร์เซ็นต์กับคอลัมน์หมายเหตุแทบไม่มีเลย
    """
    if len(cols) <= 4:
        return cols

    money = []
    for col in cols:
        toks = _column_tokens(col, lines)
        if not toks:
            continue
        with_comma = sum(1 for t in toks if "," in t)
        if with_comma / len(toks) >= 0.4:
            money.append(col)

    # ถ้าคัดแล้วเหลือน้อยเกินไป แปลว่าเกณฑ์ไม่เหมาะกับงบนี้ ใช้ของเดิม
    if len(money) >= 2:
        cols = money
    else:
        first = cols[0]
        toks = _column_tokens(first, lines)
        short = sum(1 for t in toks if "," not in t and len(t.strip("()-")) <= 2)
        if toks and short / len(toks) > 0.6:
            cols = cols[1:]

    # ปกติงบไทยมี 4 คอลัมน์ค่า (งบรวม 2 งวด + งบเฉพาะกิจการ 2 งวด)
    if len(cols) <= 4:
        return cols

    # ตารางเปรียบเทียบสองกลุ่มต้องมีจำนวนคอลัมน์เป็นเลขคู่เสมอ
    # ถ้าเป็นเลขคี่แปลว่ายังมีคอลัมน์หมายเหตุปนอยู่ ให้ใช้ 4 ตัวขวาสุดแบบเดิม
    # (SCB มี หมายเหตุ + 4 คอลัมน์ค่า = 5 ถ้าแบ่งครึ่งจะได้คอลัมน์ผิด)
    if len(cols) % 2:
        return cols[-4:]

    # เหลือเกิน 4 และเป็นเลขคู่ = งบเปรียบเทียบหลายงวด เช่นงบกำไรขาดทุนของ KBank
    #   งบการเงินรวม              | งบการเงินเฉพาะธนาคาร
    #   ไตรมาส1/69 ไตรมาส4/68 ไตรมาส1/68 | ไตรมาส1/69 ไตรมาส4/68 ไตรมาส1/68
    # เราเก็บแค่ 4 ช่องตามโครงเดิม จึงต้องเลือกให้ตรงนิยามของคอลัมน์อื่น
    #   cur  = งวดปัจจุบัน        -> คอลัมน์แรกของกลุ่ม
    #   prev = งวดเดียวกันปีก่อน  -> คอลัมน์สุดท้ายของกลุ่ม
    # (ไม่ใช่ไตรมาสก่อนหน้า เพราะงบกำไรขาดทุนของบริษัทอื่นเทียบกับงวดเดียวกัน
    #  ปีก่อนเสมอ ถ้าหยิบไตรมาส 4/68 มาจะเทียบข้ามบริษัทไม่ได้)
    half = len(cols) // 2
    left, right = cols[:half], cols[half:]
    return [left[0], left[-1], right[0], right[-1]]


def parse_line(ln: str, cols):
    """คืน (label, [ค่า 4 คอลัมน์]) หรือ None ถ้าไม่ใช่บรรทัดข้อมูล

    จุดสำคัญ: ตัดป้ายกำกับที่ "token ตัวแรกที่ตกลงในคอลัมน์ค่า"
    ไม่ใช่ที่ token ตัวแรกของบรรทัด  แก้สองปัญหาพร้อมกัน

      1. ป้ายกำกับที่มีตัวเลขอยู่ในชื่อ
         "หุ้นสามัญ 441,453,555 หุ้น มูลค่าหุ้นละ 1 บาท   441,454  441,454"
         ถ้าตัดที่ token แรก จะเหลือชื่อแค่ "หุ้นสามัญ"

      2. วรรณยุกต์ที่เพี้ยนเป็นเลข (HANDOFF บั๊ก #4)
         "องค์ประกอบอืน8 ของส่วนของผู้ถือหุ้น   (2,036,264) ..."
         เลข 8 ตรงกลางเป็น token ทำให้ชื่อถูกตัดกลางคำจน match ไม่ติด
    """
    hits = [(m.start(), m.end(), m.group()) for m in TOKEN.finditer(ln)]
    if not hits:
        return None

    vals = [None] * len(cols)
    val_starts = []
    for start, end, tok in hits:
        # ค่าติดลบพิมพ์ในวงเล็บ ")" ยื่นเลยขอบขวาของบล็อกตัวเลขออกไป
        # ทำให้ตำแหน่งท้าย token เลื่อนขวาจนหลุดคอลัมน์ เช่นหน้า 5 ของ PTT
        #     28,835,585,539   จบที่ 103
        #    (1,201,527,893)   จบที่ 108   <- ห่าง 5 ถูกทิ้งทั้งที่เป็นค่าคอลัมน์เดียวกัน
        # งบการเงินจัดชิดขวาที่ "บล็อกตัวเลข" ไม่ใช่ที่วงเล็บ จึงต้องวัดทั้งสองแบบ
        # แล้วเอาระยะที่ใกล้กว่า ไม่ใช่ขยาย tolerance ซึ่งจะไปกวาดเลขอ้างอิงหมายเหตุมาด้วย
        ends = {end}
        if tok.rstrip().endswith(")"):
            ends.add(end - (len(tok) - len(tok.rstrip(") ")))) 
        dists = [min(abs(e - c) for e in ends) for c in cols]
        i = dists.index(min(dists))
        if dists[i] > 4:
            continue
        num = to_number(tok)
        if num is None and tok.strip() != "-":
            continue
        vals[i] = num          # "-" เก็บเป็น None เท่ากับช่องว่าง
        val_starts.append(start)

    if not val_starts:
        return None

    # ไม่ตัดแถวที่ป้ายกำกับสั้นทิ้งตรงนี้ เพราะชื่ออาจถูกตัดขึ้นบรรทัดใหม่จน
    # ท่อนท้ายแทบไม่เหลือพยัญชนะ ผู้เรียกต้องเป็นคนตัดสินหลังต่อบรรทัดแล้ว
    #     GABLE: "เงินฝากธนาคารทีมB ีภาระคาประก"
    #            "        @ํ    ัน        100   100   -"   <- ท่อนท้ายคือ "ัน"
    label = clean_label(ln[: min(val_starts)])

    # กรองแถวหัวตาราง: ค่าทุกช่องเป็นปี พ.ศ. หรือ ค.ศ.
    present = [v for v in vals if v is not None]
    if present and all(2400 <= v <= 2700 or 1990 <= v <= 2100 for v in present):
        return None

    return label, vals


# --------------------------------------------------------------------------
# ตารางเทียบคำศัพท์ (alias map)
# --------------------------------------------------------------------------

class Matcher:
    """ตารางเทียบชื่อรายการ -> concept

    มีสองชั้น
      exact       {โครงพยัญชนะ: concept}                 สำหรับ alias ทั่วไป
      exact_digit {(โครงพยัญชนะ, ตัวเลข): concept}       สำหรับ alias ที่มีตัวเลขในชื่อ

    ถ้าโครงพยัญชนะหนึ่งถูกใช้ในชั้น exact_digit แล้ว ชื่อที่ให้โครงนั้นจะ
    "ต้องตรงตัวเลขด้วย" เท่านั้น ห้ามตกกลับไปเทียบแบบไม่สนตัวเลข มิฉะนั้น
    ทุนจดทะเบียนกับทุนที่ออกและชำระแล้วจะกลายเป็น concept เดียวกัน
    """

    def __init__(self):
        self.exact = {}
        self.exact_digit = {}
        self.digit_skeletons = set()

    @property
    def pairs(self):
        return list(self.exact.items())

    def __bool__(self):
        return bool(self.exact or self.exact_digit)

    def __len__(self):
        return len(self.exact) + len(self.exact_digit)

    @property
    def concepts(self):
        return set(self.exact.values()) | set(self.exact_digit.values())


def build_matcher(alias_csv) -> Matcher:
    m = Matcher()
    p = Path(alias_csv)
    if not p.exists():
        return m
    a = pd.read_csv(p, encoding="utf-8-sig").dropna(subset=["alias", "concept"])
    for r in a.itertuples():
        sk = skeleton(r.alias)
        if not sk:
            continue
        concept = str(r.concept).strip()
        d = digits(r.alias)
        if d:
            m.exact_digit.setdefault((sk, d), concept)
            m.digit_skeletons.add(sk)
        else:
            m.exact.setdefault(sk, concept)
    return m


# หัวข้อกลุ่มในงบ ใช้แยกรายการที่ "ชื่อเหมือนกันเป๊ะ" แต่คนละกลุ่ม
# กรณีจริง: MFEC มี "ต้นทุนงานบริการจ่ายล่วงหน้า" สองบรรทัดในหน้าเดียวกัน
#   สินทรัพย์หมุนเวียน    ... ต้นทุนงานบริการจ่ายล่วงหน้า   1,341,434
#   สินทรัพย์ไม่หมุนเวียน ... ต้นทุนงานบริการจ่ายล่วงหน้า     487,070
# ต่างกันแค่หัวข้อกลุ่มที่ครอบอยู่ ไม่มีอะไรในบรรทัดนั้นแยกได้เลย
SECTION_HEADERS = [
    "สินทรัพย์หมุนเวียน",
    "สินทรัพย์ไม่หมุนเวียน",
    "หนี้สินหมุนเวียน",
    "หนี้สินไม่หมุนเวียน",
    "ส่วนของผู้ถือหุ้น",
    "ส่วนของเจ้าของ",
    "ทุนจดทะเบียน",
    "ทุนที่ออกและชำระแล้ว",
    # --- งบกำไรขาดทุน ---
    # สองหัวข้อนี้จำเป็นมาก เพราะใต้แต่ละอันมีบรรทัดชื่อเหมือนกันเป๊ะ
    #   การแบ่งปันกำไร (ขาดทุน)
    #     ส่วนที่เป็นของผู้ถือหุ้นของบริษัทฯ                 83,951
    #   การแบ่งปันกำไรขาดทุนเบ็ดเสร็จรวม
    #     ส่วนที่เป็นของผู้ถือหุ้นของบริษัทฯ                 84,069
    "กำไรขาดทุนเบ็ดเสร็จอื่น",
    "การแบ่งปันกำไร",
    "การแบ่งปันกำไร (ขาดทุน)",
    "การแบ่งปันกำไรขาดทุนเบ็ดเสร็จรวม",
    "การแบ่งปันกำไรขาดทุนเบ็ดเสร็จ",
    # PTT เขียนหัวข้อนี้โดยไม่มีคำว่า "ขาดทุน" ถ้าไม่ใส่ไว้ บรรทัดลูกบรรทัดที่สอง
    # (ส่วนที่เป็นของผู้มีส่วนได้เสียฯ) จะไม่มีหัวข้อกลุ่มครอบ แล้วไปชนกับ NCI
    # ในงบฐานะการเงินซึ่งชื่อเหมือนกันเป๊ะ
    "การแบ่งปันกำไรเบ็ดเสร็จรวม",
    "การแบ่งปันกำไร (ขาดทุน) เบ็ดเสร็จรวม",
    "การแบ่งปันกำไรเบ็ดเสร็จ",
    "กำไรต่อหุ้น",
    "กำไร (ขาดทุน) ต่อหุ้น",
    "กำไรขาดทุนต่อหุ้น",
    "รายได้",
    "ค่าใช้จ่าย",
    # --- งบกระแสเงินสด ---
    "กระแสเงินสดจากกิจกรรมดำเนินงาน",
    "กระแสเงินสดจากกิจกรรมลงทุน",
    "กระแสเงินสดจากกิจกรรมจัดหาเงิน",
    # หัวข้อบล็อกกระทบยอดเงินสดท้ายงบกระแสเงินสด ใช้แยกบรรทัด
    # "เงินเบิกเกินบัญชี" ในบล็อกนี้ ออกจากบรรทัดเดียวกันในงบฐานะการเงิน
    # (งบฐานะการเงินเป็นหนี้สิน ค่าบวก / ในบล็อกนี้เป็นตัวหักออก ค่าลบ)
    "ประกอบด้วย",
    "เงินสดและรายการเทียบเท่าเงินสดในงบกระแสเงินสด ประกอบด้วย",
    "ข้อมูลกระแสเงินสดเปิดเผยเพิ่มเติม",
    "รายการที่ไม่ใช่เงินสด",
]
# {โครงพยัญชนะ: ข้อความมาตรฐาน} - เทียบด้วยโครงเพราะหัวข้อก็เพี้ยนได้เหมือนกัน
SECTION_BY_SKELETON = {skeleton(h): h for h in SECTION_HEADERS}


def detect_section(line: str):
    """บรรทัดนี้เป็นหัวข้อกลุ่มหรือไม่ ถ้าใช่คืนข้อความมาตรฐานของหัวข้อนั้น

    ต้องเทียบแบบ "ทั้งบรรทัดคือหัวข้อ" ไม่ใช่ substring
    ไม่งั้น "รวมสินทรัพย์หมุนเวียน" จะถูกอ่านเป็นหัวข้อกลุ่มด้วย
    """
    return SECTION_BY_SKELETON.get(skeleton(line))


def match_concept(label, matcher: Matcher, prefixes=None, cutoff=0.90,
                  section=None):
    """เทียบป้ายกำกับกับ alias map -> (concept, score, label_ที่ใช้)

    ลองหลายรูปแบบแล้วเลือกอันที่คะแนนสูงสุด:
        - ชื่อตามที่สกัดมา
        - ชื่อที่ตัดคำต่อท้าย "- สุทธิ"
        - ชื่อที่ต่อกับบรรทัดก่อนหน้า (กรณีชื่อถูกตัดขึ้นบรรทัดใหม่)

    การให้ "คะแนนตัดสิน" แทนที่จะบังคับต่อบรรทัดเสมอ เป็นตัวกันหัวข้อกลุ่ม
    ("สินทรัพย์หมุนเวียน") ไม่ให้ถูกเอามาต่อหน้าชื่อรายการโดยไม่ตั้งใจ
    เพราะการต่อจะทำให้คะแนนลดลง ระบบจึงเลือกชื่อเปล่าเอง
    """
    cands = label_candidates(label, prefixes, section)

    # รอบที่ 1: เทียบตรงตัว ไล่จากรูปแบบเจาะจงที่สุดลงมา ตัวแรกที่เจอชนะ
    for cand in cands:
        sk = skeleton(cand)
        if not sk:
            continue
        if sk in matcher.digit_skeletons:
            # โครงนี้ผูกกับตัวเลขในชื่อ ต้องตรงตัวเลขเท่านั้น ห้ามตกกลับ
            hit = matcher.exact_digit.get((sk, digits(cand)))
            if hit:
                return hit, 1.0, cand
            continue
        if sk in matcher.exact:
            return matcher.exact[sk], 1.0, cand

    # รอบที่ 2: ไม่เจอตรงตัวเลย ค่อยเทียบแบบใกล้เคียง เอาคะแนนสูงสุด
    best_concept, best_score, best_label = None, 0.0, label
    for cand in cands:
        sk = skeleton(cand)
        if not sk or sk in matcher.digit_skeletons:
            continue
        for k, concept in matcher.pairs:
            r = SequenceMatcher(None, sk, k).ratio()
            if r > best_score:
                best_concept, best_score, best_label = concept, r, cand
    if best_score >= cutoff:
        return best_concept, best_score, best_label
    return None, best_score, label


def gt_concept(item, matcher: Matcher, cutoff=0.98, section=None):
    """map ชื่อรายการใน ground truth เป็น concept

    ground truth พิมพ์ด้วยมือจึงสะอาด ใช้เกณฑ์เข้มกว่าฝั่งสกัด
    เพื่อไม่ให้จับคู่ผิดแล้วทำให้ตัวเลขความแม่นยำเพี้ยน

    section = หัวข้อกลุ่มที่ครอบแถวนั้นอยู่ (ถ้า ground truth มีคอลัมน์นี้)
    จำเป็นสำหรับแถวที่ชื่อซ้ำกันในหน้าเดียว เช่นงบกำไรขาดทุนของ GABLE

        การแบ่งปันกำไร (ขาดทุน)
          ส่วนที่เป็นของผู้ถือหุ้นของบริษัทฯ        83,951
        การแบ่งปันกำไรขาดทุนเบ็ดเสร็จรวม
          ส่วนที่เป็นของผู้ถือหุ้นของบริษัทฯ        84,069

    ground truth ต้องบันทึก item ว่า "ส่วนที่เป็นของผู้ถือหุ้นของบริษัทฯ"
    ตามที่งบเขียนจริง แล้วแยกสองแถวด้วยคอลัมน์ section ไม่ใช่เอาหัวข้อ
    มาต่อหน้าชื่อ เพราะเอกสารไม่ได้เขียนแบบนั้น
    """
    if section is not None and (isinstance(section, str) and section.strip()):
        section = section.strip()
    else:
        section = None
    concept, _, _ = match_concept(item, matcher, cutoff=cutoff, section=section)
    return concept


def read_gt(path):
    """อ่าน ground truth แล้วแปลงคอลัมน์ค่าเป็นตัวเลขให้เรียบร้อย

    รับได้ทั้งเลขล้วนและรูปแบบที่คนกรอกตามงบจริง
        "1,341,434"   -> 1341434.0
        "(1,164)"     -> -1164.0        วงเล็บ = ติดลบ
        "-" หรือว่าง  -> ว่าง
    ถ้าไม่แปลง pandas จะอ่านเป็นข้อความแล้วเทียบกับตัวเลขไม่ได้เลย
    """
    df = pd.read_csv(path, encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]
    # pandas อ่าน "TRUE"/"FALSE" เป็น boolean ทำให้ชื่อบริษัท TRUE
    # กลายเป็น True แล้วเทียบกับผลสกัดที่เป็นข้อความ "TRUE" ไม่ติดเลย
    # อาการคือแม่นยำตกเป็น 0% ทั้งบริษัทโดยไม่มี error ใดๆ
    if "company" in df.columns:
        df["company"] = df["company"].map(
            lambda v: "TRUE" if v is True else ("FALSE" if v is False else str(v).strip()))
    for c in COLS:
        if c in df.columns and df[c].dtype == object:
            df[c] = df[c].map(lambda v: None if pd.isna(v) else to_number(v))
    return df


def apply_cf_position(df, col="concept"):
    """เติมท้าย "_AFTER" ให้บรรทัดปรับปรุงที่อยู่ "หลัง" ยอดเงินสดเพิ่ม(ลด)สุทธิ

    ใช้กับ ground truth ของงบกระแสเงินสด ซึ่งบันทึกชื่อรายการตามที่งบเขียน
    จึงไม่มีทางรู้ตำแหน่งจากตัวชื่อ ต้องดูจากลำดับแถวในไฟล์แทน
    (แถวต้องเรียงตามลำดับในเอกสาร ซึ่ง make_gt_template เรียงให้อยู่แล้ว)
    """
    out = list(df[col])
    for _, idx in df.groupby("company", sort=False).groups.items():
        seen = False
        for i in idx:
            v = df.at[i, col]
            if v == "CASH_NET_CHANGE":
                seen = True
            elif seen and v in CF_ADJUSTMENTS:
                out[list(df.index).index(i)] = f"{v}_AFTER"
    return out


def gt_concepts(df, matcher: Matcher, cutoff=0.98):
    """map ทั้ง DataFrame ของ ground truth ใช้คอลัมน์ section ถ้ามี"""
    if "section" in df.columns:
        return [gt_concept(i, matcher, cutoff, s)
                for i, s in zip(df["item"], df["section"])]
    return [gt_concept(i, matcher, cutoff) for i in df["item"]]


# --------------------------------------------------------------------------
# การหา path ของข้อมูล (รันได้ทั้งบน Kaggle และในเครื่อง)
# --------------------------------------------------------------------------

# sovle_Data_financial มาก่อน เพราะเป็นไฟล์ชุดที่โหลดมาใหม่และสมบูรณ์กว่า
# ไฟล์ PTT กับ PRG ใน Financial_DATA เป็นคนละฉบับ/ไม่มีหัวเรื่อง ห้ามใช้
_PDF_ROOTS = [
    "/kaggle/input/datasets/switchonchannel/sovle-data-financial/sovle_Data_financial",
    "/kaggle/input/sovle-data-financial/sovle_Data_financial",
    "./sovle_Data_financial",
    "../sovle_Data_financial",
    "/kaggle/input/datasets/switchonchannel/financial-data-v2/Financial_DATA",
    "/kaggle/input/financial-data-v2/Financial_DATA",
    "./Financial_DATA",
    "../Financial_DATA",
]
_GT_ROOTS = [
    "/kaggle/input/datasets/switchonchannel/ground-truth-financial/ground truth Financial",
    "/kaggle/input/ground-truth-financial/ground truth Financial",
    ".",
    "./ground truth Financial",
]
_OUT_ROOTS = ["/kaggle/working", "."]


def _first_existing(cands, must_contain=None):
    for c in cands:
        p = Path(c)
        if not p.exists():
            continue
        if must_contain and not (p / must_contain).exists():
            continue
        return p
    return None


def default_pdf_dir():
    return _first_existing(_PDF_ROOTS)


def default_gt_dir(filename="tech-01.csv"):
    return _first_existing(_GT_ROOTS, must_contain=filename)


def default_out_dir():
    return _first_existing(_OUT_ROOTS) or Path(".")


# เรียงจากใหม่ไปเก่า เพื่อให้ใช้เวอร์ชันล่าสุดที่มีอยู่โดยอัตโนมัติ
ALIAS_NAMES = ["account_aliases_v6.csv", "account_aliases_v4.csv",
               "account_aliases_v3.csv", "account_aliases_v2.csv",
               "account_aliases.csv"]


def default_alias_path():
    for name in ALIAS_NAMES:
        d = _first_existing(_GT_ROOTS, must_contain=name)
        if d:
            return d / name
    return None


def resolve(explicit, fallback, name):
    """เลือก path ที่ผู้ใช้ระบุก่อน ถ้าไม่ระบุจึงใช้ค่าที่ตรวจเจออัตโนมัติ"""
    if explicit:
        return Path(explicit)
    if fallback is None:
        raise SystemExit(
            f"หา {name} ไม่เจอ กรุณาระบุด้วย argument (ดู --help)"
        )
    return Path(fallback)
