#!/usr/bin/env python3
"""
se_matrix.py - อ่านงบแสดงการเปลี่ยนแปลงส่วนของเจ้าของแบบเต็มตาราง (long format)

ต่างจากตัวอ่าน SE ใน extract_fs.py ตรงไหน
--------------------------------------------------------------------------
extract_fs.py เก็บเฉพาะ "คอลัมน์รวม" คอลัมน์เดียว เพราะโครงข้อมูลของทั้งระบบ
เป็น con_cur/con_prev/sep_cur/sep_prev ซึ่งใช้แกนไปกับ "งวด" หมดแล้ว
ไม่เหลือที่ให้แกน "องค์ประกอบของส่วนของเจ้าของ"

แต่งบนี้เป็นตารางสองแกนจริงๆ
    แถว = รายการเคลื่อนไหว (ยอดต้นงวด กำไรสุทธิ เงินปันผล ยอดปลายงวด)
    คอลัมน์ = องค์ประกอบ (ทุนเรือนหุ้น ส่วนเกินมูลค่าหุ้น กำไรสะสม ... รวม)

ตัวอย่างที่เห็นชัดที่สุดคือ SCB
    โอนไปกำไรสะสม   ...  (74,307)  ...  74,307  ...  -
คอลัมน์รวมเป็น "-" เพราะสองรายการหักล้างกันพอดี ถ้าเก็บแค่คอลัมน์รวม
จะเห็นเป็นแถวว่างเปล่า ทั้งที่ข้อมูลจริงมีอยู่ 2 ตัว

ไฟล์นี้จึงเก็บทุกช่องในตาราง แล้วออกเป็น long format แทนที่จะกาง 14 คอลัมน์
    company, source, page, block, period, col_index, equity_component, item, value

จุดที่ต้องระวังเป็นพิเศษ
--------------------------------------------------------------------------
หัวคอลัมน์ของงบนี้กระจายข้ามหลายบรรทัด (SCB ใช้ 10 บรรทัด) และข้อความเพี้ยน
หนักกว่าส่วนอื่นของงบ การอ่านชื่อคอลัมน์จึงเชื่อถือได้น้อยที่สุดในไฟล์นี้

**ตัวเลขไม่ได้พึ่งชื่อคอลัมน์เลย** ชื่อเป็นแค่ป้ายกำกับให้คนอ่าน
ตำแหน่งของค่าตัดสินด้วยพิกัด x อย่างเดียว และการตรวจสอบก็ใช้ความสัมพันธ์
ตามแถว (ยอดต้นงวด + รายการเคลื่อนไหว = ยอดปลายงวด) ซึ่งไม่ต้องรู้ชื่อคอลัมน์
ถ้าชื่อคอลัมน์อ่านมาเพี้ยน ตัวเลขยังถูกและยังตรวจสอบได้อยู่
"""
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

import pandas as pd

import fs_core as core
import extract_fs as ef

MAX_PAGES = 20

# บรรทัดที่เป็น "ยอดยกมา/ยอดคงเหลือ" ใช้ตัดบล็อกและใช้เป็นหลักในการตรวจสอบ
BALANCE_SKELETON = core.skeleton("ยอดคงเหลือ")
# บางงบเขียน "ยอดยกมา" แทน
BALANCE_ALT = core.skeleton("ยอดยกมา")

# ต้องมีค่าตัวเลขอย่างน้อยเท่านี้ถึงนับว่าเป็นแถวข้อมูล ไม่ใช่บรรทัดหัวตาราง
MIN_VALUES_IN_ROW = 3


def is_balance_row(label: str) -> bool:
    sk = core.skeleton(label)
    return sk.startswith(BALANCE_SKELETON) or sk.startswith(BALANCE_ALT)


