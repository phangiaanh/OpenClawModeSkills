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
    // Gemma sends buttons in interactive.blocks[n].buttons with {label,value,style} when buttons:[]
    if (hb && r.buttons.length === 0 && args.interactive && Array.isArray(args.interactive.blocks)) {
        const _flatBtns = [];
        for (const _blk of args.interactive.blocks) {
            if (!Array.isArray(_blk.buttons)) continue;
            for (const _btn of _blk.buttons) {
                if (!_btn || typeof _btn !== "object") continue;
                const _o = {};
                _o.text = typeof _btn.label === "string" ? _stripGemmaStr(_btn.label) : (typeof _btn.text === "string" ? _btn.text : "(button)");
                _o.callback_data = typeof _btn.value === "string" ? _stripGemmaStr(_btn.value) : (typeof _btn.callback_data === "string" ? _btn.callback_data : "");
                if (_btn.style) _o.style = _btn.style;
                _flatBtns.push(_o);
            }
        }
        if (_flatBtns.length > 0) {
            const _rows = [];
            for (let _i = 0; _i < _flatBtns.length; _i += 2) _rows.push(_flatBtns.slice(_i, _i + 2));
            r.buttons = _rows;
        }
    }
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

# ─── Patch 2 + 3: pi-embedded-CbMH3G07.js — bot.use() middleware (V5) ───────────
# Inject a bot.use(async (ctx, next) => {...}) with TWO parameters BEFORE
# registerTelegramHandlers() is called. The 2-param signature gives us full
# control of next() — we never call next() for cb_* or wizard text we handle,
# so the LLM is bypassed entirely.
#
# Why not inside bot.on("callback_query", async (ctx) => {...}):
#   grammY treats 1-param handlers as "leaf" middleware and auto-calls next()
#   after the function resolves, even if we return early. The LLM fires anyway.

import glob as _glob
_matches = _glob.glob('/app/dist/pi-embedded-*.js')
if not _matches:
    print("ERROR: no pi-embedded-*.js found in /app/dist/"); sys.exit(1)
if len(_matches) > 1:
    print(f"WARNING: multiple matches {_matches}, using first")
EMBEDDED = _matches[0]
print(f"pi-embedded bundle: {EMBEDDED}")
ENGINE_PATH = '/root/.openclaw/workspace/skills/OpenClawModeSkills/engine.py'
MODES_PATH = ENGINE_PATH.replace("engine.py", "modes.json")
MW_MARKER = '_EPAPHRAS_MW_V6'

with open(EMBEDDED, 'r') as f:
    esrc = f.read()

# Remove ALL old Epaphras patches so we start clean
for _old_marker in (
    '_EPAPHRAS_FAST_CB_V2', '_registerEpaphrasModesCallbacks',
    '_EPAPHRAS_ESM_V3', '_EPAPHRAS_ESM_V4', '_EPAPHRAS_TEXT_V1',
    '_EPAPHRAS_POLL_V1',
):
    if _old_marker in esrc:
        start_tag = f'// PATCH: {_old_marker}'
        end_tag = f'// END PATCH: {_old_marker}'
        si = esrc.find(start_tag)
        if si >= 0:
            si = esrc.rfind('\n', 0, si) + 1
            ei = esrc.find(end_tag, si)
            if ei >= 0:
                ei += len(end_tag)
                if ei < len(esrc) and esrc[ei] == '\n':
                    ei += 1
                esrc = esrc[:si] + esrc[ei:]
                print(f"Removed old patch: {_old_marker}")

if MW_MARKER in esrc:
    print(f"pi-embedded already patched ({MW_MARKER}) — skipping")
