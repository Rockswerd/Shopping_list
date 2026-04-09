import os
import re
import asyncio
from typing import Optional

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

FINISH_WORDS = {"завершить", "стоп", "хватит", "выход", "закрыть", "отмена"}

app = FastAPI(title="Покупки домой")


class AliceRequest(BaseModel):
    request: dict
    session: dict
    version: str
    state: dict = Field(default_factory=dict)


# ----------- УМНАЯ ОБРАБОТКА -----------

def normalize_shopping_text(text: str) -> str:
    text = text.strip().lower()

    prefixes = [
        "добавь в список покупок",
        "добавь в список",
        "запиши в список покупок",
        "запиши в список",
        "запиши",
        "добавь",
        "купи",
        "нужно купить",
        "надо купить",
    ]

    for prefix in prefixes:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break

    return text


def parse_items(text: str) -> list[str]:
    text = normalize_shopping_text(text)

    # Нормализуем разделители
    text = text.replace(";", ",")
    text = re.sub(r"\s+и\s+", ", ", text)

    # Убираем лишние пробелы и запятые
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r",\s*,+", ",", text).strip(" ,")

    parts = [part.strip(" .,!?:;") for part in text.split(",")]

    items = [part for part in parts if part]

    return items


def format_items_for_telegram(items: list[str]) -> str:
    if not items:
        return "🛒 Покупки домой\n- пусто"

    lines = ["🛒 Покупки домой"]
    lines.extend(f"- {item}" for item in items)
    return "\n".join(lines)


# ----------- ОСНОВНАЯ ЛОГИКА -----------

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


async def send_to_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    items = parse_items(text)
    message_text = format_items_for_telegram(items)

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

    # 🚀 отправка в фоне (без ожидания)
    asyncio.create_task(send_to_telegram(user_text))

    return alice_response(
        payload,
        "Добавила. Говорите еще или скажите завершить",
        session_state={"stage": "awaiting_items"},
    )
