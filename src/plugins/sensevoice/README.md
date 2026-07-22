# SenseVoice ASR 插件

本地 SenseVoice-Small INT8 ONNX 语音转文本服务。作为 OpenSquad 内置插件，可在 Agent Web「ASR 输入」中选择 **系统内置 SenseVoice ASR**。

## 重要：模型不随安装自动下载

首次部署 OpenSquad **不会**下载 SenseVoice 模型（约 150MB）。请：

1. 打开侧栏 **SenseVoice ASR** 插件面板
2. 点击 **下载模型**（来源：[ModelScope iic/SenseVoiceSmall](https://www.modelscope.cn/models/iic/SenseVoiceSmall)）
3. 下载完成后在面板或「服务管理」中 **启动服务**
4. 在 Agent Web 语音配置 → ASR 输入选择 **系统内置 SenseVoice ASR**

模型保存在：`{workspace}/data/plugins/sensevoice/model/`

## 依赖

```bash
pip install onnxruntime soundfile librosa numpy pyyaml flask flask-cors modelscope
```

另需本机安装 **ffmpeg**（浏览器 webm 等格式转 16k wav）。

## 服务端口

默认 `7101`（`ports.sensevoice` / 插件配置 `port`）。

OpenAI 兼容端点：`POST http://127.0.0.1:7101/v1/audio/transcriptions`
