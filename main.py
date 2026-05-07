import os
import re
from difflib import SequenceMatcher
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
ALICE_APPLICATION_ID = os.getenv("ALICE_APPLICATION_ID")

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

NEGATIVE_ADD_PATTERNS = [
    r"\bне\s+добавляй\b[^,]*(?:,|$)",
    r"\bне\s+добавить\b[^,]*(?:,|$)",
    r"\bне\s+записывай\b[^,]*(?:,|$)",
    r"\bне\s+записать\b[^,]*(?:,|$)",
    r"\bне\s+купи\b[^,]*(?:,|$)",
    r"\bне\s+купить\b[^,]*(?:,|$)",
]

VIEW_LIST_COMMANDS = {
    "что в списке",
    "что у нас в списке",
    "что в списке покупок",
    "что в покупках",
    "что я добавил",
    "что я добавила",
    "что уже добавил",
    "что уже добавила",
    "что я уже добавил",
    "что я уже добавила",
    "прочитай список",
    "прочитай список покупок",
    "покажи список",
    "покажи список покупок",
    "назови список",
    "назови список покупок",
    "назови покупки",
    "перечисли список",
    "перечисли покупки",
    "какие покупки",
    "какие товары в списке",
    "список",
    "покупки",
}

CLEAR_LIST_COMMANDS = {
    "очисти список",
    "очистить список",
    "очисти список покупок",
    "очистить список покупок",
    "удали все",
    "убери все",
    "удали все из списка",
    "убери все из списка",
    "удали все покупки",
    "убери все покупки",
    "сотри все",
    "сотри все покупки",
    "сотри список",
    "сбрось список",
    "начать заново",
    "начни заново",
}

UNDO_LAST_COMMANDS = {
    "отмени последнее",
    "отменить последнее",
    "удали последнее",
    "убери последнее",
    "вычеркни последнее",
    "последнее удали",
    "последнее убери",
    "последний пункт удали",
    "последний пункт убери",
    "убери последний пункт",
    "удали последний пункт",
    "откатить последнее",
}

DELETE_ITEM_PREFIXES = (
    "удали ",
    "удалить ",
    "убери ",
    "убрать ",
    "вычеркни ",
    "вычеркнуть ",
)

DELETE_ITEM_PATTERNS = [
    r"^(?:удали|удалить|убери|убрать|вычеркни|вычеркнуть)\s+(?:из\s+)?(?:покупок|списка\s+покупок|списка)\s+(.+)$",
    r"^(?:из\s+)?(?:покупок|списка\s+покупок|списка)\s+(?:удали|удалить|убери|убрать|вычеркни|вычеркнуть)\s+(.+)$",
    r"^(.+?)\s+(?:удали|удалить|убери|убрать|вычеркни|вычеркнуть)(?:\s+из\s+(?:покупок|списка\s+покупок|списка))?$",
]

DELETE_ITEM_FILLER_PATTERNS = [
    r"\bиз\s+списка\s+покупок\b",
    r"\bиз\s+списка\b",
    r"\bв\s+списке\s+покупок\b",
    r"\bв\s+списке\b",
]

QUANTITY_WORDS = {
    "один",
    "одна",
    "одно",
    "два",
    "две",
    "три",
    "четыре",
    "пять",
    "шесть",
    "семь",
    "восемь",
    "девять",
    "десять",
    "пол",
    "полтора",
    "полторы",
    "кг",
    "килограмм",
    "килограмма",
    "килограммов",
    "грамм",
    "грамма",
    "граммов",
    "литр",
    "литра",
    "литров",
    "бутылка",
    "бутылки",
    "бутылок",
    "пачка",
    "пачки",
    "пачек",
    "упаковка",
    "упаковки",
    "штука",
    "штуки",
    "штук",
}

