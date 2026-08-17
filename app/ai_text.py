import json
import os

import httpx


OPENAI_URL = "https://api.openai.com/v1/responses"


def enabled():
    return bool(os.getenv("OPENAI_API_KEY"))


def model_name():
    return os.getenv("OPENAI_MODEL", "gpt-4o")


def _output_text(payload):
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content.get("text", "")
    raise RuntimeError("OpenAI response does not contain output_text")


async def analyze_transaction(transaction):
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    source = {
        "bank": transaction.get("bank"),
        "direction": transaction.get("direction"),
        "counterparty": transaction.get("counterparty_name"),
        "counterparty_bin": transaction.get("counterparty_bin"),
        "knp": transaction.get("knp"),
        "purpose": transaction.get("payment_purpose"),
        "raw_text": (transaction.get("raw_text") or "")[:6000],
    }
    schema = {
        "type": "object",
        "properties": {
            "normalized_purpose": {"type": "string"},
            "summary": {"type": "string"},
            "category": {"type": "string"},
        },
        "required": ["normalized_purpose", "summary", "category"],
        "additionalProperties": False,
    }
    request_body = {
        "model": model_name(),
        "store": False,
        "instructions": (
            "Ты помощник бухгалтера в Казахстане. Работай только с переданным текстом банковской "
            "операции. Исправь пробелы и явные артефакты распознавания в назначении платежа, но не "
            "выдумывай факты, БИН, номера договоров или счета. Дай короткое понятное резюме и "
            "предположительную категорию учета. Если данных мало, прямо укажи это. Отвечай по-русски."
        ),
        "input": json.dumps(source, ensure_ascii=False),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "bank_transaction_text",
                "strict": True,
                "schema": schema,
            }
        },
    }
    timeout = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "45"))
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=request_body,
        )
    if response.is_error:
        detail = response.text[:500]
        raise RuntimeError(f"OpenAI API returned {response.status_code}: {detail}")
    try:
        result = json.loads(_output_text(response.json()))
    except (ValueError, KeyError) as error:
        raise RuntimeError("OpenAI returned an invalid structured response") from error
    return result
