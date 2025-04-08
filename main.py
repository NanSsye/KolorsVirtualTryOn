from pathlib import Path
import os
import time
import tomllib
import asyncio
from io import BytesIO
from typing import Optional
import xml.etree.ElementTree as ET # 导入 XML 解析库
import base64
from PIL import Image # 导入 Pillow Image

from loguru import logger
import aiofiles

from WechatAPI import WechatAPIClient
from utils.decorators import *
from utils.plugin_base import PluginBase
from .api_client import KolorsVirtualTryOnClient


class KolorsVirtualTryOn(PluginBase):
    """虚拟试衣服务插件"""
    
    description = "Kolors虚拟试衣服务"
    author = "老夏的金库"
    version = "1.0.0"
    
    def __init__(self):
        super().__init__()
        
        # 获取插件目录路径
        self.plugin_dir = Path(os.path.dirname(__file__))
        
        # 读取配置文件
        try:
            config_path = self.plugin_dir / "config.toml"
            with open(config_path, "rb") as f:
                self.config = tomllib.load(f)
                
            # 读取基本配置
            basic_config = self.config.get("basic", {})
            self.enable = basic_config.get("enable", False)
            
            # 读取请求配置
            request_config = self.config.get("request", {})
            # Keep try_on_url as it might be the only source for base_url
            self.try_on_url = request_config.get("try_on_url", "") 
            # Remove unused URLs from config loading
            # self.queue_join_url = request_config.get("queue_join_url", "")
            # self.queue_data_url = request_config.get("queue_data_url", "")
            # self.api_status_url = request_config.get("api_status_url", "")
            self.proxy = request_config.get("proxy", "")
            self.studio_token = request_config.get("studio_token", "")
            self.timeout = request_config.get("timeout", 60)
            self.cookie_string = request_config.get("cookie_string", "")
            
            # Basic validation
            if not self.try_on_url:
                raise ValueError("Config error: try_on_url is missing or empty.")
            if not self.studio_token:
                logger.warning("Config warning: studio_token is missing or empty.")
                # raise ValueError("Config error: studio_token is missing or empty.")

            logger.success(f"加载KolorsVirtualTryOn配置文件成功")
            
            # 创建资源目录
            os.makedirs("resource/KolorsVirtualTryOn", exist_ok=True)
            os.makedirs("resource/KolorsVirtualTryOn/temp", exist_ok=True)
            
        except Exception as e:
            logger.error(f"加载KolorsVirtualTryOn配置文件失败: {e}")
            self.enable = False
    
    async def async_init(self):
        """插件异步初始化"""
        if not self.enable:
            return
        
        logger.info("正在初始化KolorsVirtualTryOn插件...")
        try:
            # Derieve base_url from try_on_url (assuming it contains /run/predict)
            base_url = self.try_on_url
            if "/run/predict" in base_url:
                base_url = base_url.split("/run/predict")[0]
            elif base_url.endswith('/'): # Remove trailing slash if any
                base_url = base_url.rstrip('/')
            
            if not base_url:
                 raise ValueError("Could not derive base_url from try_on_url in config.")

            logger.debug(f"Derived Base URL for Kolors Client: {base_url}")

            # 初始化API客户端 (使用新的签名)
            client = KolorsVirtualTryOnClient(
                base_url=base_url, # Pass derived base_url
                # Remove old URL args: try_on_url, queue_join_url, queue_data_url, api_status_url
                studio_token=self.studio_token,
                cookie_string=self.cookie_string,
                proxy=self.proxy,
                timeout=self.timeout
            )
            
            self.api_client = client
            logger.success("KolorsVirtualTryOn插件初始化成功")
        except Exception as e:
            logger.error(f"初始化KolorsVirtualTryOn插件失败: {e}")
            self.enable = False
    
    async def save_image_from_message(self, bot: WechatAPIClient, message: dict) -> Optional[str]:
        """从消息中获取并保存图片 (根据文档，Content 字段包含 Base64)

        Args:
            bot: 微信API客户端
            message: 消息数据

        Returns:
            Optional[str]: 保存的图片路径或None
        """
        try:
            timestamp = int(time.time())
            image_path = f"resource/KolorsVirtualTryOn/temp/image_{timestamp}.jpg"
            
            # 从消息中获取 Base64 内容
            image_base64 = message.get("Content")
            if not image_base64 or not isinstance(image_base64, str):
                logger.error(f"图片消息的 Content 字段无效或不是字符串: {type(image_base64)}")
                # 打印消息键以供调试
                logger.debug(f"图片消息 Keys: {list(message.keys())}") 
                return None
            
            # Base64 解码
            try:
                # 移除可能的 data:image/...;base64, 前缀 (虽然文档示例没有，但保险起见)
                if "," in image_base64:
                    image_base64 = image_base64.split(',')[-1]
                image_data = base64.b64decode(image_base64)
            except Exception as decode_error:
                logger.error(f"Base64 解码失败: {decode_error}")
                logger.debug(f"Base64 字符串 (前100字符): {image_base64[:100]}...")
                return None
            
            # 验证图片数据是否为 bytes 类型且非空
            if not isinstance(image_data, bytes) or len(image_data) == 0:
                logger.error(f"解码后的图片数据无效或为空")
                return None
                
            logger.info(f"成功从 Content 解码图片，大小: {len(image_data)} bytes")
            
            # 新增验证步骤
            try:
                # 尝试用 Pillow 打开解码后的数据，看是否是有效图片
                img = Image.open(BytesIO(image_data))
                img.verify() # 验证图片完整性
                logger.info(f"解码后的图片数据验证通过 (格式: {img.format})")
            except Exception as img_verify_error:
                logger.error(f"解码后的图片数据无法通过 Pillow 验证: {img_verify_error}")
                # 可以选择记录更详细的信息，比如 image_data 的前100字节
                # logger.debug(f"无效图片数据 (前100字节): {image_data[:100]}")
                return None
            # 验证结束

            # 保存图片
            async with aiofiles.open(image_path, "wb") as f:
                await f.write(image_data)
            
            logger.info(f"保存图片到: {image_path}")
            return image_path
        except Exception as e:
            logger.error(f"保存图片失败: {e}")
            logger.exception("保存图片异常详情")
            return None
    
    @on_text_message(priority=90)
    async def handle_help(self, bot: WechatAPIClient, message: dict):
        """处理帮助命令"""
        logger.info(f"KolorsVirtualTryOn收到文本消息: {message.get('Content', '')}") # 使用 Content
        if not self.enable:
            return
        
        content = message.get("Content", "") # 使用 Content
        from_wxid = message.get("SenderWxid", message.get("FromWxid", "")) # 使用 SenderWxid 或 FromWxid
        is_group = message.get("IsGroup", False) # 使用 IsGroup
        from_group = message.get("FromWxid") if is_group else "" # 使用 FromWxid (如果是群聊)
        
        # 只处理特定命令
        if content != "#虚拟试衣" and content != "#试衣帮助":
            return
        
        reply_to = from_group if from_group else from_wxid
        
        help_text = (
            "🧥 虚拟试衣功能 🧥\n\n"
            "使用方法:\n"
            "1. 发送 \"#上传人物图片\" 然后发送一张人物照片\n"
            "2. 发送 \"#上传衣服图片\" 然后发送一张衣服照片\n"
            "3. 发送 \"#开始试衣\" 进行合成\n\n"
            "注意事项:\n"
            "- 人物照片应清晰显示人物全身\n"
            "- 衣服照片应清晰显示单件服装\n"
            "- 合成过程需要10-30秒，请耐心等待"
        )
        
        await bot.send_text_message(reply_to, help_text)
        # 阻止其他插件处理此命令
        return False
    
    @on_text_message(priority=90)
    async def handle_commands(self, bot: WechatAPIClient, message: dict):
        """处理特定命令"""
        logger.info(f"KolorsVirtualTryOn收到命令: {message.get('Content', '')}") # 使用 Content
        if not self.enable:
            return True # 让其他插件处理
        
        content = message.get("Content", "") # 使用 Content
        from_wxid = message.get("SenderWxid", message.get("FromWxid", "")) # 使用 SenderWxid 或 FromWxid
        is_group = message.get("IsGroup", False) # 使用 IsGroup
        from_group = message.get("FromWxid") if is_group else "" # 使用 FromWxid (如果是群聊)
        
        reply_to = from_group if from_group else from_wxid
        user_key = reply_to # 使用reply_to作为user_key更简洁
        
        command_handled = False # 标记是否处理了命令
        
        # 命令处理
        if content == "#上传人物图片":
            # 设置状态，等待图片
            self.user_states = getattr(self, "user_states", {})
            self.user_states[user_key] = {"state": "waiting_person", "time": time.time()}
            logger.info(f"用户 {user_key} 状态设置为 waiting_person")
            await bot.send_text_message(reply_to, "请发送人物照片")
            command_handled = True
            
        elif content == "#上传衣服图片":
            # 设置状态，等待图片
            self.user_states = getattr(self, "user_states", {})
            self.user_states[user_key] = {"state": "waiting_clothing", "time": time.time()}
            logger.info(f"用户 {user_key} 状态设置为 waiting_clothing")
            await bot.send_text_message(reply_to, "请发送衣服照片")
            command_handled = True
            
        elif content == "#开始试衣":
            self.user_data = getattr(self, "user_data", {})
            
            # 检查是否有上传的图片
            if user_key not in self.user_data:
                logger.warning(f"用户 {user_key} 未上传任何图片")
                await bot.send_text_message(reply_to, "请先上传人物图片和衣服图片\n发送 \"#虚拟试衣\" 查看使用方法")
                return False # 阻止其他插件处理
                
            user_images = self.user_data[user_key]
            if "person_image" not in user_images:
                 logger.warning(f"用户 {user_key} 未上传人物图片")
                 await bot.send_text_message(reply_to, "请先上传人物图片\n发送 \"#虚拟试衣\" 查看使用方法")
                 return False # 阻止其他插件处理
            if "clothing_image" not in user_images:
                logger.warning(f"用户 {user_key} 未上传衣服图片")
                await bot.send_text_message(reply_to, "请先上传衣服图片\n发送 \"#虚拟试衣\" 查看使用方法")
                return False # 阻止其他插件处理
            
            logger.info(f"用户 {user_key} 开始试衣流程")
            # 开始试衣流程
            await bot.send_text_message(reply_to, "开始虚拟试衣，请稍候...")
            
            try:
                # 清理旧状态和数据，避免影响下次使用
                if user_key in self.user_states:
                    del self.user_states[user_key]
                
                async with self.api_client as client:
                    result_path = await client.try_on_clothing(
                        user_images["person_image"],
                        user_images["clothing_image"]
                    )
                    
                    if result_path:
                        logger.info(f"用户 {user_key} 虚拟试衣成功，结果路径: {result_path}")
                        # 发送结果图片
                        await bot.send_text_message(reply_to, "虚拟试衣完成 ✅")
                        
                        # Convert WEBP to JPEG bytes before sending
                        try:
                            logger.debug(f"尝试将结果图片 {result_path} 转换为 JPEG 字节发送.")
                            img = Image.open(result_path)
                            img_byte_arr = BytesIO()
                            # Ensure image is RGB before saving as JPEG
                            if img.mode == 'RGBA':
                                img = img.convert('RGB')
                            elif img.mode == 'P': # Handle palette mode if necessary
                                img = img.convert('RGB')
                                
                            img.save(img_byte_arr, format='JPEG', quality=90) # Save as JPEG with quality
                            image_bytes = img_byte_arr.getvalue()
                            logger.info(f"图片已转换为 JPEG 字节，大小: {len(image_bytes)} bytes.")
                            await bot.send_image_message(reply_to, image_bytes)
                        except Exception as convert_err:
                            logger.error(f"转换或发送图片字节失败: {convert_err}. 将尝试直接发送原始路径.")
                            # Fallback to sending the original path if conversion fails
                            await bot.send_image_message(reply_to, result_path)
                        finally:
                            # Optional: Clean up the original webp file after sending if desired
                            # try:
                            #     if os.path.exists(result_path):
                            #         os.remove(result_path)
                            #         logger.debug(f"已清理原始 WEBP 文件: {result_path}")
                            # except Exception as remove_err:
                            #     logger.warning(f"清理原始 WEBP 文件失败: {remove_err}")
                            pass # Keep the file for now
                            
                    else:
                        logger.error(f"用户 {user_key} 虚拟试衣失败")
                        await bot.send_text_message(reply_to, "虚拟试衣失败，请重试")
            except Exception as e:
                logger.error(f"用户 {user_key} 试衣过程出错: {e}")
                logger.exception("试衣过程异常详情")
                await bot.send_text_message(reply_to, f"虚拟试衣出错: {str(e)}")
            finally:
                 # 清理用户数据，无论成功失败
                 if user_key in self.user_data:
                     # 保留图片路径以供调试，或按需删除
                     # if "person_image" in self.user_data[user_key]: os.remove(self.user_data[user_key]["person_image"])
                     # if "clothing_image" in self.user_data[user_key]: os.remove(self.user_data[user_key]["clothing_image"])
                     del self.user_data[user_key]
                     logger.info(f"已清理用户 {user_key} 的试衣数据")
            
            command_handled = True

        # 如果处理了命令，返回 False 阻止其他插件；否则返回 True
        return not command_handled
    
    @on_image_message(priority=90) # 提高图片处理优先级
    async def handle_image(self, bot: WechatAPIClient, message: dict):
        """处理图片消息"""
        logger.info(f"KolorsVirtualTryOn收到图片消息: {message.get('MsgId')}")
        if not self.enable:
            return True # 让其他插件处理
        
        # 使用正确的字段名
        from_wxid = message.get("SenderWxid", message.get("FromWxid", "")) # 使用 SenderWxid 或 FromWxid
        is_group = message.get("IsGroup", False) # 使用 IsGroup
        from_group = message.get("FromWxid") if is_group else "" # 使用 FromWxid (如果是群聊)
        
        user_key = from_group if from_group else from_wxid # 使用群聊ID或用户ID作为key
        reply_to = user_key
        
        # 检查用户状态
        self.user_states = getattr(self, "user_states", {})
        
        if user_key not in self.user_states:
            logger.info(f"用户 {user_key} 未处于等待图片状态，忽略图片消息")
            return True # 未处于等待状态，让其他插件处理
            
        # 检查状态是否过期（5分钟）
        current_time = time.time()
        state_info = self.user_states[user_key]
        if current_time - state_info["time"] > 300:
            logger.info(f"用户 {user_key} 的状态已过期")
            del self.user_states[user_key]
            return True # 状态过期，让其他插件处理
            
        state = state_info["state"]
        logger.info(f"用户 {user_key} 当前状态: {state}")
        
        # 保存图片
        image_path = await self.save_image_from_message(bot, message)
        if not image_path:
            await bot.send_text_message(reply_to, "保存图片失败，请重试")
            # 保存失败也阻止其他插件处理，因为意图是给本插件的
            return False 
            
        # 根据状态处理图片
        self.user_data = getattr(self, "user_data", {})
        if user_key not in self.user_data:
            self.user_data[user_key] = {}
            
        if state == "waiting_person":
            self.user_data[user_key]["person_image"] = image_path
            logger.info(f"用户 {user_key} 的人物图片已保存: {image_path}")
            await bot.send_text_message(reply_to, "人物图片已保存 ✅\n您可以继续发送 \"#上传衣服图片\" 命令")
            
        elif state == "waiting_clothing":
            self.user_data[user_key]["clothing_image"] = image_path
            logger.info(f"用户 {user_key} 的衣服图片已保存: {image_path}")
            await bot.send_text_message(reply_to, "衣服图片已保存 ✅\n您可以发送 \"#开始试衣\" 命令进行合成")
            
        # 清除状态，因为图片已收到
        logger.info(f"用户 {user_key} 的状态已清除")
        del self.user_states[user_key]
        
        # 阻止其他插件处理此图片消息
        return False 
    
    @schedule('interval', hours=2)
    async def clean_temp_files(self, bot: WechatAPIClient):
        """定期清理临时文件"""
        if not self.enable:
            return
            
        try:
            # 清理超过24小时的临时文件
            temp_dir = Path("resource/KolorsVirtualTryOn/temp")
            if not temp_dir.exists():
                return
                
            current_time = time.time()
            count = 0
            
            for file_path in temp_dir.glob("*.*"):
                try:
                    file_stat = os.stat(file_path)
                    # 如果文件超过24小时
                    if current_time - file_stat.st_mtime > 86400:
                        os.remove(file_path)
                        count += 1
                except Exception as e:
                    logger.error(f"清理文件 {file_path} 失败: {e}")
                    
            if count > 0:
                logger.info(f"已清理 {count} 个临时文件")
        except Exception as e:
            logger.error(f"清理临时文件失败: {e}")
    
    @schedule('interval', days=7)
    async def clean_result_files(self, bot: WechatAPIClient):
        """定期清理结果文件"""
        if not self.enable:
            return
            
        try:
            # 清理超过7天的结果文件
            result_dir = Path("resource/KolorsVirtualTryOn")
            if not result_dir.exists():
                return
                
            current_time = time.time()
            count = 0
            
            for file_path in result_dir.glob("result_*.jpg"):
                try:
                    file_stat = os.stat(file_path)
                    # 如果文件超过7天
                    if current_time - file_stat.st_mtime > 604800:
                        os.remove(file_path)
                        count += 1
                except Exception as e:
                    logger.error(f"清理文件 {file_path} 失败: {e}")
                    
            if count > 0:
                logger.info(f"已清理 {count} 个结果文件")
        except Exception as e:
            logger.error(f"清理结果文件失败: {e}") 