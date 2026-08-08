# รัน OCR บน Kaggle

Kaggle มี `tesseract` กับ `poppler` ติดตั้งมาแล้ว และมีสิทธิ์ `sudo` จึงลงภาษาไทยได้
ต่างจาก sandbox ในเครื่องซึ่งไม่มีสิทธิ์ root และลง `tesseract-ocr-tha` ไม่ได้

## ก่อนเริ่ม — ต้องอัปเดต dataset ก่อน

ไฟล์สองตัวนี้ **เพิ่งสร้าง/แก้** ต้องอัปขึ้น dataset ใหม่ก่อนรัน ไม่งั้นจะติดที่เซลล์ 5

| ไฟล์ | ทำไมต้องเอาตัวใหม่ |
|---|---|
| `ocr_extract.py` | **ไฟล์ใหม่** ตัวเชื่อมระหว่างผล OCR กับตัวสกัดเดิม ถ้าไม่มี เซลล์ 5 ทำไม่ได้ |
| `ocr_reader.py` | แก้ 3 บั๊ก: `PIL.Image`, ลำดับหน้าสลับตอน ≥10 หน้า, เช็คสิทธิ์เขียนก่อนเริ่ม |
| `fs_core.py` | แก้ `NOTES_MARK` ที่ไม่เคยทำงาน — **สำคัญมากกับ CP ALL 43 หน้า** |
| `selfcheck.py` | เพิ่ม `test_skeleton_constants()` กันบั๊กชนิดเดียวกันกลับมา |

ตัวเก่าของ `fs_core.py` จะปล่อยให้หน้าหมายเหตุไหลเข้ามาเป็นงบฐานะการเงิน

## โครงสร้าง dataset จริง (notebook `Financial001`)

มีสอง dataset แยกกัน **ไฟล์ที่ต้องใช้ไม่ได้อยู่โฟลเดอร์เดียวกัน** ต้องดึงมารวมเองที่ `/kaggle/working/`

```
/kaggle/input/datasets/switchonchannel/financial-datas/Financial_DATA/
    CP-ALL-Signed-FS-TH-Q126.pdf
    Humanica_Q1_31Mar69_signed_TH.pdf
    FINANCIAL_STATEMENTS_Mfec.pdf
    FINANCIAL_STATEMENTS_AIS.pdf / _IIG.pdf / _PTT.pdf
    1Q26_KBank_unreviewed-T.pdf, 20260512-gable-..., 20260513-bbik-... ฯลฯ

/kaggle/input/datasets/switchonchannel/ground-truth-financial01/ground truth Financial/
    fs_core.py  extract_fs.py  evaluate.py  crosscheck.py  selfcheck.py
    ocr_reader.py  ocr_extract.py  se_matrix.py  evaluate_se_matrix.py
    dashboard.py  run_all.py  make_gt_template.py
    account_aliases_v6.csv (และ v1–v5)
    tech-01.csv ... tech-14_se_matrix_template.csv
```

> **ระวังชื่อโฟลเดอร์มีเว้นวรรค** — `ground truth Financial` มีช่องว่างสองจุด
> ทุกครั้งที่ใช้ใน shell ต้องครอบเครื่องหมายคำพูด **ถึงแค่ท้าย `/`** แล้วค่อยตาม `*.py`
> ถ้าปิดคำพูดหลัง `*` glob จะไม่ขยายและ `cp` จะฟ้อง `No such file or directory`

> **เส้นทางอาจไม่ตรงกับที่เขียนไว้** ชื่อ dataset เปลี่ยนได้ตอนอัปใหม่
> เซลล์ที่ 0 ข้างล่างจะพิมพ์เส้นทางจริงให้ ใช้ค่านั้นแทน อย่าเดา

## เซลล์ที่ 0 — หาเส้นทางจริงก่อน