else:
    # Anchor: inject BEFORE bot.use(botRuntime.sequentialize(...)) so our middleware
    # runs in the grammY chain before sequentialize (which dispatches to LLM and may
    # not call next() for all update types). Uses __require() which is the
    # createRequire binding already imported at the top of the ESM bundle.
    ANCHOR = '\tbot.use(botRuntime.sequentialize(getTelegramSequentialKey));'
    if ANCHOR not in esrc:
        # Fallback to registerTelegramHandlers anchor if sequentialize not found
        ANCHOR = 'registerTelegramHandlers({'
        if ANCHOR not in esrc:
            print("WARNING: no injection anchor found — skipping middleware patch")
            ANCHOR = None
    if ANCHOR:
        anchor_pos = esrc.index(ANCHOR)
        INJECT_MW = (
            '// PATCH: ' + MW_MARKER + ' — intercept cb_* and wizard text before LLM\n'
            'bot.use(async (ctx, next) => {\n'
            '\tconst _ENGINE = "' + ENGINE_PATH + '";\n'
            '\tif (ctx.callbackQuery?.data?.startsWith?.("cb_")) {\n'
            '\t\ttry {\n'
            '\t\t\tconst { execFileSync: _es } = __require("child_process");\n'
            '\t\t\tconst _d = ctx.callbackQuery.data.trim();\n'
            '\t\t\tconst _out = JSON.parse(_es("python3", [_ENGINE, "handle-callback", _d], { timeout: 8000 }).toString().trim());\n'
            '\t\t\tif (!_out.error) {\n'
            '\t\t\t\ttry {\n'
            '\t\t\t\t\tif (_out.toast !== undefined) {\n'
            '\t\t\t\t\t\tawait ctx.answerCallbackQuery({text: typeof _out.toast === "string" ? _out.toast : ""}).catch(() => {});\n'
            '\t\t\t\t\t} else {\n'
            '\t\t\t\t\t\tawait ctx.answerCallbackQuery().catch(() => {});\n'
            '\t\t\t\t\t\tconst _msg = ctx.callbackQuery.message;\n'
            '\t\t\t\t\t\tconst _btns = _out.buttons || _out.inline_keyboard || [];\n'
            '\t\t\t\t\t\tconst _rm = _btns.length ? { reply_markup: { inline_keyboard: _btns } } : {};\n'
            '\t\t\t\t\t\tawait ctx.api.editMessageText(_msg.chat.id, _msg.message_id, _out.text || "", _rm);\n'
            '\t\t\t\t\t\ttry { _es("python3", [_ENGINE, "store-chat-id", String(_msg.chat.id)], {timeout: 4000}); } catch (_) {}\n'
            '\t\t\t\t\t}\n'
            '\t\t\t\t} catch (_te) { /* swallow Telegram errors */ }\n'
            '\t\t\t\treturn; // skip next() — LLM bypassed\n'
            '\t\t\t}\n'
            '\t\t} catch (_e) { /* engine error — fall through to LLM */ }\n'
            '\t\treturn next();\n'
            '\t}\n'
            '\tconst _t = ctx.message?.text;\n'
            '\tif (typeof _t === "string" && !_t.startsWith("/") && ctx.message) {\n'
            '\t\ttry {\n'
            '\t\t\tconst { readFileSync: _rfs } = __require("fs");\n'
            '\t\t\tconst _MODES = process.env.EPAPHRAS_MODES_FILE || "' + MODES_PATH + '";\n'
            '\t\t\tlet _step = "idle", _panel = null;\n'
            '\t\t\ttry { const _j = JSON.parse(_rfs(_MODES, "utf8")); _step = _j.wizard?.step ?? "idle"; _panel = _j.panel_message_id ?? null; } catch (_re) {}\n'
            '\t\t\tif (_step !== "idle") {\n'
            '\t\t\t\tconst { execFileSync: _es } = __require("child_process");\n'
            '\t\t\t\tconst _out = JSON.parse(_es("python3", [_ENGINE, "handle-text", _t], { timeout: 8000 }).toString().trim());\n'
            '\t\t\t\tif (_out.handled) {\n'
            '\t\t\t\t\tconst _btns = _out.buttons || _out.inline_keyboard || [];\n'
            '\t\t\t\t\tconst _rm = _btns.length ? { reply_markup: { inline_keyboard: _btns } } : {};\n'
            '\t\t\t\t\ttry { if (_panel) await ctx.api.editMessageText(ctx.chat.id, _panel, _out.text || "", _rm); else await ctx.reply(_out.text || "", _rm); } catch (_ee) {}\n'
            '\t\t\t\t\ttry { await ctx.deleteMessage().catch(() => {}); } catch (_de) {}\n'
            '\t\t\t\t\treturn; // skip next() — LLM bypassed\n'
            '\t\t\t\t}\n'
            '\t\t\t}\n'
            '\t\t} catch (_e) { /* fall through */ }\n'
            '\t}\n'
            '\treturn next();\n'
            '});\n'
            '// END PATCH: ' + MW_MARKER + '\n'
        )
        esrc = esrc[:anchor_pos] + INJECT_MW + esrc[anchor_pos:]
        with open(EMBEDDED, 'w') as f:
            f.write(esrc)
        print(f"Patched pi-embedded with {MW_MARKER} (before sequentialize, uses __require)")

