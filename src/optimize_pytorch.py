# -*- coding: utf-8 -*-
import gc
import os
import torch


def _log(msg: str) -> None:
    print(f"optimize_pytorch: {msg}", flush=True)


def apply() -> None:
    if not torch.cuda.is_available():
        _log("CUDA not available — skipping")
        return

    props = torch.cuda.get_device_properties(0)
    vram_gb: float = props.total_memory / (1024 ** 3)
    gpu_name: str = props.name
    sm_major: int = props.major
    sm_minor: int = props.minor
    compute: str = f"sm_{sm_major}{sm_minor}"

    _log(f"GPU: {gpu_name} | VRAM: {vram_gb:.1f} GB | Compute: {compute}")

    # TF32 — быстрее matmul с минимальной потерей точности (Ampere+)
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    _log("TF32 enabled (matmul + cuDNN)")

    # cuDNN autotuning
    torch.backends.cudnn.benchmark = True
    torch.backends.cudnn.deterministic = False
    _log("cuDNN benchmark enabled")

    # Flash Attention
    if hasattr(torch.backends.cuda, "enable_flash_sdp"):
        torch.backends.cuda.enable_flash_sdp(True)
        _log("Flash SDP enabled")

    # Отключаем math SDP и mem-efficient SDP — Flash быстрее и экономнее
    if hasattr(torch.backends.cuda, "enable_math_sdp"):
        torch.backends.cuda.enable_math_sdp(False)
    if hasattr(torch.backends.cuda, "enable_mem_efficient_sdp"):
        torch.backends.cuda.enable_mem_efficient_sdp(False)

    # BF16 reduced precision (Ampere+ sm_80, Blackwell sm_100)
    if sm_major >= 8 and hasattr(torch.backends.cuda, "matmul"):
        if hasattr(torch.backends.cuda.matmul, "allow_bf16_reduced_precision_reduction"):
            torch.backends.cuda.matmul.allow_bf16_reduced_precision_reduction = True
            _log("BF16 reduced precision reduction enabled")

    # FP16 accumulation reduction (Hopper/Blackwell — sm_90+)
    if sm_major >= 9 and hasattr(torch.backends.cuda, "matmul"):
        if hasattr(torch.backends.cuda.matmul, "allow_fp16_reduced_precision_reduction"):
            torch.backends.cuda.matmul.allow_fp16_reduced_precision_reduction = True
            _log("FP16 reduced precision reduction enabled (sm_90+)")

    # Для видео-генерации важно освободить весь мусор до старта
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()

    free_b, total_b = torch.cuda.mem_get_info(0)
    free_gb = free_b / (1024 ** 3)
    _log(f"VRAM free at startup: {free_gb:.1f} / {vram_gb:.1f} GB")
    _log(f"Done — optimizations applied for {gpu_name}")


if __name__ == "__main__":
    apply()