```python
import glob
for p in sorted(glob.glob("/kaggle/input/**/*.py", recursive=True))[:5]:
    print(p)
print("---")
for p in sorted(glob.glob("/kaggle/input/**/*.pdf", recursive=True))[:5]:
    print(p)
```

เอาเส้นทางที่ได้ไปใส่ `SRC_DIR` กับ `PDF_DIR` ในเซลล์ที่ 2

## เซลล์ที่ 1 — ติดตั้ง

```python
!apt-get -qq update
!apt-get -qq install -y tesseract-ocr-tha poppler-utils
!pip -q install pytesseract pillow
!tesseract --list-langs
```

ต้องเห็นแบบนี้

```
List of available languages (3):
eng
osd
tha
```

ถ้าไม่มี `tha` ให้หยุดตรงนี้ อย่ารันต่อ
บรรทัด `/sbin/ldconfig.real: ... is not a symbolic link` เป็นเสียงรบกวนปกติของ Kaggle ไม่ใช่ error

## เซลล์ที่ 2 — รวมไฟล์มาไว้ที่ `/kaggle/working/`

`/kaggle/input/...` เป็นแบบ **อ่านอย่างเดียว** อย่า `%cd` เข้าไปทำงานตรงนั้น
คำสั่งที่ต้องเขียนไฟล์จะพังตอนท้าย หลัง OCR ทำงานเสร็จไปหมดแล้ว

ใช้ Python แทน shell จะปลอดภัยกว่าเรื่องช่องว่างในชื่อโฟลเดอร์

```python
import shutil, glob, os
from pathlib import Path

PDF_DIR = "/kaggle/input/datasets/switchonchannel/financial-datas/Financial_DATA"
SRC_DIR = "/kaggle/input/datasets/switchonchannel/ground-truth-financial01/ground truth Financial"
WORK    = "/kaggle/working"

os.chdir(WORK)

for pat in ("*.py", "*.csv", "*.md"):
    for f in glob.glob(os.path.join(SRC_DIR, pat)):
        shutil.copy(f, WORK)

for name in ("CP-ALL-Signed-FS-TH-Q126.pdf",
             "Humanica_Q1_31Mar69_signed_TH.pdf",
             "FINANCIAL_STATEMENTS_Mfec.pdf"):
    shutil.copy(os.path.join(PDF_DIR, name), WORK)

print(sorted(os.listdir(WORK)))
```

ถ้าอยากใช้ shell จริงๆ ต้องเขียนแบบนี้ (คำพูดปิดที่ `/` แล้วค่อยตาม `*` และมี `.` ปิดท้ายเสมอ)

```python
%cd /kaggle/working/
!cp "/kaggle/input/datasets/switchonchannel/ground-truth-financial01/ground truth Financial/"*.py .
!cp "/kaggle/input/datasets/switchonchannel/ground-truth-financial01/ground truth Financial/"*.csv .
!cp "/kaggle/input/datasets/switchonchannel/financial-datas/Financial_DATA/"*.pdf .
!ls
```

## เซลล์ที่ 2.5 — ตรวจว่าไฟล์ครบและเป็นเวอร์ชันใหม่

อย่าเดา ให้เช็คเป็นรายการ ถ้าขาดจะได้รู้ตรงนี้ ไม่ใช่รู้ตอน OCR ไปแล้วครึ่งชั่วโมง

```python
need = ["fs_core.py", "extract_fs.py", "evaluate.py", "crosscheck.py", "selfcheck.py",
        "ocr_reader.py", "ocr_extract.py", "account_aliases_v6.csv",
        "CP-ALL-Signed-FS-TH-Q126.pdf", "Humanica_Q1_31Mar69_signed_TH.pdf",
        "FINANCIAL_STATEMENTS_Mfec.pdf"]
miss = [f for f in need if not Path(f).exists()]
print("ขาด:", miss if miss else "ไม่ขาด")

# เช็คว่า fs_core.py เป็นเวอร์ชันที่แก้ NOTES_MARK แล้วหรือยัง
import fs_core as core
ok = core.NOTES_MARK == core.skeleton("หมายเหตุประกอบ")
print("fs_core เวอร์ชันใหม่:", "ใช่" if ok else "*** ไม่ใช่ ต้องอัป dataset ใหม่ ***")
```

