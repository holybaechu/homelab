"use strict";

/*
 * This process intentionally owns the Discord Gateway connection and Discord
 * REST token. It has no OpenClaw Gateway token, Docker credential, or CTF
 * workspace access. Route configuration is data-only: a numeric Discord
 * channel selects either the fixed core or fixed CTF loopback endpoint.
 *
 * Plugin response contract:
 * {
 *   "text": "optional Discord reply text",
 *   "artifact": {
 *     "filename": "optional-result.zip",
 *     "base64": "base64-encoded ZIP bytes"
 *   }
 * }
 * Text is sent with allowed_mentions disabled. The optional ZIP is size- and
 * filename-bounded before it is sent to the same inbound channel.
 */

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const WebSocket = require("ws");

const DISCORD_GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json";
const DISCORD_API_BASE_URL = "https://discord.com/api/v10";
const DISCORD_MESSAGE_LIMIT = 2000;
const MAX_GATEWAY_EVENT_BYTES = 512 * 1024;
const MAX_PLUGIN_REQUEST_BYTES = 768 * 1024;
// A CTF package is represented as base64 inside the plugin JSON response.
// Keep the encoded envelope bounded as well as the decoded ZIP: 35 MiB is
// enough for a 25 MiB ZIP plus JSON/encoding overhead, but not enough for an
// arbitrarily large plugin response to occupy relay memory.
const MAX_PLUGIN_RESPONSE_BYTES = 35 * 1024 * 1024;
const MAX_ARTIFACT_BYTES = 25 * 1024 * 1024;
const MAX_REPLY_CHARS = 16 * 1024;
const MAX_ATTACHMENT_COUNT = 10;
const MAX_SEEN_MESSAGES = 4096;
const SEEN_MESSAGE_TTL_MS = 10 * 60 * 1000;
// CTF analysis may legitimately take several minutes. This is still a hard
// per-message bound; channel queues prevent an unbounded fan-out of turns.
const REQUEST_TIMEOUT_MS = 10 * 60 * 1000;
const REST_TIMEOUT_MS = 30 * 1000;
const ROUTE_VERSION = 1;
const SNOWFLAKE_RE = /^\d{17,20}$/;
const HMAC_RE = /^[0-9a-f]{64}$/i;
const ZIP_FILENAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.zip$/i;
const BASE64_RE = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;
const DISCORD_ASSET_HOSTS = new Set([
  "cdn.discordapp.com",
  "media.discordapp.net",
  "images-ext-1.discordapp.net",
  "images-ext-2.discordapp.net",
]);

const seenMessageIds = new Map();
const channelQueues = new Map();

function safeLog(event, fields = {}) {
  const payload = { service: "openclaw-discord-relay", event, ...fields };
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function fail(message) {
  const error = new Error(message);
  error.name = "RelayConfigurationError";
  return error;
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isSnowflake(value) {
  return typeof value === "string" && SNOWFLAKE_RE.test(value);
}

function readRequiredSecret(filePath, label, validator = () => true) {
  if (typeof filePath !== "string" || filePath.length === 0) {
    throw fail(`missing ${label} credential path`);
  }
  const value = fs.readFileSync(filePath, "utf8").trim();
  if (value.length === 0 || value.length > 4096 || !validator(value)) {
    throw fail(`invalid ${label} credential`);
  }
  return value;
}

function requireLoopbackEndpoint(value, label) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw fail(`invalid ${label} endpoint`);
  }
  if (
    url.protocol !== "http:" ||
    url.hostname !== "127.0.0.1" ||
    !url.port ||
    url.pathname !== "/internal/discord-relay" ||
    url.search ||
    url.hash ||
    url.username ||
    url.password
  ) {
    throw fail(`unsafe ${label} endpoint`);
  }
  return url.toString();
}

function parseRoutes(config) {
  if (!isPlainObject(config) || config.version !== ROUTE_VERSION || !isPlainObject(config.routes)) {
    throw fail("route configuration must contain version 1 and a routes object");
  }

  const entries = Object.entries(config.routes);
  if (entries.length === 0 || entries.length > 512) {
    throw fail("route configuration must contain between 1 and 512 routes");
  }

  const routes = new Map();
  for (const [channelId, route] of entries) {
    if (!isSnowflake(channelId) || !isPlainObject(route)) {
      throw fail("route keys must be numeric Discord channel IDs");
    }
    const keys = Object.keys(route).sort();
    if (keys.length !== 1 || keys[0] !== "target" || (route.target !== "core" && route.target !== "ctf")) {
      throw fail("each route must select only the core or ctf target");
    }
    routes.set(channelId, route.target);
  }
  return routes;
}