def page_text(pdf: Path, page: int) -> str:
    r = subprocess.run(
        ["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(pdf), "-"],
        capture_output=True, text=True)
    return r.stdout


def row_values(ln: str, cols: list[int]) -> tuple[str, list, int, int]:
    """คืน (ชื่อรายการ, ค่าทุกคอลัมน์) โดยไม่จำกัดจำนวนคอลัมน์ที่ 4

    ใช้ตรรกะเดียวกับ core.parse_line คือตัดชื่อที่ token ตัวแรกที่ตกในคอลัมน์ค่า
    และวัดระยะทั้งแบบรวมและไม่รวมวงเล็บปิด (ค่าติดลบยื่นเลยขอบขวา)
    """
    vals = [None] * len(cols)
    starts = []
    filled = 0
    for m in core.TOKEN.finditer(ln):
        tok = m.group()
        num = core.to_number(tok)
        is_dash = tok.strip() == "-"
        if num is None and not is_dash:
            continue
        ends = {m.end()}
        if tok.rstrip().endswith(")"):
            ends.add(m.end() - (len(tok) - len(tok.rstrip(") "))))
        dists = [min(abs(e - c) for e in ends) for c in cols]
        i = dists.index(min(dists))
        # เกณฑ์ระยะแบบยืดหยุ่นตามความห่างของคอลัมน์ แทนที่จะตายตัวที่ 4
        #
        # ตัวเลขในงบไม่ได้จบตรงกันเป๊ะทุกแถว MFEC หน้า 6 คอลัมน์กำไรสะสม
        #     แถวยอดต้นงวด 1,136,815  จบที่ 125
        #     แถวอื่นๆ                จบที่ 130
        # ห่าง 5 ซึ่งเกินเกณฑ์ 4 ไปนิดเดียว ค่าเลยหายไปทั้งช่อง
        # แต่จะขยายเป็นค่าคงที่ก็เสี่ยงไปกวาดเลขอ้างอิงหมายเหตุเข้ามา
        #
        # คอลัมน์ของงบนี้ห่างกันราว 20 ตัวอักษร ถ้าใช้ "ครึ่งหนึ่งของระยะไปยัง
        # คอลัมน์ที่ใกล้รองลงมา" เป็นเกณฑ์ จะไม่มีทางจับผิดคอลัมน์เลย
        # และยังกันเลขนอกตารางด้วยเพดาน 8
        second = min([d for j, d in enumerate(dists) if j != i], default=99)
        if dists[i] > min(8, second / 2):
            continue
        vals[i] = num
        starts.append(m.start())
        filled += 1          # "-" นับเป็นช่องที่กรอกแล้วด้วย (แปลว่าศูนย์)
    if not starts:
        return "", vals, 0, 0
    return core.clean_label(ln[: min(starts)]), vals, filled, min(starts)


def column_names(lines, cols, first_data, label_end) -> list[str]:
    """ตั้งชื่อคอลัมน์จากบล็อกหัวตาราง โดยจับ token เข้าคอลัมน์ตามพิกัด x

    หัวตารางของงบนี้เขียนเป็นข้อความหลายบรรทัดวางเรียงกันในแนวตั้ง
    จึงต้องรวมจากบนลงล่างต่อคอลัมน์ ไม่ใช่อ่านทีละบรรทัด

    ขอบเขตของคอลัมน์ = กึ่งกลางระหว่างตำแหน่งขวาของคอลัมน์ก่อนหน้ากับของตัวเอง
    ใช้ "จุดกึ่งกลางของ token" ตัดสิน เพราะข้อความหัวตารางจัดกึ่งกลาง
    ไม่ได้ชิดขวาเหมือนตัวเลข
    """
    # ทุกอย่างที่อยู่ซ้ายกว่า label_end คือคอลัมน์ชื่อรายการ ไม่ใช่หัวคอลัมน์ค่า
    # ถ้าไม่ตัดออก ชื่อบริษัทกับหัวเรื่องงบจะถูกดูดเข้ามาเป็นชื่อคอลัมน์แรก
    bounds = []
    prev = label_end
    for c in cols:
        bounds.append((prev, c + 4))
        prev = c + 4
    parts = [[] for _ in cols]
    for ln in lines[:first_data]:
        for m in re.finditer(r"\S+", ln):
            mid = (m.start() + m.end()) / 2
            if mid < label_end:
                continue
            for i, (lo, hi) in enumerate(bounds):
                if lo <= mid < hi:
                    parts[i].append(m.group())
                    break
    out = []
    for i, ps in enumerate(parts):
        name = core.clean_label(" ".join(ps))
        out.append(name if core.skeleton(name) else f"คอลัมน์ {i + 1}")
    return out


def read_page(pdf: Path, company: str, page: int, text: str) -> list[dict]:
    lines = text.split("\n")
    cols = core.find_columns(lines)
    if len(cols) < 3:
        return []

    # ตัดคอลัมน์ปลอมที่เกิดจากเลขวันที่ในชื่อแถว
    #   "ยอดคงเหลือ ณ วันที่ 1 มกราคม 2568"   <- เลข 1
    #   "ยอดคงเหลือ ณ วันที่ 31 มีนาคม 2568"  <- เลข 31
    # เลขพวกนี้อยู่ตำแหน่งเดียวกันทุกแถวจึงถูกนับเป็นคอลัมน์
    # เกณฑ์: ถ้าค่าทุกตัวในคอลัมน์นั้นเป็นจำนวนเต็ม 1-31 ทั้งหน้า ให้ทิ้ง
    def col_values(ci):
        out = []
        for ln in lines:
            for m in core.TOKEN.finditer(ln):
                num = core.to_number(m.group())
                if num is None:
                    continue
                if abs(m.end() - cols[ci]) <= 4:
                    out.append(num)
        return out
    # ตัดเฉพาะคอลัมน์ซ้ายสุดที่ติดกันเป็นแถวเท่านั้น เพราะเลขวันที่อยู่ในชื่อแถว
    # ซึ่งอยู่ซ้ายกว่าคอลัมน์ค่าเสมอ ถ้าตัดทุกตำแหน่งจะไปโดนคอลัมน์จริงที่ค่าน้อย
    # (GABLE มีคอลัมน์ที่ค่าเป็น 14 กับ 17 ซึ่งเป็นตัวเลขจริง ไม่ใช่วันที่)
    def is_date_col(vs):
        # เลขในชื่อแถว "ณ วันที่ 31 มีนาคม 2568" มีทั้งเลขวันและเลขปี
        # ต้องยอมรับทั้งสองแบบ ไม่งั้นคอลัมน์ปลอมของ SAT/SCB จะหลุดมา
        return vs and all(float(v).is_integer()
                          and (1 <= v <= 31 or 2400 <= v <= 2700) for v in vs)

    lead = 0
    while lead < len(cols) - 3:
        if is_date_col(col_values(lead)):
            lead += 1
        else:
            break
    cols = cols[lead:]

    # รวมคอลัมน์ที่จริงๆ เป็นคอลัมน์เดียวกันแต่ถูกแยกเพราะตัวเลขจบไม่ตรงกัน
    #
    # CPF หน้า 11 มีตำแหน่ง 245 กับ 254 ห่างกัน 9 ซึ่งเกินเกณฑ์รวมของ
    # find_columns (6) แต่เป็นคอลัมน์เดียวกัน ผลคือนับคอลัมน์เกินมา 1
    #
    # เกณฑ์ตัดสิน: ถ้าไม่มีบรรทัดไหนเลยที่มี "ตัวเลข" อยู่ในทั้งสองตำแหน่ง
    # แปลว่าสองตำแหน่งนี้ไม่เคยอยู่ร่วมแถวกัน = เป็นคอลัมน์เดียวกัน
    # (ถ้าเป็นคนละคอลัมน์จริง ต้องมีอย่างน้อยหนึ่งแถวที่มีเลขทั้งคู่
    #  เช่น SAT หน้า 9 ตำแหน่ง 79 กับ 90 ห่างแค่ 11 แต่มีเลขร่วมแถวกัน)
    def numeric_lines(c):
        out = set()
        for k, ln in enumerate(lines):
            for m in core.TOKEN.finditer(ln):
                if core.to_number(m.group()) is not None and abs(m.end() - c) <= 4:
                    out.add(k)
        return out

    merged = [cols[0]]
    for c in cols[1:]:
        prev = merged[-1]
        if c - prev <= 12 and not (numeric_lines(prev) & numeric_lines(c)):
            # เก็บตำแหน่งที่มีตัวเลขมากกว่าไว้เป็นตัวแทน
            merged[-1] = c if len(numeric_lines(c)) > len(numeric_lines(prev)) else prev
        else:
            merged.append(c)
    cols = merged

    # หาแถวข้อมูลก่อน เพื่อจะได้รู้ว่าบล็อกหัวตารางจบตรงไหน
    parsed = []
    for k, ln in enumerate(lines):
        label, vals, filled, start = row_values(ln, cols)
        if filled < MIN_VALUES_IN_ROW:
            continue
        # กรองบรรทัดหัวตารางที่มีแต่เลขปี พ.ศ./ค.ศ. และเลขวันที่
        # กรองบรรทัดหัวตารางที่มีแต่เลขปี ห้ามกรองด้วยช่วง 1-31 เพราะรายการจริง
        # ที่มีค่าน้อย (GABLE การจ่ายโดยใช้หุ้นเป็นเกณฑ์ = 14) จะหายไปทั้งแถว
        present = [v for v in vals if v is not None]
        if present and all(2400 <= v <= 2700 for v in present):
            continue
        parsed.append((k, label, vals, start))
    if not parsed:
        return []

    first_data = parsed[0][0]
    label_end = min(p[3] for p in parsed)
    names = column_names(lines, cols, first_data, label_end)
    unit = core.detect_unit(text)
    side = ef.equity_page_side(text) if hasattr(ef, "equity_page_side") else None

    # ต่อชื่อรายการที่ถูกตัดขึ้นบรรทัดใหม่ (บรรทัดก่อนหน้าที่ไม่มีค่าเลย)
    rows = []
    block = 0
    for idx, (k, label, vals, _st) in enumerate(parsed):
        if not core.skeleton(label):
            pre = []
            j = k - 1
            while j > first_data - 1 and len(pre) < 2:
                if row_values(lines[j], cols)[2]:
                    break
                cl = core.clean_label(lines[j])
                if core.skeleton(cl):
                    pre.insert(0, cl)
                j -= 1
            label = " ".join(pre) if pre else label
        rows.append({"line": k, "item": label, "vals": vals, "item_kind":
                     "balance" if is_balance_row(label) else "movement"})

    # แบ่งบล็อก: แต่ละบล็อกมียอดคงเหลือ 2 แถว (ต้นงวดกับปลายงวด)
    out = []
    balances = [i for i, r in enumerate(rows) if r["item_kind"] == "balance"]
    # สมมติว่าแต่ละบล็อกมียอดคงเหลือ 2 แถว (ต้นงวด/ปลายงวด) ถ้าเป็นจำนวนคี่
    # แปลว่าอ่านแถวยอดคงเหลือตกไปหรือเจอ layout แบบใหม่ ต้องเตือน ไม่ใช่เงียบ
    if len(balances) % 2:
        print(f"!! {company} หน้า {page}: พบแถวยอดคงเหลือ {len(balances)} แถว "
              f"(คาดว่าเป็นจำนวนคู่) การแบ่งบล็อกอาจผิด")
    block_of = {}
    for n, i in enumerate(balances):
        block_of[i] = n // 2
    cur = 0
    for i, r in enumerate(rows):
        if i in block_of:
            cur = block_of[i]
        r["block"] = cur

    for r in rows:
        for ci, (cname, v) in enumerate(zip(names, r["vals"])):
            out.append({
                "company": company, "source": pdf.name, "page": page,
                "unit": unit, "side": side or "", "block": r["block"],
                "row_index": r["line"], "item": r["item"],
                "col_index": ci, "equity_component": cname,
                "is_total_col": ci == len(names) - 1,
                "kind": r["item_kind"], "value": v,
            })
    return out


def extract(pdf: Path, company: str) -> list[dict]:
    rows = []
    for p in range(1, MAX_PAGES + 1):
        t = page_text(pdf, p)
        if not t.strip():
            break
        if core.page_kind(t) != "SE":
            continue
        rows.extend(read_page(pdf, company, p, t))
    return rows


def check_rows(df: pd.DataFrame, tol: float = 1.0) -> pd.DataFrame:
    """ยอดต้นงวด + รายการเคลื่อนไหว = ยอดปลายงวด ตรวจทีละคอลัมน์

    การตรวจแบบนี้ไม่ต้องรู้ชื่อคอลัมน์เลย และไม่ต้องมีเฉลย
    ครอบคลุมทุกช่องในตาราง ต่างจากสมการเดิมที่ตรวจได้เฉพาะคอลัมน์รวม

    ข้อควรระวัง: แถว "รวม..." ที่งบใส่ไว้กลางตาราง (เช่น รวมกำไรขาดทุนเบ็ดเสร็จ
    หรือ รวมการเปลี่ยนแปลงในส่วนของผู้ถือหุ้น) เป็นยอดรวมของแถวย่อยเหนือมัน
    ถ้าเอามาบวกด้วยจะนับซ้ำ จึงต้องตัดออกก่อน
    """
    res = []
    # ต้องแยกตามหน้าด้วย เพราะงบรวมกับงบเฉพาะกิจการอยู่คนละหน้าและมีจำนวน
    # คอลัมน์ไม่เท่ากัน (SCB งบรวม 14 คอลัมน์ งบเฉพาะธนาคาร 12 คอลัมน์)
    # ถ้าไม่แยก col_index ของสองหน้าจะถูกจับคู่กันมั่ว
    for (co, src, pg, blk, ci), g in df.groupby(
            ["company", "source", "page", "block", "col_index"], sort=False):
        g = g.sort_values("row_index")
        bal = g[g.kind == "balance"]
        if len(bal) < 2:
            continue
        opening = bal.iloc[0]
        closing = bal.iloc[-1]
        mid = g[(g.row_index > opening.row_index)
                & (g.row_index < closing.row_index)
                & (g.kind == "movement")]
        # ตัดแถวยอดรวมกลางตารางออก ไม่งั้นนับซ้ำ
        #
        # จะดูจากชื่อว่าขึ้นต้นด้วย "รวม" อย่างเดียวไม่ได้ เพราะบางงบเขียนคำว่า
        # รวมไว้กลางชื่อ  "กำไรขาดทุนเบ็ดเสร็จรวมสำหรับงวด" ของ GABLE
        # ซึ่งเป็นยอดรวมของสองแถวเหนือมัน ถ้านับด้วยจะได้ค่าซ้ำสองเท่า
        #
        # จึงตัดสินจากตัวเลขแทน: เดินจากบนลงล่าง เก็บผลรวมของแถวที่ยังไม่ถูกยุบ
        # ถ้าแถวไหนมีค่าเท่ากับผลรวมนั้นพอดี แปลว่ามันคือยอดรวมของแถวเหล่านั้น
        # ให้ถือว่าแถวนั้นแทนที่แถวย่อย (ไม่บวกซ้ำ) วิธีนี้ไม่ต้องพึ่งชื่อเลย
        # ตารางนี้มียอดรวมย่อยได้หลายชุดในบล็อกเดียว (SCB มี 2 ชุด)
        # เมื่อยุบชุดหนึ่งจบแล้วต้องเริ่มนับชุดใหม่จากศูนย์ ไม่ใช่นับต่อจากของเดิม
        # ไม่งั้นยอดรวมชุดที่สองจะไม่ตรงกับผลรวมสะสมและถูกนับซ้ำ
        # งบบางฉบับมียอดรวมซ้อนกันหลายชั้น (AIS มี 3 ชั้น)
        #     เงินปันผลจ่าย                          (17,070,673)
        #     รวมเงินทุนที่ได้รับจากและการจัดสรร      (17,070,673)   <- ชั้น 1
        #     การเปลี่ยนแปลงส่วนได้เสียของบริษัทย่อย         -
        #     รวมการเปลี่ยนแปลงส่วนได้เสียของบริษัทย่อย      -        <- ชั้น 1
        #     รวมรายการกับผู้ถือหุ้นที่บันทึกโดยตรง    (17,070,673)   <- ชั้น 2
        # จึงต้องเก็บเป็นกองซ้อน แล้วดูว่าค่าใหม่เท่ากับผลรวมของ "ท้ายกอง"
        # กี่ตัว ถ้าตรงให้ยุบตัวเหล่านั้นแทนด้วยยอดรวม
        # ไล่จากกลุ่มใหญ่ไปเล็ก เพื่อให้ยอดรวมชั้นบนกินยอดรวมชั้นล่างได้
        # เงื่อนไขชื่อ: ยุบได้เฉพาะแถวที่ชื่อมีคำว่า "รวม" เท่านั้น
        # ถ้าดูตัวเลขอย่างเดียว ค่าที่อ่านผิดซึ่งบังเอิญเท่ากับผลรวมท้ายกอง
        # จะถูกกลืนไปแล้วสมการผ่านทั้งที่ผิด
        # วัดแล้วเงื่อนไขนี้ไม่ทำให้ผลเปลี่ยน (429/429 เท่าเดิม) = เข้มขึ้นฟรี
        mid_s = mid.sort_values("row_index")
        stack = []
        for item, v in zip(mid_s["item"], mid_s["value"]):
            x = 0.0 if pd.isna(v) else float(v)
            hit = 0
            if x != 0.0 and "รวม" in str(item):
                for k in range(len(stack), 0, -1):
                    if abs(sum(stack[-k:]) - x) <= tol:
                        hit = k
                        break
            if hit:
                stack = stack[:-hit] + [x]
            else:
                stack.append(x)
        movement = sum(stack)
        # คอลัมน์ที่ว่างทั้งบล็อก (ทั้งยอดต้นงวด ยอดปลายงวด และรายการเคลื่อนไหว
        # ไม่มีค่าเลย) ไม่มีอะไรให้ตรวจ ต้องข้าม ไม่ใช่นับเป็นไม่ผ่าน
        # เกิดกับคอลัมน์ที่งบเว้นว่างไว้ทั้งงวด เช่น PRG
        if pd.isna(opening.value) and pd.isna(closing.value) and movement == 0:
            continue
        got = (0.0 if pd.isna(opening.value) else float(opening.value)) + movement
        want = 0.0 if pd.isna(closing.value) else float(closing.value)
        res.append({"company": co, "source": src, "page": pg, "block": blk,
                    "col_index": ci,
                    "equity_component": g.equity_component.iloc[0],
                    "opening": opening.value, "movement": movement,
                    "closing": closing.value, "computed": got,
                    "ok": abs(got - want) <= tol})
    return pd.DataFrame(res)


def collapse_audit(df: pd.DataFrame, tol: float = 1.0) -> tuple[int, int]:
    """นับว่ากฎยุบยอดรวมทำงานกี่ครั้ง และกี่ครั้งที่ชื่อแถวเป็นยอดรวมจริง

    เป็นการตรวจสอบตัวตรวจสอบอีกที ถ้าตัวเลขกับชื่อไม่เห็นตรงกัน
    แปลว่ากฎนี้กำลังยุบแถวจริงทิ้งเพื่อให้สมการผ่าน ซึ่งแย่กว่าไม่ตรวจเลย
    """
    total = named = 0
    for _, g in df.groupby(["company", "source", "page", "block", "col_index"],
                           sort=False):
        g = g.sort_values("row_index")
        bal = g[g.kind == "balance"]
        if len(bal) < 2:
            continue
        mid = g[(g.row_index > bal.iloc[0].row_index)
                & (g.row_index < bal.iloc[-1].row_index)
                & (g.kind == "movement")].sort_values("row_index")
        stack = []
        for item, v in zip(mid["item"], mid["value"]):
            x = 0.0 if pd.isna(v) else float(v)
            hit = 0
            if x != 0.0:
                for k in range(len(stack), 0, -1):
                    if abs(sum(stack[-k:]) - x) <= tol:
                        hit = k
                        break
            if hit:
                stack = stack[:-hit] + [x]
                total += 1
                if "รวม" in str(item):
                    named += 1
            else:
                stack.append(x)
    return total, named


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf-dir")
    ap.add_argument("--out", default="se_matrix.csv")
    ap.add_argument("--company")
    args = ap.parse_args()

    target = core.resolve(args.pdf_dir, core.default_pdf_dir(), "โฟลเดอร์ PDF")
    files = sorted(Path(target).glob("*.pdf")) if Path(target).is_dir() else [Path(target)]

    rows = []
    for f in files:
        comp = ef.company_from_filename(f)
        if comp is None:
            continue
        if args.company and comp.upper() != args.company.upper():
            continue
        got = extract(f, comp)
        if got:
            n_cells = sum(1 for r in got if r["value"] is not None)
            print(f"{f.name:<45} {comp:<9} {len(got):>4} ช่อง | มีค่า {n_cells:>4}")
        rows.extend(got)

    if not rows:
        print("ไม่พบงบแสดงการเปลี่ยนแปลงส่วนของเจ้าของ")
        return
    df = pd.DataFrame(rows)
    df.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(f"\nบันทึก {len(df)} แถว -> {args.out}")

    # ตรวจสุขภาพของกฎยุบยอดรวม: กฎนี้ตัดสินจากตัวเลขล้วน ไม่ดูชื่อแถว
    # ถ้ามันไปยุบแถวที่ไม่ใช่ยอดรวม การตรวจสอบทั้งหมดจะกลายเป็นของปลอม
    # จึงเทียบผลของมันกับชื่อแถวเพื่อดูว่าสองทางเห็นตรงกันไหม
    hit_all, hit_named = collapse_audit(df)
    if hit_all:
        print(f"\n=== กฎยุบยอดรวม ===")
        print(f"  ยุบ {hit_all} ครั้ง | ชื่อแถวมีคำว่า 'รวม' {hit_named} ครั้ง "
              f"({hit_named / hit_all * 100:.0f}%)")
        if hit_named < hit_all:
            print("  !! มีแถวที่ถูกยุบทั้งที่ชื่อไม่ใช่ยอดรวม ต้องตรวจด้วยตา")

    chk = check_rows(df)
    if len(chk):
        ok = int(chk.ok.sum())
        print(f"\n=== ตรวจ ยอดต้นงวด + รายการเคลื่อนไหว = ยอดปลายงวด ===")
        print(f"  ผ่าน {ok} | ไม่ผ่าน {len(chk) - ok}  (จาก {len(chk)} คอลัมน์-บล็อก)")
        bad = chk[~chk.ok]
        if len(bad):
            print(bad.head(25).to_string(index=False))


if __name__ == "__main__":
    main()