`ocr_extract.py` ไม่มี = ทำเซลล์ 5 ไม่ได้
`fs_core.py` เป็นตัวเก่า = หน้าหมายเหตุของ CP ALL จะไหลเข้ามาปนกับงบ

## เซลล์ที่ 2.7 — selfcheck (30 วินาที คุ้มมาก)

```python
!python selfcheck.py
```

ต้องได้ `ผ่าน 33 | ไม่ผ่าน 0` ถ้าได้ 21 แปลว่า `selfcheck.py` เป็นตัวเก่า
ถ้าไม่ผ่าน แปลว่าไฟล์ที่อัปมาไม่ครบชุดหรือปนเวอร์ชัน — แก้ตรงนี้ก่อน

## เซลล์ที่ 3 — วัดว่า OCR แย่ลงแค่ไหน (ทำก่อนเสมอ)

**อย่าเพิ่งไป OCR ไฟล์สแกน** ต้องรู้ก่อนว่าเครื่องมือนี้เชื่อถือได้แค่ไหน
วิธีเดียวที่วัดได้คือรัน OCR ทับไฟล์ที่ **มี text layer อยู่แล้วและมีเฉลย 100%**
แล้วเทียบกัน ถ้าไปวัดกับ CP ALL จะไม่มีอะไรให้เทียบเลย

```python
!python ocr_reader.py --compare FINANCIAL_STATEMENTS_Mfec.pdf --first 2 --last 7
```

ผลที่ได้จะบอกสองอย่างซึ่งอันตรายไม่เท่ากัน

| ตัวชี้วัด | ความหมาย | ระบบเรารับมือได้ไหม |
|---|---|---|
| อ่านไม่ได้ | ตัวเลขหายไป | **ได้** สมการจะจับ (ยอดไม่ลงตัว) |
| สร้างเกิน | ตัวเลขที่ไม่มีในงบ | **อันตราย** ดูสมเหตุสมผลและอาจไปแทนค่าจริง |

**เกณฑ์ตัดสินใจ** ถ้า "อ่านตรง" ต่ำกว่า 90% หรือ "สร้างเกิน" เกิน 5% ของทั้งหมด
อย่าเอาผล OCR ไปรวมกับข้อมูลหลัก ให้แยกเก็บและติดป้ายว่ามาจาก OCR

### ข้อจำกัดของตัวเลขนี้ที่ต้องรู้

`--compare` นับว่า "ข้อความตัวเลขนี้โผล่ที่ไหนสักแห่งในหน้าไหม" **ไม่ได้ตรวจว่าอยู่ถูกคอลัมน์ถูกแถว**
ตัวเลขที่อ่านถูกแต่ไปตกผิดคอลัมน์จะยังนับเป็น "อ่านตรง" อยู่ดี

และ MFEC เป็น PDF ดิจิทัลที่เราเรนเดอร์เป็นภาพเอง = **กรณีที่ง่ายที่สุดเท่าที่จะเป็นไปได้**
ไม่มีรอยเอียง ไม่มีจุดรบกวน ตัวอักษรคมชัดสม่ำเสมอ
ส่วน CP ALL เป็นสแกนจากกระดาษจริง **ตัวเลขที่ได้จาก MFEC จึงเป็นเพดาน ไม่ใช่ค่าที่จะเจอกับ CP ALL**

## เซลล์ที่ 3.5 — วัดระดับ "ช่อง" ด้วยเฉลยจริง (แม่นกว่าเซลล์ 3 มาก)

