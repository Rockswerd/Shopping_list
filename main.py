import os
import re
import asyncio
from typing import Optional

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

FINISH_WORDS = {
    "завершить",
    "стоп",
    "хватит",
    "выход",
    "закрыть",
    "отмена",
}

FILLER_PATTERNS = [
    r"\bалиса\b",
    r"\bпожалуйста\b",
    r"\bдобавь\b",
    r"\bдобавить\b",
    r"\bзапиши\b",
    r"\bзаписать\b",
    r"\bкупи\b",
    r"\bкупить\b",
    r"\bв\s+список\s+покупок\b",
    r"\bв\s+список\b",
    r"\bсписок\s+покупок\b",
    r"\bмне\b",
]

app = FastAPI(title="Покупки домой")


class AliceRequest(BaseModel):
    request: dict
    session: dict
    version: str
    state: dict = Field(default_factory=dict)


def extract_user_text(payload: AliceRequest) -> str:
    command = (payload.request.get("command") or "").strip()
    original_utterance = (payload.request.get("original_utterance") or "").strip()
    return command or original_utterance


def alice_response(
    request: AliceRequest,
    text: str,
    *,
    end_session: bool = False,
    session_state: Optional[dict] = None,
) -> dict:
    return {
        "version": request.version,
        "session": request.session,
        "response": {
            "text": text,
            "end_session": end_session,
        },
        "session_state": session_state or {},
    }


def clean_text(text: str) -> str:
    text = text.lower().strip()

    for pattern in FILLER_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

    text = text.replace(";", ",")
    text = text.replace(" ещё ", ", ")
    text = text.replace(" еще ", ", ")

    text = re.sub(r"\s+и\s+", ", ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"(,\s*){2,}", ", ", text)

    return text.strip(" ,.!?:;")


def split_short_plain_list(text: str) -> list[str]:
    """
    Если человек сказал короткое перечисление без запятых:
    'сыр колбаса хлеб'
    то считаем это списком отдельных товаров.

    Но длинные фразы не режем, чтобы не ломать:
    'таблетки для посудомойки'
    'приправа для болоньезе'
    """
    words = text.split()

    if len(words) <= 1:
        return [text] if text else []

    # Если слов 2-4 и все слова короткие/обычные, скорее всего это перечисление
    if 2 <= len(words) <= 4:
        if all(len(word) <= 12 for word in words):
            return words

    return [text]


def parse_items(text: str) -> list[str]:
    text = clean_text(text)

    if not text:
        return []

    if "," in text:
        raw_parts = [part.strip(" .,!?:;") for part in text.split(",")]
        parts = [part for part in raw_parts if part]
    else:
        parts = split_short_plain_list(text)

    cleaned_parts = []
    for part in parts:
        part = re.sub(r"\s+", " ", part).strip(" .,!?:;")
        if part:
            cleaned_parts.append(part)

    return cleaned_parts


def format_items_for_telegram(items: list[str]) -> str:
    if not items:
        return "🛒 Покупки домой\n- пусто"

    lines = ["🛒 Покупки домой"]
    lines.extend(f"- {item}" for item in items)
    return "\n".join(lines)


async def send_to_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    items = parse_items(text)
    message_text = format_items_for_telegram(items)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_text,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception:
        pass


@app.get("/")
async def healthcheck():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(payload: AliceRequest) -> dict:
    if payload.session.get("new"):
        return alice_response(
            payload,
            "Говорите, что добавить в список покупок",
            session_state={"stage": "awaiting_items"},
        )

    user_text = extract_user_text(payload)
    normalized_text = user_text.lower().strip()

    if normalized_text in FINISH_WORDS:
        return alice_response(
            payload,
            "Хорошо, закрываю список",
            end_session=True,
        )

    if not user_text:
        return alice_response(
            payload,
            "Я не расслышала. Скажите, что добавить, или скажите завершить",
            session_state={"stage": "awaiting_items"},
        )

    asyncio.create_task(send_to_telegram(user_text))

    return alice_response(
        payload,
        "Добавила. Говорите еще или скажите завершить",
        session_state={"stage": "awaiting_items"},
    )
