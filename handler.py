# -*- coding: utf-8 -*-
import runpod
import json
import urllib.parse
import time
import os
import requests
import base64
from io import BytesIO
import websocket
import uuid
import tempfile
import socket
import traceback
import subprocess
import boto3
from botocore.config import Config as BotoConfig

COMFY_API_AVAILABLE_INTERVAL_MS: int = 50
COMFY_API_AVAILABLE_MAX_RETRIES: int = 500
WEBSOCKET_RECONNECT_ATTEMPTS = int(os.environ.get("WEBSOCKET_RECONNECT_ATTEMPTS", 5))
WEBSOCKET_RECONNECT_DELAY_S = int(os.environ.get("WEBSOCKET_RECONNECT_DELAY_S", 3))
COMFY_HOST: str = "127.0.0.1:8188"
REFRESH_WORKER: bool = os.environ.get("REFRESH_WORKER", "false").lower() == "true"
WATERMARK_PATH: str = "/opt/watermark.png"

if os.environ.get("WEBSOCKET_TRACE", "false").lower() == "true":
    websocket.enableTrace(True)

# S3 client
_s3_endpoint: str = os.environ.get("BUCKET_ENDPOINT_URL")
_s3_access_key: str = os.environ.get("BUCKET_ACCESS_KEY_ID")
_s3_secret_key: str = os.environ.get("BUCKET_SECRET_ACCESS_KEY")
S3_BUCKET_NAME: str = os.environ.get("BUCKET_NAME", "comfyui-outputs")

s3_client: boto3.client = None
if _s3_endpoint is not None and _s3_access_key is not None and _s3_secret_key is not None:
    s3_client = boto3.client(
        "s3",
        endpoint_url=_s3_endpoint,
        aws_access_key_id=_s3_access_key,
        aws_secret_access_key=_s3_secret_key,
        config=BotoConfig(signature_version="s3v4"),
    )
    print(f"worker-comfyui - S3 client initialized (endpoint: {_s3_endpoint}, bucket: {S3_BUCKET_NAME})")
else:
    print("worker-comfyui - S3 client not initialized (missing BUCKET_ENDPOINT_URL, BUCKET_ACCESS_KEY_ID, or BUCKET_SECRET_ACCESS_KEY)")


def _comfy_server_status():
    try:
        resp: requests.Response = requests.get(f"http://{COMFY_HOST}/", timeout=5)
        return {"reachable": resp.status_code == 200, "status_code": resp.status_code}
    except Exception as exc:
        return {"reachable": False, "error": str(exc)}


def _attempt_websocket_reconnect(ws_url, max_attempts, delay_s, initial_error):
    print(f"worker-comfyui - Websocket closed: {initial_error}. Reconnecting...")
    last_error = initial_error
    for attempt in range(max_attempts):
        srv_status = _comfy_server_status()
        if not srv_status["reachable"]:
            print(f"worker-comfyui - ComfyUI HTTP unreachable, aborting reconnect")
            raise websocket.WebSocketConnectionClosedException(
                "ComfyUI HTTP unreachable during websocket reconnect"
            )
        print(f"worker-comfyui - Reconnect attempt {attempt + 1}/{max_attempts}...")
        try:
            new_ws = websocket.WebSocket()
            new_ws.connect(ws_url, timeout=10)
            print(f"worker-comfyui - Websocket reconnected")
            return new_ws
        except (websocket.WebSocketException, ConnectionRefusedError, socket.timeout, OSError) as e:
            last_error = e
            print(f"worker-comfyui - Reconnect attempt {attempt + 1} failed: {e}")
            if attempt < max_attempts - 1:
                time.sleep(delay_s)

    raise websocket.WebSocketConnectionClosedException(
        f"Failed to reconnect. Last error: {last_error}"
    )


def validate_input(job_input):
    if job_input is None:
        return None, "Please provide input"

    if isinstance(job_input, str):
        try:
            job_input = json.loads(job_input)
        except json.JSONDecodeError:
            return None, "Invalid JSON format in input"

    workflow = job_input.get("workflow")
    if workflow is None:
        return None, "Missing 'workflow' parameter"

    images = job_input.get("images")
    if images is not None:
        if not isinstance(images, list) or not all(
            "name" in image and "image" in image for image in images
        ):
            return None, "'images' must be a list of objects with 'name' and 'image' keys"

    return {
        "workflow": workflow,
        "images": images,
        "comfy_org_api_key": job_input.get("comfy_org_api_key"),
    }, None