เซลล์ 3 วัดแค่ว่าตัวเลขปรากฏไหม เซลล์นี้วัดว่า **ค่าไปอยู่ในช่องที่ถูกต้องไหม**
ด้วยการเอาผล OCR ป้อนเข้าตัวสกัดจริง แล้วเทียบกับ `tech-01.csv` ซึ่งได้ 100% ตอนใช้ text layer

```python
# 1) OCR ทั้งไฟล์ MFEC
!python ocr_reader.py --pdf FINANCIAL_STATEMENTS_Mfec.pdf \
        --out /kaggle/working/mfec_ocr/ --first 1 --last 10

# 2) ป้อนเข้าตัวสกัดเดิม
!python ocr_extract.py \
    --layout /kaggle/working/mfec_ocr/FINANCIAL_STATEMENTS_Mfec.layout.txt \
    --company MFEC --statement bs --out mfec_ocr_bs.csv
```

```python
# 3) ตัดเฉลยเหลือเฉพาะ MFEC ไม่งั้นบริษัทอื่นจะถูกนับเป็น "หาไม่เจอ"
import pandas as pd
d = pd.read_csv('tech-01.csv', encoding='utf-8-sig')
mfec = d[d.company == 'MFEC']
mfec.to_csv('tech-01_mfec.csv', index=False, encoding='utf-8-sig')
print(len(mfec), 'แถว')
```

```python
!python evaluate.py --gt tech-01_mfec.csv --extracted mfec_ocr_bs.csv
```

**ตัวเลขนี้คือคำตอบจริงของคำถาม "OCR แย่ลงแค่ไหน"**
เพราะเทียบกับ 100% ที่ได้จาก text layer โดยตรง ช่องต่อช่อง คอลัมน์ต่อคอลัมน์

| ผลที่ได้ | แปลว่า |
|---|---|
| ใกล้ 100% | OCR ใช้แทน text layer ได้จริง |
| 80–95% | ใช้ได้แต่ต้องติดป้ายว่าเชื่อถือน้อยกว่า |
| ต่ำกว่า 80% | อย่าเอาไปรวมกับข้อมูลหลัก |
| ความครอบคลุมต่ำแต่แม่นสูง | OCR อ่านตกไปเยอะ แต่ที่อ่านได้ถูก — ปลอดภัยกว่าแบบกลับกัน |

## เซลล์ที่ 4 — OCR ไฟล์สแกนจริง

**เริ่มจากช่วงแคบก่อน** งบการเงินอยู่ต้นเล่มเสมอ ที่เหลือเป็นหมายเหตุ
CP ALL มี 43 หน้า แต่ไม่จำเป็นต้อง OCR ทั้งหมด และการ OCR หมดจะเปลืองเวลามาก

```python
!python ocr_reader.py --pdf CP-ALL-Signed-FS-TH-Q126.pdf \
        --out /kaggle/working/cpall/ --first 1 --last 15 --classify
```

ดูผล `--classify` ก่อน ถ้าเจอครบ BS / IS / CF / SE แล้วก็ไม่ต้องขยาย
ถ้ายังขาดบางงบค่อยเพิ่ม `--last` ทีละ 5 หน้า

```python
!python ocr_reader.py --pdf Humanica_Q1_31Mar69_signed_TH.pdf \
        --out /kaggle/working/humanica/ --first 1 --last 15 --classify
```

ใส่ `--out` เป็น absolute path ใต้ `/kaggle/working/` เสมอ จะได้ไม่พลาดไปเขียนลง `/kaggle/input/`

**ถ้าไม่มีหน้าไหนถูกจัดเลย** แปลว่า OCR อ่านหัวเรื่องไม่ออก ให้ลอง `--psm 4` หรือ `--psm 11`
ก่อนจะไปแก้อย่างอื่น

