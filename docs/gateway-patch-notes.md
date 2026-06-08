# Gateway Patch Notes — Gemma/GreenNode Tool Call Fix

**File patched:** `/app/node_modules/@mariozechner/pi-ai/dist/providers/openai-completions.js`
**Patch script:** `/tmp/full_patch_v2.py` (idempotent, safe to re-run after pod restart)

---

## Context

The Gemma/GreenNode model never emits the OpenAI streaming tool-call format.
Instead it emits a native format as a plain **text** block:

```
<|tool_call>call:message{action:<|"|>send<|"|>,message:<|"|>...<|"|>,target:<|"|>717110884<|"|>}<tool_call|>
```

Because the gateway sees `stopReason: "stop"` on a text block, no tool call is
registered and the raw string is forwarded to Telegram as a chat message.

---

## Issues Fixed

### Issue 1 — Raw native format reaching Telegram (no interception)

**Symptom:** Telegram shows the literal `<|tool_call>...<tool_call|>` string instead of executing the tool.

**Root cause:** `finishCurrentBlock` in `openai-completions.js` had no handling for
the native Gemma format. Text blocks were unconditionally emitted as `text_end`.

**Fix:** Added `_parseGemmaNativeToolCall` function and wired it into `finishCurrentBlock`:

```js
if (block.type === "text") {
    const _nc = _parseGemmaNativeToolCall(block.text);
    if (_nc) {
        blocks[blockIndex()] = _nc.tcBlock;
        stream.push({ type: "toolcall_end", contentIndex: blockIndex(), toolCall: _nc.tcBlock, partial: output });
    } else {
        stream.push({ type: "text_end", contentIndex: blockIndex(), content: block.text, partial: output });
    }
}
```

---

### Issue 2 — `<|"|>` used as string delimiters (not `"`)

**Symptom:** All string values unparseable — `action:send` instead of `action:"send"`.

**Root cause:** Gemma uses `<|"|>` as its string delimiter token instead of a real
double-quote character.

**Fix:**
```js
m[2].replace(/<\|"\|>/g, '"')
```

---

### Issue 3 — First key never gets quoted → `JSON.parse` fails at position 1

**Symptom:** `JSON.parse` throws `"Expected property name or '}' at position 1"`.

**Root cause:** The key-quoting regex `([{,]\s*)([a-zA-Z_]+)(\s*:)` requires `{` or
`,` immediately before a key. The extracted inner content starts with `action:...`
(no leading `{`), so the first key is never quoted. Then `"{" + withKeys + "}"` is
called after the regex, which is too late — `action` is already unquoted inside.

**Fix:** Wrap in `{}` *before* applying the regex, then parse the result directly
without adding braces again:

```js
// Before (broken)
const withKeys = withStr.replace(/([{,]\s*)([a-zA-Z_]+)(\s*:)/g, '$1"$2"$3');
JSON.parse("{" + withKeys + "}");  // action still unquoted

// After (correct)
const withBraces = "{" + withStr + "}";
const withKeys = withBraces.replace(/([{,]\s*)([a-zA-Z_]+)(\s*:)/g, '$1"$2"$3');
JSON.parse(withKeys);
```

---

### Issue 4 — Literal control characters inside string values

**Symptom:** `JSON.parse` throws `"Bad control character in string literal at position N"`.

**Root cause:** The model emits real `\n` / `\r` / `\t` bytes inside string values
(e.g. in the `message` field). JSON does not allow bare control characters inside
quoted strings.

**Fix:**
```js
withStr
  .replace(/\n/g, '\\n')
  .replace(/\r/g, '\\r')
  .replace(/\t/g, '\\t')
```

---

### Issue 5 — Button objects missing `text` field

**Symptom:** Telegram rejects the message with `buttons/0/0: must have required property 'text'`.

**Root cause:** Gemma sometimes emits button objects with only `callback_data`,
omitting `text` entirely.

**Fix:** `sanitizeMessageButtons` fills in a fallback `text` derived from `callback_data`:

```js
if (!("text" in o)) {
    const cd = typeof o.callback_data === "string" ? o.callback_data : "";
    o.text = cd.replace(/^cb_[a-z_]+:/, "") || "(button)";
}
```

---

### Issue 6 — Double-quoted key names from OpenAI streaming parse

