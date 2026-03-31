ARG BASE_IMAGE=catcoderr/comfy-worker

FROM ${BASE_IMAGE}

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && pip cache purge 2>/dev/null || true

RUN cd /comfyui/custom_nodes \
    && git clone --depth=1 --no-tags https://github.com/vrgamegirl19/comfyui-vrgamedevgirl.git comfyui-vrgamedevgirl \
    && git clone --depth=1 --no-tags https://github.com/princepainter/ComfyUI-PainterI2Vadvanced.git ComfyUI-PainterI2Vadvanced \
    && pip install --no-cache-dir kornia librosa imageio av stable-ts demucs transformers accelerate huggingface_hub google-generativeai torchcodec 2>/dev/null || \
       pip install --no-cache-dir kornia librosa imageio av stable-ts demucs transformers accelerate huggingface_hub google-generativeai \
    && python3 -c "\
import os; \
d = next((x for x in os.listdir('.') if x.lower()=='comfyui-frame-interpolation'), None); \
f = d+'/__init__.py' if d else None; \
c = open(f).read() if f else ''; \
patch = '''
class _RIFEInterpolationCompat:
    @classmethod
    def INPUT_TYPES(s):
        from vfi_models.rife import RIFE_VFI
        t = RIFE_VFI.INPUT_TYPES(s)
        req = t.get(\"required\", {})
        opt = t.get(\"optional\", {})
        frames = req.pop(\"frames\", (\"IMAGE\",))
        opt.update(req)
        return {\"required\": {\"frames\": frames}, \"optional\": opt}
    RETURN_TYPES = (\"IMAGE\",)
    FUNCTION = \"vfi\"
    CATEGORY = \"ComfyUI-Frame-Interpolation/VFI\"
    def vfi(self, frames, **kwargs):
        from vfi_models.rife import RIFE_VFI
        node = RIFE_VFI()
        kw = {\"ckpt_name\": \"rife49.pth\", \"clear_cache_after_n_frames\": 10, \"multiplier\": 2, \"fast_mode\": True, \"ensemble\": True, \"scale_factor\": 1.0, \"dtype\": \"float32\", \"torch_compile\": False}
        kw.update(kwargs)
        return node.vfi(frames=frames, **kw)
NODE_CLASS_MAPPINGS[\"RIFEInterpolation\"] = _RIFEInterpolationCompat
'''; \
open(f,'a').write(patch) if f and 'RIFEInterpolation' not in c else None" \
    && find . -name ".git" -type d -exec rm -rf {} + 2>/dev/null || true

COPY watermark.png /opt/watermark.png
COPY scripts/comfy-manager-set-mode.sh /usr/local/bin/comfy-manager-set-mode
RUN sed -i 's/\r$//' /usr/local/bin/comfy-manager-set-mode && chmod +x /usr/local/bin/comfy-manager-set-mode
COPY src/start.sh src/optimize_pytorch.py handler.py ./
RUN sed -i 's/\r$//' /start.sh && chmod +x /start.sh

CMD ["/start.sh"]
