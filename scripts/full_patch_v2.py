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
MW_MARKER = '_EPAPHRAS_MW_V5'

with open(EMBEDDED, 'r') as f:
    esrc = f.read()

# Remove ALL old Epaphras patches so we start clean
for _old_marker in (
    '_EPAPHRAS_FAST_CB_V2', '_registerEpaphrasModesCallbacks',
    '_EPAPHRAS_ESM_V3', '_EPAPHRAS_ESM_V4', '_EPAPHRAS_TEXT_V1',
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
            '\t\t\t\t\tawait ctx.answerCallbackQuery().catch(() => {});\n'
            '\t\t\t\t\tconst _msg = ctx.callbackQuery.message;\n'
            '\t\t\t\t\tconst _btns = _out.buttons || _out.inline_keyboard || [];\n'
            '\t\t\t\t\tconst _rm = _btns.length ? { reply_markup: { inline_keyboard: _btns } } : {};\n'
            '\t\t\t\t\tawait ctx.api.editMessageText(_msg.chat.id, _msg.message_id, _out.text || "", _rm);\n'
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

# ─── Patch 4: zernio webhook receiver (_EPAPHRAS_WEBHOOK_V1) ─────────────────────
# Mounts POST /zernio/webhook on the runtime HTTP server: verify HMAC -> dedup ->
# ack 200 -> shell to engine.py handle-webhook (which matches + logs). No Telegram.
#
# Spike result (Task 8 Step 1):
#   Bundle: /app/dist/gateway-cli-Ce2czDuO.js
#   Framework: raw node:http createServer -> delegates to async function handleRequest(req, res)
#   Anchor: "async function handleRequest(req, res) {" (appears exactly once)
#   Available in scope: fs (node:fs), crypto (node:crypto), spawnSync (node:child_process)
#   No __require() — this is an ESM bundle; use module-scope imports directly.
import glob as _g4
WH_MARKER = '_EPAPHRAS_WEBHOOK_V1'
RECV_BUNDLE_GLOB = '/app/dist/gateway-cli-*.js'
_recv_matches = _g4.glob(RECV_BUNDLE_GLOB)
if not _recv_matches:
    print("WARNING: receiver bundle not found — skipping Patch 4")
else:
    RECV_FILE = _recv_matches[0]
    with open(RECV_FILE, 'r') as f:
        rsrc = f.read()
    if WH_MARKER in rsrc:
        print(f"receiver already patched ({WH_MARKER}) — skipping")
    else:
        # Anchor: top of handleRequest, which is the single request dispatcher for the
        # node:http createServer. Inject our webhook route before the websocket check so
        # it is the very first thing evaluated for every incoming request.
        # This anchor was verified unique (count=1) in the live pod bundle.
        RECV_ANCHOR = 'async function handleRequest(req, res) {'
        if RECV_ANCHOR not in rsrc:
            print("WARNING: receiver HTTP anchor not found — skipping Patch 4 "
                  "(re-run the Task 8 spike and update RECV_ANCHOR)")
        else:
            # The gateway-cli bundle uses top-level ESM imports, so fs, crypto and
            # spawnSync are already in scope — no require() or __require() needed.
            INJECT = (
                'async function handleRequest(req, res) {\n'
                '// PATCH: ' + WH_MARKER + '\n'
                'if (req.method === "POST" && (req.url || "").split("?")[0] === "/zernio/webhook") {\n'
                '  const _ENGINE = "' + ENGINE_PATH + '";\n'
                '  const _MODES = process.env.EPAPHRAS_MODES_FILE || "' + MODES_PATH + '";\n'
                '  const _chunks = [];\n'
                '  req.on("data", (c) => _chunks.push(c));\n'
                '  req.on("end", () => {\n'
                '    try {\n'
                '      const _raw = Buffer.concat(_chunks);\n'
                '      let _secret = null, _eid = req.headers["x-zernio-event-id"];\n'
                '      try { _secret = JSON.parse(fs.readFileSync(_MODES, "utf8")).webhook?.secret || null; } catch (_) {}\n'
                '      if (!_secret) { res.writeHead(401); res.end("no secret"); return; }\n'
                '      const _sig = req.headers["x-zernio-signature"] || "";\n'
                '      const _calc = crypto.createHmac("sha256", _secret).update(_raw).digest("hex");\n'
                '      const _a = Buffer.from(_calc), _b = Buffer.from(String(_sig));\n'
                '      if (_a.length !== _b.length || !crypto.timingSafeEqual(_a, _b)) {\n'
                '        res.writeHead(401); res.end("bad signature"); return;\n'
                '      }\n'
                '      globalThis.__epaphrasSeen = globalThis.__epaphrasSeen || new Set();\n'
                '      const _seen = globalThis.__epaphrasSeen;\n'
                '      if (_eid && _seen.has(_eid)) { res.writeHead(200); res.end("dup"); return; }\n'
                '      if (_eid) { _seen.add(_eid); if (_seen.size > 1000) _seen.delete(_seen.values().next().value); }\n'
                '      res.writeHead(200); res.end("ok");  // ack before processing (5s budget)\n'
                '      try {\n'
                '        spawnSync("python3", [_ENGINE, "handle-webhook", _raw.toString("utf8")], { timeout: 8000 });\n'
                '      } catch (_pe) {\n'
                '        try { fs.appendFileSync("/tmp/wh_err.log", String(_pe) + "\\n"); } catch (_) {}\n'
                '      }\n'
                '    } catch (_e) {\n'
                '      try { res.writeHead(500); res.end("err"); } catch (_) {}\n'
                '    }\n'
                '  });\n'
                '  return;\n'
                '}\n'
                '// END PATCH: ' + WH_MARKER + '\n'
            )
            rsrc = rsrc.replace(RECV_ANCHOR, INJECT, 1)
            with open(RECV_FILE, 'w') as f:
                f.write(rsrc)
            print(f"Patched receiver with {WH_MARKER}")
            import os as _os4
            for _jiti in _g4.glob('/tmp/jiti/dist-gateway-cli-*.cjs'):
                try:
                    _os4.remove(_jiti); print(f"Deleted JITI cache: {_jiti}")
                except Exception as _e:
                    print(f"WARNING: could not delete JITI cache {_jiti}: {_e}")
