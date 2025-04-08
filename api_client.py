import json
import base64
import asyncio
import time
import os
from io import BytesIO
from typing import Optional, Dict, Any, List, Tuple
import uuid
import aiohttp
import aiofiles
from PIL import Image
from loguru import logger
import re
import random
import string
from PIL import UnidentifiedImageError

class KolorsVirtualTryOnClient:
    """Kolors虚拟试衣API客户端"""
    
    def __init__(self, 
                 base_url: str, # Base URL of the Gradio app
                 studio_token: str,
                 cookie_string: str,
                 proxy: Optional[str] = None,
                 timeout: int = 60):
        """初始化客户端

        Args:
            base_url: Gradio 应用的基础 URL (例如: https://kwai-kolors-kolors-virtual-try-on.ms.show).
                      重要: 不应包含 /run/predict 或其他子路径.
            studio_token: Studio Token.
            cookie_string: Cookie 字符串.
            proxy: 代理地址.
            timeout: 请求超时时间(秒).
        """
        # Store the base URL without trailing slashes
        self.base_url = base_url.rstrip('/')
        # Derive other URLs from the base URL (common Gradio pattern)
        self.upload_url = f"{self.base_url}/upload"
        self.predict_url = f"{self.base_url}/run/predict"
        self.queue_join_url = f"{self.base_url}/queue/join" # May not be used directly
        self.queue_data_url = f"{self.base_url}/queue/data"
        self.api_status_url = f"{self.base_url}/info" # Often /info or /status, adjust if needed

        self.studio_token = studio_token
        self.cookie_string = cookie_string
        self.proxy = proxy
        self.timeout = timeout
        self.session: Optional[aiohttp.ClientSession] = None
        self.session_hash: Optional[str] = None # Initialize session_hash
        
        # 创建临时目录
        self.save_dir = "resource/KolorsVirtualTryOn"
        os.makedirs(self.save_dir, exist_ok=True)
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        if self.session:
            await self.session.close()
            self.session = None
    
    def _get_params(self) -> Dict[str, str]:
        """获取通用请求参数"""
        # timestamp = int(time.time() * 1000) # Often not needed for Gradio API
        # Return only necessary params, studio_token is usually in headers/cookies
        return {
            # "t": str(timestamp),
            # "__theme": "light",
            # "studio_token": self.studio_token, # Usually not needed as query param
            # "backend_url": "%2F" # Usually not needed
        }
    
    def _get_headers(self, content_type: Optional[str] = "application/json") -> Dict[str, str]:
        """获取通用请求头 (允许指定 Content-Type, None to omit)"""
        headers = {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            # Content-Type is handled carefully below
            "origin": self.base_url, # Dynamically set origin
            "referer": f"{self.base_url}/", # Dynamically set referer
            "sec-ch-ua": "\"Chromium\";v=\"134\", \"Not:A-Brand\";v=\"24\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
            "x-studio-token": self.studio_token, # Include x-studio-token if required
        }
        if content_type:
            headers["content-type"] = content_type

        if self.cookie_string: # Prioritize provided cookie string
            headers["Cookie"] = self.cookie_string
        elif self.studio_token: # Fallback to setting studio_token cookie
             headers["Cookie"] = f"studio_token={self.studio_token}"
            
        return headers
    
    async def _ensure_session(self):
        """Ensures the aiohttp session is active."""
        if not self.session or self.session.closed:
             logger.warning("aiohttp session was closed or not initialized. Creating a new one.")
             self.session = aiohttp.ClientSession()

    async def _prepare_image(self, image_path: str) -> str:
        """准备图像，返回Base64编码

        Args:
            image_path: 图像路径

        Returns:
            str: Base64编码的图像
        """
        async with aiofiles.open(image_path, "rb") as f:
            image_data = await f.read()
            
        # 压缩图像以减小体积
        img = Image.open(BytesIO(image_data))
        
        # 检查图像大小，如果太大则调整大小
        max_size = 1024  # 最大宽度或高度
        if img.width > max_size or img.height > max_size:
            ratio = min(max_size / img.width, max_size / img.height)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)
        
        # 保存为JPEG，控制质量
        output = BytesIO()
        img.save(output, format="JPEG", quality=85)
        image_data = output.getvalue()
        
        # 返回Base64编码
        return f"data:image/jpeg;base64,{base64.b64encode(image_data).decode('utf-8')}"

    async def _upload_file_multipart(self, image_path: str, image_type: str) -> Optional[Dict[str, Any]]:
        """
        Uploads a file using multipart/form-data to the /upload endpoint. (Async)

        Args:
            image_path: Path to the image file.
            image_type: 'person' or 'clothing'.

        Returns:
            The file info dictionary (containing path, name, url etc.) needed for \
            the /run/predict payload if successful, otherwise None.
        """
        await self._ensure_session()
        # Generate a random upload_id (assuming client-side generation)
        upload_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        # Use the correctly derived upload_url
        request_url = f"{self.upload_url}?upload_id={upload_id}"

        logger.info(f"[{upload_id}] Uploading {image_type} image ({os.path.basename(image_path)}) with upload_id: {upload_id} to {request_url}")

        try:
            async with aiofiles.open(image_path, "rb") as fp:
                file_content = await fp.read()
                if not file_content:
                     logger.error(f"[{upload_id}] Failed to read image file or file is empty: {image_path}")
                     return None

            data = aiohttp.FormData()
            data.add_field('files',
                           file_content,
                           filename=os.path.basename(image_path),
                           # Let aiohttp determine Content-Type based on filename potentially,
                           # or explicitly set 'image/jpeg', 'image/png' if known/needed.
                           # Setting to octet-stream is often safe.
                           content_type='application/octet-stream')

            # Get headers, explicitly request NO Content-Type as aiohttp handles it for FormData
            headers = self._get_headers(content_type=None)

            async with self.session.post(
                request_url, # Use the correct upload URL
                data=data,
                headers=headers,
                proxy=self.proxy,
                timeout=aiohttp.ClientTimeout(total=self.timeout * 2) # Increase timeout for uploads
            ) as response:
                response_text = await response.text()
                if response.status == 200:
                    try:
                        response_data = json.loads(response_text)
                        logger.debug(f"[{upload_id}] Raw /upload response data: {response_data}")

                        # --- Handle different Gradio upload response formats --- 
                        file_info_to_return = None

                        if isinstance(response_data, list) and len(response_data) > 0:
                            first_item = response_data[0]

                            # Format 1: List contains a dictionary with file details
                            if isinstance(first_item, dict) and all(k in first_item for k in ("name", "size", "is_file", "path")):
                                logger.info(f"[{upload_id}] Detected upload response format: Dictionary with details.")
                                file_info = first_item
                                essential_info = {
                                   "name": file_info.get("name"),
                                   "size": file_info.get("size"),
                                   "path": file_info.get("path"), # Server path
                                   "is_file": True,
                                   "orig_name": file_info.get("orig_name", file_info.get("name"))
                                }
                                # Add URL if present (often relative)
                                if "url" in file_info and file_info["url"]:
                                     # Assume URL is relative like /file=... and needs base
                                     relative_url = file_info["url"]
                                     if relative_url.startswith('/'):
                                         # Construct full URL using base_url
                                         essential_info["url"] = f"{self.base_url}{relative_url}"
                                     else: # If it's somehow absolute already?
                                         essential_info["url"] = relative_url
                                elif "path" in essential_info: # Construct URL from path if needed
                                      # Construct full URL using base_url and path
                                      essential_info["url"] = f"{self.base_url}/file={essential_info['path']}"
                                file_info_to_return = essential_info

                            # Format 2: List contains just the server path string
                            elif isinstance(first_item, str) and first_item.strip():
                                logger.info(f"[{upload_id}] Detected upload response format: String path only.")
                                server_path = first_item.strip()
                                original_filename = os.path.basename(image_path)
                                # Construct the dictionary needed for predict
                                essential_info = {
                                    "name": original_filename, # Use original filename
                                    # "size": None, # Size might not be available or needed
                                    "path": server_path, # Server path is crucial
                                    "is_file": True,
                                    "orig_name": original_filename,
                                    # Construct the likely URL format using base_url
                                    "url": f"{self.base_url}/file={server_path}" 
                                }
                                file_info_to_return = essential_info
                        
                        # --- End Format Handling ---

                        if file_info_to_return:
                            logger.info(f"[{upload_id}] Successfully processed upload response for {image_type}. Path: {file_info_to_return.get('path')}")
                            return file_info_to_return
                        else:
                            logger.error(f"[{upload_id}] Upload response format unexpected or invalid content: {response_data}")
                            return None
                            
                    except json.JSONDecodeError:
                        logger.error(f"[{upload_id}] Failed to decode JSON from upload response: {response_text}")
                        return None
                    except Exception as e:
                        logger.error(f"[{upload_id}] Error processing upload response: {e}", exc_info=True)
                        return None
                else:
                    logger.error(f"[{upload_id}] Failed to upload {image_type} image. Status: {response.status}, Response: {response_text}")
                    return None

        except FileNotFoundError:
            logger.error(f"[{upload_id}] Image file not found: {image_path}")
            return None
        except aiohttp.ClientError as e:
             logger.error(f"[{upload_id}] Network error during async upload: {e}", exc_info=True)
             return None
        except Exception as e:
            logger.error(f"[{upload_id}] Unexpected error during async upload: {e}", exc_info=True)
            return None
            
    async def _trigger_predict(self, person_file_info: Dict[str, Any], clothing_file_info: Dict[str, Any]) -> bool:
        """
        Triggers the prediction/synthesis process by calling /run/predict. (Async)

        Args:
            person_file_info: File info object for the person image (from /upload).
            clothing_file_info: File info object for the clothing image (from /upload).

        Returns:
            True if the prediction was triggered successfully, False otherwise.
        """
        await self._ensure_session()
        # Ensure session_hash is set (should be set by try_on_clothing)
        if not self.session_hash:
             self.session_hash = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
             logger.warning(f"Session hash was not set before _trigger_predict, generated: {self.session_hash}")

        # Use the correctly derived predict_url
        predict_url = self.predict_url
        logger.info(f"[{self.session_hash}] Triggering prediction at {predict_url}")

        # Construct the payload based on Gradio structure
        # fn_index=2 was identified previously
        # The 'data' array must contain the file info objects exactly as needed by the Gradio function
        payload = {
            "fn_index": 2, # Target function index
            "data": [
                person_file_info,    # Input 1: Person Image (File object)
                clothing_file_info,  # Input 2: Clothing Image (File object)
                # Add placeholders for other inputs if the Gradio function expects more
                # Check the Gradio config or network tab for exact structure if needed
                # Example: None, # Input 3 (e.g., seed)
                # Example: True, # Input 4 (e.g., boolean flag)
                 None,                # Placeholder for potential other inputs (e.g., seed)
                 None                 # Placeholder
            ],
            "event_data": None, # Usually None for direct predict calls
            "session_hash": self.session_hash # Link this request to the session
        }
        logger.debug(f"[{self.session_hash}] /run/predict payload: {json.dumps(payload, indent=2)}")

        try:
            async with self.session.post(
                predict_url, # Use the correct predict URL
                json=payload, # Send data as JSON
                headers=self._get_headers(content_type="application/json"), # Set correct Content-Type
                proxy=self.proxy,
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                 response_text = await response.text()
                 if response.status == 200:
                     # A 200 OK from /run/predict usually means the job was accepted by the queue.
                     # The actual result will come via the SSE stream.
                     logger.info(f"[{self.session_hash}] Prediction request accepted (HTTP {response.status}). Waiting for SSE stream.")
                     logger.debug(f"[{self.session_hash}] /run/predict raw response: {response_text}")
                     try:
                         # Log response for debugging, might contain job ID etc.
                         logger.info(f"[{self.session_hash}] /run/predict raw response: {response_text}")
                         # Attempt to parse JSON, but it might not be JSON
                         try:
                              response_data = json.loads(response_text)
                              logger.debug(f"[{self.session_hash}] /run/predict JSON response: {response_data}")
                         except json.JSONDecodeError:
                              logger.warning(f"[{self.session_hash}] /run/predict response was not valid JSON, but status was 200 OK.")
                     except Exception as e:
                          logger.error(f"[{self.session_hash}] Error processing /run/predict response: {e}")

                     logger.info(f"[{self.session_hash}] Prediction triggered successfully.")
                     return True
                 else:
                     logger.error(f"[{self.session_hash}] Failed to trigger prediction. Status: {response.status}, Response: {response_text}")
                     return False
        except aiohttp.ClientError as e:
            logger.error(f"[{self.session_hash}] Network error during prediction trigger: {e}")
            return False
        except Exception as e:
            logger.error(f"[{self.session_hash}] Unexpected error triggering prediction: {e}", exc_info=True)
            return False

    async def _join_queue(self, person_file_info: Dict[str, Any], clothing_file_info: Dict[str, Any]) -> bool:
        """Joins the Gradio queue after triggering predict. (Async)

        Args:
            person_file_info: File info object for the person image.
            clothing_file_info: File info object for the clothing image.

        Returns:
            True if joining the queue was successful, False otherwise.
        """
        await self._ensure_session()
        if not self.session_hash:
            logger.error("Cannot join queue: session_hash is not set.")
            return False

        # Add studio_token as query param to match browser request more closely (optional)
        join_url = f"{self.queue_join_url}?studio_token={self.studio_token}"
        logger.info(f"[{self.session_hash}] Attempting to join queue at {join_url}")

        # Construct payload mirroring browser request, including data array content
        payload = {
            "data": [
                person_file_info,
                clothing_file_info,
                0,     # Placeholder/default for slider (based on browser log)
                True   # Placeholder/default for checkbox (based on browser log)
            ],
            # "dataType": ["image", "image", "slider", "checkbox"], # Optional: Include if known and needed
            "fn_index": 2, 
            "session_hash": self.session_hash
        }
        logger.debug(f"[{self.session_hash}] /queue/join payload: {json.dumps(payload)}")

        try:
            async with self.session.post(
                join_url,
                json=payload,
                headers=self._get_headers(content_type="application/json"),
                proxy=self.proxy,
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                response_text = await response.text()
                if response.status == 200:
                    logger.info(f"[{self.session_hash}] Successfully joined queue (HTTP {response.status}).")
                    logger.debug(f"[{self.session_hash}] /queue/join raw response: {response_text}")
                    # You might parse response_text if it contains useful info
                    return True
                else:
                    logger.error(f"[{self.session_hash}] Failed to join queue. Status: {response.status}, Response: {response_text}")
                    return False
        except aiohttp.ClientError as e:
            logger.error(f"[{self.session_hash}] Network error during queue join: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"[{self.session_hash}] Unexpected error joining queue: {e}", exc_info=True)
            return False

    async def upload_person_image(self, image_path: str) -> Optional[Dict[str, Any]]:
        """Uploads the person image using multipart/form-data. (Async)"""
        # Ensure session_hash is set for logging context consistency
        if not hasattr(self, 'session_hash') or not self.session_hash:
             self.session_hash = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
             logger.warning(f"Session hash generated in upload_person_image: {self.session_hash}")
        logger.info(f"[{self.session_hash}] Starting person image upload: {image_path}")
        return await self._upload_file_multipart(image_path, 'person')

    async def upload_clothing_image(self, image_path: str) -> Optional[Dict[str, Any]]:
        """Uploads the clothing image using multipart/form-data. (Async)"""
         # Ensure session_hash is set
        if not hasattr(self, 'session_hash') or not self.session_hash:
             self.session_hash = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
             logger.warning(f"Session hash generated in upload_clothing_image: {self.session_hash}")
        logger.info(f"[{self.session_hash}] Starting clothing image upload: {image_path}")
        return await self._upload_file_multipart(image_path, 'clothing')

    async def start_synthesis(self) -> bool:
        """
        Deprecated/Incorrect: This step is replaced by _trigger_predict.
        Keeping the method signature for now but it shouldn't be called directly
        in the new flow. Will remove later if confirmed unnecessary. (Async)
        """
        logger.warning(f"[{getattr(self, 'session_hash', 'N/A')}] start_synthesis() called, but should use _trigger_predict() instead.")
        return False # Indicate incorrect usage

    async def wait_for_result(self, max_wait_time: int = 120) -> Optional[str]:
        """等待合成结果 (SSE Stream on /queue/data) (Async)

        Args:
            max_wait_time: 最大等待时间(秒)

        Returns:
            Optional[str]: 合成结果图片的URL或Base64数据, 或None
        """
        # Ensure session_hash is available
        if not hasattr(self, 'session_hash') or not self.session_hash:
             logger.error("无法等待结果：无效的 session_hash")
             return None

        if not self.session:
            self.session = aiohttp.ClientSession()

        url = f"{self.queue_data_url}?session_hash={self.session_hash}&studio_token={self.studio_token}"
        logger.debug(f"[{self.session_hash}] 开始监听 SSE 事件流: {url}")

        headers = {
            "accept": "text/event-stream",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "cache-control": "no-cache",
            "referer": "https://kwai-kolors-kolors-virtual-try-on.ms.show/",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
            "Cookie": self.cookie_string if self.cookie_string else f"studio_token={self.studio_token}"
        }
        proxy = self.proxy

        start_time = time.time()
        result_url_or_data = None
        last_log_time = start_time
        data_json_str = ""

        try:
            async with self.session.get(
                url,
                headers=headers,
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=max_wait_time + 10) # Increased timeout for SSE connection itself
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    if "Session not found" in error_text:
                         logger.error(f"[{self.session_hash}] 获取 SSE 事件流失败: 会话未找到 (session_hash: {self.session_hash}).")
                    else:
                         logger.error(f"[{self.session_hash}] 获取 SSE 事件流失败: HTTP {response.status} - {error_text}")
                    return None

                logger.info(f"[{self.session_hash}] SSE 连接成功，开始接收事件...")
                async for line_bytes in response.content:
                    if not line_bytes:
                        continue

                    current_time = time.time()
                    if current_time - start_time > max_wait_time:
                        logger.warning(f"[{self.session_hash}] 等待合成结果超时 ({max_wait_time}秒)")
                        break

                    try:
                        line = line_bytes.decode('utf-8').strip()

                        if line and current_time - last_log_time > 5: # Log raw data periodically
                            logger.debug(f"[{self.session_hash}] SSE 原始数据: {line}")
                            last_log_time = current_time

                        if line.startswith("data:"):
                            data_json_str = line[len("data:"):].strip()
                            if data_json_str: # Ensure data is not empty
                                data = json.loads(data_json_str)
                                msg_type = data.get("msg")
                                logger.debug(f"[{self.session_hash}] SSE Parsed Data: {data}")

                                if msg_type == "unexpected_error" or data.get("success") is False:
                                     error_message = data.get("message", "未知错误")
                                     logger.error(f"[{self.session_hash}] SSE 事件: 收到错误: {error_message} (数据: {data})")
                                     # Potentially break if it's a fatal error like session not found
                                     if "Session not found" in error_message:
                                         break

                                elif msg_type == "progress":
                                     # Example: Extract progress if available
                                     progress_data = data.get("output", {}).get("progress")
                                     if progress_data is not None:
                                         # Gradio progress often sends a list of dictionaries like [{"progress": 0.1, "desc": "Running..."}]
                                         if isinstance(progress_data, list) and progress_data:
                                             unit_progress = progress_data[0].get("progress")
                                             if unit_progress is not None:
                                                  logger.info(f"[{self.session_hash}] 合成进度: {unit_progress * 100:.1f}%")
                                         elif isinstance(progress_data, (int, float)): # Simpler progress format
                                              logger.info(f"[{self.session_hash}] 合成进度: {progress_data * 100:.1f}%")


                                elif msg_type == "process_completed":
                                    logger.info(f"[{self.session_hash}] SSE: Processing completed.")
                                    output_data = data.get("output", {}).get("data")
                                    success = data.get("success", False)

                                    if success and output_data:
                                        # Extract the actual result from output_data
                                        if isinstance(output_data, list) and len(output_data) > 0:
                                            result_item = output_data[0] # Assume first item is the main result
                                            found_result = None

                                            # --- Result Format Handling --- 
                                            # Check 1: Direct Base64 string
                                            if isinstance(result_item, str) and result_item.startswith("data:image"):
                                                logger.info(f"[{self.session_hash}] Found Base64 image data in result.")
                                                found_result = result_item
                                            # Check 2: Dictionary with direct, valid URL
                                            elif isinstance(result_item, dict) and "url" in result_item and isinstance(result_item.get("url"), str) and result_item.get("url").startswith(("http://", "https://", "/file=")):
                                                logger.info(f"[{self.session_hash}] Found dictionary with valid URL: {result_item['url']}")
                                                found_result = result_item['url'].strip()
                                            # Check 3: Dictionary with path and is_file (needs URL construction)
                                            elif isinstance(result_item, dict) and "path" in result_item and result_item.get("is_file"):
                                                logger.info(f"[{self.session_hash}] Found dictionary with path/is_file.")
                                                file_path = result_item["path"]
                                                found_result = f"{self.base_url}/file={file_path}"
                                                logger.info(f"[{self.session_hash}] Constructed result URL from path: {found_result}")
                                            # Check 4: Direct URL/Path string (less common but possible)
                                            elif isinstance(result_item, str) and result_item.startswith(("http://", "https://", "/file=")):
                                                logger.info(f"[{self.session_hash}] Found direct URL/Path string in result: {result_item}")
                                                found_result = result_item.strip()
                                            # Add more checks if other formats are observed
                                            
                                            # Validate and assign the found result
                                            if found_result:
                                                result_url_or_data = found_result
                                                logger.info(f"[{self.session_hash}] 获取到有效合成结果.")
                                                break # Exit loop once valid result found
                                            else:
                                                logger.warning(f"[{self.session_hash}] Result item format unrecognized or invalid: {result_item}")
                                                logger.warning(f"[{self.session_hash}] Full 'process_completed' data: {data}")
                                        else:
                                            logger.warning(f"[{self.session_hash}] Received 'process_completed' but 'output.data' is not a non-empty list: {output_data}")
                                            logger.warning(f"[{self.session_hash}] Full 'process_completed' data: {data}")
                                    else:
                                        # Handle failure case indicated by success=False or missing output
                                        error_msg = data.get("output", {}).get("error") or "Processing completed but success=False or output missing."
                                        logger.error(f"[{self.session_hash}] SSE indicated processing failure: {error_msg}")
                                        logger.error(f"[{self.session_hash}] Full 'process_completed' data: {data}")
                                        # Optionally break here if it's a definitive failure
                                        # break

                                elif msg_type == "process_starts":
                                    logger.info(f"[{self.session_hash}] SSE 事件: 处理开始 (process_starts)")
                                elif msg_type == "process_generating":
                                    logger.info(f"[{self.session_hash}] SSE 事件: 正在生成 (process_generating)")
                                # Add handling for other msg types if observed (e.g., queue updates)
                            else:
                                pass # Empty data field is common (e.g., keep-alive)

                        elif line.startswith("event: close"):
                            logger.info(f"[{self.session_hash}] SSE 事件: 连接关闭 (event: close)")
                            break # Server closed the stream
                        elif line == "":
                            pass # Ignore empty lines (often act as separators)
                        else:
                            logger.warning(f"[{self.session_hash}] SSE: 未知行格式: {line}")

                    except json.JSONDecodeError as json_err:
                        logger.error(f"[{self.session_hash}] 解析 SSE JSON 数据出错: {json_err}, 原始数据: '{data_json_str}'")
                        continue # Skip this line and try the next one
                    except Exception as e:
                        logger.error(f"[{self.session_hash}] 处理 SSE 行数据时出错: {e}, 行: '{line}'")
                        logger.exception(f"[{self.session_hash}] SSE 处理异常详情")
                        continue # Skip this line

        except asyncio.TimeoutError:
            logger.warning(f"[{self.session_hash}] 监听 SSE 事件流超时 ({max_wait_time}秒)")
        except aiohttp.ClientConnectionError as conn_err:
            logger.error(f"[{self.session_hash}] SSE 连接错误: {conn_err}")
        except aiohttp.ClientPayloadError as payload_error:
             logger.error(f"[{self.session_hash}] 读取 SSE 载荷错误: {payload_error}")
        except Exception as e:
            logger.error(f"[{self.session_hash}] 监听 SSE 事件流时发生未知错误: {e}")
            logger.exception(f"[{self.session_hash}] SSE 监听异常详情")


        if not result_url_or_data:
            logger.error(f"[{self.session_hash}] 未能在超时前或因错误获取到合成结果")
        else:
             logger.info(f"[{self.session_hash}] 成功获取结果 URL/Data/Path")

        # Clean up session if it was created internally just for SSE
        # Consider if self.session should persist across the whole try_on process
        # await self.session.close()
        # self.session = None

        return result_url_or_data
    
    async def check_modelscope_status(self) -> bool:
        """检查ModelScope API状态 (Async)"""
        if not self.session:
             self.session = aiohttp.ClientSession()

        url = self.api_status_url
        params = self._get_params()
        headers = self._get_headers(content_type="application/json") # Status check likely uses JSON
        proxy = self.proxy

        try:
            logger.debug(f"[{getattr(self, 'session_hash', 'N/A')}] Checking ModelScope status at {url}")
            async with self.session.get(url, params=params, headers=headers, proxy=proxy, timeout=self.timeout) as response:
                response_text = await response.text()
                if response.status == 200:
                    try:
                        data = json.loads(response_text)
                        # Heuristic check: look for keys common in Gradio status/info endpoints
                        if "version" in data or "components" in data or "config" in data or data.get("status") == "running":
                            logger.info(f"ModelScope API status check successful (HTTP {response.status}). Response snippet: {response_text[:100]}...")
                            return True
                        else:
                             logger.warning(f"ModelScope API status check returned 200 OK, but response format unexpected: {response_text[:200]}...")
                             return False # Or True, depending on how strict the check needs to be
                    except json.JSONDecodeError:
                         logger.warning(f"ModelScope API status check returned 200 OK, but response was not valid JSON: {response_text[:200]}...")
                         return False # Treat non-JSON 200 as potentially problematic
                else:
                    logger.error(f"ModelScope API status check failed: HTTP {response.status}, Response: {response_text[:200]}...")
                    return False
        except aiohttp.ClientError as e:
             logger.error(f"Network error during ModelScope status check: {e}")
             return False
        except asyncio.TimeoutError:
             logger.error("Timeout during ModelScope status check.")
             return False
        except Exception as e:
            logger.error(f"Unexpected error during ModelScope status check: {e}")
            return False


    async def download_result_image(self, image_url_or_data: str, save_path: str) -> bool:
        """下载或保存合成结果图片 (URL或Base64) (Async)"""
        if image_url_or_data.startswith("data:image"):
            try:
                header, encoded = image_url_or_data.split(",", 1)
                image_bytes = base64.b64decode(encoded)
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                async with aiofiles.open(save_path, "wb") as f:
                    await f.write(image_bytes)
                # Verify save
                if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                     logger.info(f"[{getattr(self, 'session_hash', 'N/A')}] Successfully saved Base64 result image to: {save_path}")
                     return True
                else:
                     logger.error(f"[{getattr(self, 'session_hash', 'N/A')}] Saving Base64 image failed or resulted in empty file: {save_path}")
                     return False
            except Exception as e:
                logger.error(f"[{getattr(self, 'session_hash', 'N/A')}] Parsing and saving Base64 image data failed: {e}")
                logger.exception(f"[{getattr(self, 'session_hash', 'N/A')}] Base64 save exception details")
                return False

        elif image_url_or_data.startswith(("http://", "https://", "/file=")):
            download_url = image_url_or_data
            if image_url_or_data.startswith("/file="):
                 # Construct full URL from base_url and relative path
                 # Use self.base_url correctly here
                 download_url = f"{self.base_url}{image_url_or_data}"
                 logger.info(f"[{getattr(self, 'session_hash', 'N/A')}] Constructed download URL: {download_url}")


            if not self.session: self.session = aiohttp.ClientSession()
            try:
                logger.info(f"[{getattr(self, 'session_hash', 'N/A')}] Starting download result image URL: {download_url}")
                proxy = self.proxy
                # Use minimal headers for file download
                headers = {"User-Agent": self._get_headers().get("user-agent", "aiohttp")}
                async with self.session.get(download_url, headers=headers, proxy=proxy, timeout=aiohttp.ClientTimeout(total=self.timeout * 2)) as response:
                    if response.status == 200:
                        image_data = await response.read()
                        if not image_data:
                             logger.error(f"[{getattr(self, 'session_hash', 'N/A')}] Download image failed: Response body was empty. URL: {download_url}")
                             return False
                        os.makedirs(os.path.dirname(save_path), exist_ok=True)
                        async with aiofiles.open(save_path, "wb") as f:
                            await f.write(image_data)
                        # Verify save
                        if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                            logger.info(f"[{getattr(self, 'session_hash', 'N/A')}] Successfully downloaded and saved result image to: {save_path}")
                            return True
                        else:
                             logger.error(f"[{getattr(self, 'session_hash', 'N/A')}] Saving downloaded image failed or resulted in empty file: {save_path}")
                             return False
                    else:
                        error_text = await response.text()
                        logger.error(f"[{getattr(self, 'session_hash', 'N/A')}] Download image failed: HTTP {response.status}, URL: {download_url}, Response: {error_text[:200]}...")
                        return False
            except aiohttp.ClientError as e:
                 logger.error(f"[{getattr(self, 'session_hash', 'N/A')}] Network error downloading result image URL: {e}, URL: {download_url}")
                 return False
            except asyncio.TimeoutError:
                 logger.error(f"[{getattr(self, 'session_hash', 'N/A')}] Timeout downloading result image URL: {download_url}")
                 return False
            except Exception as e:
                logger.error(f"[{getattr(self, 'session_hash', 'N/A')}] Failed to download result image URL: {e}, URL: {download_url}")
                logger.exception(f"[{getattr(self, 'session_hash', 'N/A')}] Image download exception details")
                return False
        else:
            logger.error(f"[{getattr(self, 'session_hash', 'N/A')}] Cannot handle result format: {image_url_or_data[:100]}...")
            return False

    async def try_on_clothing(self, person_image_path: str, clothing_image_path: str) -> Optional[str]:
        """
        Performs the virtual try-on process asynchronously using Gradio API conventions.

        Steps:
        1. Generate session hash.
        2. Upload person image -> Get file info.
        3. Upload clothing image -> Get file info.
        4. Trigger prediction with file info and session hash.
        5. Join the event queue using the session hash and file info.
        6. Wait for result via SSE stream using session hash.
        7. Download/save the result image.
        8. Clean up session.

        Args:
            person_image_path: Path to the person's image.
            clothing_image_path: Path to the clothing image.

        Returns:
            Path to the saved resulting image if successful, otherwise None.
        """
        self.session_hash = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        log_prefix = f"[{self.session_hash}]"
        logger.info(f"{log_prefix} Starting async virtual try-on process...")

        # Ensure aiohttp session is active (using context manager is preferred pattern)
        await self._ensure_session()

        saved_image_path = None # Initialize result path

        try:
            # --- Optional Status Check --- (Keep commented out for now)
            # status_ok = await self.check_modelscope_status()
            # if not status_ok:
            #      logger.warning(f"{log_prefix} Gradio status check failed or service unavailable. Proceeding with caution...")
            #      # Decide whether to abort or continue

            # --- Step 2: Upload Person Image ---
            logger.info(f"{log_prefix} Uploading person image: {person_image_path}")
            person_file_info = await self._upload_file_multipart(person_image_path, 'person')
            if not person_file_info:
                logger.error(f"{log_prefix} Person image upload failed.")
                return None # Abort
            logger.info(f"{log_prefix} Person image uploaded successfully. Server Path: {person_file_info.get('path')}")

            # --- Step 3: Upload Clothing Image ---
            logger.info(f"{log_prefix} Uploading clothing image: {clothing_image_path}")
            clothing_file_info = await self._upload_file_multipart(clothing_image_path, 'clothing')
            if not clothing_file_info:
                logger.error(f"{log_prefix} Clothing image upload failed.")
                return None # Abort
            logger.info(f"{log_prefix} Clothing image uploaded successfully. Server Path: {clothing_file_info.get('path')}")

            # --- Step 4: Trigger Prediction ---
            logger.info(f"{log_prefix} Triggering prediction...")
            triggered = await self._trigger_predict(person_file_info, clothing_file_info)
            if not triggered:
                logger.error(f"{log_prefix} Failed to trigger prediction/synthesis.")
                return None # Abort
            logger.info(f"{log_prefix} Prediction triggered successfully.")
            
            # --- Step 5: Join Queue ---
            logger.info(f"{log_prefix} Joining queue...")
            joined = await self._join_queue(person_file_info, clothing_file_info)
            if not joined:
                logger.error(f"{log_prefix} Failed to join the event queue.")
                return None # Abort
            logger.info(f"{log_prefix} Successfully joined queue. Waiting for results via SSE...")

            # --- Step 6: Wait for Result ---
            result_url_or_data = await self.wait_for_result()
            if not result_url_or_data:
                logger.error(f"{log_prefix} Failed to get result via SSE (Timeout or Error).")
                return None # Abort
            logger.info(f"{log_prefix} Successfully received result identifier (URL/Data/Path).")

            # --- Step 7: Download/Save Result ---
            # Determine file extension based on result (URL or Base64 header)
            file_extension = ".png" # Default
            if isinstance(result_url_or_data, str):
                if result_url_or_data.startswith("data:image/webp"):
                     file_extension = ".webp"
                elif result_url_or_data.startswith("data:image/jpeg"):
                     file_extension = ".jpg"
                elif ".webp" in result_url_or_data.lower():
                     file_extension = ".webp"
                elif ".jpg" in result_url_or_data.lower() or "jpeg" in result_url_or_data.lower():
                     file_extension = ".jpg"
                # Add more checks if needed
            
            result_filename = f"result_{self.session_hash}{file_extension}" 
            save_path = os.path.join(self.save_dir, result_filename)
            logger.info(f"{log_prefix} Attempting to save result to: {save_path}")

            downloaded = await self.download_result_image(result_url_or_data, save_path)

            if downloaded:
                 logger.info(f"{log_prefix} Virtual try-on process completed successfully. Result saved to: {save_path}")
                 saved_image_path = save_path # Set the successful path
            else:
                 logger.error(f"{log_prefix} Failed to download or save the final result image.")
                 # saved_image_path remains None

        except Exception as e:
            # Catch any unexpected errors during the overall process
            logger.error(f"{log_prefix} An unexpected error occurred during the try_on_clothing process: {e}", exc_info=True)
            saved_image_path = None # Ensure None is returned on error
        # --- Session Cleanup --- is handled by __aexit__ if using async with

        return saved_image_path

    async def close(self):
        """Explicitly close the aiohttp session if needed."""
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None
            logger.info("aiohttp session closed.") 