def check_server(url, retries=500, delay=50):
    print(f"worker-comfyui - Checking API server at {url}...")
    for i in range(retries):
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                print(f"worker-comfyui - API is reachable")
                return True
        except requests.RequestException:
            pass
        time.sleep(delay / 1000)

    print(f"worker-comfyui - Failed to connect to server at {url} after {retries} attempts.")
    return False


def upload_images(images):
    if not images:
        return {"status": "success", "message": "No images to upload", "details": []}

    responses = []
    upload_errors = []
    print(f"worker-comfyui - Uploading {len(images)} image(s)...")

    for image in images:
        try:
            name = image["name"]
            image_data_uri = image["image"]
            base64_data = image_data_uri.split(",", 1)[1] if "," in image_data_uri else image_data_uri
            blob = base64.b64decode(base64_data)

            files = {
                "image": (name, BytesIO(blob), "image/png"),
                "overwrite": (None, "true"),
            }
            response = requests.post(f"http://{COMFY_HOST}/upload/image", files=files, timeout=30)
            response.raise_for_status()
            responses.append(f"Successfully uploaded {name}")
            print(f"worker-comfyui - Successfully uploaded {name}")
        except Exception as e:
            error_msg = f"Error uploading {image.get('name', 'unknown')}: {e}"
            print(f"worker-comfyui - {error_msg}")
            upload_errors.append(error_msg)

    if upload_errors:
        return {"status": "error", "message": "Some images failed to upload", "details": upload_errors}
    return {"status": "success", "message": "All images uploaded successfully", "details": responses}


def queue_workflow(workflow, client_id, comfy_org_api_key=None):
    payload = {"prompt": workflow, "client_id": client_id}

    key_from_env = os.environ.get("COMFY_ORG_API_KEY")
    effective_key = comfy_org_api_key if comfy_org_api_key else key_from_env
    if effective_key:
        payload["extra_data"] = {"api_key_comfy_org": effective_key}

    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    response = requests.post(f"http://{COMFY_HOST}/prompt", data=data, headers=headers, timeout=30)

    if response.status_code == 400:
        print(f"worker-comfyui - ComfyUI returned 400: {response.text}")
        try:
            error_data = response.json()
            error_message = "Workflow validation failed"
            error_details = []

            if "error" in error_data:
                error_info = error_data["error"]
                if isinstance(error_info, dict):
                    error_message = error_info.get("message", error_message)
                else:
                    error_message = str(error_info)

            if "node_errors" in error_data:
                for node_id, node_error in error_data["node_errors"].items():
                    if isinstance(node_error, dict):
                        for error_type, error_msg in node_error.items():
                            error_details.append(f"Node {node_id} ({error_type}): {error_msg}")
                    else:
                        error_details.append(f"Node {node_id}: {node_error}")

            if error_details:
                raise ValueError(f"{error_message}:\n" + "\n".join(f"• {d}" for d in error_details))
            else:
                raise ValueError(f"{error_message}. Raw response: {response.text}")

        except (json.JSONDecodeError, KeyError):
            raise ValueError(f"ComfyUI validation failed: {response.text}")

    response.raise_for_status()
    return response.json()


def get_history(prompt_id):
    response = requests.get(f"http://{COMFY_HOST}/history/{prompt_id}", timeout=30)
    response.raise_for_status()
    return response.json()


def get_video_data(filename, subfolder, file_type):
    print(f"worker-comfyui - Fetching video: type={file_type}, subfolder={subfolder}, filename={filename}")
    url_values = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": file_type})
    try:
        response = requests.get(f"http://{COMFY_HOST}/view?{url_values}", timeout=60)
        response.raise_for_status()
        print(f"worker-comfyui - Fetched {filename} ({len(response.content)} bytes)")
        return response.content
    except Exception as e:
        print(f"worker-comfyui - Error fetching {filename}: {e}")
        return None


