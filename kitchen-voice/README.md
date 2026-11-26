# 智能厨房助手：语音模块

实现：Python录音 → 云端识别ASR → Agent → 云端语音合成TTS → 本机音箱播放。ASR和TTS可分别选择阿里云或讯飞。目标设备为树莓派4B、C270i自带USB麦克风、蓝牙音箱和显示屏。

**调试先读：[树莓派硬件调试说明](docs/树莓派联合调试指南.md)。** 首轮用网页完成录音、播放和云端测试，再接实体按钮、真实Agent、视频与手势。默认Agent为mock，只模拟文字交接，不控制视频。

## 在灶边工作台使用

在 Cook Helper 仓库根目录安装开发和语音依赖。如果已有 `.venv`，直接执行安装命令即可。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt -r kitchen-voice/requirements.txt
./scripts/dev.sh
```

把私密配置放在 `kitchen-voice/.env`，或在项目根目录的 `.env` 填写语音配置。系统环境和根目录配置优先。讯飞使用 `ASR_PROVIDER=xfyun` 与 `XFYUN_APP_ID`、`XFYUN_API_KEY`、`XFYUN_API_SECRET`。阿里云使用 `ASR_PROVIDER=aliyun` 与 `DASHSCOPE_API_KEY`、`DASHSCOPE_WORKSPACE`，地域可填写 `DASHSCOPE_REGION`。

打开主应用后点击「语音」，允许浏览器访问麦克风。再次点击「停止」后，录音经所配置的服务转写，文字填进输入栏，可修改再发送。单段录音最多 20 秒。浏览器麦克风需要本机地址或 HTTPS。未配置云端识别时，支持语音识别的浏览器可继续使用自带的转写功能。

## 文件存放

源码包已经上传GitHub；私密配置包包含真实密钥，已私发。源码包本身不包含真实`.env`。

```text
kitchen-voice/
├─ .env                 ← 私密包里的文件放这里，与README.md同级
├─ .env.example         ← 空白模板，不是真实配置
├─ .gitignore
├─ README.md
├─ requirements.txt
├─ requirements-dev.txt
├─ voice/               ← 源码和测试网页
├─ tests/               ← 不调用真实云端的自动测试
└─ docs/                ← 调试说明和验收记录
```

已有自己的`.env`时先备份再合并。名称必须为`.env`，不能是`.env.txt`，不用把内容粘贴进Python。私密配置副本已清空电脑设备编号，恢复录音采样率初始值；其他云端配置保留，原开发机配置未修改。

## 树莓派安装与启动

建议使用64位Raspberry Pi OS桌面版和系统自带Python 3.11～3.13，实际音频仍须现场验证。先进入解压后的`kitchen-voice`目录：

```bash
sudo apt update
sudo apt install python3-full python3-venv libportaudio2 libsndfile1
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

把私密配置包中的`.env`放好，再执行：

```bash
.venv/bin/python -m voice providers
.venv/bin/python -m voice serve
```

在**树莓派自己的浏览器**打开`http://127.0.0.1:8765/`。终端须一直运行；Ctrl+C停止服务。关闭网页不会停止服务。修改`.env`后先停止旧服务，再重新启动。不要用sudo运行Python服务，以免改变音频会话。

`voice providers`只检查配置是否填写，不打印密钥、不调用云端；真实权限和额度要实际测试。

## Windows同学测试

进入本目录，使用本机Python创建环境，不复制别人的虚拟环境。以Python3.12为例：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m voice providers
.\.venv\Scripts\python.exe -m voice serve
```

已安装3.11时可将第一行改为`py -3.11`。现有开发机继续使用已有`.venv`即可，无需重建。启动前放好`.env`，浏览器打开`http://127.0.0.1:8765/`，旧页面用Ctrl+F5刷新。

## 测试顺序

