"""
Patch pi-embedded-CbMH3G07.js — ESM-compatible fast callback handler.
Injects BEFORE shouldSkipUpdate (to bypass dedup) using await import() for ESM.
"""
import sys

EMBEDDED = '/app/dist/pi-embedded-CbMH3G07.js'
ENGINE_PATH = '/app/skills/openclaw-mode-skills/engine.py'
MARKER = '_EPAPHRAS_ESM_V3'

with open(EMBEDDED, 'r') as f:
    src = f.read()

if MARKER in src:
    print(f"Already patched ({MARKER}) — skipping")
    sys.exit(0)

# Anchor: right after "if (!callback) return;" and BEFORE "if (shouldSkipUpdate"
# This is inside bot.on("callback_query", async (ctx) => {
ANCHOR = 'if (!callback) return;\n\t\tif (shouldSkipUpdate(ctx)) return;'

if ANCHOR not in src:
    print("ERROR: anchor not found")
    # Debug: show what we have around the area
    cbq = src.index('bot.on("callback_query"')
    print("Context around bot.on:", repr(src[cbq:cbq+500]))
    sys.exit(1)

cbq_pos = src.index('bot.on("callback_query"')
anchor_pos = src.index(ANCHOR, cbq_pos)
print(f"Anchor at {anchor_pos}, bot.on at {cbq_pos}")

# The injection goes after "if (!callback) return;\n\t\t" but BEFORE "if (shouldSkipUpdate"
# Split at that exact point
ANCHOR_BEFORE = 'if (!callback) return;\n\t\t'
ANCHOR_AFTER = 'if (shouldSkipUpdate(ctx)) return;'

INJECT = (
    '// PATCH: ' + MARKER + ' — fast ESM-compatible cb_* intercept (before shouldSkipUpdate)\n'
    '\t\tif (/^cb_(setmode:|toggle:|back$)/.test(callback.data ?? "")) {\n'
    '\t\t\ttry {\n'
    '\t\t\t\tconst { execFileSync: _es } = await import("child_process");\n'
    '\t\t\t\tconst _d = (callback.data ?? "").trim();\n'
    '\t\t\t\tconst _ENGINE = "' + ENGINE_PATH + '";\n'
    '\t\t\t\tlet _ea;\n'
    '\t\t\t\tif (_d.startsWith("cb_setmode:")) _ea = ["setmode", _d.slice(11)];\n'
    '\t\t\t\telse if (_d.startsWith("cb_toggle:")) _ea = ["toggle", _d.slice(10)];\n'
    '\t\t\t\telse _ea = ["render-modes"];\n'
    '\t\t\t\tconst _out = JSON.parse(_es("python3", [_ENGINE].concat(_ea), { timeout: 5000 }).toString().trim());\n'
    '\t\t\t\tif (!_out.error) {\n'
    '\t\t\t\t\ttry {\n'
    '\t\t\t\t\t\tawait bot.api.answerCallbackQuery(callback.id).catch(() => {});\n'
    '\t\t\t\t\t\tconst _msg = callback.message;\n'
    '\t\t\t\t\t\tconst _btns = _out.buttons || _out.inline_keyboard || [];\n'
    '\t\t\t\t\t\tconst _rm = _btns.length ? { reply_markup: { inline_keyboard: _btns } } : {};\n'
    '\t\t\t\t\t\tawait bot.api.editMessageText(_msg.chat.id, _msg.message_id, _out.text || "", _rm);\n'
    '\t\t\t\t\t} catch(_te) { /* swallow Telegram errors (e.g. message not modified) */ }\n'
    '\t\t\t\t\treturn; // always return when engine succeeded\n'
    '\t\t\t\t}\n'
    '\t\t\t} catch(_e) { /* engine error or ESM import fail — fall through */ }\n'
    '\t\t}\n'
    '\t\t// END PATCH: ' + MARKER + '\n'
    '\t\t'
)

# Replace "if (!callback) return;\n\t\tif (shouldSkipUpdate" with our injection in between
OLD = 'if (!callback) return;\n\t\tif (shouldSkipUpdate(ctx)) return;'
NEW = 'if (!callback) return;\n\t\t' + INJECT + 'if (shouldSkipUpdate(ctx)) return;'

# Only replace the FIRST occurrence after bot.on
before_anchor = src[:anchor_pos]
at_anchor = src[anchor_pos:]
at_anchor = at_anchor.replace(OLD, NEW, 1)
src = before_anchor + at_anchor

with open(EMBEDDED, 'w') as f:
    f.write(src)

print(f"Patched {EMBEDDED} with ESM-compatible intercept (v3)")
print(f"Anchor was at position {anchor_pos}")
