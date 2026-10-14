const DEFAULT_CHAT_ID = -1004419987380;
const DEFAULT_TOPIC_IDS = [263];
const DEFAULT_GENERAL_CHAT_URL = "https://t.me/c/4419987380/1";

const URL_RE = /(?:https?:\/\/|www\.)\S+|t\.me\/\S+/i;
const URL_ENTITY_TYPES = new Set(["url", "text_link"]);
const MEDIA_FIELDS = new Set([
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
]);
const SERVICE_MESSAGE_FIELDS = new Set([
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
]);

class TelegramAPIError extends Error {
  constructor(errorCode, description) {
    super(description);
    this.errorCode = errorCode;
  }
}

function configFrom(env) {
  const configuredTopics = (env.ALLOWED_TOPIC_IDS ?? DEFAULT_TOPIC_IDS.join(","))
    .split(",")
    .map((value) => Number(value.trim()))
    .filter(Number.isInteger);

  return {
    chatId: Number(env.ALLOWED_CHAT_ID ?? DEFAULT_CHAT_ID),
    topicIds: new Set(configuredTopics),
    generalChatUrl: env.GENERAL_CHAT_URL ?? DEFAULT_GENERAL_CHAT_URL,
  };
}

function safeEqual(provided, expected) {
  if (!provided || !expected) return false;

  const left = new TextEncoder().encode(provided);
  const right = new TextEncoder().encode(expected);
  if (left.length !== right.length) return false;

  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left[index] ^ right[index];
  }
  return difference === 0;
}

function hasLink(text, entities) {
  if (typeof text === "string" && URL_RE.test(text)) return true;
  return Array.isArray(entities)
    && entities.some((entity) => URL_ENTITY_TYPES.has(entity?.type));
}

function hasAllowedContent(message) {
  for (const field of MEDIA_FIELDS) {
    if (Object.hasOwn(message, field)) return true;
  }

  const previewOptions = message.link_preview_options;
  if (previewOptions && Object.keys(previewOptions).length > 0) return true;

  return hasLink(message.text, message.entities)
    || hasLink(message.caption, message.caption_entities);
}

async function telegramApi(env, method, payload) {
  if (!env.TELEGRAM_BOT_TOKEN) {
    throw new Error("TELEGRAM_BOT_TOKEN secret is missing");
  }

  const response = await fetch(
    `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(10_000),
    },
  );

  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error(`Telegram ${method} returned a non-JSON response`);
  }

  if (!response.ok || !result.ok) {
    throw new TelegramAPIError(
      result.error_code ?? response.status,
      result.description ?? `Telegram ${method} failed`,
    );
  }

  return result;
}

async function postGuidanceNotice(env, config, topicId) {
  let noticeMessageId;
  try {
    const result = await telegramApi(env, "sendMessage", {
      chat_id: config.chatId,
      message_thread_id: topicId,
      text: `Please post general messages in the General chat: ${config.generalChatUrl}`,
    });
    noticeMessageId = result.result.message_id;
  } catch (error) {
    console.warn("Could not post the General chat guidance notice", error);
    return;
  }

  try {
    await env.NOTICE_CLEANUP.send(
      {
        chatId: config.chatId,
        topicId,
        messageId: noticeMessageId,
      },
      { delaySeconds: 8 },
    );
  } catch (error) {
    console.warn("Could not queue guidance notice cleanup; removing it now", error);
    try {
      await telegramApi(env, "deleteMessage", {
        chat_id: config.chatId,
        message_id: noticeMessageId,
      });
    } catch (deleteError) {
      console.warn("Could not remove the unscheduled guidance notice", deleteError);
    }
  }
}

async function moderateMessage(env, message) {
  const config = configFrom(env);
  const chat = message.chat ?? {};
  if (chat.id !== config.chatId) return;

  const topicId = message.message_thread_id;
  if (!config.topicIds.has(topicId)) return;

  if (message.from?.is_bot) return;
  if ([...SERVICE_MESSAGE_FIELDS].some((field) => Object.hasOwn(message, field))) return;
  if (hasAllowedContent(message)) return;
  if (!Number.isInteger(message.message_id)) return;

  try {
    await telegramApi(env, "deleteMessage", {
      chat_id: config.chatId,
      message_id: message.message_id,
    });
    console.log(
      `Deleted text-only message chat_id=${config.chatId} topic_id=${topicId} message_id=${message.message_id}`,
    );
  } catch (error) {
    console.warn(
      `Could not delete message chat_id=${config.chatId} topic_id=${topicId} message_id=${message.message_id}`,
      error,
    );
    return;
  }

  await postGuidanceNotice(env, config, topicId);
}

async function handleWebhook(request, env) {
  if (!safeEqual(
    request.headers.get("X-Telegram-Bot-Api-Secret-Token"),
    env.WEBHOOK_SECRET,
  )) {
    return Response.json({ error: "unauthorized" }, { status: 403 });
  }

  let update;
  try {
    update = await request.json();
  } catch {
    return Response.json({ error: "invalid update" }, { status: 400 });
  }
  if (!update || typeof update !== "object" || Array.isArray(update)) {
    return Response.json({ error: "invalid update" }, { status: 400 });
  }

  const message = update.message ?? update.edited_message;
  if (message && typeof message === "object" && !Array.isArray(message)) {
    await moderateMessage(env, message);
  }

  return Response.json({ ok: true });
}

async function deleteQueuedNotice(env, body) {
  const config = configFrom(env);
  if (
    !body
    || body.chatId !== config.chatId
    || !config.topicIds.has(body.topicId)
    || !Number.isInteger(body.messageId)
  ) {
    return;
  }

  try {
    await telegramApi(env, "deleteMessage", {
      chat_id: body.chatId,
      message_id: body.messageId,
    });
    console.log(
      `Deleted temporary guidance notice topic_id=${body.topicId} message_id=${body.messageId}`,
    );
  } catch (error) {
    if (error instanceof TelegramAPIError && error.errorCode === 400) {
      console.log("Guidance notice already removed or no longer deletable");
      return;
    }
    throw error;
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/healthz") {
      return Response.json({ ok: true });
    }
    if (request.method === "POST" && url.pathname === "/telegram/webhook") {
      return handleWebhook(request, env);
    }
    return new Response("Not found", { status: 404 });
  },

  async queue(batch, env) {
    for (const message of batch.messages) {
      try {
        await deleteQueuedNotice(env, message.body);
        message.ack();
      } catch (error) {
        console.warn("Could not delete temporary guidance notice; retrying", error);
        message.retry({ delaySeconds: 10 });
      }
    }
  },
};
