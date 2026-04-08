import os
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


async def send_to_telegram(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    message_text = f"🛒 Покупки домой\n{text}"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message_text,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                return False
    except Exception:
        return False

    return True


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