ADJECTIVE_ENDINGS = (
    "ый",
    "ий",
    "ой",
    "ая",
    "яя",
    "ое",
    "ее",
    "ые",
    "ие",
    "ого",
    "его",
    "ую",
    "юю",
)

app = FastAPI(title="Покупки домой")

ACTIVE_SESSIONS: dict[str, dict] = {}


class AliceRequest(BaseModel):
    request: dict
    session: dict
    version: str
    state: dict = Field(default_factory=dict)


def log(message: str):
    print(message, flush=True)


def now_date_string() -> str:
    try:
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y")
    except Exception:
        pass
    return datetime.now().strftime("%d.%m.%Y")


def now_datetime_string() -> str:
    try:
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
    except Exception:
        pass
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def extract_user_text(payload: AliceRequest) -> str:
    command = (payload.request.get("command") or "").strip()
    original_utterance = (payload.request.get("original_utterance") or "").strip()
    return command or original_utterance


def get_application_id(payload: AliceRequest) -> str:
    application = payload.session.get("application") or {}
    return str(application.get("application_id") or payload.session.get("application_id") or "")


def get_session_id(payload: AliceRequest) -> str:
    return str(payload.session.get("session_id", ""))


def get_message_id(payload: AliceRequest) -> int:
    return int(payload.session.get("message_id", 0))


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


def normalize_command(text: str) -> str:
    text = text.lower().replace("ё", "е").strip()
    text = re.sub(r"[.!?:;]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,")


def is_authorized_alice_app(payload: AliceRequest) -> bool:
    if not ALICE_APPLICATION_ID:
        return True
    return get_application_id(payload) == ALICE_APPLICATION_ID


def clean_text(text: str) -> str:
    text = normalize_command(text)

    for pattern in NEGATIVE_ADD_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

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

    if any(re.search(r"\d", word) or word in QUANTITY_WORDS for word in words):
        return [text]

    if 2 <= len(words) <= 4 and all(len(word) <= 12 for word in words):
        grouped_words = []
        index = 0

        while index < len(words):
            word = words[index]

            if index + 1 < len(words) and word.endswith(ADJECTIVE_ENDINGS):
                grouped_words.append(f"{word} {words[index + 1]}")
                index += 2
            else:
                grouped_words.append(word)
                index += 1

        return grouped_words

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


def escape_telegram_markdown(text: str) -> str:
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!\\])", r"\\\1", text)


def build_telegram_message(items: list[str]) -> str:
    updated_at = escape_telegram_markdown(now_datetime_string())
    title = "*Список покупок*"

    if not items:
        return f"{title}\n_Обновлено: {updated_at}_\n\n• пусто"

    lines = [
        title,
        f"_Обновлено: {updated_at}_",
        "",
    ]
    lines.extend(f"• {escape_telegram_markdown(item)}" for item in items)
    return "\n".join(lines)


def format_items_for_alice(items: list[str]) -> str:
    if not items:
        return "Список пока пустой"

    if len(items) == 1:
        return f"В списке сейчас: {items[0]}"

    return f"В списке сейчас: {', '.join(items)}"


def build_added_response(items: list[str]) -> str:
    added_text = ", ".join(items)
    if len(items) == 1:
        return f"Записала: {added_text}. Что еще добавить?"
    return f"Добавила: {added_text}. Продолжайте или скажите завершить"


def remember_response(session_data: dict, message_id: int, response_text: str, *, end_session: bool = False):
    session_data["last_processed_message_id"] = message_id
    session_data["last_response_text"] = response_text
    session_data["last_end_session"] = end_session


