#!/usr/bin/env bash

set -euo pipefail

log() { printf "%s | %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# Use libtcmalloc for better memory management (best-effort)
TCMALLOC="$(ldconfig -p | grep -Po "libtcmalloc.so.\d" | head -n 1 || true)"
if [ -n "${TCMALLOC:-}" ]; then
  export LD_PRELOAD="${TCMALLOC}"
  log "worker-comfyui: Using ${TCMALLOC} via LD_PRELOAD"
else
  log "worker-comfyui: libtcmalloc not found (continuing without it)"
fi

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
        # Remove existing dir/link so we can replace with symlink
        rm -rf "$target"
        ln -sfn "$subdir" "$target"
        log "worker-comfyui:   ${target} -> ${subdir}"
    done
else
    log "worker-comfyui: No models found on Network Volume, using models from image"
fi

# Inventory custom nodes to aid debugging
if [ -d "/comfyui/custom_nodes" ]; then
  log "worker-comfyui: Listing custom_nodes (depth=1)"
  find /comfyui/custom_nodes -maxdepth 1 -mindepth 1 -type d -printf "%f\n" | sort | sed 's/^/  - /'
fi

log "worker-comfyui: Starting ComfyUI"
export PYTHONUNBUFFERED=1

# Check GPU availability before starting ComfyUI
log "worker-comfyui: Checking GPU availability"
if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits | while IFS=, read -r gpu_name gpu_mem; do
        log "worker-comfyui: GPU detected: ${gpu_name} (${gpu_mem}MB)"
    done
else
    log "worker-comfyui: WARNING - nvidia-smi not found. GPU may not be accessible."
fi

# Check if CUDA is available via Python (more reliable)
if python3 -c "import torch; print('CUDA available:', torch.cuda.is_available())" 2>/dev/null; then
    CUDA_AVAILABLE=$(python3 -c "import torch; print('yes' if torch.cuda.is_available() else 'no')" 2>/dev/null || echo "unknown")
    if [ "$CUDA_AVAILABLE" = "no" ]; then
        log "worker-comfyui: ERROR - CUDA is not available via PyTorch"
        log "worker-comfyui: This usually means:"
        log "worker-comfyui:   1. Container was started without GPU access (missing --gpus all or similar)"
        log "worker-comfyui:   2. NVIDIA drivers are not installed or not compatible"
        log "worker-comfyui:   3. CUDA runtime is not properly installed"
        log "worker-comfyui:"
        log "worker-comfyui: For RunPod: Ensure your endpoint is configured with GPU access enabled"
        log "worker-comfyui: For local testing: Use 'docker run --gpus all ...' to enable GPU access"
    else
        log "worker-comfyui: CUDA is available via PyTorch"
    fi
else
    log "worker-comfyui: WARNING - Could not check CUDA availability via Python"
fi

# Performance optimizations for RTX 4090 and high-end GPUs
# Enable PyTorch optimizations for faster inference
export PYTORCH_ENABLE_MPS_FALLBACK=0  # Disable MPS (we're using CUDA)
export TORCH_CUDNN_BENCHMARK=1        # Enable cuDNN autotuning
export TORCH_CUDNN_DETERMINISTIC=0    # Allow non-deterministic algorithms (faster)

# Apply runtime PyTorch optimizations (TF32, cuDNN benchmark, memory settings)
if [ -f "/optimize_pytorch.py" ]; then
    log "worker-comfyui: Applying PyTorch performance optimizations for RTX 4090"
    python /optimize_pytorch.py || log "worker-comfyui: Warning - PyTorch optimization script failed"
fi

# Note: LowVRAM mode is often enabled automatically by models
# If you have 24GB+ VRAM (RTX 4090), consider disabling lowVRAM in workflow settings

# Serve the API and don't shutdown the container
wait_for_server() {
  local host="127.0.0.1" port="8188" timeout="180" elapsed=0
  while ! (exec 3<>/dev/tcp/${host}/${port}) 2>/dev/null; do
    sleep 1; elapsed=$((elapsed+1))
    if [ "$elapsed" -eq 1 ] || [ $((elapsed % 10)) -eq 0 ]; then
      log "worker-comfyui: Waiting for ComfyUI on ${host}:${port} (${elapsed}s)"
    fi
    if [ "$elapsed" -ge "$timeout" ]; then
      log "worker-comfyui: ComfyUI did not become ready within ${timeout}s"
      if [ -f "/comfyui/user/comfyui.log" ]; then
        log "worker-comfyui: Last 200 lines of /comfyui/user/comfyui.log"
        tail -n 200 /comfyui/user/comfyui.log || true
      fi
      return 1
    fi
  done
  log "worker-comfyui: ComfyUI is accepting connections on ${host}:${port}"
  return 0
}

: "${COMFY_LOG_LEVEL:=DEBUG}"

if [ "${SERVE_API_LOCALLY:-false}" = "true" ]; then
    python -u /comfyui/main.py --disable-auto-launch --disable-metadata --listen --verbose "${COMFY_LOG_LEVEL}" --log-stdout &
    COMFY_PID=$!
    log "worker-comfyui: ComfyUI PID=${COMFY_PID} (listen mode)"
    if wait_for_server; then
      log "worker-comfyui: Starting RunPod Handler (local serve)"
      python -u /handler.py --rp_serve_api --rp_api_host=0.0.0.0
    else
      log "worker-comfyui: Handler not started due to ComfyUI readiness failure"
      exit 1
    fi
else
    python -u /comfyui/main.py --disable-auto-launch --disable-metadata --verbose "${COMFY_LOG_LEVEL}" --log-stdout &
    COMFY_PID=$!
    log "worker-comfyui: ComfyUI PID=${COMFY_PID}"
    if wait_for_server; then
      log "worker-comfyui: Starting RunPod Handler"
      python -u /handler.py
    else
      log "worker-comfyui: Handler not started due to ComfyUI readiness failure"
      exit 1
    fi
fi