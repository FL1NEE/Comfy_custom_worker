ARG BASE_IMAGE=runpod/pytorch:1.0.3-cu1281-torch271-ubuntu2404

FROM ${BASE_IMAGE}

ARG COMFYUI_VERSION=latest
ARG SKIP_SAGEATTENTION=false
ARG TORCH_CUDA_ARCH_LIST="8.9"

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_PREFER_BINARY=1
ENV PYTHONUNBUFFERED=1
ENV CMAKE_BUILD_PARALLEL_LEVEL=8
ENV PIP_NO_INPUT=1

ENV PYTORCH_ALLOC_CONF=max_split_size_mb:512
ENV TORCH_COMPILE_DEBUG=0
ENV TORCH_CUDNN_BENCHMARK=1
ENV CUDA_LAUNCH_BLOCKING=0
ENV MALLOC_ARENA_MAX=2

ENV TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST}
ENV TRITON_CACHE_DIR=/tmp/triton_cache

# Extra system deps not in runpod base
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# uv (runpod image has pip but not uv)
RUN pip install uv && uv --version

# ComfyUI (install into existing venv, skip PyTorch — already in base image)
RUN pip install comfy-cli \
    && /usr/bin/yes | comfy --workspace /comfyui install --version "${COMFYUI_VERSION}" --skip-torch-check --nvidia

# sageattention — BEFORE custom nodes so it doesn't rebuild on node changes
RUN if [ "$SKIP_SAGEATTENTION" != "true" ]; then \
      timeout 1200 pip install sageattention==2.2.0 --no-build-isolation || \
      echo "sageattention installation failed, continuing without it"; \
    fi

WORKDIR /comfyui

COPY scripts/comfy-node-install.sh /usr/local/bin/comfy-node-install
RUN sed -i 's/\r$//' /usr/local/bin/comfy-node-install && chmod +x /usr/local/bin/comfy-node-install

# All custom nodes in one layer
RUN comfy-node-install \
    ComfyLiterals \
    comfyui-detail-daemon \
    comfyui-easy-use \
    comfyui-florence2 \
    comfyui-frame-interpolation \
    ComfyUI-GGUF \
    ComfyUI-GGUF-FantasyTalking \
    comfyui-impact-pack \
    comfyui-kjnodes \
    ComfyUI-LatentSyncWrapper \
    comfyui-logic \
    ComfyUI-Manager \
    comfyui-rmbg \
    comfyui-segment-anything-2 \
    ComfyUI-VibeVoice \
    comfyui-videohelpersuite \
    ComfyUI-WanAnimatePreprocess \
    ComfyUI-WanVideoWrapper \
    ComfyUI_Comfyroll_CustomNodes \
    comfyui_essentials \
    comfyui_layerstyle \
    ComfyUI_LayerStyle_Advance \
    ComfyUI_JPS-Nodes \
    comfyui_ultimatesdupscale \
    comfyui-custom-scripts \
    comfyui-image-selector \
    rgthree-comfy \
    was-node-suite-comfyui \
    cg-use-everywhere \
    comfy-plasma \
    comfyui_controlnet_aux \
    masquerade-nodes-comfyui \
    mikey_nodes \
    && cd custom_nodes \
    && git clone --depth=1 --no-tags https://github.com/ClownsharkBatwing/RES4LYF.git RES4LYF || true \
    && comfy-node-install teacache || true \
    && git clone --depth=1 --no-tags https://github.com/chrisgoringe/cg-image-picker.git cg-image-picker || true \
    && comfy-node-install havocscall_custom_nodes || true \
    && git clone --depth=1 --no-tags https://github.com/adieyal/comfyui-dynamicprompts.git comfyui-dynamicprompts || true \
    && cd /comfyui/custom_nodes \
    && if [ -d "comfyui-detail-daemon" ] && [ ! -d "ComfyUI-Detail-Daemon" ]; then mv comfyui-detail-daemon ComfyUI-Detail-Daemon; fi \
    && if [ -d "comfyui-easy-use" ] && [ ! -d "ComfyUI-Easy-Use" ]; then mv comfyui-easy-use ComfyUI-Easy-Use; fi \
    && if [ -d "comfyui-florence2" ] && [ ! -d "ComfyUI-Florence2" ]; then mv comfyui-florence2 ComfyUI-Florence2; fi \
    && if [ -d "comfyui-frame-interpolation" ] && [ ! -d "ComfyUI-Frame-Interpolation" ]; then mv comfyui-frame-interpolation ComfyUI-Frame-Interpolation; fi \
    && if [ -d "comfyui-impact-pack" ] && [ ! -d "ComfyUI-Impact-Pack" ]; then mv comfyui-impact-pack ComfyUI-Impact-Pack; fi \
    && if [ -d "comfyui-kjnodes" ] && [ ! -d "ComfyUI-KJNodes" ]; then mv comfyui-kjnodes ComfyUI-KJNodes; fi \
    && if [ -d "comfyui-logic" ] && [ ! -d "ComfyUI-Logic" ]; then mv comfyui-logic ComfyUI-Logic; fi \
    && if [ -d "comfyui-rmbg" ] && [ ! -d "ComfyUI-RMBG" ]; then mv comfyui-rmbg ComfyUI-RMBG; fi \
    && if [ -d "comfyui-segment-anything-2" ] && [ ! -d "ComfyUI-segment-anything-2" ]; then mv comfyui-segment-anything-2 ComfyUI-segment-anything-2; fi \
    && if [ -d "comfyui-videohelpersuite" ] && [ ! -d "ComfyUI-VideoHelperSuite" ]; then mv comfyui-videohelpersuite ComfyUI-VideoHelperSuite; fi \
    && if [ -d "comfyui_layerstyle" ] && [ ! -d "ComfyUI_LayerStyle" ]; then mv comfyui_layerstyle ComfyUI_LayerStyle; fi \
    && if [ -d "comfyui_ultimatesdupscale" ] && [ ! -d "ComfyUI_UltimateSDUpscale" ]; then mv comfyui_ultimatesdupscale ComfyUI_UltimateSDUpscale; fi \
    && if [ -d "comfyui_essentials" ] && [ ! -d "ComfyUI_essentials" ]; then mv comfyui_essentials ComfyUI_essentials; fi \
    && if [ -d "havocscall_custom_nodes" ] && [ ! -d "comfyui_HavocsCall_Custom_Nodes" ]; then mv havocscall_custom_nodes comfyui_HavocsCall_Custom_Nodes; fi \
    && if [ -d "teacache" ] && [ ! -d "ComfyUI-TeaCache" ]; then mv teacache ComfyUI-TeaCache; fi

# Project custom nodes + their deps
COPY --chown=root:root custom_nodes custom_nodes
RUN find custom_nodes -name "requirements.txt" -type f 2>/dev/null | \
      xargs -I {} pip install -r {} || true

WORKDIR /

# Handler dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Application files (most frequently changed — last for max cache hits)
COPY watermark.png /opt/watermark.png
COPY scripts/comfy-manager-set-mode.sh /usr/local/bin/comfy-manager-set-mode
RUN sed -i 's/\r$//' /usr/local/bin/comfy-manager-set-mode && chmod +x /usr/local/bin/comfy-manager-set-mode
ADD src/start.sh src/optimize_pytorch.py handler.py test_input.json ./
RUN sed -i 's/\r$//' /start.sh && chmod +x /start.sh && chmod +x /optimize_pytorch.py

CMD ["/start.sh"]
