#!/usr/bin/env python3
"""
run_all.py - รันการตรวจสอบทั้งหมดในคำสั่งเดียว แล้วสรุปว่าผ่านหรือไม่

    python run_all.py
    python run_all.py --pdf-dir /path/to/pdfs

ทำไมต้องรันครบทุกอย่าง ไม่ใช่แค่ชุดเดียว
--------------------------------------------------------------------------
เวอร์ชันก่อนหน้ารันเฉลยแค่ชุดเดียว (tech-01 งบฐานะการเงิน) แล้วขึ้น "380/380"
คนที่ clone repo มาแล้วรันไฟล์นี้จะเข้าใจว่านั่นคือการตรวจทั้งหมด
ทั้งที่จริงมีเฉลย 14 ชุด รวม 2,705 ช่อง และยังมีการตรวจอีก 2 แบบที่ไม่ถูกเรียกเลย
(crosscheck ด้วยสมการ และตารางงบส่วนของเจ้าของแบบเต็ม)

**การรายงานผลบางส่วนแล้วให้ดูเหมือนผลทั้งหมด คือความผิดพลาดแบบเดียวกับที่
โปรเจกต์นี้พยายามกำจัด** ไฟล์นี้จึงรันทุกอย่างและสรุปเป็นตารางเดียว

ลำดับการตรวจ
    1. selfcheck              unit test + ตรวจ alias map ชนกันไหม
    2. validate_ground_truth  เฉลยแต่ละชุดสอดคล้องกันเองไหม
    3. extract_fs             สกัดทุกงบ
    4. evaluate x 13          เทียบกับเฉลยทีละชุด (ทั้งความแม่นยำและความครอบคลุม)
    5. crosscheck             สมการบัญชี ไม่ต้องใช้เฉลย
    6. se_matrix              ตารางงบส่วนของเจ้าของแบบเต็ม + สมการตามแถว
    7. evaluate_se_matrix     เทียบตารางนั้นกับเฉลย tech-14
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import fs_core as core

HERE = Path(__file__).resolve().parent

# (ไฟล์เฉลย, งบที่ต้องเทียบด้วย)
GT_PAIRS = [
    ("tech-01.csv", "bs"),
    ("tech-02_template_v2_fixed.csv", "bs"),
    ("tech-03_full.csv", "is"),
    ("tech-04_cf_sorted.csv", "cf"),
    ("tech-05_bank_template.csv", "bs"),
    ("tech-06_ais_cf_template.csv", "cf"),
    ("tech-07_bank_is_template.csv", "is"),
    ("tech-08_se_template_v2.csv", "se"),
    ("tech-09_true_bs_template.csv", "bs"),
    ("tech-10_ptt_is_template.csv", "is"),
    ("tech-11_scb_se_template.csv", "se"),
    ("tech-12_prg_bs_template.csv", "bs"),
    ("tech-13_stanley_bs_template.csv", "bs"),
]


def run(script, extra=(), quiet=True):
    # encoding="utf-8" จำเป็น เพราะสคริปต์ลูกพิมพ์ภาษาไทยออกมา
    # ถ้าปล่อยให้ decode ตาม locale บน Windows จะได้ข้อความเพี้ยน
    # แล้ว regex ที่ใช้ดึงตัวเลขสรุปผลจะจับไม่ได้ กลายเป็นรายงานว่า 0 แถว
    r = subprocess.run([sys.executable, str(HERE / script), *extra],
                       capture_output=quiet, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "")


def head(title):
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-dir")
    ap.add_argument("--aliases")
    ap.add_argument("--verbose", action="store_true",
                    help="แสดงผลดิบของทุกสคริปต์")
    args = ap.parse_args()

    pdf = ["--pdf-dir", args.pdf_dir] if args.pdf_dir else []
    alias = ["--aliases", args.aliases] if args.aliases else []
    quiet = not args.verbose
    problems = []

    # ------------------------------------------------------------------ 1
    # ตรวจโปรแกรมภายนอกก่อนขั้นแรกเสมอ ถ้าขาดแล้วปล่อยให้รันต่อ ทุกขั้นจะพัง
    # ด้วยข้อความคนละแบบ ทำให้ไล่หาสาเหตุจริงยาก
    miss = core.missing_poppler()
    if miss:
        print(f"!! เครื่องนี้ไม่มี: {', '.join(miss)}")
        print(f"   ระบบใช้โปรแกรมพวกนี้อ่าน PDF ทั้งหมด จะรันต่อไม่ได้")
        print(f"   วิธีติดตั้ง — {core.poppler_howto()}")
        return 1

    head("1. selfcheck — unit test และ alias map")
    rc, out = run("selfcheck.py", alias, quiet)
    m = re.search(r"ผ่าน (\d+) \| ไม่ผ่าน (\d+)", out)
    if m:
        print(f"  unit test  ผ่าน {m.group(1)} | ไม่ผ่าน {m.group(2)}")
        if m.group(2) != "0":
            problems.append("selfcheck ไม่ผ่าน")
    if "โครงพยัญชนะชนกัน" in out and "ไม่มีโครงพยัญชนะชนกัน" not in out:
        problems.append("alias map มีโครงพยัญชนะชนกัน")
    if rc != 0:
        problems.append("selfcheck จบด้วย error")
        print(out[-1500:] if quiet else "")
        print("\n!! selfcheck ไม่ผ่าน หยุดตรงนี้ ไม่ต้องเสียเวลารัน extract")
        return 1
    print("  alias map  ไม่มีโครงพยัญชนะชนกัน")

    # ------------------------------------------------------------------ 2
    head("2. validate_ground_truth — เฉลยสอดคล้องกันเองไหม")
    for g, _ in GT_PAIRS:
        if not (HERE / g).exists():
            continue
        _, out = run("validate_ground_truth.py", ["--gt", g] + alias, quiet)
        bad = re.search(r"ผ่าน (\d+) \| ไม่ผ่าน (\d+)", out)
        miss = re.search(r"ยังไม่มีใน alias map \((\d+) แถว\)", out)
        n_bad = bad.group(2) if bad else "?"
        n_miss = miss.group(1) if miss else "0"
        flag = ""
        if n_bad not in ("0", "?"):
            flag = "  << เลขคณิตไม่ผ่าน"
            problems.append(f"{g}: เลขคณิตในเฉลยไม่ผ่าน")
        if n_miss != "0":
            flag += f"  << {n_miss} แถวไม่มีใน alias map"
            problems.append(f"{g}: {n_miss} แถวยังไม่ถูกนำมาเทียบ")
        print(f"  {g:<34} เลขคณิตไม่ผ่าน {n_bad} | ไม่มี alias {n_miss}{flag}")

    # ------------------------------------------------------------------ 3
    head("3. extract_fs — สกัดทุกงบ")

    # ลบผลลัพธ์รอบก่อนทิ้งก่อนเสมอ
    #
    # ถ้าไม่ลบ แล้วรอบนี้สกัดไม่ได้ (เช่นพิมพ์ --pdf-dir ผิด) ขั้นตอนถัดไปจะไป
    # อ่านไฟล์เก่าแล้วรายงานว่า "ผ่านทั้งหมด" ทั้งที่ไม่ได้ตรวจอะไรใหม่เลย
    # เป็นการรายงานผลเก่าว่าเป็นผลใหม่ ซึ่งอันตรายกว่าการฟ้อง error เสียอีก
    for s in ("bs", "is", "cf", "se"):
        (HERE / f"_run_{s}.csv").unlink(missing_ok=True)

    n_rows = {}
    for s in ("bs", "is", "cf", "se"):
        rc, out = run("extract_fs.py",
                      ["--statement", s, "--out", f"_run_{s}.csv"] + pdf + alias,
                      quiet)
        n = re.search(r"บันทึก (\d+) แถว", out)
        n_rows[s] = int(n.group(1)) if n else 0
        print(f"  {s.upper():<4} {n_rows[s]:>5} แถว")
        if rc != 0:
            problems.append(f"extract_fs --statement {s} ล้มเหลว")

    if sum(n_rows.values()) == 0:
        where = args.pdf_dir or "(ค่าเริ่มต้น)"
        print(f"\n!! สกัดไม่ได้เลยสักแถวจาก {where}")
        print("   ตรวจว่ามีไฟล์ .pdf อยู่จริงและ --pdf-dir ชี้ถูกที่")
        print("   repo นี้ไม่เก็บไฟล์ PDF ไว้ (เป็นเอกสารของบริษัทอื่น)")
        print("   ดาวน์โหลดจากเว็บนักลงทุนสัมพันธ์ของแต่ละบริษัท หรือ set.or.th")
        print("\n   หยุดตรงนี้ ไม่รันขั้นต่อไป เพราะจะกลายเป็นการตรวจไฟล์เก่า")
        return 1

    run("extract_fs.py", ["--statement", "all", "--out", "all.csv"] + pdf + alias,
        quiet)

    # ------------------------------------------------------------------ 4
    head("4. evaluate — เทียบกับเฉลย 13 ชุด")
    print(f"  {'ชุดเฉลย':<34} {'ช่อง':>6} {'ถูก':>6} {'แม่นยำ':>8} {'ครอบคลุม':>10}")
    tot = ok = 0
    for g, s in GT_PAIRS:
        if not (HERE / g).exists():
            continue
        _, out = run("evaluate.py",
                     ["--gt", g, "--extracted", f"_run_{s}.csv"] + alias, quiet)
        a = re.search(r"ช่องที่เทียบ (\d+) \| ถูก (\d+)", out)
        c = re.search(r"ground truth (\d+) แถว \| map เป็น concept ได้ (\d+)", out)
        if not a:
            problems.append(f"{g}: evaluate ไม่คืนผล")
            print(f"  {g:<34} {'ล้มเหลว':>6}")
            continue
        n, k = int(a.group(1)), int(a.group(2))
        tot += n
        ok += k
        rows, mapped = (int(c.group(1)), int(c.group(2))) if c else (0, 0)
        cov = mapped / rows * 100 if rows else 0
        flag = ""
        if k != n:
            problems.append(f"{g}: ผิด {n - k} ช่อง")
            flag = "  <<"
        if cov < 100:
            problems.append(f"{g}: ครอบคลุมแค่ {cov:.0f}%")
            flag += " ครอบคลุมไม่ครบ"
        print(f"  {g:<34} {n:>6} {k:>6} {k / n * 100:>7.1f}% "
              f"{mapped:>4}/{rows:<4}{flag}")
    if tot:
        print(f"  {'รวม':<34} {tot:>6} {ok:>6} {ok / tot * 100:>7.1f}%")

    # ------------------------------------------------------------------ 5
    head("5. crosscheck — สมการบัญชี (ไม่ใช้เฉลย)")
    _, out = run("crosscheck.py", ["--extracted", "all.csv"] + alias, quiet)
    m = re.search(r"ผ่าน (\d+) \| ผิด (\d+) \| ข้าม (\d+)", out)
    if m:
        print(f"  ผ่าน {m.group(1)} | ผิด {m.group(2)} | ข้าม {m.group(3)}")
        if m.group(2) != "0":
            problems.append(f"crosscheck ผิด {m.group(2)} ข้อ")

    # ------------------------------------------------------------------ 6
    head("6. se_matrix — งบส่วนของเจ้าของแบบเต็มตาราง")
    _, out = run("se_matrix.py", ["--out", "se_m.csv"] + pdf, quiet)
    m = re.search(r"ผ่าน (\d+) \| ไม่ผ่าน (\d+)", out)
    c = re.search(r"ยุบ (\d+) ครั้ง \| ชื่อแถวมีคำว่า 'รวม' (\d+) ครั้ง", out)
    if m:
        print(f"  สมการตามแถว  ผ่าน {m.group(1)} | ไม่ผ่าน {m.group(2)}")
        if m.group(2) != "0":
            problems.append(f"se_matrix ไม่ผ่าน {m.group(2)} จุด")
    if c:
        print(f"  กฎยุบยอดรวม  ยุบ {c.group(1)} | ชื่อมีคำว่ารวม {c.group(2)}")
        if c.group(1) != c.group(2):
            problems.append("กฎยุบยอดรวมยุบแถวที่ไม่ใช่ยอดรวม")

    # ------------------------------------------------------------------ 7
    head("7. evaluate_se_matrix — เทียบตารางกับเฉลย tech-14")
    if (HERE / "tech-14_se_matrix_template.csv").exists():
        _, out = run("evaluate_se_matrix.py",
                     ["--gt", "tech-14_se_matrix_template.csv",
                      "--extracted", "se_m.csv", "--power"], quiet)
        a = re.search(r"เทียบ (\d+) ช่อง \| ถูก (\d+)", out)
        p = re.search(r"จับได้ (\d+)/(\d+) หน้า", out)
        if a:
            n, k = int(a.group(1)), int(a.group(2))
            print(f"  เทียบ {n} ช่อง | ถูก {k} | {k / n * 100:.1f}%")
            if k != n:
                problems.append(f"tech-14: ผิด {n - k} ช่อง")
        if p:
            print(f"  พลังตรวจจับคอลัมน์เลื่อน  จับได้ {p.group(1)}/{p.group(2)} หน้า")
            if p.group(1) != p.group(2):
                problems.append("เฉลย tech-14 จับคอลัมน์เลื่อนได้ไม่ครบทุกหน้า")

    # ------------------------------------------------------------------
    head("สรุป")
    if problems:
        print(f"  พบปัญหา {len(problems)} จุด")
        for p in problems:
            print(f"    - {p}")
        return 1
    print("  ผ่านทั้งหมด")
    print("  ดูผลแบบมีหน้าจอ: streamlit run dashboard.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
