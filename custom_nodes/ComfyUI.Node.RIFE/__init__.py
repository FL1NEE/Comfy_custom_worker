from .comfyui_node import RIFE, MakeInterpolationStateList

# ComfyUI Node Mappings
NODE_CLASS_MAPPINGS = {
    "RIFE": RIFE,
    "MakeInterpolationStateList": MakeInterpolationStateList,
}

# Human-readable node display names
NODE_DISPLAY_NAME_MAPPINGS = {
    "RIFE": "RIFE Video Interpolation",
    "MakeInterpolationStateList": "Make Interpolation State List",
}

# Export version and metadata
__version__ = "1.0.0"

# Additional exports for introspection
__all__ = [
    'RIFE',
    'NODE_CLASS_MAPPINGS', 
    'NODE_DISPLAY_NAME_MAPPINGS'
] 
