import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, Query, UploadFile

from .ai_text import analyze_transaction, enabled as ai_enabled, model_name
from .db import (
    approve_many,
    database_kind,
    delete_many,
    get_tx,
    init_db,
    insert_tx,
    list_tx,
    mark_exported,
    save_ai_result,
    set_status,
    status_counts,
    update_tx,
)
from .pdf_parser import parse_pdf


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if os.getenv("VERCEL") and os.getenv("APP_API_KEY", "change-me") == "change-me":
        raise RuntimeError("Задайте безопасный APP_API_KEY в настройках Vercel")
    init_db()
    yield


app = FastAPI(title="Выписки → 1С", version="1.0.0", lifespan=lifespan)


def auth(x_api_key: str | None):
    wanted = os.getenv("APP_API_KEY", "change-me")
    if x_api_key != wanted:
        raise HTTPException(401, "Неверный X-API-Key")


def max_pdf_mb():
    try:
        configured = max(1, int(os.getenv("MAX_PDF_MB", "15")))
    except ValueError:
        configured = 15
    # Vercel Function принимает не более 4,5 МБ вместе с multipart-обвязкой.
    return min(configured, 4) if os.getenv("VERCEL") else configured


@app.get("/health")
def health():
    return {
        "ok": True,
        "ai_enabled": ai_enabled(),
        "ai_model": model_name(),
        "database": database_kind(),
        "max_pdf_mb": max_pdf_mb(),
    }


@app.get("/")
def home():
    return {"service": "Выписки → 1С API", "docs": "/docs", "ui": "http://localhost:3000"}


@app.post("/api/v1/pdf")
async def upload_pdf(file: UploadFile = File(...), x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    filename = Path(file.filename or "statement.pdf").name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Поддерживаются только PDF-выписки")
    data = await file.read()
    max_bytes = max_pdf_mb() * 1024 * 1024
    if not data or len(data) > max_bytes:
        raise HTTPException(413, f"PDF должен быть от 1 байта до {max_bytes // 1024 // 1024} МБ")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Файл не похож на PDF")
    try:
        bank, _, transactions = parse_pdf(data, filename)
    except Exception as error:
        raise HTTPException(422, f"Не удалось прочитать PDF: {error}") from error

    created = duplicates = 0
    ids = []
    for transaction in transactions:
        tx_id, duplicate = insert_tx(transaction)
        ids.append(tx_id)
        duplicates += int(duplicate)
        created += int(not duplicate)
    return {
        "bank": bank,
        "detected_operations": len(transactions),
        "created": created,
        "duplicates": duplicates,
        "ids": ids,
        "file_deleted": True,
        "warning": None if transactions else "Операции не найдены — нужен пример выписки для настройки шаблона.",
    }


@app.post("/api/v1/transactions/import-json")
async def import_json(payload: dict, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    items = payload.get("transactions", [])
    if not isinstance(items, list):
        raise HTTPException(400, "transactions должен быть массивом")
    created = duplicates = 0
    for transaction in items:
        transaction.setdefault("source_type", "json")
        transaction.setdefault("source_name", "manual-json")
        transaction.setdefault("raw_json", json.dumps(transaction, ensure_ascii=False))
        _, duplicate = insert_tx(transaction)
        duplicates += int(duplicate)
        created += int(not duplicate)
    return {"created": created, "duplicates": duplicates}


@app.get("/api/v1/transactions")
def transactions(status: str | None = None, limit: int = Query(500, le=5000)):
    return {"items": list_tx(status=status, limit=limit), "counts": status_counts()}


@app.patch("/api/v1/transactions/{tx_id}")
def edit_transaction(tx_id: int, payload: dict, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    current = get_tx(tx_id)
    if not current:
        raise HTTPException(404, "Операция не найдена")
    if current["status"] != "review":
        raise HTTPException(409, "Редактировать можно только операцию на проверке")
    if "amount" in payload:
        try:
            payload["amount"] = float(payload["amount"])
        except (TypeError, ValueError) as error:
            raise HTTPException(400, "Некорректная сумма") from error
        if payload["amount"] <= 0:
            raise HTTPException(400, "Сумма должна быть больше нуля")
    updated = update_tx(tx_id, payload)
    return {"item": updated}


@app.post("/api/v1/transactions/approve")
def approve_transactions(payload: dict, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    ids = payload.get("ids", [])
    if not isinstance(ids, list):
        raise HTTPException(400, "ids должен быть массивом")
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in ids):
        raise HTTPException(400, "ids должен содержать только положительные номера операций")
    return {"approved": approve_many(ids)}


@app.post("/api/v1/transactions/delete")
def delete_transactions(payload: dict, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    ids = payload.get("ids", [])
    if not isinstance(ids, list):
        raise HTTPException(400, "ids должен быть массивом")
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in ids):
        raise HTTPException(400, "ids должен содержать только положительные номера операций")
    return {"deleted": delete_many(ids)}


@app.post("/api/v1/transactions/{tx_id}/approve")
def approve_transaction(tx_id: int, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    if not get_tx(tx_id):
        raise HTTPException(404, "Операция не найдена")
    if not set_status(tx_id, "ready", only_from="review"):
        raise HTTPException(409, "Операция уже обработана")
    return {"ok": True}


@app.post("/api/v1/transactions/{tx_id}/ignore")
def ignore_transaction(tx_id: int, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    if not set_status(tx_id, "ignored", only_from="review"):
        raise HTTPException(409, "Операция не найдена или уже обработана")
    return {"ok": True}


@app.post("/api/v1/transactions/{tx_id}/ai-text")
async def ai_for_transaction(tx_id: int, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    transaction = get_tx(tx_id)
    if not transaction:
        raise HTTPException(404, "Операция не найдена")
    if not ai_enabled():
        raise HTTPException(503, "Добавьте OPENAI_API_KEY в .env")
    try:
        result = await analyze_transaction(transaction)
    except Exception as error:
        raise HTTPException(502, f"GPT-4o недоступен: {error}") from error
    save_ai_result(tx_id, result["summary"], result["category"])
    return {"model": model_name(), **result}


@app.get("/api/v1/transactions/export")
def export_transactions(
    status: str = "ready",
    limit: int = Query(500, le=5000),
    x_api_key: str | None = Header(default=None),
):
    auth(x_api_key)
    return {"schema": "bankhub.kz/v2", "items": list_tx(status=status, limit=limit)}


@app.post("/api/v1/transactions/{tx_id}/mark-exported")
def exported(tx_id: int, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    if not mark_exported(tx_id):
        raise HTTPException(409, "Выгрузить можно только подтвержденную операцию")
    return {"ok": True}
