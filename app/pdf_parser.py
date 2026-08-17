import re
from io import BytesIO

DATE_RE = re.compile(r"\b(?P<date>\d{2}[./-]\d{2}[./-]\d{4})\b")
AMOUNT_RE = re.compile(
    r"(?<![\d./:])(?P<amount>(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[.,]\d{2})?)(?![\d./:])"
)
BIN_RE = re.compile(r"\b(?:БИН|ИИН|BIN|IIN)\s*[:№]?\s*(\d{12})\b", re.I)
IBAN_RE = re.compile(r"(?<![A-Z0-9])(KZ[0-9A-Z]{18})(?![A-Z0-9])", re.I)
KNP_RE = re.compile(r"\bКНП\s*[:№]?\s*(\d{3})\b", re.I)
KBE_RE = re.compile(r"\bКБ[еЕ]\s*[:№]?\s*(\d{2})\b", re.I)
DOC_RE = re.compile(r"(?:№|N|номер)\s*([A-Za-zА-Яа-я0-9/_-]{1,30})", re.I)
PURPOSE_RE = re.compile(r"(?:назначение(?: платежа)?|purpose)\s*[:\-]\s*(.+)", re.I)
COUNTERPARTY_RE = re.compile(
    r"(?:контрагент|получатель|отправитель|плательщик|beneficiary|sender)\s*[:\-]\s*([^\n]{2,160})",
    re.I,
)
KASPI_START_RE = re.compile(
    r"^(?P<document>\d{1,20})\s+(?P<date>\d{2}[./-]\d{2}[./-]\d{4})(?:\s|$)"
)
KASPI_AMOUNT_LINE_RE = re.compile(
    r"^(?P<amount>(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[.,]\d{2})?)\s+(?P<details>.+)$"
)
KASPI_PAYMENT_RE = re.compile(
    r"(?P<iban>KZ[0-9A-Z]{18})"
    r"(?:\s+(?P<bik>[A-Z0-9]{8}))?"
    r"\s+(?P<knp>\d{3})"
    r"(?:\s+(?P<purpose>.+))?$",
    re.I,
)
KASPI_BLOCK_ENDS = (
    "отчет сформирован",
    "наименование и бик обслуживающего банка",
    "дата последнего движения",
    "входящий остаток",
    "исходящий остаток",
    "номер документа",
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

def infer_kaspi_direction(purpose: str):
    value = purpose.lower()
    incoming = (
        "продажи с kaspi.kz",
        "возврат оплаты за",
        "взнос наличных",
        "зачисление",
        "поступление",
    )
    outgoing = (
        "возврат продаж с kaspi.kz",
        "оплата",
        "ипн ",
        "перевод ",
        "за товары",
        "комисси",
    )
    if any(marker in value for marker in incoming):
        return "incoming"
    if any(marker in value for marker in outgoing):
        return "outgoing"
    if value.startswith((
        "за ",
        "платежи за ",
        "обязательные ",
        "отчисления ",
        "социальные отчисления ",
        "взносы ",
        "социальный налог ",
    )):
        return "outgoing"
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

def parse_kaspi_text(text: str, source_name: str):
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    starts = [index for index, line in enumerate(lines) if KASPI_START_RE.match(line)]
    account_m = IBAN_RE.search(source_name)
    account = account_m.group(1).upper() if account_m else None
    txs = []

    for number, start in enumerate(starts):
        end = starts[number + 1] if number + 1 < len(starts) else len(lines)
        block_lines = lines[start:end]
        for index, line in enumerate(block_lines[1:], start=1):
            if line.lower().startswith(KASPI_BLOCK_ENDS):
                block_lines = block_lines[:index]
                break
        start_m = KASPI_START_RE.match(block_lines[0])
        if not start_m:
            continue

        amount_index = None
        amount_m = None
        for index, line in enumerate(block_lines[1:], start=1):
            candidate = KASPI_AMOUNT_LINE_RE.match(line)
            if candidate:
                amount_index = index
                amount_m = candidate
                break
        if amount_index is None or amount_m is None:
            continue

        amount = norm_amount(amount_m.group("amount"))
        if amount <= 0:
            continue

        amount_details = amount_m.group("details").strip()
        detail_lines = [amount_details, *block_lines[amount_index + 1:]]
        details = re.sub(r"\s+", " ", " ".join(detail_lines)).strip()
        payment_m = KASPI_PAYMENT_RE.search(details)

        counterparty_name = re.split(r"\s+БИН/ИИН\b", amount_details, maxsplit=1, flags=re.I)[0]
        counterparty_name = IBAN_RE.split(counterparty_name, maxsplit=1)[0].strip(" -")
        if not counterparty_name:
            counterparty_name = None

        bin_m = BIN_RE.search(details)
        iban = payment_m.group("iban").upper() if payment_m else None
        bik = payment_m.group("bik").upper() if payment_m and payment_m.group("bik") else None
        knp = payment_m.group("knp") if payment_m else None
        purpose = payment_m.group("purpose").strip() if payment_m and payment_m.group("purpose") else infer_purpose("\n".join(block_lines))
        document_number = start_m.group("document")

        txs.append({
            "bank": "KASPI",
            "account": account,
            "external_id": document_number,
            "operation_date": norm_date(start_m.group("date")),
            "amount": amount,
            "direction": infer_kaspi_direction(purpose),
            "currency": "KZT",
            "counterparty_name": counterparty_name,
            "counterparty_bin": bin_m.group(1) if bin_m else None,
            "counterparty_iban": iban,
            "counterparty_bik": bik,
            "document_number": document_number,
            "knp": knp,
            "kbe": None,
            "payment_purpose": purpose,
            "source_type": "pdf",
            "source_name": source_name,
            "raw_text": "\n".join(block_lines),
            "raw_json": None,
            "status": "new",
        })
    return txs

def parse_text(text: str, source_name: str):
    bank = detect_bank(text)
    if bank == "KASPI":
        kaspi_transactions = parse_kaspi_text(text, source_name)
        if kaspi_transactions:
            return bank, text, kaspi_transactions

    return bank, text, parse_generic_text(text, source_name, bank)

def parse_generic_text(text: str, source_name: str, bank: str):
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
    return txs

def parse_pdf(pdf_bytes: bytes, source_name: str):
    return parse_text(extract_text(pdf_bytes), source_name)
