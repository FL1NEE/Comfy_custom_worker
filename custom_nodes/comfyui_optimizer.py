# -*- coding: utf-8 -*-
# Тонкая обёртка: запускает optimize_pytorch внутри процесса ComfyUI,
# чтобы torch.backends настройки реально применились к инференсу.
import sys

sys.path.insert(0, "/")

try:
    import optimize_pytorch
    optimize_pytorch.apply()
except Exception as e:
    print(f"comfyui_optimizer: failed to apply optimizations: {e}", flush=True)

NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}