> Kaggle จำกัดเวลาเซสชัน OCR ที่ 300dpi ใช้เวลาราว 10–20 วินาทีต่อหน้า
> ถ้าจะรันหลายสิบหน้า ควรกด **Save Version → Run All** ไม่ใช่รันสด
> ผลใน `/kaggle/working/` จะติดไปกับ output ของ version ให้เอาไปใช้ต่อได้

## เซลล์ที่ 5 — ป้อนเข้าตัวสกัดเดิม

`extract_fs` รับ path ของ PDF แล้วเรียก `pdftotext` เอง ถ้าไฟล์เป็นภาพสแกน
`pdftotext` จะคืนข้อความว่าง ตัวสกัดได้ 0 แถว **โดยไม่มี error**

`ocr_extract.py` แก้ด้วยการสลับ `core.page_text` / `core.n_pages` ให้อ่านจากผล OCR
**โดยไม่ต้องแก้โค้ดเดิมสักบรรทัด** ที่เหลือทั้งหมด (จำแนกหน้า, หาคอลัมน์, แยกชื่อรายการ,
ตัวอ่าน SE, การเติมหน่วย) ทำงานเหมือนเดิมทุกอย่าง

**ต้องเปิด PDF ดูหน่วยเงินเองก่อน** OCR อ่านบรรทัด `(หน่วย: พันบาท)` ไม่ออก
และระบบไม่เดาให้ เพราะผิดหน่วย = ผิด 1,000 เท่าแบบเงียบๆ

```python
!python ocr_extract.py \
    --layout /kaggle/working/cpall/CP-ALL-Signed-FS-TH-Q126.layout.txt \
    --company CPALL --statement all --unit thousand --out cpall_extracted.csv
```

```python
!python ocr_extract.py \
    --layout /kaggle/working/humanica/Humanica_Q1_31Mar69_signed_TH.layout.txt \
    --company HUMANICA --statement all --unit baht --out humanica_extracted.csv
```

ทุกแถวจะถูกติดป้าย

| คอลัมน์ | ค่า | ความหมาย |
|---|---|---|
| `reader` | `ocr` | มาจาก OCR ไม่ใช่ text layer |
| `page_how` | `หัวเรื่อง` | จำแนกหน้าได้จากหัวเรื่องตรงตัว เชื่อถือได้มากสุด |
| | `หัวเรื่องคล้าย 0.88` | เดาจากหัวเรื่องที่คล้าย เชื่อได้น้อยลง |
| | `เนื้อหา` | เดาจากเนื้อหาในหน้า เชื่อได้น้อยสุด |

ถ้าอยากได้เฉพาะหน้าที่มั่นใจ ใส่ `--strict-pages`
วัดกับ MFEC แล้วโหมดนี้ได้ข้อมูลน้อยกว่า (50.0% เทียบกับ 63.1%) **แต่ค่าผิดเป็น 0**

**ถ้าขึ้นว่า "OCR อ่านหัวเรื่องของงบไม่ออก"** ให้กลับไปปรับ `--psm` ที่เซลล์ 4
อย่าเพิ่งไปแก้ที่อื่น — ถ้าจำแนกหน้าไม่ได้ ขั้นต่อไปไม่มีความหมาย

## เซลล์ที่ 6 — ตรวจสอบด้วยสมการ (ขั้นที่ห้ามข้าม)

ผล OCR **ไม่มีเฉลย** สิ่งเดียวที่ตรวจได้คือสมการในตัวมันเอง

```python
!python crosscheck.py --extracted cpall_extracted.csv
```

ถ้าขึ้น `FileNotFoundError: cpall_extracted.csv` แปลว่า**เซลล์ 5 ยังไม่สำเร็จ**
ให้กลับไปดูเซลล์ 5 ก่อน ไม่ใช่ปัญหาของ `crosscheck.py`

