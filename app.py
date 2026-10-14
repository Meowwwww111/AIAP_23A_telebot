import hmac
import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Flask, jsonify, request
from google.cloud import tasks_v2
from google.protobuf.timestamp_pb2 import Timestamp

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("topic-moderator")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
TASKS_SHARED_SECRET = os.environ["TASKS_SHARED_SECRET"]
ALLOWED_CHAT_ID = int(os.getenv("ALLOWED_CHAT_ID", "-1004419987380"))
ALLOWED_TOPIC_IDS = {
    int(topic_id.strip())
    for topic_id in os.getenv("ALLOWED_TOPIC_IDS", "263").split(",")
    if topic_id.strip()
}
GENERAL_CHAT_URL = os.getenv(
    "GENERAL_CHAT_URL", "https://t.me/c/4419987380/1"
)
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
TASKS_LOCATION = os.getenv("TASKS_LOCATION", "asia-southeast1")
TASKS_QUEUE = os.getenv("TASKS_QUEUE", "topic-notice-cleanup")
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
NOTICE_DELETE_DELAY_SECONDS = 8

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


class TelegramAPIError(Exception):
    def __init__(self, error_code: int | None, description: str):
        super().__init__(description)
        self.error_code = error_code


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
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            response_data = json.loads(error.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            response_data = {}
        raise TelegramAPIError(
            response_data.get("error_code", error.code),
            response_data.get("description", "Telegram API request failed"),
        ) from None

    if not result.get("ok"):
        raise TelegramAPIError(
            result.get("error_code"), result.get("description", "Telegram API request failed")
        )
    return result


def schedule_notice_deletion(chat_id: int, topic_id: int, message_id: int) -> None:
    if not GOOGLE_CLOUD_PROJECT:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is required to schedule notice deletion")

    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(GOOGLE_CLOUD_PROJECT, TASKS_LOCATION, TASKS_QUEUE)
    schedule_time = Timestamp()
    schedule_time.FromDatetime(
        datetime.now(timezone.utc) + timedelta(seconds=NOTICE_DELETE_DELAY_SECONDS)
    )
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": f"{request.url_root.rstrip('/')}/internal/delete-notice",
            "headers": {
                "Content-Type": "application/json",
                "X-Task-Secret": TASKS_SHARED_SECRET,
            },
            "body": json.dumps(
                {
                    "chat_id": chat_id,
                    "topic_id": topic_id,
                    "message_id": message_id,
                }
            ).encode("utf-8"),
        },
        "schedule_time": schedule_time,
    }
    client.create_task(request={"parent": parent, "task": task})


def post_guidance_notice(topic_id: int) -> None:
    notice_text = (
        "Please post general messages in the General chat: "
        f"{GENERAL_CHAT_URL}"
    )
    try:
        result = telegram_api(
            "sendMessage",
            {
                "chat_id": ALLOWED_CHAT_ID,
                "message_thread_id": topic_id,
                "text": notice_text,
            },
        )
        notice_message_id = result["result"]["message_id"]
    except (urllib.error.URLError, TimeoutError, TelegramAPIError, ValueError, KeyError):
        logger.warning("Could not post the General chat guidance notice")
        return

    try:
        schedule_notice_deletion(ALLOWED_CHAT_ID, topic_id, notice_message_id)
        logger.info(
            "Posted temporary guidance notice topic_id=%s message_id=%s",
            topic_id,
            notice_message_id,
        )
    except Exception:
        # Do not leave an unscheduled notice in the topic if task creation fails.
        logger.warning("Could not schedule guidance notice cleanup; removing it now")
        try:
            telegram_api(
                "deleteMessage",
                {"chat_id": ALLOWED_CHAT_ID, "message_id": notice_message_id},
            )
        except (urllib.error.URLError, TimeoutError, TelegramAPIError, ValueError):
            logger.warning("Could not remove the unscheduled guidance notice")


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
    except (urllib.error.URLError, TimeoutError, TelegramAPIError, ValueError):
        logger.warning(
            "Could not delete message chat_id=%s topic_id=%s message_id=%s",
            ALLOWED_CHAT_ID,
            thread_id,
            message_id,
        )
        return

    post_guidance_notice(thread_id)


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})


@app.post("/internal/delete-notice")
def delete_notice():
    provided_secret = request.headers.get("X-Task-Secret", "")
    if not hmac.compare_digest(provided_secret, TASKS_SHARED_SECRET):
        return jsonify({"error": "unauthorized"}), 403

    task = request.get_json(silent=True)
    if not isinstance(task, dict):
        return jsonify({"error": "invalid task"}), 400

    try:
        chat_id = int(task["chat_id"])
        topic_id = int(task["topic_id"])
        message_id = int(task["message_id"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "invalid task"}), 400

    if chat_id != ALLOWED_CHAT_ID or topic_id not in ALLOWED_TOPIC_IDS:
        return jsonify({"error": "unexpected chat or topic"}), 400

    try:
        telegram_api(
            "deleteMessage",
            {"chat_id": chat_id, "message_id": message_id},
        )
    except TelegramAPIError as error:
        if error.error_code == 400:
            # The task may be retried after the notice was already removed.
            logger.info("Guidance notice already removed or no longer deletable")
            return jsonify({"ok": True})
        logger.warning("Telegram rejected delayed guidance notice deletion")
        return jsonify({"error": "Telegram deletion failed"}), 500
    except (urllib.error.URLError, TimeoutError, ValueError):
        logger.warning("Telegram API unavailable during delayed notice deletion")
        return jsonify({"error": "Telegram API unavailable"}), 500

    logger.info(
        "Deleted temporary guidance notice topic_id=%s message_id=%s",
        topic_id,
        message_id,
    )
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
