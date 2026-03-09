# -*- coding: utf-8 -*-
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

    # TF32 — быстрее matmul с минимальной потерей точности (Ampere+, работает на Blackwell)
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    _log("TF32 enabled (matmul + cuDNN)")

    # cuDNN autotuning — выбирает быстрейший алгоритм под фиксированный размер входа
    torch.backends.cudnn.benchmark = True
    torch.backends.cudnn.deterministic = False
    _log("cuDNN benchmark enabled")

    # Flash Attention (если собрано в данном билде PyTorch)
    if hasattr(torch.backends.cuda, "enable_flash_sdp"):
        torch.backends.cuda.enable_flash_sdp(True)
        _log("Flash SDP enabled")

    # BF16 нативный формат на Blackwell (sm_100) — особенно важно для Wan Q8
    if sm_major >= 8 and hasattr(torch.backends.cuda, "matmul"):
        if hasattr(torch.backends.cuda.matmul, "allow_bf16_reduced_precision_reduction"):
            torch.backends.cuda.matmul.allow_bf16_reduced_precision_reduction = True
            _log("BF16 reduced precision reduction enabled")

    torch.cuda.empty_cache()
    _log(f"Done — optimizations applied for {gpu_name}")


if __name__ == "__main__":
    apply()
