import sys

with open('/app/node_modules/@mariozechner/pi-ai/dist/providers/openai-completions.js', 'r') as f:
    src = f.read()

_patch1_done = '_parseGemmaNativeToolCall' in src and 'withBraces' in src
if _patch1_done:
    print("openai-completions.js already patched (v3) — skipping")

# Strip any older partial patches first
if 'sanitizeMessageButtons' in src and '_parseGemmaNativeToolCall' not in src:
    print("Has v2 patch but missing native parser — will add incrementally")
    # fall through to add native parser only
    NEEDS_HELPERS = False
elif 'sanitizeMessageButtons' not in src:
    NEEDS_HELPERS = True
else:
    NEEDS_HELPERS = False

if NEEDS_HELPERS:
    ANCHOR = 'export const streamOpenAICompletions'
    if ANCHOR not in src:
        print("ERROR: anchor not found"); sys.exit(1)

    HELPERS = r"""// PATCH: normalize Gemma/GreenNode double-quoted keys + strip control tokens from button values
function normalizeDoubleQuotedArgs(value) {
    if (Array.isArray(value)) return value.map(normalizeDoubleQuotedArgs);
    if (value !== null && typeof value === "object") {
        const out = {};
        for (const [k, v] of Object.entries(value)) {
            const key = k.length > 2 && k.startsWith('"') && k.endsWith('"') ? k.slice(1,-1) : k;
            out[key] = normalizeDoubleQuotedArgs(v);
        }
        return out;
    }
    if (typeof value === "string" && value.length > 2 && value.startsWith('"') && value.endsWith('"'))
        return value.slice(1,-1);
    return value;
}
const _GEMMA_TOK_RE1 = /<\|[^|>]*\|>/g;
const _GEMMA_TOK_RE2 = /<\|[^"|{}\[\]]*\|?/g;
function _stripGemmaStr(s) {
    if (typeof s !== "string") return s;
    return s.replace(_GEMMA_TOK_RE1, "").replace(_GEMMA_TOK_RE2, "").trim();
}
function sanitizeMessageButtons(args) {
    if (!args || typeof args !== "object" || Array.isArray(args)) return args;
    const hb = Array.isArray(args.buttons), hi = Array.isArray(args.inline_keyboard);
    if (!hb && !hi) return args;
    const sanitizeRow = (row) => Array.isArray(row) ? row.map((btn) => {
        if (!btn || typeof btn !== "object" || Array.isArray(btn)) return btn;
        const o = {};
        for (const [k, v] of Object.entries(btn)) o[k] = typeof v === "string" ? _stripGemmaStr(v) : v;
        if (!("text" in o)) { const cd = typeof o.callback_data === "string" ? o.callback_data : ""; o.text = cd.replace(/^cb_[a-z_]+:/, "") || "(button)"; }
        if (!("callback_data" in o)) o.callback_data = "";
        return o;
    }) : row;
    const r = Object.assign({}, args);
    if (hb) r.buttons = args.buttons.map(sanitizeRow);
    if (hi) r.inline_keyboard = args.inline_keyboard.map(sanitizeRow);
    return r;
}
// END PATCH
"""

    src = src.replace(ANCHOR, HELPERS + ANCHOR, 1)

    OLD1 = 'block.arguments = parseStreamingJson(block.partialArgs);'
    NEW1 = ('const _p1 = normalizeDoubleQuotedArgs(parseStreamingJson(block.partialArgs));\n'
            '                        block.arguments = block.name === "message" ? sanitizeMessageButtons(_p1) : _p1;')
    if OLD1 not in src:
        print("ERROR: call site 1 not found"); sys.exit(1)
    src = src.replace(OLD1, NEW1, 1)

    OLD2 = 'currentBlock.arguments = parseStreamingJson(currentBlock.partialArgs);'
    NEW2 = ('const _p2 = normalizeDoubleQuotedArgs(parseStreamingJson(currentBlock.partialArgs));\n'
            '                                    currentBlock.arguments = currentBlock.name === "message" ? sanitizeMessageButtons(_p2) : _p2;')
    if OLD2 not in src:
        print("ERROR: call site 2 not found"); sys.exit(1)
    src = src.replace(OLD2, NEW2, 1)
    print("Applied v2 helper patches")

