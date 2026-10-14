import hmac
import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any

from flask import Flask, jsonify, request

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("topic-moderator")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
ALLOWED_CHAT_ID = int(os.getenv("ALLOWED_CHAT_ID", "-1004419987380"))
ALLOWED_TOPIC_IDS = {
    int(topic_id.strip())
    for topic_id in os.getenv("ALLOWED_TOPIC_IDS", "263").split(",")
    if topic_id.strip()
}
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

URL_RE = re.compile(r"(?:https?://|www\.)\S+|t\.me/\S+", re.IGNORECASE)
URL_ENTITY_TYPES = {"url", "text_link"}
MEDIA_FIELDS = {
    "animation",
    "audio",
    "contact",
    "document",
    "game",
    "giveaway",
    "giveaway_winners",
    "invoice",
    "location",
    "paid_media",
    "photo",
    "poll",
    "sticker",
    "venue",
    "video",
    "video_note",
    "voice",
}
SERVICE_MESSAGE_FIELDS = {
    "new_chat_members",
    "left_chat_member",
    "new_chat_title",
    "new_chat_photo",
    "delete_chat_photo",
    "group_chat_created",
    "supergroup_chat_created",
    "channel_chat_created",
    "message_auto_delete_timer_changed",
    "migrate_to_chat_id",
    "migrate_from_chat_id",
    "pinned_message",
    "forum_topic_created",
    "forum_topic_edited",
    "forum_topic_closed",
    "forum_topic_reopened",
    "general_forum_topic_hidden",
    "general_forum_topic_unhidden",
    "video_chat_scheduled",
    "video_chat_started",
    "video_chat_ended",
    "video_chat_participants_invited",
    "write_access_allowed",
    "proximity_alert_triggered",
    "boost_added",
    "chat_background_set",
}

app = Flask(__name__)


def has_link(text: str | None, entities: list[dict[str, Any]] | None) -> bool:
    if text and URL_RE.search(text):
        return True
    return any(entity.get("type") in URL_ENTITY_TYPES for entity in entities or [])


def has_allowed_content(message: dict[str, Any]) -> bool:
    if any(field in message for field in MEDIA_FIELDS):
        return True
    if message.get("link_preview_options"):
        return True
    return has_link(message.get("text"), message.get("entities")) or has_link(
        message.get("caption"), message.get("caption_entities")
    )


def telegram_api(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{TELEGRAM_API_BASE}/{method}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {result}")
    return result


def moderate_message(message: dict[str, Any]) -> None:
    chat = message.get("chat") or {}
    if chat.get("id") != ALLOWED_CHAT_ID:
        return

    thread_id = message.get("message_thread_id")
    if thread_id not in ALLOWED_TOPIC_IDS:
        return

    sender = message.get("from") or {}
    if sender.get("is_bot"):
        return

    if SERVICE_MESSAGE_FIELDS.intersection(message):
        return

    if has_allowed_content(message):
        return

    message_id = message.get("message_id")
    if not isinstance(message_id, int):
        return

    try:
        telegram_api(
            "deleteMessage",
            {"chat_id": ALLOWED_CHAT_ID, "message_id": message_id},
        )
        logger.info(
            "Deleted text-only message chat_id=%s topic_id=%s message_id=%s",
            ALLOWED_CHAT_ID,
            thread_id,
            message_id,
        )
    except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError):
        # Acknowledge the update so a transient deletion error does not cause
        # Telegram to deliver the same update repeatedly.
        logger.exception(
            "Could not delete message chat_id=%s topic_id=%s message_id=%s",
            ALLOWED_CHAT_ID,
            thread_id,
            message_id,
        )


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.post("/telegram/webhook")
def telegram_webhook():
    provided_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(provided_secret, WEBHOOK_SECRET):
        return jsonify({"error": "unauthorized"}), 403

    update = request.get_json(silent=True)
    if not isinstance(update, dict):
        return jsonify({"error": "invalid update"}), 400

    message = update.get("message") or update.get("edited_message")
    if isinstance(message, dict):
        moderate_message(message)

    return jsonify({"ok": True})