function loadConfiguration() {
  const routesPath = process.env.OPENCLAW_DISCORD_RELAY_ROUTES_PATH;
  if (typeof routesPath !== "string" || !path.isAbsolute(routesPath)) {
    throw fail("missing absolute route configuration path");
  }
  const routes = parseRoutes(JSON.parse(fs.readFileSync(routesPath, "utf8")));
  const token = readRequiredSecret(
    process.env.OPENCLAW_DISCORD_BOT_TOKEN_FILE,
    "Discord bot token",
  );
  const coreHmac = readRequiredSecret(
    process.env.OPENCLAW_RELAY_CORE_HMAC_FILE,
    "core relay HMAC",
    (value) => HMAC_RE.test(value),
  );
  const ctfHmac = readRequiredSecret(
    process.env.OPENCLAW_RELAY_CTF_HMAC_FILE,
    "CTF relay HMAC",
    (value) => HMAC_RE.test(value),
  );
  if (crypto.timingSafeEqual(Buffer.from(coreHmac), Buffer.from(ctfHmac))) {
    throw fail("core and CTF relay HMAC credentials must differ");
  }

  return {
    routes,
    token,
    endpoints: {
      core: {
        url: requireLoopbackEndpoint(process.env.OPENCLAW_RELAY_CORE_ENDPOINT, "core"),
        hmac: coreHmac,
      },
      ctf: {
        url: requireLoopbackEndpoint(process.env.OPENCLAW_RELAY_CTF_ENDPOINT, "CTF"),
        hmac: ctfHmac,
      },
    },
  };
}

function truncateText(value, maximum) {
  const codepoints = Array.from(value);
  return codepoints.length > maximum ? codepoints.slice(0, maximum).join("") : value;
}

function splitDiscordText(value) {
  const codepoints = Array.from(value);
  if (codepoints.length === 0) {
    return [];
  }
  const chunks = [];
  for (let index = 0; index < codepoints.length; index += DISCORD_MESSAGE_LIMIT) {
    chunks.push(codepoints.slice(index, index + DISCORD_MESSAGE_LIMIT).join(""));
  }
  return chunks;
}

function sanitizeDiscordAssetUrl(value) {
  if (typeof value !== "string" || value.length > 4096) {
    return undefined;
  }
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:" ||
      !DISCORD_ASSET_HOSTS.has(url.hostname.toLowerCase()) ||
      url.port ||
      url.username ||
      url.password
    ) {
      return undefined;
    }
    return url.toString();
  } catch {
    return undefined;
  }
}

function normalizeAttachments(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.slice(0, MAX_ATTACHMENT_COUNT).flatMap((attachment) => {
    if (!isPlainObject(attachment) || !isSnowflake(attachment.id) || typeof attachment.filename !== "string") {
      return [];
    }
    const normalized = {
      id: attachment.id,
      filename: truncateText(attachment.filename, 255),
      size: Number.isSafeInteger(attachment.size) && attachment.size >= 0 ? attachment.size : 0,
    };
    if (typeof attachment.content_type === "string" && attachment.content_type.length <= 255) {
      normalized.contentType = attachment.content_type;
    }
    const url = sanitizeDiscordAssetUrl(attachment.url);
    if (url) {
      normalized.url = url;
    }
    const proxyUrl = sanitizeDiscordAssetUrl(attachment.proxy_url);
    if (proxyUrl) {
      normalized.proxyUrl = proxyUrl;
    }
    if (Number.isInteger(attachment.width) && attachment.width >= 0) {
      normalized.width = attachment.width;
    }
    if (Number.isInteger(attachment.height) && attachment.height >= 0) {
      normalized.height = attachment.height;
    }
    return [normalized];
  });
}