# Now add native Gemma format parser (v3)
if '_parseGemmaNativeToolCall' not in src:
    NATIVE_PARSER = r"""
// PATCH v3: intercept native Gemma <|tool_call>...<tool_call|> format
function _parseGemmaNativeToolCall(text) {
    const t = (text || "").trim();
    const m = t.match(/^<\|tool_call\>call:(\w+)\{([\s\S]*)\}<tool_call\|>$/);
    if (!m) return null;
    const toolName = m[1];
    const withStr = m[2].replace(/<\|"\|>/g, '"').replace(/\n/g, '\\n').replace(/\r/g, '\\r').replace(/\t/g, '\\t');
    const withBraces = "{" + withStr + "}";
    const withKeys = withBraces.replace(/([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*:)/g, '$1"$2"$3');
    let args;
    try {
        args = JSON.parse(withKeys);
    } catch(e) {
        return null;
    }
    if (args.target !== undefined && args.to === undefined) { args.to = args.target; delete args.target; }
    const cleanArgs = toolName === "message" ? sanitizeMessageButtons(normalizeDoubleQuotedArgs(args)) : normalizeDoubleQuotedArgs(args);
    const tcBlock = { type: "toolCall", id: "gemma-nc-" + Math.random().toString(36).slice(2, 10), name: toolName, arguments: cleanArgs };
    return { tcBlock };
}
// END PATCH v3
"""
    # Insert before export const streamOpenAICompletions (or before the sanitizeMessageButtons block)
    ANCHOR = 'export const streamOpenAICompletions'
    if ANCHOR not in src:
        print("ERROR: anchor not found for native parser"); sys.exit(1)
    src = src.replace(ANCHOR, NATIVE_PARSER + ANCHOR, 1)
    print("Inserted _parseGemmaNativeToolCall")
else:
    # Fix withBraces bug if present
    OLD_WS = "    const withStr = m[2].replace(/<\\|\"\\|>/g, '\"');"
    NEW_WS = "    const withStr = m[2].replace(/<\\|\"\\|>/g, '\"').replace(/\\n/g, '\\\\n').replace(/\\r/g, '\\\\r').replace(/\\t/g, '\\\\t');"
    if OLD_WS in src and 'withBraces' not in src:
        src = src.replace(OLD_WS, NEW_WS, 1)
        print("Fixed: escaped control chars in withStr")
    if 'withBraces' not in src:
        OLD_WK = "    const withKeys = withStr.replace(/([{,]\\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\\s*:)/g, '$1\"$2\"$3');\n    let args;\n    try {\n        args = JSON.parse(\"{\" + withKeys + \"}\");"
        NEW_WK = "    const withBraces = \"{\" + withStr + \"}\";\n    const withKeys = withBraces.replace(/([{,]\\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\\s*:)/g, '$1\"$2\"$3');\n    let args;\n    try {\n        args = JSON.parse(withKeys);"
        if OLD_WK in src:
            src = src.replace(OLD_WK, NEW_WK, 1)
            print("Fixed: withBraces wrapping for first-key quoting")
        else:
            print("WARNING: could not find withKeys pattern to fix")
    print("_parseGemmaNativeToolCall already present")

# Add finishCurrentBlock interception for text blocks
if '_parseGemmaNativeToolCall' in src and 'const _nc = _parseGemmaNativeToolCall' not in src:
    OLD_FIN = ('if (block.type === "text") {\n'
               '                    stream.push({ type: "text_end", contentIndex: blockIndex(), content: block.text, partial: output });\n'
               '                }')
    NEW_FIN = ('if (block.type === "text") {\n'
               '                    const _nc = _parseGemmaNativeToolCall(block.text);\n'
               '                    if (_nc) {\n'
               '                        blocks[blockIndex()] = _nc.tcBlock;\n'
               '                        stream.push({ type: "toolcall_end", contentIndex: blockIndex(), toolCall: _nc.tcBlock, partial: output });\n'
               '                    } else {\n'
               '                        stream.push({ type: "text_end", contentIndex: blockIndex(), content: block.text, partial: output });\n'
               '                    }\n'
               '                }')
    if OLD_FIN not in src:
        print("WARNING: text_end interception anchor not found — may already be patched or pattern changed")
    else:
        src = src.replace(OLD_FIN, NEW_FIN, 1)
        print("Added native format interception in finishCurrentBlock")
else:
    if 'const _nc = _parseGemmaNativeToolCall' in src:
        print("finishCurrentBlock interception already present")