def apply_watermark(input_path, output_path):
    if not os.path.isfile(WATERMARK_PATH):
        print(f"worker-comfyui - Watermark not found at {WATERMARK_PATH}, skipping")
        return False

    interval = 1.25
    opacity = 0.6
    scale_factor = 0.08

    x_expr = (
        f"if(eq(mod(floor(t/{interval:.2f}),4),0),15,"
        f"if(eq(mod(floor(t/{interval:.2f}),4),1),W-w-15,"
        f"if(eq(mod(floor(t/{interval:.2f}),4),2),W-w-15,"
        f"15)))"
    )
    y_expr = (
        f"if(eq(mod(floor(t/{interval:.2f}),4),0),15,"
        f"if(eq(mod(floor(t/{interval:.2f}),4),1),15,"
        f"if(eq(mod(floor(t/{interval:.2f}),4),2),H-h-15,"
        f"H-h-15)))"
    )

    filter_complex = (
        f"[1:v]scale=-1:ih*{scale_factor:.2f},format=rgba,"
        f"colorchannelmixer=aa={opacity}[wm];"
        f"[0:v][wm]overlay=x='{x_expr}':y='{y_expr}':shortest=1[outv]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-loop", "1",
        "-i", WATERMARK_PATH,
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "2:a",
        "-c:v", "libx264",
        "-preset", "faster",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        output_path,
    ]

    print(f"worker-comfyui - Applying watermark: {input_path} -> {output_path}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"worker-comfyui - FFmpeg failed (exit {result.returncode}): {result.stderr[-500:]}")
            return False
        print(f"worker-comfyui - Watermark applied")
        return True
    except subprocess.TimeoutExpired:
        print(f"worker-comfyui - FFmpeg timed out after 300s")
        return False
    except Exception as e:
        print(f"worker-comfyui - Watermark error: {e}")
        return False


def upload_video_to_s3(video_bytes, filename, job_id):
    if not s3_client:
        raise RuntimeError("S3 not configured. Set BUCKET_ENDPOINT_URL, BUCKET_ACCESS_KEY_ID, BUCKET_SECRET_ACCESS_KEY.")

    ext = os.path.splitext(filename)[1].lower()
    tmp_in_path = None
    tmp_out_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp_in:
            tmp_in.write(video_bytes)
            tmp_in_path = tmp_in.name
        tmp_out_path = tmp_in_path + "_wm" + ext
        if apply_watermark(tmp_in_path, tmp_out_path):
            with open(tmp_out_path, "rb") as f:
                video_bytes = f.read()
            print(f"worker-comfyui - Watermarked video ({len(video_bytes)} bytes)")
        else:
            print(f"worker-comfyui - Proceeding without watermark")
    except Exception as e:
        print(f"worker-comfyui - Watermark step failed: {e}")
    finally:
        for p in [tmp_in_path, tmp_out_path]:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    s3_key = f"outputs/{job_id}/{filename}"
    content_types = {
        ".mp4": "video/mp4", ".webm": "video/webm", ".avi": "video/x-msvideo",
        ".mov": "video/quicktime", ".mkv": "video/x-matroska",
    }

    s3_client.put_object(
        Bucket=S3_BUCKET_NAME, Key=s3_key, Body=video_bytes,
        ContentType=content_types.get(ext, "application/octet-stream"),
    )
    print(f"worker-comfyui - Uploaded to S3: {S3_BUCKET_NAME}/{s3_key}")
    return s3_key


def handler(job):
    job_input = job["input"]
    job_id = job["id"]

    validated_data, error_message = validate_input(job_input)
    if error_message:
        return {"error": error_message}

    workflow = validated_data["workflow"]
    input_images = validated_data.get("images")

    if not check_server(f"http://{COMFY_HOST}/", COMFY_API_AVAILABLE_MAX_RETRIES, COMFY_API_AVAILABLE_INTERVAL_MS):
        return {"error": f"ComfyUI server ({COMFY_HOST}) not reachable after multiple retries."}

    if input_images:
        upload_result = upload_images(input_images)
        if upload_result["status"] == "error":
            return {"error": "Failed to upload input images", "details": upload_result["details"]}

    ws = None
    client_id = str(uuid.uuid4())
    prompt_id = None
    output_data = []
    errors = []

    try:
        ws_url = f"ws://{COMFY_HOST}/ws?clientId={client_id}"
        print(f"worker-comfyui - Connecting to websocket: {ws_url}")
        ws = websocket.WebSocket()
        ws.connect(ws_url, timeout=10)
        print(f"worker-comfyui - Websocket connected")

        try:
            queued_workflow = queue_workflow(
                workflow, client_id,
                comfy_org_api_key=validated_data.get("comfy_org_api_key"),
            )
            prompt_id = queued_workflow.get("prompt_id")
            if not prompt_id:
                raise ValueError(f"Missing 'prompt_id' in queue response: {queued_workflow}")
            print(f"worker-comfyui - Queued workflow: {prompt_id}")
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Error queuing workflow: {e}")

        print(f"worker-comfyui - Waiting for execution ({prompt_id})...")
        execution_done = False
        while True:
            try:
                out = ws.recv()
                if not isinstance(out, str):
                    continue
                message = json.loads(out)
                msg_type = message.get("type")

                if msg_type == "executing":
                    data = message.get("data", {})
                    if data.get("node") is None and data.get("prompt_id") == prompt_id:
                        print(f"worker-comfyui - Execution finished for {prompt_id}")
                        execution_done = True
                        break
                elif msg_type == "execution_error":
                    data = message.get("data", {})
                    if data.get("prompt_id") == prompt_id:
                        error_details = f"Node {data.get('node_type')} ({data.get('node_id')}): {data.get('exception_message')}"
                        print(f"worker-comfyui - Execution error: {error_details}")
                        errors.append(f"Workflow execution error: {error_details}")
                        break

            except websocket.WebSocketTimeoutException:
                continue
            except websocket.WebSocketConnectionClosedException as closed_err:
                try:
                    ws = _attempt_websocket_reconnect(
                        ws_url, WEBSOCKET_RECONNECT_ATTEMPTS, WEBSOCKET_RECONNECT_DELAY_S, closed_err,
                    )
                    continue
                except websocket.WebSocketConnectionClosedException:
                    raise
            except json.JSONDecodeError:
                print(f"worker-comfyui - Invalid JSON from websocket")

        if not execution_done and not errors:
            raise ValueError("Workflow loop exited without completion or error.")

        print(f"worker-comfyui - Fetching history for {prompt_id}...")
        history = get_history(prompt_id)

        if prompt_id not in history:
            error_msg = f"Prompt {prompt_id} not found in history."
            print(f"worker-comfyui - {error_msg}")
            return {"error": error_msg, "details": errors} if errors else {"error": error_msg}

        outputs = history[prompt_id].get("outputs", {})
        if not outputs and not errors:
            errors.append(f"No outputs for prompt {prompt_id}.")

        print(f"worker-comfyui - Processing {len(outputs)} output nodes...")
        for node_id, node_output in outputs.items():
            output_items = node_output.get("images") or node_output.get("gifs") or []
            if not output_items:
                continue

            for item in output_items:
                filename = item.get("filename")
                if not filename or item.get("type") == "temp":
                    continue

                video_bytes = get_video_data(filename, item.get("subfolder", ""), item.get("type"))
                if video_bytes:
                    try:
                        s3_key = upload_video_to_s3(video_bytes, filename, job_id)
                        output_data.append({"filename": filename, "type": "path", "data": s3_key})
                        print(f"worker-comfyui - Video uploaded: {s3_key}")
                    except Exception as e:
                        error_msg = f"Failed to upload {filename} to S3: {e}"
                        print(f"worker-comfyui - {error_msg}")
                        errors.append(error_msg)
                else:
                    errors.append(f"Failed to fetch {filename} from /view endpoint.")

    except websocket.WebSocketException as e:
        print(f"worker-comfyui - WebSocket Error: {e}\n{traceback.format_exc()}")
        return {"error": f"WebSocket error: {e}"}
    except requests.RequestException as e:
        print(f"worker-comfyui - HTTP Error: {e}\n{traceback.format_exc()}")
        return {"error": f"HTTP error: {e}"}
    except ValueError as e:
        print(f"worker-comfyui - {e}\n{traceback.format_exc()}")
        return {"error": str(e)}
    except Exception as e:
        print(f"worker-comfyui - Unexpected error: {e}\n{traceback.format_exc()}")
        return {"error": f"Unexpected error: {e}"}
    finally:
        if ws and ws.connected:
            ws.close()

    if not output_data and errors:
        return {"error": "Job failed", "details": errors}
    if not output_data:
        return {"status": "success_no_output", "images": []}

    result = {"images": output_data}
    if errors:
        result["errors"] = errors
    print(f"worker-comfyui - Done. {len(output_data)} video(s).")
    return result


if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
