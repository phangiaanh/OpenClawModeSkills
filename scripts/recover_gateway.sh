#!/bin/sh
# recover_gateway.sh — full gateway patch recovery after pod restart
# Run from LOCAL machine. Applies all patches and restarts the gateway.
#
# Usage:
#   POD=<pod-name> ./recover_gateway.sh
#   POD=<pod-name> KUBECONFIG=<path> ./recover_gateway.sh
#
# What this does:
#   1. Copies patch scripts and engine files to pod
#   2. Applies openai-completions.js patch (Gemma format)
#   3. Applies pi-embedded ESM_V3 fast callback patch
#   4. Restarts the gateway subprocess (kill -USR1 on parent)

set -e

POD="${POD:?POD env var required}"
KUBECONFIG="${KUBECONFIG:-$HOME/Documents/kubeconfig_prod.yaml}"
NS="agent-core-53461"
SKILLS_LOCAL="/Users/lap15626/source/agents/epaphras/OpenClawModeSkills"

KC="kubectl --kubeconfig $KUBECONFIG -n $NS"

echo "=== Copying patch scripts ==="
$KC cp ./full_patch_v2.py $POD:/tmp/full_patch_v2.py -c gateway
$KC cp ./patch_esm_v3.py $POD:/tmp/patch_esm_v3.py -c gateway

echo "=== Copying engine files ==="
$KC exec $POD -c gateway -- mkdir -p /app/skills/openclaw-mode-skills/templates
$KC cp "$SKILLS_LOCAL/engine.py" $POD:/app/skills/openclaw-mode-skills/engine.py -c gateway
$KC cp "$SKILLS_LOCAL/templates/modes.default.json" $POD:/app/skills/openclaw-mode-skills/templates/modes.default.json -c gateway

echo "=== Applying openai-completions.js patch ==="
$KC exec $POD -c gateway -- python3 /tmp/full_patch_v2.py

echo "=== Verifying engine ==="
$KC exec $POD -c gateway -- python3 /app/skills/openclaw-mode-skills/engine.py render-modes | grep -c "cb_setmode" | xargs -I{} echo "  mode buttons: {}"

echo "=== Restarting gateway subprocess ==="
PARENT_PID=$($KC exec $POD -c gateway -- sh -c 'pgrep -f "^openclaw$" | head -1')
echo "  Sending SIGUSR1 to parent PID $PARENT_PID"
$KC exec $POD -c gateway -- kill -USR1 "$PARENT_PID"

echo "=== Waiting for new gateway process ==="
sleep 3
$KC exec $POD -c gateway -- sh -c 'ps aux | grep -E "openclaw" | grep -v grep'

echo ""
echo "=== Done. Monitor with: ==="
echo "$KC exec $POD -c gateway -- tail -f /tmp/openclaw/openclaw-\$(date +%Y-%m-%d).log | grep -E --line-buffered 'embedded run start|callback_query|ESM_V3'"
