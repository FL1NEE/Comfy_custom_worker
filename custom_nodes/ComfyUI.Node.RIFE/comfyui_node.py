import torch
import folder_paths
import typing
import einops
import gc
import os
from comfy.model_management import get_torch_device, soft_empty_cache

folder_paths.add_model_folder_path("rife", os.path.join(folder_paths.models_dir, "RIFE"))
DEVICE = get_torch_device()


class InterpolationStateList():
    def __init__(self, frame_indices: typing.List[int], is_skip_list: bool):
        self.frame_indices = frame_indices
        self.is_skip_list = is_skip_list

    def is_frame_skipped(self, frame_index):
        is_frame_in_list = frame_index in self.frame_indices
        return self.is_skip_list and is_frame_in_list or not self.is_skip_list and not is_frame_in_list


class MakeInterpolationStateList:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "frame_indices": ("STRING", {"multiline": True, "default": "1,2,3"}),
                "is_skip_list": ("BOOLEAN", {"default": True},),
            },
        }

    RETURN_TYPES = ("INTERPOLATION_STATES",)
    FUNCTION = "execute"
    CATEGORY = "RIFE"

    def execute(self, frame_indices: str, is_skip_list: bool):
        frame_indices_list = [int(item) for item in frame_indices.split(',')]
        interpolation_state_list = InterpolationStateList(
            frame_indices=frame_indices_list,
            is_skip_list=is_skip_list,
        )
        return (interpolation_state_list,)


