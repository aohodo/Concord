# Concord Case Console

Concord 的 Vue 人机协作界面。它不是独立的客服聊天框，而是 Python Agent
运行时的 Case Console：用户可以持续补充问题和证据，并观察 M1–M3 的进度。

## 当前能力

- 通过 `/chat` 进入真实 M1 → M2 → M3 主链路；
- 保持稳定 `case_id`，支持多轮补充、目标纠正和旧结果失效；
- 展示当前目标、事实、开放证据、进度事件和后台 Job；
- 支持暂停、恢复、取消和重新打开 Case；
- 允许用户覆盖回答深度与进度播报偏好；
- 支持图片、文本、日志、JSON/CSV 和带转写的语音证据；
- 默认展示短回答，完整细节可按需展开，避免把阅读成本强加给用户。

## 本地运行

先启动 Python 后端：

```powershell
cd ..\backend
D:\anaconda3\envs\concord\python.exe main.py
```

再启动前端：

```powershell
npm install
npm run dev
```

访问 `http://localhost:5173`。默认 API 地址为 `http://localhost:8000`，也可覆盖：

```powershell
$env:VITE_PYTHON_API_URL = "http://localhost:8000"
npm run dev
```

生产构建：

```powershell
npm run build
```

Java 目录只保留为只读参考，不属于 Concord 当前产品调用链。
