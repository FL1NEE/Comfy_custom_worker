ARG BASE_IMAGE=ghcr.io/fl1nee/comfy_custom_worker:base-latest

FROM ${BASE_IMAGE}

# Кастомные ноды проекта + их зависимости
COPY --chown=root:root custom_nodes custom_nodes
RUN find custom_nodes -name "requirements.txt" -type f 2>/dev/null | \
      xargs -I {} pip install --no-cache-dir -r {} \
    && pip cache purge 2>/dev/null || true

# Зависимости handler'а
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && pip cache purge 2>/dev/null || true

# Файлы приложения (меняются чаще всего — последний слой для максимального cache hit)
COPY watermark.png /opt/watermark.png
COPY src/start.sh src/optimize_pytorch.py handler.py ./
RUN sed -i 's/\r$//' /start.sh && chmod +x /start.sh

CMD ["/start.sh"]