# ─── Patch 3: CB intercept inside bot.on("callback_query") — belt-and-suspenders ─
# Inserted right before `await processMessage(buildSyntheticContext(...))`.
# Uses local helpers (editCallbackMessage, replyToCallbackChat) already in scope.
# If V5 middleware handles the request first this code is never reached.
CB_MARKER = '_EPAPHRAS_CB_INTERCEPT'

# Reload after V5 patch may have written the file
with open(EMBEDDED, 'r') as f:
    esrc = f.read()

if CB_MARKER in esrc:
    print(f"pi-embedded already patched ({CB_MARKER}) — skipping")
else:
    CB_ANCHOR = '\t\t\tawait processMessage(buildSyntheticContext(ctx, buildSyntheticTextMessage({'
    if CB_ANCHOR not in esrc:
        print("WARNING: processMessage anchor not found — skipping CB intercept")
    else:
        cb_anchor_pos = esrc.index(CB_ANCHOR)
        INJECT_CB = (
            '// PATCH: ' + CB_MARKER + '\n'
            '\t\t\tif (data.startsWith("cb_")) {\n'
            '\t\t\t\ttry {\n'
            '\t\t\t\t\tconst { execFileSync: _cbes } = __require("child_process");\n'
            '\t\t\t\t\tconst _cbRaw = _cbes("python3", ["' + ENGINE_PATH + '", "handle-callback", data], { encoding: "utf-8", timeout: 10000 });\n'
            '\t\t\t\t\tconst _cbOut = JSON.parse(_cbRaw);\n'
            '\t\t\t\t\tif (_cbOut && !_cbOut.error && _cbOut.text) {\n'
            '\t\t\t\t\t\tconst _cbKb = _cbOut.buttons ? { reply_markup: { inline_keyboard: _cbOut.buttons } } : void 0;\n'
            '\t\t\t\t\t\ttry { await editCallbackMessage(_cbOut.text, _cbKb); } catch (_ce) { try { await replyToCallbackChat(_cbOut.text, _cbKb); } catch (_) {} }\n'
            '\t\t\t\t\t\treturn; // LLM bypassed\n'
            '\t\t\t\t\t}\n'
            '\t\t\t\t\t// engine returned error — fall through to processMessage\n'
            '\t\t\t\t} catch (_cbe) {\n'
            '\t\t\t\t\ttry { __require("fs").appendFileSync("/tmp/cb_err.log", String(Date.now()) + " data=" + data + " err=" + String(_cbe) + "\\n"); } catch (_) {}\n'
            '\t\t\t\t}\n'
            '\t\t\t}\n'
            '// END PATCH: ' + CB_MARKER + '\n'
        )
        esrc = esrc[:cb_anchor_pos] + INJECT_CB + esrc[cb_anchor_pos:]
        with open(EMBEDDED, 'w') as f:
            f.write(esrc)
        print(f"Patched pi-embedded with {CB_MARKER} (before processMessage call)")
        # Invalidate JITI cache so next start compiles from patched ESM
        import os as _os, glob as _glob2
        for _jiti in _glob2.glob('/tmp/jiti/dist-pi-embedded-*.cjs'):
            try:
                _os.remove(_jiti)
                print(f"Deleted JITI cache: {_jiti}")
            except Exception as _e:
                print(f"WARNING: could not delete JITI cache {_jiti}: {_e}")

