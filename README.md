# KolorsVirtualTryOn 插件

![097f181a3ab66efe5629839c9a01085](https://github.com/user-attachments/assets/da2649f7-7338-4e3c-92fe-cfcbd1be989b)

## 介绍

KolorsVirtualTryOn 是一个虚拟试衣服务插件，基于快手 Kolors 模型实现。用户可以通过上传人物照片和衣服照片，合成虚拟试衣效果图。

## 功能

- 上传人物照片
- 上传衣服照片
- 自动合成试衣效果图
- 定期清理临时文件和结果文件

## 安装

1. 确保 XXXBot 已经安装并能正常运行。
2. 将 `KolorsVirtualTryOn` 文件夹放入 `plugins` 目录。
3. 安装依赖：

```bash
pip install -r plugins/KolorsVirtualTryOn/requirements.txt
```

## 配置说明

编辑 `config.toml` 文件：

```toml
[basic]
# 是否启用插件
enable = true

# 请求配置
[request]
# Kolors Gradio 应用的基础 URL (末尾不带斜杠)
base_url = "https://kwai-kolors-kolors-virtual-try-on.ms.show"
# 代理地址，为空则不使用代理
proxy = ""
# studio token (必要)
studio_token = "YOUR_STUDIO_TOKEN_HERE"
# Cookie 字符串 (通常会自动设置, 但如果遇到认证问题可以手动填入从浏览器复制的完整 Cookie)
cookie_string = ""
# 请求超时时间(秒)
timeout = 60
```

其中：

- `enable`: 是否启用插件
- `base_url`: Gradio 应用的基础 URL，例如 `https://kwai-kolors-kolors-virtual-try-on.ms.show`。**请确保末尾没有斜杠 (`/`)**。插件会自动从此基础 URL 推导出 `/upload`, `/run/predict`, `/queue/join`, `/queue/data` 等具体端点。
- `proxy`: 代理地址，例如 `http://127.0.0.1:10809`，如果不需要代理则留空 `""`。
- `studio_token`: 用于访问 API 的 Studio Token，必须填写。
- `cookie_string`: 完整的 Cookie 字符串。通常 `studio_token` 会通过 Cookie 发送，但如果遇到认证问题（尤其是在 SSE 连接时），可以尝试从浏览器开发者工具中复制完整的 Cookie 值粘贴到这里。如果留空，插件会尝试仅使用 `studio_token` 作为 Cookie。
- `timeout`: 请求超时时间(秒)。

## 使用方法

用户可以在聊天中发送以下命令使用插件功能：

1. `#虚拟试衣` 或 `#试衣帮助`: 查看帮助信息
2. `#上传人物图片`: 上传人物照片
3. `#上传衣服图片`: 上传衣服照片
4. `#开始试衣`: 进行合成

## 注意事项

- 人物照片应清晰显示人物全身
- 衣服照片应清晰显示单件服装
- 合成过程需要 10-30 秒，请耐心等待
- 插件会自动清理超过 24 小时的临时文件和超过 7 天的结果文件

## 依赖

- aiohttp>=3.8.0
- aiofiles>=0.8.0
- pillow>=9.0.0
- sseclient-py>=1.7.2

## 开发者

- 作者: 老夏的金库
- 版本: 1.0.0