with open('/app/node_modules/@mariozechner/pi-ai/dist/providers/openai-completions.js', 'w') as f:
    f.write(src)
print("All patches applied (v3)")

# ─── Patch 2: pi-embedded-CbMH3G07.js — ESM_V3 fast callback intercept ─────────
# Injected BEFORE shouldSkipUpdate inside bot.on("callback_query").
# Uses await import("child_process") — required because pi-embedded is an ESM module.
# require() is NOT available in ESM and would silently throw → fall through to LLM.

import glob as _glob
_matches = _glob.glob('/app/dist/pi-embedded-*.js')
if not _matches:
    print("ERROR: no pi-embedded-*.js found in /app/dist/"); sys.exit(1)
if len(_matches) > 1:
    print(f"WARNING: multiple matches {_matches}, using first")
EMBEDDED = _matches[0]
print(f"pi-embedded bundle: {EMBEDDED}")
ENGINE_PATH = '/root/.openclaw/workspace/skills/OpenClawModeSkills/engine.py'
EMBED_MARKER = '_EPAPHRAS_ESM_V4'

with open(EMBEDDED, 'r') as f:
    esrc = f.read()

# Remove old broken patches if present
for _old_marker in ('_EPAPHRAS_FAST_CB_V2', '_registerEpaphrasModesCallbacks', '_EPAPHRAS_ESM_V3'):
    if _old_marker in esrc:
        start_tag = f'// PATCH: {_old_marker}'
        end_tag = f'// END PATCH: {_old_marker}'
        si = esrc.find(start_tag)
        if si >= 0:
            # Walk back to nearest newline
            si = esrc.rfind('\n', 0, si) + 1
            ei = esrc.find(end_tag, si)
            if ei >= 0:
                ei += len(end_tag)
                if ei < len(esrc) and esrc[ei] == '\n':
                    ei += 1
                esrc = esrc[:si] + esrc[ei:]
                print(f"Removed old patch: {_old_marker}")

if EMBED_MARKER in esrc:
    print(f"pi-embedded already patched ({EMBED_MARKER}) — skipping")
else:
    # Anchor: right after "if (!callback) return;" and BEFORE "if (shouldSkipUpdate"
    # This is inside bot.on("callback_query", async (ctx) => {
    ANCHOR = 'if (!callback) return;\n\t\tif (shouldSkipUpdate(ctx)) return;'
    if ANCHOR not in esrc:
        print("WARNING: ESM_V3 anchor not found in pi-embedded — skipping")
    else:
        cbq_pos = esrc.index('bot.on("callback_query"')
        anchor_pos = esrc.index(ANCHOR, cbq_pos)
        INJECT = (
            '// PATCH: ' + EMBED_MARKER + ' — generic cb_* intercept (before shouldSkipUpdate)\n'
            '\t\tif (/^cb_/.test(callback.data ?? "")) {\n'
            '\t\t\ttry {\n'
            '\t\t\t\tconst { execFileSync: _es } = await import("child_process");\n'
            '\t\t\t\tconst _d = (callback.data ?? "").trim();\n'
            '\t\t\t\tconst _ENGINE = "' + ENGINE_PATH + '";\n'
            '\t\t\t\tconst _out = JSON.parse(_es("python3", [_ENGINE, "handle-callback", _d], { timeout: 8000 }).toString().trim());\n'
            '\t\t\t\tif (!_out.error) {\n'
            '\t\t\t\t\ttry {\n'
            '\t\t\t\t\t\tawait bot.api.answerCallbackQuery(callback.id).catch(() => {});\n'
            '\t\t\t\t\t\tconst _msg = callback.message;\n'
            '\t\t\t\t\t\tconst _btns = _out.buttons || _out.inline_keyboard || [];\n'
            '\t\t\t\t\t\tconst _rm = _btns.length ? { reply_markup: { inline_keyboard: _btns } } : {};\n'
            '\t\t\t\t\t\tawait bot.api.editMessageText(_msg.chat.id, _msg.message_id, _out.text || "", _rm);\n'
            '\t\t\t\t\t} catch(_te) { /* swallow Telegram errors (e.g. message not modified) */ }\n'
            '\t\t\t\t\treturn;\n'
            '\t\t\t\t}\n'
            '\t\t\t} catch(_e) { /* engine error or ESM import fail — fall through */ }\n'
            '\t\t}\n'
            '\t\t// END PATCH: ' + EMBED_MARKER + '\n'
            '\t\t'
        )
        OLD = 'if (!callback) return;\n\t\tif (shouldSkipUpdate(ctx)) return;'
        NEW = 'if (!callback) return;\n\t\t' + INJECT + 'if (shouldSkipUpdate(ctx)) return;'
        before = esrc[:anchor_pos]
        after = esrc[anchor_pos:].replace(OLD, NEW, 1)
        esrc = before + after
        with open(EMBEDDED, 'w') as f:
            f.write(esrc)
        print(f"Patched pi-embedded-CbMH3G07.js with {EMBED_MARKER} (ESM-compatible, before shouldSkipUpdate)")

