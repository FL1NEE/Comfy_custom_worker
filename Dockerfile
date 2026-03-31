ARG BASE_IMAGE=catcoderr/comfy-worker

FROM ${BASE_IMAGE}

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && pip cache purge 2>/dev/null || true

RUN cd /comfyui/custom_nodes \
    && git clone --depth=1 --no-tags https://github.com/vrgamegirl19/comfyui-vrgamedevgirl.git comfyui-vrgamedevgirl \
    && git clone --depth=1 --no-tags https://github.com/princepainter/ComfyUI-PainterI2Vadvanced.git ComfyUI-PainterI2Vadvanced \
    && python3 -c "f='ComfyUI-Frame-Interpolation/__init__.py'; c=open(f).read(); open(f,'a').write('\nNODE_CLASS_MAPPINGS[\"RIFEInterpolation\"] = NODE_CLASS_MAPPINGS[\"RIFE VFI\"]\n') if 'RIFEInterpolation' not in c else None" \
    && find . -name ".git" -type d -exec rm -rf {} + 2>/dev/null || true

COPY watermark.png /opt/watermark.png
COPY scripts/comfy-manager-set-mode.sh /usr/local/bin/comfy-manager-set-mode
RUN sed -i 's/\r$//' /usr/local/bin/comfy-manager-set-mode && chmod +x /usr/local/bin/comfy-manager-set-mode
COPY src/start.sh src/optimize_pytorch.py handler.py ./
RUN sed -i 's/\r$//' /start.sh && chmod +x /start.sh

CMD ["/start.sh"]
