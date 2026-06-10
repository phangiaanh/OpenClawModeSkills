#!/usr/bin/env python3
"""
Re-apply the _EPAPHRAS_CB_INTERCEPT_TS patch to bot-handlers.runtime.ts
in the openclaw gateway container.

This patch makes cb_* Telegram button callbacks bypass the LLM and be handled
directly by engine.py handle-callback, editing the message in ~200ms instead of 10-20s.

Usage (inside the gateway container):
  python3 /root/.openclaw/workspace/skills/OpenClawModeSkills/scripts/patch_gateway_ts.py
  python3 /root/.openclaw/workspace/skills/OpenClawModeSkills/scripts/patch_gateway_ts.py --restart

--restart  Patch + restart the gateway in one shot using a background subshell so
           Kubernetes does NOT see the main process die (avoids a full pod restart
           that would reset the overlayfs and undo the patch).
"""

import os
import subprocess
import sys

TARGET = '/app/extensions/telegram/src/bot-handlers.runtime.ts'
JITI_CACHE_PATTERN = '/tmp/jiti/src-bot-handlers.runtime.*.cjs'
ENGINE_PATH = '/root/.openclaw/workspace/skills/OpenClawModeSkills/engine.py'

PATCH_MARKER = '// PATCH: _EPAPHRAS_CB_INTERCEPT_TS'
PATCH_END_MARKER = '// END PATCH: _EPAPHRAS_CB_INTERCEPT_TS'

INTERCEPT = f"""\
      {PATCH_MARKER}
      try {{
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const _epCp: any = require("node:child_process");
        if (data.startsWith("cb_")) {{
          const _epProc = _epCp.spawnSync("python3", [
            "{ENGINE_PATH}",
            "handle-callback", data
          ], {{ encoding: "utf-8", timeout: 10000 }});
          if (_epProc.status === 0 && _epProc.stdout) {{
            const _epOut = JSON.parse((_epProc.stdout as string).trim());
            if (_epOut && !_epOut.error && _epOut.text) {{
              const _epKb: any = _epOut.buttons ? {{ reply_markup: {{ inline_keyboard: _epOut.buttons }} }} : {{}};
              try {{
                await bot.api.editMessageText(callbackMessage.chat.id, callbackMessage.message_id, _epOut.text as string, _epKb);
              }} catch (_epSe: unknown) {{
                try {{ await bot.api.sendMessage(callbackMessage.chat.id, _epOut.text as string, _epKb); }} catch (_epM: unknown) {{ void _epM; }}
              }}
              return;
            }}
          }}
        }}
      }} catch (_epErr: unknown) {{ void _epErr; }}
      {PATCH_END_MARKER}
"""

INJECT_AFTER = (
    '      if (!data || !callbackMessage) {\n'
    '        return;\n'
    '      }\n'
)

def apply_patch(src: str) -> str:
    # Remove existing patch if present
    if PATCH_MARKER in src:
        start = src.find(PATCH_MARKER)
        end = src.find(PATCH_END_MARKER)
        if start >= 0 and end >= 0:
            old_block = src[start:end + len(PATCH_END_MARKER) + 1]
            src = src.replace(old_block, '', 1)
            print('Removed existing patch')

    # Inject new patch
    if INJECT_AFTER not in src:
        print(f'ERROR: inject point not found in {TARGET}')
        print('Looking for:', repr(INJECT_AFTER))
        sys.exit(1)

    src = src.replace(INJECT_AFTER, INJECT_AFTER + INTERCEPT, 1)
    return src


GATEWAY_BINARY = '/usr/local/bin/openclaw'
RESTART_LOG = '/tmp/oc_restart.log'


def find_gateway_pid():
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            comm = open(f'/proc/{pid}/comm').read().strip()
            if comm == 'openclaw-gatewa':
                return int(pid)
        except OSError:
            pass
    return None


def do_restart():
    import signal, time

    gw_pid = find_gateway_pid()
    if gw_pid:
        print(f'Killing gateway (PID {gw_pid})...')
        try:
            os.kill(gw_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        time.sleep(4)
    else:
        print('Gateway process not found — starting fresh.')

    print(f'Starting new gateway → {RESTART_LOG}')
    log = open(RESTART_LOG, 'a')
    proc = subprocess.Popen(
        [GATEWAY_BINARY, 'gateway', '--allow-unconfigured'],
        stdout=log,
        stderr=log,
        cwd='/app',
        start_new_session=True,  # detach from current process group (immune to SIGHUP)
    )
    print(f'Gateway started (PID {proc.pid}). Check {RESTART_LOG} for status.')


def main():
    do_restart_flag = '--restart' in sys.argv

    if not os.path.exists(TARGET):
        print(f'ERROR: {TARGET} not found')
        sys.exit(1)

    if not os.path.exists(ENGINE_PATH):
        print(f'ERROR: {ENGINE_PATH} not found')
        sys.exit(1)

    src = open(TARGET).read()

    if PATCH_MARKER in src:
        print('Patch already applied, re-applying fresh...')

    patched = apply_patch(src)

    if PATCH_MARKER not in patched:
        print('ERROR: patch injection failed')
        sys.exit(1)

    open(TARGET, 'w').write(patched)
    print(f'Patched: {TARGET}')

    import glob
    for cache in glob.glob(JITI_CACHE_PATTERN):
        os.remove(cache)
        print(f'Cleared JITI cache: {cache}')

    print()
    if do_restart_flag:
        do_restart()
    else:
        print('SUCCESS. To restart the gateway (pod-safe):')
        print(f'  {SAFE_RESTART_CMD}')
        print()
        print('Or re-run this script with --restart to do it automatically.')


if __name__ == '__main__':
    main()
