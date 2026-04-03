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

# Определяем VRAM и SM-версию GPU за один вызов Python
read -r VRAM_MB SM_VER <<< "$(python3 -c "
import torch
if torch.cuda.is_available():
    p = torch.cuda.get_device_properties(0)
    vram = int(p.total_memory / 1024 / 1024)
    sm   = p.major * 10 + p.minor   # 100 = sm_100 (Blackwell), 90 = sm_90 (Hopper), 89 = sm_89 (Ada)
    print(vram, sm)
else:
    print(0, 0)
" 2>/dev/null || echo "0 0")"

# --- VRAM mode ---
if [ -z "${COMFY_VRAM_MODE:-}" ]; then
    # highvram только если VRAM > 40GB — держит всё в GPU без выгрузки
    # normalvram для 32GB и ниже — text encoder выгружается после кодирования промпта,
    # освобождая ~10GB перед семплингом (T5/umt5-xxl + два Wan = ~37GB > 32GB без выгрузки)
    if [ "$VRAM_MB" -ge 40960 ]; then
        COMFY_VRAM_MODE="highvram"
    elif [ "$VRAM_MB" -ge 10240 ]; then
        COMFY_VRAM_MODE="normalvram"
    else
        COMFY_VRAM_MODE="lowvram"
    fi
    log "worker-comfyui: Auto-detected VRAM=${VRAM_MB}MB SM=${SM_VER} → --${COMFY_VRAM_MODE}"
else
    log "worker-comfyui: VRAM mode from env → --${COMFY_VRAM_MODE} (VRAM=${VRAM_MB}MB SM=${SM_VER})"
fi

# --- PyTorch CUDA allocator ---
if [ "$VRAM_MB" -ge 20480 ]; then
    export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True,garbage_collection_threshold:0.9"
elif [ "$VRAM_MB" -ge 10240 ]; then
    export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True,garbage_collection_threshold:0.8,max_split_size_mb:512"
else
    export PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True,garbage_collection_threshold:0.6,max_split_size_mb:256"
fi
log "worker-comfyui: PYTORCH_CUDA_ALLOC_CONF=${PYTORCH_CUDA_ALLOC_CONF}"

COMFY_ARGS="--disable-auto-launch --disable-metadata --${COMFY_VRAM_MODE} --verbose ${COMFY_LOG_LEVEL} --log-stdout"

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