def detect_delete_item(text: str) -> Optional[str]:
    normalized_text = normalize_command(text)

    for pattern in DELETE_ITEM_PATTERNS:
        match = re.match(pattern, normalized_text, flags=re.IGNORECASE)
        if match:
            item_text = match.group(1).strip()

            for filler_pattern in DELETE_ITEM_FILLER_PATTERNS:
                item_text = re.sub(filler_pattern, " ", item_text, flags=re.IGNORECASE)

            return clean_text(item_text)

    for prefix in DELETE_ITEM_PREFIXES:
        if normalized_text.startswith(prefix):
            item_text = normalized_text.removeprefix(prefix).strip()

            for pattern in DELETE_ITEM_FILLER_PATTERNS:
                item_text = re.sub(pattern, " ", item_text, flags=re.IGNORECASE)

            return clean_text(item_text)

    return None


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_command(left), normalize_command(right)).ratio()


def find_item_matches(existing_items: list[str], requested_item: str) -> list[str]:
    normalized_requested = normalize_command(requested_item)
    exact_matches = [
        item for item in existing_items
        if normalize_command(item) == normalized_requested
    ]

    if exact_matches:
        return exact_matches

    partial_matches = [
        item for item in existing_items
        if len(normalized_requested) >= 4 and normalized_requested in normalize_command(item)
    ]

    if partial_matches:
        return partial_matches

    fuzzy_matches = [
        item for item in existing_items
        if similarity(item, requested_item) >= 0.82
    ]

    return fuzzy_matches


def remove_items(existing_items: list[str], requested_items: list[str]) -> tuple[list[str], list[str], list[str]]:
    ambiguous_items = []
    items_to_remove = set()

    for requested_item in requested_items:
        matches = find_item_matches(existing_items, requested_item)

        if len(matches) == 1:
            items_to_remove.add(normalize_command(matches[0]))
        elif len(matches) > 1:
            ambiguous_items.extend(matches)

    remaining_items = []
    removed_items = []

    for item in existing_items:
        if normalize_command(item) in items_to_remove:
            removed_items.append(item)
        else:
            remaining_items.append(item)

    return remaining_items, removed_items, ambiguous_items


async def telegram_api_call(method: str, payload: dict) -> dict:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"

    safe_payload = dict(payload)
    if "chat_id" in safe_payload:
        safe_payload["chat_id"] = str(safe_payload["chat_id"])

    log(f"TELEGRAM REQUEST METHOD: {method}")
    log(f"TELEGRAM REQUEST PAYLOAD: {safe_payload}")

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload)

    log(f"TELEGRAM HTTP STATUS: {response.status_code}")
    log(f"TELEGRAM RAW RESPONSE: {response.text}")

    response.raise_for_status()
    data = response.json()

    if not data.get("ok"):
        description = data.get("description", "Unknown Telegram API error")
        raise RuntimeError(f"Telegram API error in {method}: {description}")

    return data


async def upsert_telegram_list(items: list[str], message_id: Optional[int]) -> int:
    if not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is not set")

    text = build_telegram_message(items)

    if message_id is None:
        log("TELEGRAM ACTION: sendMessage because message_id is None")
        data = await telegram_api_call(
            "sendMessage",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "MarkdownV2",
            },
        )
        new_message_id = int(data["result"]["message_id"])
        log(f"TELEGRAM NEW MESSAGE_ID: {new_message_id}")
        return new_message_id

    log(f"TELEGRAM ACTION: editMessageText message_id={message_id}")

    try:
        await telegram_api_call(
            "editMessageText",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "message_id": message_id,
                "text": text,
                "parse_mode": "MarkdownV2",
            },
        )
        return message_id

    except RuntimeError as exc:
        error_text = str(exc).lower()
        log(f"TELEGRAM EDIT ERROR: {str(exc)}")

        if "message is not modified" in error_text:
            log("TELEGRAM EDIT IGNORED: message is not modified")
            return message_id

        raise

    except Exception as exc:
        log(f"TELEGRAM UNEXPECTED ERROR: {repr(exc)}")
        raise


def get_or_create_session_data(session_id: str) -> dict:
    if session_id not in ACTIVE_SESSIONS:
        ACTIVE_SESSIONS[session_id] = {
            "items": [],
            "telegram_message_id": None,
            "last_processed_message_id": None,
            "last_response_text": "Говорите, что добавить в список покупок",
            "last_end_session": False,
        }
    return ACTIVE_SESSIONS[session_id]


