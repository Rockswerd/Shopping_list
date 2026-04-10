import os
import re
from datetime import datetime
from typing import Optional

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

FINISH_WORDS = {
    "завершить",
    "стоп",
    "хватит",
    "выход",
    "закрыть",
    "отмена",
    "все",
    "готово",
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


def now_date_string() -> str:
    try:
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y")
    except Exception:
        pass
    return datetime.now().strftime("%d.%m.%Y")


def extract_user_text(payload: AliceRequest) -> str:
    command = (payload.request.get("command") or "").strip()
    original_utterance = (payload.request.get("original_utterance") or "").strip()
    return command or original_utterance


def get_session_state(payload: AliceRequest) -> dict:
    return payload.state.get("session", {}) or {}


def alice_response(
    request: AliceRequest,
    text: str,
    *,
    end_session: bool = False,
    session_state: Optional[dict] = None,
) -> dict:
    response = {
        "version": request.version,
        "session": request.session,
        "response": {
            "text": text,
            "end_session": end_session,
        },
    }

    if session_state is not None:
        response["session_state"] = session_state

    return response


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
    words = text.split()

    if len(words) <= 1:
        return [text] if text else []

    if 2 <= len(words) <= 4 and all(len(word) <= 12 for word in words):
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


def build_telegram_message(items: list[str]) -> str:
    title = f"🛒 Список покупок - {now_date_string()}"
    if not items:
        return f"{title}\n- пусто"

    lines = [title]
    lines.extend(f"- {item}" for item in items)
    return "\n".join(lines)


async def telegram_api_call(method: str, payload: dict) -> dict:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()

    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")

    return data


async def upsert_telegram_list(items: list[str], message_id: Optional[int]) -> Optional[int]:
    if not TELEGRAM_CHAT_ID:
        return None

    text = build_telegram_message(items)

    if message_id is None:
        data = await telegram_api_call(
            "sendMessage",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
            },
        )
        return data["result"]["message_id"]

    try:
        await telegram_api_call(
            "editMessageText",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "message_id": message_id,
                "text": text,
            },
        )
        return message_id
    except Exception:
        data = await telegram_api_call(
            "sendMessage",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
            },
        )
        return data["result"]["message_id"]


@app.get("/")
async def healthcheck():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(payload: AliceRequest) -> dict:
    current_state = get_session_state(payload)

    if payload.session.get("new"):
        return alice_response(
            payload,
            "Говорите, что добавить в список покупок",
            session_state={
                "stage": "awaiting_items",
                "items": [],
                "telegram_message_id": None,
            },
        )

    user_text = extract_user_text(payload)
    normalized_text = user_text.lower().strip()

    items = current_state.get("items", [])
    if not isinstance(items, list):
        items = []

    telegram_message_id = current_state.get("telegram_message_id")
    if not isinstance(telegram_message_id, int):
        telegram_message_id = None

    if normalized_text in FINISH_WORDS:
        if not items:
            return alice_response(
                payload,
                "Список пустой. Закрываю",
                end_session=True,
            )

        if telegram_message_id is None:
            try:
                telegram_message_id = await upsert_telegram_list(items, None)
            except Exception:
                return alice_response(
                    payload,
                    "Не получилось сохранить список в Телеграм. Попробуйте еще раз",
                    end_session=False,
                    session_state={
                        "stage": "awaiting_items",
                        "items": items,
                        "telegram_message_id": telegram_message_id,
                    },
                )

        return alice_response(
            payload,
            "Готово. Список сохранен",
            end_session=True,
        )

    if not user_text:
        return alice_response(
            payload,
            "Я не расслышала. Скажите, что добавить, или скажите завершить",
            session_state={
                "stage": "awaiting_items",
                "items": items,
                "telegram_message_id": telegram_message_id,
            },
        )

    new_items = parse_items(user_text)
    if not new_items:
        return alice_response(
            payload,
            "Не поняла, что добавить. Скажите товар еще раз",
            session_state={
                "stage": "awaiting_items",
                "items": items,
                "telegram_message_id": telegram_message_id,
            },
        )

    items = items + new_items

    try:
        telegram_message_id = await upsert_telegram_list(items, telegram_message_id)
    except Exception:
        return alice_response(
            payload,
            "Не получилось обновить список в Телеграм. Попробуйте еще раз",
            end_session=False,
            session_state={
                "stage": "awaiting_items",
                "items": items,
                "telegram_message_id": telegram_message_id,
            },
        )

    added_text = ", ".join(new_items)

    return alice_response(
        payload,
        f"Добавила: {added_text}. Говорите еще или скажите завершить",
        session_state={
            "stage": "awaiting_items",
            "items": items,
            "telegram_message_id": telegram_message_id,
        },
    )