class RIFE:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "ckpt_name": (folder_paths.get_filename_list("rife"), ),
                "frames": ("IMAGE", ),
                "clear_cache_after_n_frames": ("INT", {"default": 10, "min": 1, "max": 1000}),
                "multiplier": ("INT", {"default": 2, "min": 1}),
                "fast_mode": ("BOOLEAN", {"default":True}),
                "ensemble": ("BOOLEAN", {"default":True}),
                "scale_factor": ([0.25, 0.5, 1.0, 2.0, 4.0], {"default": 1.0}),
                "rife_version": (["4.0", "4.2", "4.3", "4.5", "4.6", "4.7", "4.10"], {"default": "4.7"})
            },
            "optional": {
                "optional_interpolation_states": ("INTERPOLATION_STATES", )
            }
        }

    RETURN_TYPES = ("IMAGE", )
    FUNCTION = "execute"
    CATEGORY = "RIFE"

    def execute(
        self,
        ckpt_name: typing.AnyStr,
        frames: torch.Tensor,
        clear_cache_after_n_frames = 10,
        multiplier: typing.SupportsInt = 2,
        fast_mode = False,
        ensemble = False,
        scale_factor = 1.0,
        rife_version = "4.7",
        optional_interpolation_states: InterpolationStateList = None,
        **kwargs
    ):
        from .rife_arch import IFNet

        model_path = folder_paths.get_full_path("rife", ckpt_name)
        if model_path is None:
            raise FileNotFoundError(
                f"Model '{ckpt_name}' not found in models/rife folder. "
                f"Please download the model and place it in ComfyUI/models/rife/"
            )

        interpolation_model = IFNet(arch_ver=rife_version)
        interpolation_model.load_state_dict(torch.load(model_path))
        interpolation_model.eval().to(get_torch_device())

        frames = einops.rearrange(frames[..., :3], "n h w c -> n c h w")

        def return_middle_frame(frame_0, frame_1, timestep, model, scale_list, in_fast_mode, in_ensemble):
            return model(frame_0, frame_1, timestep, scale_list, in_fast_mode, in_ensemble)

        scale_list = [8 / scale_factor, 4 / scale_factor, 2 / scale_factor, 1 / scale_factor]

        args = [interpolation_model, scale_list, fast_mode, ensemble]
        out = self.generic_frame_loop(
            type(self).__name__,
            frames,
            clear_cache_after_n_frames,
            multiplier,
            return_middle_frame,
            *args,
            interpolation_states=optional_interpolation_states,
            dtype=torch.float32
        )

        return (einops.rearrange(out, "n c h w -> n h w c")[..., :3].cpu(),)


    @staticmethod
    def _generic_frame_loop(
            frames,
            clear_cache_after_n_frames,
            multiplier: int,
            return_middle_frame_function,
            *return_middle_frame_function_args,
            interpolation_states: InterpolationStateList = None,
            use_timestep=True,
            dtype=torch.float16,
            final_logging=True):

        def non_timestep_inference(frame0, frame1, n):
            middle = return_middle_frame_function(frame0, frame1, None, *return_middle_frame_function_args)
            if n == 1:
                return [middle]
            first_half = non_timestep_inference(frame0, middle, n=n//2)
            second_half = non_timestep_inference(middle, frame1, n=n//2)
            if n%2:
                return [*first_half, middle, *second_half]
            else:
                return [*first_half, *second_half]

        output_frames = torch.zeros(multiplier*frames.shape[0], *frames.shape[1:], dtype=dtype, device="cpu")
        out_len = 0
        number_of_frames_processed_since_last_cleared_cuda_cache = 0

        for frame_itr in range(len(frames) - 1):
            frame0 = frames[frame_itr:frame_itr+1]
            output_frames[out_len] = frame0
            out_len += 1
            frame0 = frame0.to(dtype=torch.float32)
            frame1 = frames[frame_itr+1:frame_itr+2].to(dtype=torch.float32)

            if interpolation_states is not None and interpolation_states.is_frame_skipped(frame_itr):
                continue

            middle_frame_batches = []

            if use_timestep:
                for middle_i in range(1, multiplier):
                    timestep = middle_i/multiplier
                    middle_frame = return_middle_frame_function(
                        frame0.to(DEVICE),
                        frame1.to(DEVICE),
                        timestep,
                        *return_middle_frame_function_args
                    ).detach().cpu()
                    middle_frame_batches.append(middle_frame.to(dtype=dtype))
            else:
                middle_frames = non_timestep_inference(frame0.to(DEVICE), frame1.to(DEVICE), multiplier - 1)
                middle_frame_batches.extend(torch.cat(middle_frames, dim=0).detach().cpu().to(dtype=dtype))

            for middle_frame in middle_frame_batches:
                output_frames[out_len] = middle_frame
                out_len += 1

            number_of_frames_processed_since_last_cleared_cuda_cache += 1
            if number_of_frames_processed_since_last_cleared_cuda_cache >= clear_cache_after_n_frames:
                print("RIFE: Clearing cache...", end=' ')
                soft_empty_cache()
                number_of_frames_processed_since_last_cleared_cuda_cache = 0
                print("Done cache clearing")

            gc.collect()

        if final_logging:
            print(f"RIFE done! {len(output_frames)} frames generated at resolution: {output_frames[0].shape}")
        output_frames[out_len] = frames[-1:]
        out_len += 1
        if final_logging:
            print("RIFE: Final clearing cache...", end = ' ')
        soft_empty_cache()
        if final_logging:
            print("Done cache clearing")
        return output_frames[:out_len]

    @staticmethod
    def generic_frame_loop(
            model_name,
            frames,
            clear_cache_after_n_frames,
            multiplier: typing.Union[typing.SupportsInt, typing.List],
            return_middle_frame_function,
            *return_middle_frame_function_args,
            interpolation_states: InterpolationStateList = None,
            use_timestep=True,
            dtype=torch.float32):

        assert len(frames) >= 2, f"RIFE model {model_name} requires at least 2 frames to work with, only found {frames.shape[0]}. Please check the frame input using PreviewImage."

        if type(multiplier) == int:
            return RIFE._generic_frame_loop(
                frames,
                clear_cache_after_n_frames,
                multiplier,
                return_middle_frame_function,
                *return_middle_frame_function_args,
                interpolation_states=interpolation_states,
                use_timestep=use_timestep,
                dtype=dtype
            )
        if type(multiplier) == list:
            multipliers = list(map(int, multiplier))
            multipliers += [2] * (len(frames) - len(multipliers) - 1)
            frame_batches = []
            for frame_itr in range(len(frames) - 1):
                multiplier = multipliers[frame_itr]
                if multiplier == 0: continue
                frame_batch = RIFE._generic_frame_loop(
                    frames[frame_itr:frame_itr+2],
                    clear_cache_after_n_frames,
                    multiplier,
                    return_middle_frame_function,
                    *return_middle_frame_function_args,
                    interpolation_states=interpolation_states,
                    use_timestep=use_timestep,
                    dtype=dtype,
                    final_logging=False
                )
                if frame_itr != len(frames) - 2:
                    frame_batch = frame_batch[:-1]
                frame_batches.append(frame_batch)
            output_frames = torch.cat(frame_batches)
            print(f"RIFE done! {len(output_frames)} frames generated at resolution: {output_frames[0].shape}")
            return output_frames
        raise NotImplementedError(f"multipiler of {type(multiplier)}")