function selectInboundMessage(payload, routes, botUserId) {
  if (!isPlainObject(payload) || payload.t !== "MESSAGE_CREATE" || !isPlainObject(payload.d)) {
    return undefined;
  }
  const message = payload.d;
  if (!isSnowflake(message.guild_id) || !isSnowflake(message.channel_id)) {
    return undefined; // DMs and malformed events are never routed.
  }
  if (!isPlainObject(message.author) || message.author.bot || message.webhook_id) {
    return undefined;
  }
  if (!isSnowflake(message.id) || !isSnowflake(message.author.id) || message.author.id === botUserId) {
    return undefined;
  }
  const target = routes.get(message.channel_id);
  if (!target) {
    return undefined;
  }
  const attachments = normalizeAttachments(message.attachments);
  const content = typeof message.content === "string" ? truncateText(message.content, 8000) : "";
  if (content.length === 0 && attachments.length === 0) {
    return undefined;
  }
  const inbound = {
    version: ROUTE_VERSION,
    source: "discord",
    target,
    message: {
      id: message.id,
      channelId: message.channel_id,
      guildId: message.guild_id,
      authorId: message.author.id,
      content,
      attachments,
    },
  };
  if (typeof message.timestamp === "string" && message.timestamp.length <= 64) {
    inbound.message.timestamp = message.timestamp;
  }
  if (isPlainObject(message.message_reference) && isSnowflake(message.message_reference.message_id)) {
    inbound.message.referencedMessageId = message.message_reference.message_id;
  }
  return inbound;
}

function claimMessage(messageId) {
  const now = Date.now();
  for (const [knownId, seenAt] of seenMessageIds) {
    if (now - seenAt > SEEN_MESSAGE_TTL_MS) {
      seenMessageIds.delete(knownId);
    }
  }
  if (seenMessageIds.has(messageId)) {
    return false;
  }
  seenMessageIds.set(messageId, now);
  if (seenMessageIds.size > MAX_SEEN_MESSAGES) {
    seenMessageIds.delete(seenMessageIds.keys().next().value);
  }
  return true;
}

function enqueueChannel(channelId, job) {
  const previous = channelQueues.get(channelId) || Promise.resolve();
  const queued = previous.catch(() => undefined).then(job);
  channelQueues.set(channelId, queued);
  queued
    .finally(() => {
      if (channelQueues.get(channelId) === queued) {
        channelQueues.delete(channelId);
      }
    })
    .catch(() => undefined);
  return queued;
}

async function startTimedFetch(url, options, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal, redirect: "error" });
    return {
      response,
      dispose() {
        clearTimeout(timer);
      },
    };
  } catch (error) {
    clearTimeout(timer);
    throw error;
  }
}

async function readLimitedBody(response, maximumBytes) {
  const contentLength = Number(response.headers.get("content-length"));
  if (Number.isFinite(contentLength) && contentLength > maximumBytes) {
    throw new Error("response exceeds configured size limit");
  }
  if (!response.body) {
    return Buffer.alloc(0);
  }
  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  let completed = false;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        completed = true;
        break;
      }
      total += value.byteLength;
      if (total > maximumBytes) {
        throw new Error("response exceeds configured size limit");
      }
      chunks.push(Buffer.from(value));
    }
  } finally {
    if (!completed) {
      await reader.cancel().catch(() => undefined);
    }
    reader.releaseLock();
  }
  return Buffer.concat(chunks, total);
}

function hmacHeaders(body, token) {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const nonce = crypto.randomBytes(16).toString("hex");
  const canonical = `${ROUTE_VERSION}.${timestamp}.${nonce}.${body}`;
  const signature = crypto.createHmac("sha256", token).update(canonical).digest("hex");
  return {
    "content-type": "application/json",
    "content-length": String(Buffer.byteLength(body)),
    "x-openclaw-relay-version": String(ROUTE_VERSION),
    "x-openclaw-relay-timestamp": timestamp,
    "x-openclaw-relay-nonce": nonce,
    "x-openclaw-relay-signature": signature,
  };
}

function decodeZipArtifact(value) {
  if (!isPlainObject(value) || typeof value.filename !== "string" || typeof value.base64 !== "string") {
    throw new Error("invalid artifact response");
  }
  if (!ZIP_FILENAME_RE.test(value.filename) || value.base64.length === 0 || !BASE64_RE.test(value.base64)) {
    throw new Error("artifact response does not contain a safe ZIP");
  }
  if (value.base64.length > Math.ceil((MAX_ARTIFACT_BYTES * 4) / 3) + 4) {
    throw new Error("artifact response exceeds configured size limit");
  }
  const bytes = Buffer.from(value.base64, "base64");
  if (bytes.length === 0 || bytes.length > MAX_ARTIFACT_BYTES || bytes[0] !== 0x50 || bytes[1] !== 0x4b) {
    throw new Error("artifact response is not a bounded ZIP");
  }
  return { filename: value.filename, bytes };
}