**Symptom:** Parsed args object has keys like `"action"` (with literal quote chars)
instead of `action`.

**Root cause:** `parseStreamingJson` sometimes returns objects with keys wrapped in
extra double-quote characters when the model outputs `"action":"send"` in streaming
chunks.

**Fix:** `normalizeDoubleQuotedArgs` recursively strips wrapping quotes from keys
and string values:

```js
const key = k.startsWith('"') && k.endsWith('"') && k.length > 2
    ? k.slice(1, -1) : k;
```

---

## Complete Fixed Function

```js
function _parseGemmaNativeToolCall(text) {
    const t = (text || "").trim();
    const m = t.match(/^<\|tool_call\>call:(\w+)\{([\s\S]*)\}<tool_call\|>$/);
    if (!m) return null;
    const toolName = m[1];
    const withStr = m[2]
        .replace(/<\|"\|>/g, '"')
        .replace(/\n/g, '\\n')
        .replace(/\r/g, '\\r')
        .replace(/\t/g, '\\t');
    const withBraces = "{" + withStr + "}";
    const withKeys = withBraces.replace(/([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)/g, '$1"$2"$3');
    let args;
    try { args = JSON.parse(withKeys); } catch(e) { return null; }
    if (args.target !== undefined && args.to === undefined) {
        args.to = args.target; delete args.target;
    }
    const cleanArgs = toolName === "message"
        ? sanitizeMessageButtons(normalizeDoubleQuotedArgs(args))
        : normalizeDoubleQuotedArgs(args);
    const tcBlock = {
        type: "toolCall",
        id: "gemma-nc-" + Math.random().toString(36).slice(2, 10),
        name: toolName,
        arguments: cleanArgs
    };
    return { tcBlock };
}
```

---

---

## Fast Callback Handler (patch to `pi-embedded-CbMH3G07.js`)

### Problem

Button interactions were slow (3–5 s) because every `cb_*` callback went through the full LLM round-trip:

```
Telegram → callback_query → LLM agent → store-msgid → get-msgid → editMessageText
```

### Solution

Register `cb_setmode`, `cb_toggle`, and `cb_back` as **plugin interactive handler** namespaces inside the gateway process. OpenClaw checks `dispatchPluginInteractiveHandler` **before** routing to the LLM agent. If the handler returns `{ handled: true }`, the LLM path is skipped entirely.

The patched IIFE in `pi-embedded-CbMH3G07.js` is injected right before `bot.on("callback_query"`:

```js
(function _registerEpaphrasModesCallbacks() {
    // calls registerPluginInteractiveHandler for cb_setmode / cb_toggle / cb_back
    // handler: runs engine.py → calls respond.editMessage({ text, buttons })
    // returns { handled: true } → LLM path bypassed
})();
```

New flow (< 200 ms):

```
Telegram → callback_query → answerCallbackQuery → dispatchPluginInteractiveHandler
    → _modesHandler → python3 engine.py → editMessageText (via respond.editMessage)
```

The sidecar (`callback_sidecar.py`) is **no longer needed** — it was made obsolete by this in-process approach.

---

## Re-applying After Pod Restart

Patches are applied to the container writable layer and are lost on pod restart.

`full_patch_v2.py` now patches **both** files in one run.

```bash
POD=<current-pod-name>
KC="kubectl --kubeconfig ~/Documents/kubeconfig_prod.yaml -n agent-core-53461"

# 1. Copy master patch script
$KC cp /tmp/full_patch_v2.py $POD:/tmp/full_patch_v2.py -c gateway

# 2. Apply (idempotent — safe to re-run)
$KC exec $POD -c gateway -- python3 /tmp/full_patch_v2.py

# 3. Restart gateway subprocess
$KC exec $POD -c gateway -- \
  sh -c 'kill $(pgrep -f "openclaw gateway") 2>/dev/null; sleep 1; \
         nohup openclaw gateway --allow-unconfigured > /tmp/gw-restart.log 2>&1 < /dev/null &'
```

`full_patch_v2.py` applies:
1. `openai-completions.js` — Gemma native format interception (v3)
2. `pi-embedded-CbMH3G07.js` — Epaphras Modes fast callback handler

Replace `<current-pod-name>` with the live pod name from `kubectl get pods`.