@app.get("/")
async def healthcheck():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(payload: AliceRequest) -> dict:
    session_id = get_session_id(payload)
    message_id = get_message_id(payload)

    log(f"ALICE REQUEST: session_id={session_id}, message_id={message_id}, new={payload.session.get('new')}")
    log(f"ALICE TEXT: {extract_user_text(payload)}")

    if not is_authorized_alice_app(payload):
        log(f"ALICE UNAUTHORIZED APPLICATION_ID: {get_application_id(payload)}")
        return alice_response(
            payload,
            "Этот адрес не подключен к вашему навыку",
            end_session=True,
        )

    if not session_id:
        return alice_response(
            payload,
            "Ошибка сессии. Попробуйте запустить навык еще раз",
            end_session=True,
        )

    if payload.session.get("new"):
        ACTIVE_SESSIONS[session_id] = {
            "items": [],
            "telegram_message_id": None,
            "last_processed_message_id": None,
            "last_response_text": "Говорите, что добавить в список покупок",
            "last_end_session": False,
        }
        return alice_response(
            payload,
            "Говорите, что добавить в список покупок",
            session_state={"stage": "awaiting_items"},
        )

    session_data = get_or_create_session_data(session_id)

    if session_data.get("last_processed_message_id") == message_id:
        log("ALICE DUPLICATE REQUEST IGNORED")
        return alice_response(
            payload,
            session_data.get("last_response_text", "Хорошо"),
            end_session=session_data.get("last_end_session", False),
            session_state={"stage": "awaiting_items"} if not session_data.get("last_end_session", False) else None,
        )

    items = session_data.get("items", [])
    telegram_message_id = session_data.get("telegram_message_id")

    user_text = extract_user_text(payload)
    normalized_text = normalize_command(user_text)

    if normalized_text in FINISH_WORDS:
        response_text = "Готово. Список сохранен" if items else "Список пустой. Закрываю"
        if not items:
            ACTIVE_SESSIONS.pop(session_id, None)
            return alice_response(
                payload,
                response_text,
                end_session=True,
            )

        ACTIVE_SESSIONS.pop(session_id, None)
        return alice_response(
            payload,
            response_text,
            end_session=True,
        )

    if not user_text:
        response_text = "Я не расслышала. Скажите, что добавить, или скажите завершить"
        remember_response(session_data, message_id, response_text)
        return alice_response(
            payload,
            response_text,
            session_state={"stage": "awaiting_items"},
        )

    if normalized_text in VIEW_LIST_COMMANDS:
        response_text = format_items_for_alice(items)
        remember_response(session_data, message_id, response_text)
        return alice_response(
            payload,
            response_text,
            session_state={"stage": "awaiting_items"},
        )

    if normalized_text in CLEAR_LIST_COMMANDS:
        items = []
        try:
            if telegram_message_id is not None:
                telegram_message_id = await upsert_telegram_list(items, telegram_message_id)
        except Exception as exc:
            log(f"FINAL TELEGRAM ERROR: {repr(exc)}")
            response_text = "Не получилось очистить список в Телеграм. Попробуйте еще раз"
            remember_response(session_data, message_id, response_text)
            return alice_response(
                payload,
                response_text,
                session_state={"stage": "awaiting_items"},
            )

        response_text = "Очистила список. Можно начинать заново"
        session_data["items"] = items
        session_data["telegram_message_id"] = telegram_message_id
        remember_response(session_data, message_id, response_text)
        return alice_response(
            payload,
            response_text,
            session_state={"stage": "awaiting_items"},
        )

    if normalized_text in UNDO_LAST_COMMANDS:
        if not items:
            response_text = "Отменять нечего, список пустой"
            remember_response(session_data, message_id, response_text)
            return alice_response(
                payload,
                response_text,
                session_state={"stage": "awaiting_items"},
            )

        removed_item = items[-1]
        items = items[:-1]

        try:
            telegram_message_id = await upsert_telegram_list(items, telegram_message_id)
        except Exception as exc:
            log(f"FINAL TELEGRAM ERROR: {repr(exc)}")
            response_text = "Не получилось обновить список в Телеграм. Попробуйте еще раз"
            remember_response(session_data, message_id, response_text)
            return alice_response(
                payload,
                response_text,
                session_state={"stage": "awaiting_items"},
            )

        response_text = f"Убрала последнее: {removed_item}"
        session_data["items"] = items
        session_data["telegram_message_id"] = telegram_message_id
        remember_response(session_data, message_id, response_text)
        return alice_response(
            payload,
            response_text,
            session_state={"stage": "awaiting_items"},
        )

    delete_item_text = detect_delete_item(user_text)
    if delete_item_text:
        requested_items = parse_items(delete_item_text)

        if not requested_items:
            response_text = "Скажите, что именно убрать из списка"
            remember_response(session_data, message_id, response_text)
            return alice_response(
                payload,
                response_text,
                session_state={"stage": "awaiting_items"},
            )

        items, removed_items, ambiguous_items = remove_items(items, requested_items)

        if ambiguous_items:
            response_text = f"Нашла несколько похожих товаров: {', '.join(ambiguous_items)}. Назовите точнее"
            remember_response(session_data, message_id, response_text)
            return alice_response(
                payload,
                response_text,
                session_state={"stage": "awaiting_items"},
            )

        if not removed_items:
            response_text = f"Не нашла в списке: {', '.join(requested_items)}"
            remember_response(session_data, message_id, response_text)
            return alice_response(
                payload,
                response_text,
                session_state={"stage": "awaiting_items"},
            )

        try:
            telegram_message_id = await upsert_telegram_list(items, telegram_message_id)
        except Exception as exc:
            log(f"FINAL TELEGRAM ERROR: {repr(exc)}")
            response_text = "Не получилось обновить список в Телеграм. Попробуйте еще раз"
            remember_response(session_data, message_id, response_text)
            return alice_response(
                payload,
                response_text,
                session_state={"stage": "awaiting_items"},
            )

        response_text = f"Убрала: {', '.join(removed_items)}"
        session_data["items"] = items
        session_data["telegram_message_id"] = telegram_message_id
        remember_response(session_data, message_id, response_text)
        return alice_response(
            payload,
            response_text,
            session_state={"stage": "awaiting_items"},
        )

    new_items = parse_items(user_text)
    log(f"PARSED ITEMS: {new_items}")

    if not new_items:
        response_text = "Не поняла, что добавить. Скажите товар еще раз"
        remember_response(session_data, message_id, response_text)
        return alice_response(
            payload,
            response_text,
            session_state={"stage": "awaiting_items"},
        )

    items = items + new_items

    try:
        telegram_message_id = await upsert_telegram_list(items, telegram_message_id)
    except Exception as exc:
        log(f"FINAL TELEGRAM ERROR: {repr(exc)}")

        response_text = "Не получилось обновить список в Телеграм. Попробуйте еще раз"
        session_data["items"] = items
        session_data["telegram_message_id"] = telegram_message_id
        remember_response(session_data, message_id, response_text)
        return alice_response(
            payload,
            response_text,
            end_session=False,
            session_state={"stage": "awaiting_items"},
        )

    response_text = build_added_response(new_items)

    session_data["items"] = items
    session_data["telegram_message_id"] = telegram_message_id
    remember_response(session_data, message_id, response_text)

    log(f"SESSION UPDATED: items={items}, telegram_message_id={telegram_message_id}")

    return alice_response(
        payload,
        response_text,
        session_state={"stage": "awaiting_items"},
    )