# ─── Patch 4: hourly poll timer (_EPAPHRAS_POLL_V1) ─────────────────────────────
# Injects a setInterval into the gateway process that shells out to engine.py poll
# once per hour. The engine enforces the 08:00–20:00 ICT window itself.
POLL_MARKER = '_EPAPHRAS_POLL_V1'

# Reload EMBEDDED (it was already written by Patch 3)
with open(EMBEDDED, 'r') as f:
    _psrc = f.read()

# Always strip old poll patch so we can re-inject the updated version
if POLL_MARKER in _psrc:
    _ps_start = '// PATCH: ' + POLL_MARKER
    _ps_end = '// END PATCH: ' + POLL_MARKER
    _ps_si = _psrc.find(_ps_start)
    if _ps_si >= 0:
        _ps_si = _psrc.rfind('\n', 0, _ps_si) + 1
        _ps_ei = _psrc.find(_ps_end, _ps_si)
        if _ps_ei >= 0:
            _ps_ei += len(_ps_end)
            if _ps_ei < len(_psrc) and _psrc[_ps_ei] == '\n':
                _ps_ei += 1
            _psrc = _psrc[:_ps_si] + _psrc[_ps_ei:]
            print(f"Removed old {POLL_MARKER} for re-injection")

if True:  # always inject
    POLL_ANCHOR = '\tbot.use(botRuntime.sequentialize(getTelegramSequentialKey));'
    if POLL_ANCHOR not in _psrc:
        POLL_ANCHOR = 'registerTelegramHandlers({'
        if POLL_ANCHOR not in _psrc:
            print("WARNING: no injection anchor for poll timer — skipping Patch 4")
            POLL_ANCHOR = None
    if POLL_ANCHOR:
        _poll_anchor_pos = _psrc.index(POLL_ANCHOR)
        _SKILL_DIR = ENGINE_PATH.rsplit('/', 1)[0]
        INJECT_POLL = (
            '// PATCH: ' + POLL_MARKER + ' — hourly poll timer\n'
            '(function _registerEpaphrasPoll() {\n'
            '  if (globalThis.__epaphrasPollV1) return;\n'
            '  globalThis.__epaphrasPollV1 = true;\n'
            '  const { execFileSync } = __require("child_process");\n'
            '  const _logFs = __require("fs");\n'
            '  const SKILL_DIR = process.env.EPAPHRAS_SKILL_DIR || "' + _SKILL_DIR + '";\n'
            '  const INTERVAL_MS = 60 * 60 * 1000;\n'
            '  _logFs.appendFileSync("/tmp/epaphras_poll.log", new Date().toISOString() + " IIFE_REGISTERED\\n");\n'
            '  function tick() {\n'
            '    try {\n'
            '      const out = execFileSync("python3", [SKILL_DIR + "/engine.py", "poll"],\n'
            '                              { cwd: SKILL_DIR, env: process.env, timeout: 120000 }).toString().trim();\n'
            '      _logFs.appendFileSync("/tmp/epaphras_poll.log", new Date().toISOString() + " " + out + "\\n");\n'
            '      try {\n'
            '        const payload = JSON.parse(out);\n'
            '        if (payload && payload.emit && payload.chat_id) {\n'
            '          // bot is the grammY Bot instance at bundle module scope\n'
            '          bot.api.sendMessage(payload.chat_id, payload.emit.text || "(no text)", {\n'
            '            reply_markup: { inline_keyboard: payload.emit.buttons }\n'
            '          }).catch(e => {\n'
            '            try { _logFs.appendFileSync("/tmp/epaphras_poll.log", new Date().toISOString() + " SEND_ERR " + String(e) + "\\n"); } catch (_) {}\n'
            '          });\n'
            '        }\n'
            '      } catch (_pe) {}\n'
            '    } catch (e) {\n'
            '      try { _logFs.appendFileSync("/tmp/epaphras_poll.log", new Date().toISOString() + " ERR " + String(e.message || e) + "\\n"); } catch (_) {}\n'
            '    }\n'
            '  }\n'
            '  setInterval(tick, INTERVAL_MS);\n'
            '})();\n'
            '// END PATCH: ' + POLL_MARKER + '\n'
        )
        _psrc = _psrc[:_poll_anchor_pos] + INJECT_POLL + _psrc[_poll_anchor_pos:]
        with open(EMBEDDED, 'w') as f:
            f.write(_psrc)
        print(f"Patched pi-embedded with {POLL_MARKER}")

