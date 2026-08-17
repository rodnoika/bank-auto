import re
from io import BytesIO

DATE_RE = re.compile(r"\b(?P<date>\d{2}[./-]\d{2}[./-]\d{4})\b")
AMOUNT_RE = re.compile(r"(?<!\d)(?P<amount>\d{1,3}(?:[ \u00a0]\d{3})*(?:[.,]\d{2})|\d+(?:[.,]\d{2}))(?!\d)")
BIN_RE = re.compile(r"\b(?:БИН|ИИН|BIN|IIN)\s*[:№]?\s*(\d{12})\b", re.I)
IBAN_RE = re.compile(r"\b(KZ\d{18})\b", re.I)
KNP_RE = re.compile(r"\bКНП\s*[:№]?\s*(\d{3})\b", re.I)
KBE_RE = re.compile(r"\bКБ[еЕ]\s*[:№]?\s*(\d{2})\b", re.I)
DOC_RE = re.compile(r"(?:№|N|номер)\s*([A-Za-zА-Яа-я0-9/_-]{1,30})", re.I)
PURPOSE_RE = re.compile(r"(?:назначение(?: платежа)?|purpose)\s*[:\-]\s*(.+)", re.I)
COUNTERPARTY_RE = re.compile(
    r"(?:контрагент|получатель|отправитель|плательщик|beneficiary|sender)\s*[:\-]\s*([^\n]{2,160})",
    re.I,
)

def extract_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join((page.extract_text() or "") for page in reader.pages)

def detect_bank(text: str) -> str:
    t = text.lower()
    if "kaspi" in t or "каспи" in t:
        return "KASPI"
    if "forte" in t or "форте" in t:
        return "FORTE"
    if "halyk" in t or "халык" in t or "народный банк" in t:
        return "HALYK"
    if "bank centercredit" in t or "центркредит" in t or "bcc.kz" in t:
        return "BCC"
    if "freedom bank" in t or "банк фридом" in t:
        return "FREEDOM"
    if "bereke bank" in t or "береке банк" in t:
        return "BEREKE"
    if "alata u city bank" in t or "alatau city bank" in t or "алатау сити банк" in t:
        return "ALATAU_CITY"
    if "eurasian bank" in t or "евразийский банк" in t:
        return "EURASIAN"
    return "UNKNOWN"

def norm_date(s):
    p = re.split(r"[./-]", s)
    return f"{p[2]}-{p[1]}-{p[0]}"

def norm_amount(s):
    return float(s.replace("\u00a0","").replace(" ","").replace(",", "."))

def infer_direction(block: str):
    b = block.lower()
    incoming = ["поступ", "кредит", "зачис", "incoming", "credit"]
    outgoing = ["спис", "дебет", "исход", "outgoing", "debit", "комисси"]
    if any(x in b for x in incoming): return "incoming"
    if any(x in b for x in outgoing): return "outgoing"
    return None

def infer_purpose(block: str):
    m = PURPOSE_RE.search(block)
    if m:
        return m.group(1).strip()
    # fallback: choose longest meaningful line, excluding obvious headers
    lines = [re.sub(r"\s+"," ",x).strip() for x in block.splitlines() if len(x.strip()) > 12]
    banned = ("выписка","остаток","дата","сумма","оборот","банк","account","statement")
    lines = [x for x in lines if not any(b in x.lower() for b in banned)]
    return max(lines, key=len) if lines else ""

def parse_pdf(pdf_bytes: bytes, source_name: str):
    text = extract_text(pdf_bytes)
    bank = detect_bank(text)
    lines = [re.sub(r"\s+"," ",x).strip() for x in text.splitlines() if x.strip()]

    starts = []
    for i, line in enumerate(lines):
        dm = DATE_RE.search(line)
        ams = list(AMOUNT_RE.finditer(line))
        if dm and ams:
            starts.append(i)

    txs = []
    for n, start in enumerate(starts):
        end = starts[n+1] if n+1 < len(starts) else min(len(lines), start+15)
        block_lines = lines[start:end]
        block = "\n".join(block_lines)

        dm = DATE_RE.search(lines[start])
        amounts = [m.group("amount") for m in AMOUNT_RE.finditer(lines[start])]
        if not dm or not amounts:
            continue

        # Usually the largest number on transaction row is transaction amount.
        amount = max((norm_amount(a) for a in amounts), default=0.0)
        if amount == 0:
            continue

        bin_m = BIN_RE.search(block)
        iban_m = IBAN_RE.search(block)
        knp_m = KNP_RE.search(block)
        kbe_m = KBE_RE.search(block)
        doc_m = DOC_RE.search(block)
        counterparty_m = COUNTERPARTY_RE.search(block)

        txs.append({
            "bank": bank,
            "account": None,
            "external_id": None,
            "operation_date": norm_date(dm.group("date")),
            "amount": amount,
            "direction": infer_direction(block),
            "currency": "KZT",
            "counterparty_name": counterparty_m.group(1).strip() if counterparty_m else None,
            "counterparty_bin": bin_m.group(1) if bin_m else None,
            "counterparty_iban": iban_m.group(1).upper() if iban_m else None,
            "document_number": doc_m.group(1) if doc_m else None,
            "knp": knp_m.group(1) if knp_m else None,
            "kbe": kbe_m.group(1) if kbe_m else None,
            "payment_purpose": infer_purpose(block),
            "source_type": "pdf",
            "source_name": source_name,
            "raw_text": block,
            "raw_json": None,
            "status": "new"
        })
    return bank, text, txs
