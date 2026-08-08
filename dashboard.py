#!/usr/bin/env python3
"""
dashboard.py - หน้าจอดูข้อมูลงบการเงินไทย พร้อมหลักฐานว่าเชื่อถือได้แค่ไหน

รัน:
    pip install streamlit
    streamlit run dashboard.py

ทำไมถึงโชว์ "ความน่าเชื่อถือ" คู่กับตัวเลข

    ตัวเลขการเงิน 7 บริษัทใครก็พิมพ์มือได้ใน 20 นาที สิ่งที่ยากคือการดึงมัน
    ออกจาก PDF ไทยที่ข้อความเพี้ยนทุกไฟล์ และการ "รู้ว่าเมื่อไหร่ที่มันผิด"
    dashboard นี้จึงให้ตามรอยทุกตัวเลขกลับไปถึงไฟล์ หน้า และบรรทัดดิบได้
    พร้อมแสดงผลตรวจสมการงบที่ทำได้โดยไม่ต้องมีเฉลย

ไฟล์ที่ใช้ (สร้างจาก extract_fs.py / evaluate.py)
    all.csv                        ผลสกัดทุกงบทุกบริษัท
    tech-*.csv                     ground truth ที่กรอกด้วยมือ
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

import fs_core as core

HERE = Path(__file__).resolve().parent
COLS = core.COLS
COL_LABEL = {
    "con_cur": "งบรวม งวดปัจจุบัน",
    "con_prev": "งบรวม งวดก่อน",
    "sep_cur": "งบเฉพาะกิจการ งวดปัจจุบัน",
    "sep_prev": "งบเฉพาะกิจการ งวดก่อน",
}
STMT_LABEL = {"BS": "งบฐานะการเงิน", "IS": "งบกำไรขาดทุน", "CF": "งบกระแสเงินสด",
              "SE": "งบแสดงการเปลี่ยนแปลงส่วนของเจ้าของ"}
# ตัวคูณให้เป็นบาททั้งหมด — ห้ามเทียบข้ามบริษัทก่อนปรับหน่วย ผิดได้ 1,000 เท่า
UNIT_MULT = {"baht": 1, "thousand": 1_000, "million": 1_000_000}

GT_FILES = {
    "tech-01.csv": "งบฐานะการเงิน (MFEC GABLE BBIK IIG)",
    "tech-02_template_v2_fixed.csv": "งบฐานะการเงิน (AIS CPF PTT)",
    "tech-03_full.csv": "งบกำไรขาดทุน (MFEC GABLE BBIK IIG)",
    "tech-04_cf_sorted.csv": "งบกระแสเงินสด (MFEC GABLE BBIK IIG)",
    "tech-05_bank_template.csv": "งบฐานะการเงิน — ธนาคาร (KBANK SCB)",
    "tech-06_ais_cf_template.csv": "งบกระแสเงินสด (AIS)",
    "tech-07_bank_is_template.csv": "งบกำไรขาดทุน — ธนาคาร (KBANK SCB)",
    "tech-08_se_template_v2.csv": "งบแสดงการเปลี่ยนแปลงส่วนของเจ้าของ (MFEC GABLE BBIK SAT)",
    "tech-09_true_bs_template.csv": "งบฐานะการเงิน (TRUE)",
    "tech-10_ptt_is_template.csv": "งบกำไรขาดทุน (PTT)",
    "tech-11_scb_se_template.csv": "งบแสดงการเปลี่ยนแปลงส่วนของเจ้าของ — ธนาคาร (SCB)",
    "tech-12_prg_bs_template.csv": "งบฐานะการเงิน (PRG)",
    "tech-13_stanley_bs_template.csv": "งบฐานะการเงิน (Thai-Stanley)",
}

# ผลวัดความแม่นยำของ OCR เทียบกับ text layer บนไฟล์เดียวกันและเฉลยชุดเดียวกัน
# (MFEC งบฐานะการเงิน 84 ช่อง เทียบกับ tech-01)
# ตัวเลขนี้สำคัญกว่า "ทำ OCR ได้" เพราะบอกว่าเชื่อได้แค่ไหน ไม่ใช่แค่ว่าทำได้
OCR_BENCH = {
    "text layer": 100.0,
    "OCR เข้มงวด (ใช้เฉพาะหน้าที่อ่านหัวเรื่องออก)": 50.0,
    "OCR ผ่อนปรน (เดาหน้าจากหัวเรื่องที่คล้าย)": 63.1,
}

# ผลจริงกับ PDF สแกน ทั้งคู่ไม่ผ่านด่านตัดสินใจ จึงไม่ถูกรวมเข้า all.csv
# เก็บไว้แสดงเพื่อบอกว่าระบบ "รู้ตัวว่าทำไม่ได้" ไม่ใช่ปล่อยข้อมูลแย่ๆ เข้ามาเงียบๆ
OCR_SCANNED = [
    {"ไฟล์": "CP ALL (43 หน้า)", "จับคู่รายการบัญชีได้": "36%",
     "มาจากหน้าที่มั่นใจ": "81%", "สมการที่ผ่าน": "0 (ข้าม 32)",
     "ผลตัดสิน": "ไม่ผ่าน — ไม่รวมเข้าระบบ"},
    {"ไฟล์": "Humanica (32 หน้า)", "จับคู่รายการบัญชีได้": "41%",
     "มาจากหน้าที่มั่นใจ": "37%", "สมการที่ผ่าน": "0",
     "ผลตัดสิน": "ไม่ผ่าน — ไม่รวมเข้าระบบ"},
]

st.set_page_config(page_title="งบการเงินไทย — ข้อมูลและความน่าเชื่อถือ",
                   layout="wide")


# --------------------------------------------------------------------------
# โหลดข้อมูล
# --------------------------------------------------------------------------

@st.cache_data
def load_extracted():
    df = pd.read_csv(HERE / "all.csv", encoding="utf-8-sig")

    # ผลจาก OCR เก็บแยกไฟล์และต้องแยกให้เห็นชัด ห้ามปนกับข้อมูลที่วัดแล้ว
    # OCR แม่นยำแค่ 63% เทียบกับ 100% ของ text layer ผู้ใช้ต้องเห็นความต่างนี้
    for name in ("cpall_extracted.csv", "humanica_extracted.csv"):
        p = HERE / name
        if p.exists():
            df = pd.concat([df, pd.read_csv(p, encoding="utf-8-sig")],
                           ignore_index=True)
    if "reader" not in df.columns:
        df["reader"] = "text"
    df["reader"] = df["reader"].fillna("text")
    df["mult"] = df["unit"].map(UNIT_MULT)
    return df


@st.cache_data
def load_matcher():
    return core.build_matcher(core.default_alias_path() or
                              HERE / "account_aliases_v6.csv")


@st.cache_data
def load_gt():
    """อ่าน ground truth ทุกไฟล์ที่มี แล้วต่อกันเป็นตารางเดียว"""
    out = []
    for name, desc in GT_FILES.items():
        p = HERE / name
        if not p.exists():
            continue
        d = core.read_gt(p)
        d["gt_file"] = name
        d["gt_desc"] = desc
        out.append(d)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def dedup(df, key="concept"):
    """เลือกแถวเดียวต่อ (บริษัท, งบ, concept) สำหรับแท็บดูข้อมูล

    เรียกตัวกลางของระบบ ห้ามเขียนเกณฑ์การเลือกแถวเองที่นี่
    คีย์มี "งบ" ด้วยเพราะหน้าจอนี้ตั้งใจให้ดูแยกงบ ต่างจากตอนตรวจสมการ
    ที่ต้องยุบเหลือค่าเดียวต่อบริษัท
    """
    return core.pick_rows(df, keys=("company", "statement", key))


ex = load_extracted()
matcher = load_matcher()
gt = load_gt()

st.title("งบการเงินไทย — ข้อมูลและความน่าเชื่อถือ")
st.caption(
    f"สกัดจาก PDF {ex.source.nunique()} ไฟล์ · {ex.company.nunique()} บริษัท · "
    f"{len(ex):,} บรรทัด · ไตรมาส 1/2569"
)

tab_sum, tab_fin, tab_trace, tab_qc = st.tabs(
    ["ภาพรวม", "ข้อมูลการเงิน", "ตามรอยตัวเลข", "ผลตรวจคุณภาพ"])


# --------------------------------------------------------------------------
# 1. ภาพรวม
# --------------------------------------------------------------------------
with tab_sum:
    mapped = int(ex.concept.notna().sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("บรรทัดที่สกัดได้", f"{len(ex):,}")
    c2.metric("จับคู่เป็นรายการบัญชีได้", f"{mapped:,}",
              f"{mapped/len(ex)*100:.0f}%")
    c3.metric("รายการบัญชีไม่ซ้ำ", f"{ex.concept.nunique():,}")
    if len(gt):
        # นับ "ทุกช่องที่ถูกนำมาเทียบ" ไม่ใช่เฉพาะช่องที่มีตัวเลข
        #
        # ช่องที่งบเขียน "-" แล้วเฉลยเว้นว่างไว้ ก็เป็นการทดสอบจริง คือทดสอบว่า
        # ระบบต้องไม่ไปหยิบค่าจากที่อื่นมาใส่ ถ้านับแต่ช่องที่มีตัวเลขจะได้ 2,275
        # ซึ่งไม่ตรงกับ 2,705 ที่รายงานใน README -> ตัวเลขสองที่ขัดกันเอง
        st_cells = len(gt) * len(COLS)
        c4.metric("ช่องเฉลยที่ตรวจแล้ว", f"{st_cells:,}",
                  help="นับทุกช่องที่นำมาเทียบ รวมช่องที่งบเขียน '-' "
                       "ซึ่งเป็นการทดสอบว่าระบบต้องไม่เดาค่าใส่")

    st.subheader("แต่ละไฟล์สกัดได้แค่ไหน")
    per = (ex.groupby(["company", "statement"]).size().unstack(fill_value=0)
             .rename(columns=STMT_LABEL))
    per["รวม"] = per.sum(axis=1)
    st.dataframe(per.sort_values("รวม", ascending=False), width="stretch")

    n_ocr = int((ex.reader == "ocr").sum())
    if n_ocr:
        st.warning(
            f"**{n_ocr:,} บรรทัดมาจาก OCR** (ติดป้าย `reader=ocr`) ซึ่งเป็น PDF "
            "สแกนที่ไม่มี text layer ความแม่นยำต่ำกว่ามาก ดูหัวข้อ "
            "“OCR แย่ลงแค่ไหน” ในแท็บผลตรวจคุณภาพก่อนนำไปใช้"
        )
    else:
        st.info(
            "**ไฟล์ที่ยังสกัดไม่ได้** — CP ALL และ Humanica เป็น PDF สแกน ไม่มี "
            "text layer ต้องทำ OCR ก่อน ระบบตรวจเจอเองและข้ามไป ไม่ได้เงียบหาย"
        )


# --------------------------------------------------------------------------
# 2. ข้อมูลการเงิน
# --------------------------------------------------------------------------
with tab_fin:
    st.subheader("เทียบข้ามบริษัท")
    left, right = st.columns([1, 3])
    with left:
        stmt = st.selectbox("งบ", list(STMT_LABEL),
                            format_func=lambda k: STMT_LABEL[k])
        sub = dedup(ex[ex.statement == stmt])

        # เรียงรายการตาม "จำนวนบริษัทที่มีรายการนั้น" ไม่ใช่ตามตัวอักษร
        #
        # รายการบัญชีส่วนใหญ่มีแค่ไม่กี่บริษัท ถ้าเรียงตามตัวอักษรแล้วเลือกตัวแรก
        # ผู้ใช้จะเปิดมาเจอกราฟที่มีแท่งเดียวและคิดว่าระบบพัง
        # (เจอจริง: งบส่วนของเจ้าของเปิดมาได้ DIVIDEND_PAYABLE ซึ่งมีบริษัทเดียว)
        # ต้องเลือกคอลัมน์ก่อนรายการ เพราะจำนวนบริษัทขึ้นกับคอลัมน์ที่เลือก
        # ถ้านับโดยไม่ดูคอลัมน์ ป้ายจะบอก "3 บริษัท" แต่กราฟขึ้นแค่ 2 (เจอจริง)
        col = st.selectbox("คอลัมน์", COLS, format_func=lambda c: COL_LABEL[c])
        norm = st.checkbox("ปรับหน่วยเป็นบาททั้งหมด", value=True,
                           help="งบแต่ละบริษัทใช้หน่วยต่างกัน (บาท / พันบาท) "
                                "ถ้าไม่ปรับ การเทียบจะผิดได้ 1,000 เท่า")

        # นับเฉพาะบริษัทที่จะขึ้นกราฟจริง = มีค่าในคอลัมน์นั้น และแปลงหน่วยได้
        usable = sub[sub[col].notna()]
        if norm:
            usable = usable[usable["mult"].notna()]

        # เรียงรายการตาม "จำนวนบริษัทที่มีรายการนั้น" ไม่ใช่ตามตัวอักษร
        #
        # รายการบัญชีส่วนใหญ่มีแค่ไม่กี่บริษัท ถ้าเรียงตามตัวอักษรแล้วเลือกตัวแรก
        # ผู้ใช้จะเปิดมาเจอกราฟที่มีแท่งเดียวและคิดว่าระบบพัง
        # (เจอจริง: งบส่วนของเจ้าของเปิดมาได้ DIVIDEND_PAYABLE ซึ่งมีบริษัทเดียว)
        n_comp = usable.groupby("concept").company.nunique()
        concepts = list(n_comp.sort_values(ascending=False).index)
        if not concepts:
            st.warning("ไม่มีรายการในคอลัมน์นี้")
            st.stop()
        default = next((c for c in ("TOTAL_ASSETS", "TOTAL_REVENUE", "CASH_END",
                                    "SE_CLOSING") if c in concepts), concepts[0])
        concept = st.selectbox(
            "รายการ", concepts, index=concepts.index(default),
            format_func=lambda c: f"{c}  ({n_comp[c]} บริษัท)")

    with right:
        d = sub[sub.concept == concept].copy()

        # บริษัทที่ไม่รู้หน่วยเงินจะแปลงเป็นบาทไม่ได้ ต้อง "บอก" ไม่ใช่ "ตัดเงียบ"
        #
        # เดิม mult เป็น NaN แล้วโดน dropna ทิ้งไป ผลคือ IIG (ซึ่งเอกสารไม่เคย
        # เขียนหน่วยไว้เลย) หายจากทุกกราฟโดยไม่มีอะไรเตือน
        # นี่คือความผิดพลาดชนิดเดียวกับที่ทั้งโปรเจกต์พยายามกำจัด
        if norm:
            lost = sorted(d.loc[d["mult"].isna() & d[col].notna(), "company"].unique())
            if lost:
                st.warning(
                    f"**{', '.join(lost)} ไม่ได้อยู่ในกราฟ** เพราะเอกสารไม่ได้ระบุ"
                    " หน่วยเงินไว้ จึงแปลงเป็นบาทไม่ได้ และระบบไม่เดาให้"
                    " (เดาผิด = ผิด 1,000 เท่า) — เอาเครื่องหมายถูกออกเพื่อดูค่าดิบ"
                )
        d["ค่า"] = d[col] * (d["mult"] if norm else 1)
        d = d.dropna(subset=["ค่า"]).sort_values("ค่า", ascending=False)
        if d.empty:
            st.warning("ไม่มีข้อมูลในคอลัมน์นี้")
        else:
            if d.unit.nunique() > 1 and not norm:
                st.error("บริษัทในกราฟนี้ใช้หน่วยต่างกัน ควรเปิดการปรับหน่วย")
            if len(d) == 1:
                st.info(
                    f"รายการนี้พบในบริษัทเดียว — ไม่ใช่ข้อผิดพลาด "
                    f"งบแต่ละบริษัทมีรายการไม่เหมือนกัน "
                    f"เลือกรายการที่มีหลายบริษัทจากตัวเลือกด้านซ้าย "
                    f"(ตัวเลขในวงเล็บคือจำนวนบริษัทที่มีรายการนั้น)"
                )
            if (d.reader == "ocr").any():
                st.warning(
                    "กราฟนี้มีบริษัทที่ข้อมูลมาจาก OCR ซึ่งวัดได้ 63.1% "
                    "เทียบกับ 100% ของ text layer — ดูคอลัมน์ “ที่มา” ด้านล่าง"
                )
            st.bar_chart(d.set_index("company")["ค่า"])
            show = d[["company", "reader", "unit", "ค่า", "item_raw",
                      "source", "page"]].copy()
            show["reader"] = show["reader"].map(
                {"text": "text layer", "ocr": "OCR (แม่นยำ 63%)"})
            show.columns = ["บริษัท", "ที่มา", "หน่วยในงบ",
                            "ค่า (บาท)" if norm else "ค่า",
                            "ชื่อรายการที่อ่านได้", "ไฟล์", "หน้า"]
            st.dataframe(show, width="stretch", hide_index=True)

    st.caption(
        "ชื่อรายการที่แสดงคือข้อความดิบที่อ่านได้จาก PDF ซึ่งมักเพี้ยน "
        "ระบบจับคู่ด้วย “โครงพยัญชนะ” (ตัดสระและวรรณยุกต์ทิ้ง) จึงทนความเพี้ยนได้"
    )


# --------------------------------------------------------------------------
# 3. ตามรอยตัวเลข
# --------------------------------------------------------------------------
with tab_trace:
    st.subheader("ตัวเลขนี้มาจากไหน")
    st.caption("เลือกบริษัทและงบ แล้วดูได้ทุกบรรทัดว่ามาจากไฟล์ไหน หน้าไหน "
               "อยู่ใต้หัวข้อกลุ่มอะไร และระบบมั่นใจแค่ไหน")

    c1, c2, c3 = st.columns(3)
    comp = c1.selectbox("บริษัท", sorted(ex.company.unique()))
    stmt2 = c2.selectbox("งบ ", sorted(ex[ex.company == comp].statement.unique()),
                         format_func=lambda k: STMT_LABEL.get(k, k))
    only_mapped = c3.checkbox("เฉพาะบรรทัดที่จับคู่ได้", value=False)

    t = ex[(ex.company == comp) & (ex.statement == stmt2)].copy()
    if only_mapped:
        t = t[t.concept.notna()]
    t = t[["source", "page", "section", "item_raw", "concept", "match_score"] + COLS]
    t.columns = (["ไฟล์", "หน้า", "หัวข้อกลุ่ม", "ข้อความดิบจาก PDF",
                  "รายการบัญชี", "ความมั่นใจ"] + [COL_LABEL[c] for c in COLS])
    st.dataframe(t, width="stretch", hide_index=True,
                 column_config={"ความมั่นใจ": st.column_config.ProgressColumn(
                     "ความมั่นใจ", min_value=0, max_value=1, format="%.2f")})

    st.caption(
        "ความมั่นใจ 1.00 = ชื่อตรงกับพจนานุกรมรายการบัญชีเป๊ะ · "
        "ต่ำกว่า 0.90 = จับคู่ไม่ได้ ระบบปล่อยว่างไว้แทนที่จะเดา"
    )


# --------------------------------------------------------------------------
# 4. ผลตรวจคุณภาพ
# --------------------------------------------------------------------------
with tab_qc:
    st.subheader("ตรวจด้วยสมการงบ — ไม่ต้องใช้เฉลย")
    st.caption(
        "งบที่ถูกต้องต้องบวกลบลงตัวเสมอ เช่น หนี้สิน + ส่วนของเจ้าของ = รวม "
        "หรือ เงินสดต้นงวด + เงินสดเพิ่มลดสุทธิ = เงินสดปลายงวด "
        "ใช้ได้กับทุกบริษัทแม้ไม่มีคนมากรอกเฉลย จึงจับได้ว่าค่าไหน "
        "“หน้าตาสมเหตุสมผลแต่มาจากตารางผิด”"
    )

    # ใช้ตัวสร้าง lut กลางของระบบ ห้ามเขียน dedup เองที่นี่
    #
    # เดิมหน้าจอนี้ dedup ด้วย (บริษัท, งบ, concept) แล้วยุบลง dict ที่มีคีย์
    # (บริษัท, concept) ผลคืองบที่มาทีหลังทับงบก่อนหน้าแบบสุ่ม
    # -> SCB DERIVATIVE_ASSETS ได้ผลต่างจากงบกระแสเงินสดแทนยอดคงเหลือ
    #    และรายงานว่ามีสมการไม่ผ่าน 1 ข้อ ทั้งที่ crosscheck.py บอกว่าผ่านหมด
    lut, _ = core.build_equation_lut(ex[ex.reader != "ocr"])

    rows = []
    for comp_ in sorted(ex.company.unique()):
        ok, bad, skip, fails = core.check_equations(
            lut, [comp_], verbose=False)
        rows.append({"บริษัท": comp_, "ผ่าน": ok, "ไม่ผ่าน": bad,
                     "ตรวจไม่ได้": skip,
                     "สถานะ": "ไม่ผ่าน" if bad else ("ผ่าน" if ok else "ไม่มีข้อมูลพอ"),
                     "รายละเอียด": " · ".join(fails)})
    q = pd.DataFrame(rows)
    a, b, c = st.columns(3)
    a.metric("สมการที่ผ่าน", int(q["ผ่าน"].sum()))
    b.metric("สมการที่ไม่ผ่าน", int(q["ไม่ผ่าน"].sum()))
    c.metric("ตรวจไม่ได้ (ข้อมูลไม่พอ)", int(q["ตรวจไม่ได้"].sum()))
    st.dataframe(q, width="stretch", hide_index=True)

    st.divider()
    st.subheader("ความแม่นยำเทียบเฉลยที่กรอกด้วยมือ")
    if gt.empty:
        st.warning("ยังไม่พบไฟล์ ground truth")
    else:
        gg = gt.copy()
        gg["concept"] = core.gt_concepts(gg, matcher)

        # ต้องนับ "ความครอบคลุม" คู่กับความแม่นยำเสมอ
        #
        # แถวเฉลยที่จับคู่ concept ไม่ได้จะไม่ถูกนำมาเทียบเลย ถ้ารายงานแต่
        # ความแม่นยำ ตัวเลขจะสวยขึ้นเพราะ "ไม่ได้ตรวจ" ไม่ใช่เพราะ "ถูก"
        # evaluate.py เตือนเรื่องนี้ไว้ใน docstring ตั้งแต่ต้น แต่หน้าจอนี้เคยลืม
        # -> เคยแสดง SCB SE 100% ทั้งที่ 2 ใน 8 แถวไม่เคยถูกตรวจ
        cov = (gg.assign(mapped=gg.concept.notna())
                 .groupby("gt_desc")["mapped"].agg(["sum", "size"]))

        recs = []
        for f, g in gg.groupby("gt_desc"):
            stm = ("SE" if "เปลี่ยนแปลง" in f else "BS" if "ฐานะ" in f else "IS" if "กำไรขาดทุน" in f else "CF")
            lut2 = {(r.company, r.concept): r
                    for r in dedup(ex[ex.statement == stm]).itertuples()}
            for r in g.dropna(subset=["concept"]).itertuples():
                got = lut2.get((r.company, r.concept))
                for col in COLS:
                    want, have = getattr(r, col), (getattr(got, col)
                                                   if got is not None else None)
                    wn, hn = pd.isna(want), (have is None or pd.isna(have))
                    ok = (wn and hn) or (not wn and not hn and
                                         abs(float(want) - float(have)) < 0.005)
                    recs.append({"ชุดเฉลย": f, "บริษัท": r.company,
                                 "กับดัก": r.trap, "ถูก": ok})
        d = pd.DataFrame(recs)
        s1, s2 = st.columns(2)
        with s1:
            byf = d.groupby("ชุดเฉลย")["ถูก"].agg(["sum", "count"])
            byf["แม่นยำ %"] = (byf["sum"] / byf["count"] * 100).round(1)
            byf["ครอบคลุม %"] = (cov["sum"] / cov["size"] * 100).round(1)
            byf.columns = ["ถูก", "ทั้งหมด", "แม่นยำ %", "ครอบคลุม %"]
            st.dataframe(byf, width="stretch")
            gap = byf[byf["ครอบคลุม %"] < 100]
            if len(gap):
                st.warning(
                    "**ชุดที่ครอบคลุมไม่ถึง 100% มีแถวเฉลยที่ยังไม่ถูกนำมาเทียบ** "
                    "ตัวเลขความแม่นยำของชุดนั้นจึงไม่ใช่ความแม่นยำจริง: "
                    + " · ".join(gap.index)
                )
            else:
                st.success("ทุกชุดเฉลยถูกนำมาเทียบครบทุกแถว (ครอบคลุม 100%)")
        with s2:
            byt = d.groupby("กับดัก")["ถูก"].agg(["sum", "count"])
            byt["แม่นยำ %"] = (byt["sum"] / byt["count"] * 100).round(1)
            byt.columns = ["ถูก", "ทั้งหมด", "แม่นยำ %"]
            st.dataframe(byt.sort_values("แม่นยำ %"), width="stretch")
        st.caption(
            "“กับดัก” คือประเภทความยากที่แต่ละแถวทดสอบ เช่น `parentheses` "
            "(วงเล็บ = ค่าติดลบ) `thai_digit` (วรรณยุกต์กลายเป็นตัวเลข) "
            "`long_name` (ชื่อยาวถูกตัดขึ้นบรรทัดใหม่) — วัดแยกตามกับดัก "
            "เพราะระบบจะพังที่กับดัก ไม่ใช่ที่บรรทัดปกติ"
        )

    st.divider()
    st.subheader("OCR แย่ลงแค่ไหน")
    st.caption(
        "PDF สแกนต้องผ่าน OCR ก่อน ซึ่งอ่านผิดได้ คำถามคือ “ผิดแค่ไหน” "
        "วัดโดยรัน OCR ทับไฟล์ที่ **มี text layer และมีเฉลย 100% อยู่แล้ว** "
        "(MFEC งบฐานะการเงิน 84 ช่อง) แล้วเทียบช่องต่อช่อง "
        "ถ้าไปวัดกับ CP ALL จะไม่มีอะไรให้เทียบเลย"
    )
    bench = pd.DataFrame({"วิธีอ่าน": list(OCR_BENCH),
                          "แม่นยำ %": list(OCR_BENCH.values())})
    st.dataframe(bench, width="stretch", hide_index=True)
    st.bar_chart(bench.set_index("วิธีอ่าน")["แม่นยำ %"])
    st.markdown(
        "**ความผิดพลาดสองแบบนี้อันตรายไม่เท่ากัน**\n\n"
        "- *ข้อมูลหาย* — สมการงบจับได้ เพราะยอดรวมไม่ลงตัว\n"
        "- *ข้อมูลผิด* — **จับไม่ได้** เพราะหน้าตาสมเหตุสมผล\n\n"
        "โหมดผ่อนปรนได้ข้อมูลมากกว่า (63.1% เทียบกับ 50.0%) แต่แลกมาด้วยค่าผิด "
        "5 ช่องที่โหมดเข้มงวดไม่มีเลย ระบบจึงไม่เลือกแทนผู้ใช้ แต่ติดป้าย "
        "`page_how` ไว้ทุกแถวว่ามาจากหน้าที่จำแนกด้วยวิธีไหน"
    )
    if (ex.reader == "ocr").any() and "page_how" in ex.columns:
        o = ex[ex.reader == "ocr"]
        st.dataframe(
            o.groupby(["company", "page_how"]).size()
             .rename("บรรทัด").reset_index(),
            width="stretch", hide_index=True)

    st.markdown("**ผลจริงกับ PDF สแกน**")
    st.dataframe(pd.DataFrame(OCR_SCANNED), width="stretch", hide_index=True)
    st.error(
        "**CP ALL และ Humanica ไม่ผ่านด่านตัดสินใจ จึงไม่ถูกรวมเข้าระบบ** — "
        "จับคู่รายการบัญชีได้เพียง 36% และ 41% (text layer ปกติได้ 60–70%) "
        "และสมการงบดุลรันไม่ได้เลยสักข้อเพราะขาด `TOTAL_ASSETS` / "
        "`TOTAL_LIABILITIES` กล่าวคือ **ระบบตรวจสอบบอด** ไม่ใช่ “ตรวจแล้วไม่เจอปัญหา” "
        "การไม่เอาข้อมูลชุดนี้เข้าระบบคือผลลัพธ์ที่ถูกต้อง ไม่ใช่ความล้มเหลว"
    )