# ─── Patch 5: cb_* intercept in bot-handlers.runtime.ts (_EPAPHRAS_CB_INTERCEPT_TS) ─
# The Telegram callback handler is loaded via JITI from the TS source at
# /app/extensions/telegram/src/bot-handlers.runtime.ts — NOT from the compiled JS
# bundle. We inject spawnSync("python3", engine, "handle-callback") right after the
# early-return guard, so cb_* callbacks edit the panel directly without hitting the LLM.
TS_TARGET = '/app/extensions/telegram/src/bot-handlers.runtime.ts'
TS_MARKER = '_EPAPHRAS_CB_INTERCEPT_TS'
TS_END_MARKER = '// END PATCH: _EPAPHRAS_CB_INTERCEPT_TS'
TS_INJECT_AFTER = '      if (!data || !callbackMessage) {\n        return;\n      }\n'

import os as _os5
if not _os5.path.exists(TS_TARGET):
    print(f"WARNING: {TS_TARGET} not found — skipping Patch 5 (TS cb intercept)")
else:
    with open(TS_TARGET) as _f5:
        _ts_src = _f5.read()

    # Remove existing patch if present (idempotent)
    if TS_MARKER in _ts_src:
        _ts_si = _ts_src.find('// PATCH: ' + TS_MARKER)
        _ts_ei = _ts_src.find(TS_END_MARKER)
        if _ts_si >= 0 and _ts_ei >= 0:
            _old_block = _ts_src[_ts_si:_ts_ei + len(TS_END_MARKER) + 1]
            _ts_src = _ts_src.replace(_old_block, '', 1)

    if TS_INJECT_AFTER not in _ts_src:
        print(f"WARNING: TS inject anchor not found in {TS_TARGET} — skipping Patch 5")
    else:
        _TS_INTERCEPT = (
            '      // PATCH: ' + TS_MARKER + '\n'
            '      try {\n'
            '        // eslint-disable-next-line @typescript-eslint/no-require-imports\n'
            '        const _epCp: any = require("node:child_process");\n'
            '        if (data.startsWith("cb_")) {\n'
            '          const _epProc = _epCp.spawnSync("python3", [\n'
            '            "' + ENGINE_PATH + '",\n'
            '            "handle-callback", data\n'
            '          ], { encoding: "utf-8", timeout: 10000 });\n'
            '          if (_epProc.status === 0 && _epProc.stdout) {\n'
            '            const _epOut = JSON.parse((_epProc.stdout as string).trim());\n'
            '            if (_epOut && !_epOut.error && _epOut.text) {\n'
            '              const _epKb: any = _epOut.buttons ? { reply_markup: { inline_keyboard: _epOut.buttons } } : {};\n'
            '              try {\n'
            '                await bot.api.editMessageText(callbackMessage.chat.id, callbackMessage.message_id, _epOut.text as string, _epKb);\n'
            '              } catch (_epSe: unknown) {\n'
            '                try { await bot.api.sendMessage(callbackMessage.chat.id, _epOut.text as string, _epKb); } catch (_epM: unknown) { void _epM; }\n'
            '              }\n'
            '              return;\n'
            '            }\n'
            '          }\n'
            '        }\n'
            '      } catch (_epErr: unknown) { void _epErr; }\n'
            '      ' + TS_END_MARKER + '\n'
        )
        _ts_src = _ts_src.replace(TS_INJECT_AFTER, TS_INJECT_AFTER + _TS_INTERCEPT, 1)
        with open(TS_TARGET, 'w') as _f5w:
            _f5w.write(_ts_src)
        print(f"Patched {TS_TARGET} with {TS_MARKER}")
        import glob as _g5
        for _jiti5 in _g5.glob('/tmp/jiti/src-bot-handlers.runtime.*.cjs'):
            try:
                _os5.remove(_jiti5); print(f"Deleted JITI cache: {_jiti5}")
            except Exception as _e5:
                print(f"WARNING: could not delete JITI TS cache {_jiti5}: {_e5}")
