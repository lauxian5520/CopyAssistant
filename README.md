# CopyAssistant

CopyAssistant 是面向 Windows 的剪贴板 AI 助手。复制一段文字或一张截图，预览输入后，可以用选定的模型翻译、总结或解释。

[下载 Windows x64 免安装版](https://github.com/lauxian5520/CopyAssistant/releases/latest)

## 使用方法

1. 下载名称以 `windows-x64.zip` 结尾的 Release 附件，解压整个目录并双击 `CopyAssistant.exe`。
2. 在「设置 → AI 服务」中编辑预置的 DeepSeek，或新增其他兼容服务。填写 API URL、API Key 和模型 ID。仅当对应模型支持图片时勾选「此模型支持图片」。
3. 在其他应用复制文字，或通过 Windows 截图工具复制位图。在主窗口选择服务与处理模式，点击「处理」。
4. 在结果区查看输出，点击「复制结果」可将它写回剪贴板。在「设置 → 提示词」可修改三个 System Prompt 和翻译目标语言。

自动处理默认关闭。开启后，每次新复制的内容会发送到当前选择的服务。进入设置时自动关闭，以免发送正在复制的配置项。运行中的请求结束后，只处理等待期间最新的一项。关闭窗口会结束监听。

主窗口默认置顶，切到浏览器或阅读器时仍会显示在上方。取消工具栏里的「总是置顶」可恢复普通窗口，重启后会保留选择。

图片需要复制到剪贴板。复制资源管理器中的文件只提供文件路径。图片按原分辨率转为 PNG 后发送，编码后的大小上限为 20 MiB。

## API 配置

服务须兼容 OpenAI Chat Completions。API URL 接受基础地址（如 `https://api.deepseek.com` 或 `https://api.example.com/v1`）和以 `/chat/completions` 结尾的完整地址。远程服务须使用 HTTPS，环回地址也可使用 HTTP。DeepSeek 示例使用 `deepseek-flash`，其他模型可在设置中填写服务商给出的 ID。

API Key 保存在当前 Windows 用户的凭据管理器。发布版的普通设置和日志位于 `%LOCALAPPDATA%\CopyAssistant\data\copy_assistant`，更新时解压新版即可沿用原配置。应用会把当前输入与模式提示词发送给选中的服务。剪贴板内容和处理结果只保留在内存中。

## 从源码运行

在 Windows x64 上安装 Python 3.11，从仓库根目录执行以下 PowerShell 命令。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Set-Location code/copy_assistant
..\..\.venv\Scripts\python.exe app.py
```

开发时的设置和日志位于 `data/copy_assistant/`。在 `code/copy_assistant/` 运行测试。

```powershell
..\..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 构建 Windows ZIP

在仓库根目录安装构建依赖，再执行打包脚本。首次构建会下载与打包版本匹配的 Qt 源码归档，用于提取随包分发的许可和第三方声明。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

构建完成后，可直接运行 `dist/CopyAssistant/CopyAssistant.exe`，或解压 `dist/` 下的 ZIP 后运行。请将 EXE 和同目录的 `_internal/` 一起保留。`build/` 存放 PyInstaller 的中间文件，其中的 EXE 缺少运行时依赖。

PySide、Shiboken 和 Qt DLL 作为独立文件保留在解压目录中，可以替换成兼容的重新构建版本。对应源码位置和组件版本见 ZIP 内的 `third_party/COMPONENTS.txt`。

## 许可证

CopyAssistant 源码采用 [MIT](LICENSE)。随程序分发的依赖保留各自的许可证，见 ZIP 内的 `third_party/`。
