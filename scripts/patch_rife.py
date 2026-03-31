import os

nodes_dir = "/comfyui/custom_nodes"
d = next((x for x in os.listdir(nodes_dir) if x.lower() == "comfyui-frame-interpolation"), None)
if not d:
    print("ComfyUI-Frame-Interpolation not found, skipping patch")
    exit(0)

f = os.path.join(nodes_dir, d, "__init__.py")
c = open(f).read()

if "RIFEInterpolation" in c:
    print("RIFEInterpolation already patched, skipping")
    exit(0)

patch = """

class _RIFEInterpolationCompat:
    @classmethod
    def INPUT_TYPES(s):
        from vfi_models.rife import RIFE_VFI
        t = RIFE_VFI.INPUT_TYPES(s)
        req = t.get("required", {})
        opt = t.get("optional", {})
        frames = req.pop("frames", ("IMAGE",))
        opt.update(req)
        return {"required": {"frames": frames}, "optional": opt}

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "vfi"
    CATEGORY = "ComfyUI-Frame-Interpolation/VFI"

    def vfi(self, frames, **kwargs):
        from vfi_models.rife import RIFE_VFI
        node = RIFE_VFI()
        kw = {
            "ckpt_name": "rife49.pth",
            "clear_cache_after_n_frames": 10,
            "multiplier": 2,
            "fast_mode": True,
            "ensemble": True,
            "scale_factor": 1.0,
            "dtype": "float32",
            "torch_compile": False,
        }
        kw.update(kwargs)
        return node.vfi(frames=frames, **kw)

NODE_CLASS_MAPPINGS["RIFEInterpolation"] = _RIFEInterpolationCompat
"""

with open(f, "a") as fp:
    fp.write(patch)

print(f"Patched {f} with RIFEInterpolation compat class")