function parsePluginResponse(value) {
  if (!isPlainObject(value)) {
    throw new Error("plugin response must be a JSON object");
  }
  if (value.text !== undefined && typeof value.text !== "string") {
    throw new Error("plugin response text must be a string");
  }
  const text = typeof value.text === "string" ? truncateText(value.text, MAX_REPLY_CHARS) : "";
  const artifact = value.artifact === undefined ? undefined : decodeZipArtifact(value.artifact);
  if (text.length === 0 && !artifact) {
    throw new Error("plugin response contains neither text nor artifact");
  }
  return { text, artifact };
}

async function forwardToPlugin(endpoint, inbound) {
  const body = JSON.stringify(inbound);
  if (Buffer.byteLength(body) > MAX_PLUGIN_REQUEST_BYTES) {
    throw new Error("inbound Discord event exceeds configured size limit");
  }
  const request = await startTimedFetch(
    endpoint.url,
    {
      method: "POST",
      headers: hmacHeaders(body, endpoint.hmac),
      body,
    },
    REQUEST_TIMEOUT_MS,
  );
  try {
    const responseBody = await readLimitedBody(request.response, MAX_PLUGIN_RESPONSE_BYTES);
    if (!request.response.ok) {
      throw new Error(`plugin returned HTTP ${request.response.status}`);
    }
    try {
      return parsePluginResponse(JSON.parse(responseBody.toString("utf8")));
    } catch (error) {
      if (error instanceof SyntaxError) {
        throw new Error("plugin returned invalid JSON");
      }
      throw error;
    }
  } finally {
    if (request.response.body) {
      await request.response.body.cancel().catch(() => undefined);
    }
    request.dispose();
  }
}

async function discordApiRequest(token, channelId, text, artifact, retryCount = 0) {
  const url = `${DISCORD_API_BASE_URL}/channels/${channelId}/messages`;
  const safeText = text.length > 0 ? text : undefined;
  let body;
  let headers = { authorization: `Bot ${token}` };
  if (artifact) {
    body = new FormData();
    body.set(
      "payload_json",
      JSON.stringify({ content: safeText, allowed_mentions: { parse: [] } }),
    );
    body.set(
      "files[0]",
      new Blob([artifact.bytes], { type: "application/zip" }),
      artifact.filename,
    );
  } else {
    headers = { ...headers, "content-type": "application/json" };
    body = JSON.stringify({ content: safeText, allowed_mentions: { parse: [] } });
  }
  const request = await startTimedFetch(url, { method: "POST", headers, body }, REST_TIMEOUT_MS);
  let retryAfterMs;
  try {
    if (request.response.status === 429) {
      if (retryCount >= 1) {
        throw new Error("Discord REST rate limit retry exhausted");
      }
      const retryBody = await readLimitedBody(request.response, 4096);
      retryAfterMs = 1000;
      try {
        const retryAfter = JSON.parse(retryBody.toString("utf8")).retry_after;
        if (typeof retryAfter === "number" && retryAfter >= 0 && retryAfter <= 10) {
          retryAfterMs = Math.ceil(retryAfter * 1000);
        }
      } catch {
        // A short bounded retry is safer than trusting an unparseable response.
      }
    } else if (!request.response.ok) {
      throw new Error(`Discord REST returned HTTP ${request.response.status}`);
    }
  } finally {
    if (request.response.body) {
      await request.response.body.cancel().catch(() => undefined);
    }
    request.dispose();
  }
  if (retryAfterMs !== undefined) {
    await new Promise((resolve) => setTimeout(resolve, retryAfterMs));
    return discordApiRequest(token, channelId, text, artifact, retryCount + 1);
  }
}

async function sendPluginResponse(token, channelId, response) {
  const chunks = splitDiscordText(response.text);
  if (!response.artifact) {
    for (const chunk of chunks) {
      await discordApiRequest(token, channelId, chunk);
    }
    return;
  }

  const finalChunk = chunks.pop() || "";
  for (const chunk of chunks) {
    await discordApiRequest(token, channelId, chunk);
  }
  await discordApiRequest(token, channelId, finalChunk, response.artifact);
}

async function handleInbound(configuration, inbound) {
  const channelId = inbound.message.channelId;
  const endpoint = configuration.endpoints[inbound.target];
  try {
    const response = await forwardToPlugin(endpoint, inbound);
    await sendPluginResponse(configuration.token, channelId, response);
    safeLog("message_delivered", { channelId, target: inbound.target });
  } catch (error) {
    safeLog("message_delivery_failed", { channelId, target: inbound.target, reason: error.name || "Error" });
    try {
      await discordApiRequest(
        configuration.token,
        channelId,
        "The requested agent is temporarily unavailable. Please try again shortly.",
      );
    } catch (replyError) {
      safeLog("failure_reply_failed", { channelId, reason: replyError.name || "Error" });
    }
  }
}