ถ้าสมการงบดุลไม่ผ่าน = OCR อ่านตัวเลขผิด **ห้ามเอาข้อมูลไปใช้ต่อ**
ถ้าผ่าน ก็ยังบอกได้แค่ว่า "ไม่ขัดกันเอง" ไม่ได้แปลว่าถูก
(บทเรียนจาก se_matrix: คอลัมน์เลื่อนทั้งคอลัมน์ สมการก็ยังผ่าน 428/428)

## เซลล์ที่ 6.5 — ด่านตัดสินใจ (ไม่มีเฉลย จึงต้องดูหลายสัญญาณพร้อมกัน)

CP ALL กับ Humanica **ไม่มีเฉลย** จึงวัดความแม่นยำตรงๆ ไม่ได้เลย
สิ่งที่ทำได้คือดูสัญญาณทางอ้อมหลายตัวประกอบกัน แล้วตัดสินว่าเชื่อได้แค่ไหน

```python
import pandas as pd

for name in ("cpall_extracted.csv", "humanica_extracted.csv"):
    try:
        d = pd.read_csv(name, encoding="utf-8-sig")
    except FileNotFoundError:
        print(f"{name}: ไม่มีไฟล์ ข้ามไป"); continue

    mapped = d.concept.notna().mean()
    strong = (d.page_how == "หัวเรื่อง").mean() if "page_how" in d else float("nan")
    stmts  = sorted(d.statement.dropna().unique())
    print(f"\n=== {name} ===")
    print(f"  แถวทั้งหมด            {len(d):,}")
    print(f"  จับคู่รายการบัญชีได้   {mapped*100:.0f}%   (text layer ปกติได้ 60-70%)")
    print(f"  มาจากหน้าที่มั่นใจ     {strong*100:.0f}%")
    print(f"  งบที่เจอ              {stmts}")
    print(f"  หน่วยเงิน             {sorted(set(d.unit))}")
```

**เกณฑ์ตัดสิน** ต้องผ่านทุกข้อ ไม่ใช่ผ่านข้อใดข้อหนึ่ง

| สัญญาณ | ผ่าน | ไม่ผ่าน แปลว่า |
|---|---|---|
| จับคู่รายการบัญชีได้ | ≥ 50% | OCR อ่านชื่อรายการไม่ออก ข้อมูลใช้ไม่ได้ |
| มาจากหน้าที่มั่นใจ | ≥ 50% | ส่วนใหญ่เป็นการเดาว่าหน้านั้นเป็นงบอะไร |
| งบที่เจอ | มี BS เป็นอย่างน้อย | ไม่เจอแม้แต่งบฐานะการเงิน = จำแนกหน้าพัง |
| สมการงบดุล (เซลล์ 6) | **ผ่าน ≥ 1 ข้อ และไม่ผ่าน 0 ข้อ** | ดูคำอธิบายข้างล่าง |
| หน่วยเงิน | ไม่ใช่ `unknown` | ต้องใส่ `--unit` เอง |

> **เกณฑ์สมการต้องเขียนแบบนี้ ไม่ใช่ "ไม่มีข้อไหนไม่ผ่าน"**
>
> ตอนแรกเขียนไว้ว่า "ไม่มีข้อไหนไม่ผ่าน" ซึ่งผิด เพราะผลอย่าง
> `ผ่าน 0 | ผิด 0 | ข้าม 32` เข้าเงื่อนไขนั้นทั้งที่**ไม่ได้ตรวจอะไรเลยสักข้อ**
> "ข้าม" แปลว่าไม่มี concept ที่สมการต้องใช้ (เช่น `TOTAL_ASSETS`) จึงรันไม่ได้
> ต้องมีอย่างน้อย 1 ข้อที่ผ่านจริง ถึงจะพูดได้ว่าสมการยืนยันอะไรให้เรา

**ถ้าไม่ผ่านข้อใดข้อหนึ่ง อย่าเอาไฟล์นี้ไปรวมกับ `all.csv`**
เก็บไว้เป็นไฟล์แยกและระบุใน README ว่าทำได้แค่ไหน — นั่นก็เป็นผลลัพธ์ที่ซื่อสัตย์แล้ว

