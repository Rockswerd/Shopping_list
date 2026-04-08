import os
from typing import Optional

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FINISH_WORDS = {"завершить", "стоп", "хватит", "выход"}

app = FastAPI(title="Покупки домой")


class AliceRequest(BaseModel):
    request: dict
    session: dict
    version: str
    state: dict = Field(default_factory=dict)


def alice_response(
    request: AliceRequest,
    text: str,
    *,
    end_session: bool = False,
    session_state: Optional[dict] = None,
) -> dict:
    """Build a response in Yandex Dialogs webhook format."""
    return {
        "version": request.version,
        "session": request.session,
        "response": {
            "text": text,
            "end_session": end_session,
        },
        "session_state": session_state or {},
    }


async def send_to_telegram(text: str) -> bool:
    """Send the shopping item text to the configured Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
    except httpx.HTTPError:
        return False

    return True


@app.post("/webhook")
async def webhook(payload: AliceRequest) -> dict:
    if payload.session.get("new"):
        return alice_response(
            payload,
            "Говорите, что добавить в список покупок",
            session_state={"stage": "awaiting_items"},
        )

    user_text = payload.request.get("original_utterance", "").strip()

    if user_text.lower() in FINISH_WORDS:
        return alice_response(
            payload,
            "Хорошо, закрываю список",
            end_session=True,
        )

    if not await send_to_telegram(user_text):
        return alice_response(
            payload,
            "Не получилось отправить список. Попробуйте еще раз",
            session_state={"stage": "awaiting_items"},
        )

    return alice_response(
        payload,
        "Добавила. Говорите еще или скажите завершить",
        session_state={"stage": "awaiting_items"},
    )