function startGateway(configuration) {
  let socket;
  let heartbeatTimer;
  let reconnectTimer;
  let lastSequence = null;
  let botUserId = "";
  let reconnectAttempt = 0;
  let stopping = false;

  function clearTimers() {
    clearInterval(heartbeatTimer);
    clearTimeout(reconnectTimer);
    heartbeatTimer = undefined;
    reconnectTimer = undefined;
  }

  function send(payload) {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(payload));
    }
  }

  function heartbeat() {
    send({ op: 1, d: lastSequence });
  }

  function scheduleReconnect() {
    if (stopping || reconnectTimer) {
      return;
    }
    const delay = Math.min(30_000, 1_000 * 2 ** Math.min(reconnectAttempt, 5));
    reconnectAttempt += 1;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = undefined;
      connect();
    }, delay + Math.floor(Math.random() * 250));
  }

  function connect() {
    if (stopping) {
      return;
    }
    clearInterval(heartbeatTimer);
    socket = new WebSocket(DISCORD_GATEWAY_URL, {
      handshakeTimeout: 15_000,
      perMessageDeflate: false,
    });
    socket.on("message", (raw) => {
      void handleGatewayPayload(raw);
    });
    socket.on("error", () => undefined);
    socket.on("close", () => {
      clearInterval(heartbeatTimer);
      heartbeatTimer = undefined;
      scheduleReconnect();
    });
  }

  async function handleGatewayPayload(raw) {
    try {
      const rawBuffer = Buffer.isBuffer(raw) ? raw : Buffer.from(raw);
      if (rawBuffer.length > MAX_GATEWAY_EVENT_BYTES) {
        throw new Error("Gateway event exceeds configured size limit");
      }
      const payload = JSON.parse(rawBuffer.toString("utf8"));
      if (!isPlainObject(payload)) {
        return;
      }
      if (typeof payload.s === "number") {
        lastSequence = payload.s;
      }
      if (payload.op === 10 && isPlainObject(payload.d) && Number.isFinite(payload.d.heartbeat_interval)) {
        const interval = Math.max(1_000, Math.floor(payload.d.heartbeat_interval));
        clearInterval(heartbeatTimer);
        heartbeatTimer = setInterval(heartbeat, interval);
        heartbeat();
        send({
          op: 2,
          d: {
            token: configuration.token,
            intents: (1 << 9) | (1 << 15),
            properties: {
              $os: process.platform,
              $browser: "openclaw-discord-relay",
              $device: "openclaw-discord-relay",
            },
          },
        });
        return;
      }
      if (payload.op === 1) {
        heartbeat();
        return;
      }
      if (payload.op === 7 || payload.op === 9) {
        socket?.close();
        return;
      }
      if (payload.op !== 0) {
        return;
      }
      if (payload.t === "READY" && isPlainObject(payload.d) && isPlainObject(payload.d.user)) {
        botUserId = isSnowflake(payload.d.user.id) ? payload.d.user.id : "";
        reconnectAttempt = 0;
        safeLog("gateway_ready");
        return;
      }
      const inbound = selectInboundMessage(payload, configuration.routes, botUserId);
      if (!inbound || !claimMessage(inbound.message.id)) {
        return;
      }
      void enqueueChannel(inbound.message.channelId, () => handleInbound(configuration, inbound));
    } catch (error) {
      safeLog("gateway_event_rejected", { reason: error.name || "Error" });
    }
  }

  process.once("SIGTERM", () => {
    stopping = true;
    clearTimers();
    socket?.close(1000, "service stop");
  });
  process.once("SIGINT", () => {
    stopping = true;
    clearTimers();
    socket?.close(1000, "service stop");
  });

  connect();
}

function main() {
  process.umask(0o077);
  const configuration = loadConfiguration();
  safeLog("starting", { routeCount: configuration.routes.size });
  startGateway(configuration);
}

if (require.main === module) {
  try {
    main();
  } catch (error) {
    safeLog("configuration_failed", { reason: error.name || "Error" });
    process.exitCode = 78;
  }
}

module.exports = {
  decodeZipArtifact,
  hmacHeaders,
  parsePluginResponse,
  parseRoutes,
  requireLoopbackEndpoint,
  selectInboundMessage,
};