# ─── Patch 3: pi-embedded text intercept for wizard free-text steps ────────────
TEXT_MARKER = '_EPAPHRAS_TEXT_V1'
with open(EMBEDDED, 'r') as f:
    tsrc = f.read()

if TEXT_MARKER in tsrc:
    print(f"pi-embedded already has {TEXT_MARKER} — skipping text intercept")
else:
    # grammY registers text via bot.on("message", ...) or bot.on("message:text", ...).
    # Inject right after the handler's opening `=> {`.
    import re as _re
    m = _re.search(r'bot\.on\(\s*["\']message(?::text)?["\']\s*,\s*async\s*\(([^)]*)\)\s*=>\s*\{', tsrc)
    if not m:
        print("WARNING: message handler anchor not found — text intercept NOT applied. "
              "Grep the bundle: grep -n 'bot.on(\"message' " + EMBEDDED)
    else:
        ctx_name = m.group(1).strip() or "ctx"
        insert_at = m.end()
        INJECT_TEXT = (
            '\n\t\t// PATCH: ' + TEXT_MARKER + ' — capture wizard free-text\n'
            '\t\ttry {\n'
            '\t\t\tconst _t = ' + ctx_name + '.message?.text;\n'
            '\t\t\tif (typeof _t === "string" && !_t.startsWith("/")) {\n'
            '\t\t\t\tconst _fs = await import("fs");\n'
            '\t\t\t\tconst _MODES = process.env.EPAPHRAS_MODES_FILE || "' + ENGINE_PATH.replace("engine.py", "modes.json") + '";\n'
            '\t\t\t\tlet _step = "idle", _panel = null;\n'
            '\t\t\t\ttry { const _j = JSON.parse(_fs.readFileSync(_MODES, "utf8")); _step = _j.wizard?.step ?? "idle"; _panel = _j.panel_message_id ?? null; } catch (_re) {}\n'
            '\t\t\t\tif (_step !== "idle") {\n'
            '\t\t\t\t\tconst { execFileSync: _es } = await import("child_process");\n'
            '\t\t\t\t\tconst _out = JSON.parse(_es("python3", ["' + ENGINE_PATH + '", "handle-text", _t], { timeout: 8000 }).toString().trim());\n'
            '\t\t\t\t\tif (_out.handled) {\n'
            '\t\t\t\t\t\tconst _btns = _out.buttons || _out.inline_keyboard || [];\n'
            '\t\t\t\t\t\tconst _rm = _btns.length ? { reply_markup: { inline_keyboard: _btns } } : {};\n'
            '\t\t\t\t\t\ttry { if (_panel) await bot.api.editMessageText(' + ctx_name + '.chat.id, _panel, _out.text || "", _rm); else await ' + ctx_name + '.reply(_out.text || "", _rm); } catch (_ee) {}\n'
            '\t\t\t\t\t\ttry { await bot.api.deleteMessage(' + ctx_name + '.chat.id, ' + ctx_name + '.message.message_id).catch(() => {}); } catch (_de) {}\n'
            '\t\t\t\t\t\treturn;\n'
            '\t\t\t\t\t}\n'
            '\t\t\t\t}\n'
            '\t\t\t}\n'
            '\t\t} catch (_e) { /* fall through to normal handling */ }\n'
            '\t\t// END PATCH: ' + TEXT_MARKER + '\n'
        )
        tsrc = tsrc[:insert_at] + INJECT_TEXT + tsrc[insert_at:]
        with open(EMBEDDED, 'w') as f:
            f.write(tsrc)
        print(f"Patched pi-embedded with {TEXT_MARKER} (wizard text intercept)")