## เซลล์ที่ 7 — บันทึกผลกลับออกมา

```python
import os
for f in ("cpall_extracted.csv", "humanica_extracted.csv"):
    if os.path.exists(f):
        print(f, os.path.getsize(f), "ไบต์")
!ls -la /kaggle/working/cpall/ | head
```

กด **Save Version** เพื่อให้ไฟล์ใน `/kaggle/working/` ติดไปกับ output
ถ้าไม่ save ผลจะหายเมื่อปิดเซสชัน

ดาวน์โหลด `cpall_extracted.csv` / `humanica_extracted.csv` มาวางในโฟลเดอร์เดียวกับ
`dashboard.py` แล้ว dashboard จะโหลดเองอัตโนมัติ พร้อมขึ้นคำเตือนว่ามีข้อมูลจาก OCR
และแสดงคอลัมน์ "ที่มา" ในทุกตาราง

## สิ่งที่ต้องเพิ่มถ้าจะเอา OCR เข้าระบบจริง

1. **คอลัมน์ `reader`** — `ocr_extract.py` ใส่ให้แล้ว เหลือให้ `dashboard.py` อ่านไปใช้
   ผู้ใช้ควรเห็นว่าตัวเลขของ CP ALL เชื่อถือได้น้อยกว่าบริษัทอื่น
2. **อย่าขยาย `MAX_PAGES` โดยไม่ตรวจ `fs_core.py` ก่อน** — CP ALL มี 43 หน้า
   ถ้าใช้ตัวเก่าที่ `NOTES_MARK` ไม่ทำงาน หน้าหมายเหตุจะไหลเข้ามาเป็นงบฐานะการเงิน
   เซลล์ 2.5 มีตัวเช็คให้แล้ว
3. **เฉลยของ OCR อย่างน้อย 1 หน้า** ถ้าไม่มี จะไม่มีทางรู้เลยว่า OCR อ่านผิดไหม
   ใช้วิธีเดียวกับ tech-14 คือกรอกแค่แถวเดียวต่อหน้าแต่ครบทุกคอลัมน์
4. **ล็อกเวอร์ชัน alias** ใน dataset มี `account_aliases_v1..v6.csv` อยู่ครบ
   ระบุให้ชัดว่าใช้ v6 อย่าปล่อยให้ glob ไปหยิบตัวเก่า

## กับดักที่เจอมาแล้วบน Kaggle

| อาการ | สาเหตุ | วิธีแก้ |
|---|---|---|
| `AttributeError: module 'PIL' has no attribute 'Image'` | `ocr_reader.py` ตัวเก่า | อัป dataset ใหม่ (แก้ที่ `need()` แล้ว) |
| `FileNotFoundError: cpall_extracted.csv` | เซลล์ 5 ยังไม่ได้รันหรือไม่สำเร็จ | รัน `ocr_extract.py` ก่อน |
| `cp: missing destination file operand` | ลืม `.` ปิดท้ายคำสั่ง `cp` | เติม ` .` ต่อท้าย |
| `cp: cannot stat '...ground truth Financial*.py'` | ปิดเครื่องหมายคำพูดหลัง `*` | ครอบคำพูดถึงแค่ท้าย `/` แล้วค่อยตาม `*.py` |
| เขียนไฟล์ไม่ได้ตอนจบ | `%cd` เข้าไปใน `/kaggle/input/` | `%cd /kaggle/working/` ก่อนเสมอ |
| ผล OCR หายหลังปิดเซสชัน | ไม่ได้ Save Version | ใช้ Save Version → Run All |
| หน้าสลับลำดับตอน OCR ≥10 หน้า | `ocr_reader.py` ตัวเก่าเรียงไฟล์แบบข้อความ | อัป dataset ใหม่ |
