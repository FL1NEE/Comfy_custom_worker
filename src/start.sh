#!/usr/bin/env bash

set -euo pipefail

log() { printf "%s | %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# Ensure ComfyUI-Manager runs in offline network mode inside the container
log "worker-comfyui: Setting ComfyUI-Manager network mode to offline"
comfy-manager-set-mode offline || echo "worker-comfyui - Could not set ComfyUI-Manager network_mode" >&2

# Symlink models from Network Volume if available
log "worker-comfyui: Checking for models on mounted volumes"
VOLUME_MODELS_DIR=""
for candidate in /workspace/models /runpod-volume/models /workplace/models; do
    if [ -d "$candidate" ] && [ "$(ls -A "$candidate" 2>/dev/null)" ]; then
        VOLUME_MODELS_DIR="$candidate"
        break
    fi
done

if [ -n "$VOLUME_MODELS_DIR" ]; then
    log "worker-comfyui: Symlinking models from ${VOLUME_MODELS_DIR}"
    for subdir in "$VOLUME_MODELS_DIR"/*/; do
        [ -d "$subdir" ] || continue
        name="$(basename "$subdir")"
        target="/comfyui/models/${name}"
        rm -rf "$target"
        ln -sfn "$subdir" "$target"
        log "worker-comfyui:   ${target} -> ${subdir}"
    done
else
    log "worker-comfyui: No models found on Network Volume, using models from image"
fi

# GPU pre-flight check
log "worker-comfyui: Running GPU pre-flight check"
python3 /optimize_pytorch.py || log "worker-comfyui: WARNING — optimize_pytorch.py failed (non-fatal)"

# Inventory custom nodes to aid debugging
if [ -d "/comfyui/custom_nodes" ]; then
    log "worker-comfyui: Listing custom_nodes (depth=1)"
    find /comfyui/custom_nodes -maxdepth 1 -mindepth 1 -type d -printf "%f\n" | sort | sed 's/^/  - /'
fi

log "worker-comfyui: Starting ComfyUI"
export PYTHONUNBUFFERED=1

wait_for_server() {
    local host="127.0.0.1"
    local port="8188"
    local timeout="180"
    local elapsed=0
    while ! (exec 3<>/dev/tcp/${host}/${port}) 2>/dev/null; do
        sleep 1; elapsed=$((elapsed + 1))
        if [ "$elapsed" -eq 1 ] || [ $((elapsed % 10)) -eq 0 ]; then
            log "worker-comfyui: Waiting for ComfyUI on ${host}:${port} (${elapsed}s)"
        fi
        if [ "$elapsed" -ge "$timeout" ]; then
            log "worker-comfyui: ComfyUI did not become ready within ${timeout}s"
            if [ -f "/comfyui/user/comfyui.log" ]; then
                log "worker-comfyui: Last 50 lines of comfyui.log:"
                tail -n 50 /comfyui/user/comfyui.log || true
            fi
            return 1
        fi
    done
    log "worker-comfyui: ComfyUI is ready on ${host}:${port}"
    return 0
}

: "${COMFY_LOG_LEVEL:=INFO}"

COMFY_ARGS="--disable-auto-launch --disable-metadata --highvram --verbose ${COMFY_LOG_LEVEL} --log-stdout"

if [ "${SERVE_API_LOCALLY:-false}" = "true" ]; then
    python -u /comfyui/main.py ${COMFY_ARGS} --listen &
    COMFY_PID=$!
    log "worker-comfyui: ComfyUI PID=${COMFY_PID} (listen mode)"
    if wait_for_server; then
        log "worker-comfyui: Starting RunPod Handler (local serve)"
        python -u /handler.py --rp_serve_api --rp_api_host=0.0.0.0
    else
        log "worker-comfyui: Handler not started — ComfyUI failed to become ready"
        exit 1
    fi
else
    python -u /comfyui/main.py ${COMFY_ARGS} &
    COMFY_PID=$!
    log "worker-comfyui: ComfyUI PID=${COMFY_PID}"
    if wait_for_server; then
        log "worker-comfyui: Starting RunPod Handler"
        python -u /handler.py
    else
        log "worker-comfyui: Handler not started — ComfyUI failed to become ready"
        exit 1
    fi
fi