1. 连接设备，重新扫描、选择并应用，播放提示音。
2. 录一句话，结束后回放，先确认本地声音清楚。
3. 点击识别，核对菜名和数字。
4. 用短句先测整段合成，再测边生成边播放与停止。
5. 测完整流程；mock回复不代表视频控制已接通。

页面控制运行Python服务那台机器的设备。录音和本地回放不联网；识别和合成会发送录音/文字给所选云服务，可能计费。全部识别文字都交给Agent，不在本模块匹配固定指令。

## 服务商和声音

`.env`中的`ASR_PROVIDER`、`TTS_PROVIDER`分别填`aliyun`或`xfyun`，未设置时兼容阿里云。页面可临时分别切换，应用后只在本次运行有效，不改`.env`。

- 讯飞：`XFYUN_APP_ID`、`XFYUN_API_KEY`、`XFYUN_API_SECRET`；两服务凭据不同时分别填`XFYUN_ASR_*`和`XFYUN_TTS_*`。对应语音听写流式v2和在线语音合成v2。见[配置与分步测试](docs/讯飞配置与分步测试.md)。
- 阿里云：`DASHSCOPE_API_KEY`、`DASHSCOPE_WORKSPACE`、`DASHSCOPE_REGION`；地域、业务空间和密钥须匹配。
- 讯飞音色：`XFYUN_TTS_VOICE`；语速/音高/音量：`XFYUN_TTS_SPEED`、`XFYUN_TTS_PITCH`、`XFYUN_TTS_VOLUME`，默认50、范围0～100。音色用控制台已开通的参数。

修改后重启并重新生成语音；“再次播放”播放的是旧文件。

## 现有接口

只监听本机`127.0.0.1:8765`，单进程运行，完整参数见本机`/docs`。

| 接口 | 用途 |
|---|---|
| `GET /health` | 服务、录音与忙状态 |
| `GET /providers` | 服务商和配置状态，不返回密钥 |
| `GET /devices` | 音频设备 |
| `POST /devices/refresh` | 停止放音、重新扫描并清空旧选择 |
| `POST /settings/devices` | 应用输入/输出编号，仅本次运行有效 |
| `POST /record/start`、`POST /record/stop` | 开始录音、结束并返回文件名 |
| `POST /asr` | JSON `{"file":"录音文件名.wav"}`，仅识别 |
| `POST /agent` | JSON `{"text":"文字"}`，仅测Agent |
| `POST /tts` | JSON `{"text":"回复"}`，仅生成WAV |
| `POST /tts/speak` | 流式合成并播放 |
| `POST /play`、`POST /play/stop` | 播放指定文件、停止放音 |
| `POST /pipeline` | JSON `{"file":"录音文件名.wav","play":true}`，完整链路 |

实体按钮GPIO驱动尚未包含，但网页可以完整模拟录音交互。按钮适配可先写可配置框架，无需等装好；正式接线前确认GPIO编号和信号电平，细节见硬件说明。

真实Agent配置`AGENT_MODE=http`、`AGENT_URL`和可选`AGENT_TOKEN`。请求`{"request_id":"唯一编号","text":"识别原文"}`；响应`{"request_id":"原编号","reply_text":"可朗读回复"}`。Agent取得动作结果后再确认成功；语音模块不执行额外工具字段，不自动重试命令。

## 测试、版本与GitHub

最近一次44项本地自动测试通过；当前电脑设备扫描与输出已有用户确认，ARM64/Python 3.13的全部依赖已完成解析下载验证。树莓派、GPIO、真实Agent等仍须逐项填写[人工验收记录](docs/人工验收记录.md)，不预填通过。最终判断见[树莓派4B兼容性审查](docs/树莓派4B最终兼容性审查.md)。

开发者用项目虚拟环境执行：

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

GitHub只上传本目录的源码、测试、依赖、模板和文档；不上传`.env`、私密配置包、虚拟环境、`runtime`录音和缓存。保留`.gitignore`，上传前检查暂存列表。源码包内`交付清单.json`提供版本和文件SHA256供双方核对。
