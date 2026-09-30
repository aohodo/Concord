# Qwen3.8-Flash 图像输入烟测

日期：2026-09-28

## 模型能力依据

本机百炼模型索引 `models/groups/qwen3.8-flash.json` 记录：

- model: `qwen3.8-flash`
- request modality: `Image, Text, Video`
- response modality: `Text`
- context window: 1,000,000
- max input tokens: 991,808
- max output tokens: 131,072

因此 Concord 只接入图片输入，不设计图片输出。

## 代码验证

`OpenAICompatibleMessagesClient` 已支持：

- 原生 OpenAI `image_url` 内容块；
- Anthropic 风格 base64 `image` 内容块到 OpenAI 兼容格式的转换；
- `trust_env=False`，不继承虚拟网络代理。

适配器测试验证图像块不会被压扁或丢弃。

## 真实模型烟测

输入为无敏感信息的合成 VPN 故障截图，包含：

- `Connection failed`
- `Error 691: Access denied`
- `Network: Connected`
- `Last action: Restarted client 3 times`
- `Server logs are not shown in this screen.`

模型结构化输出：

```json
{
  "visible_problem": "Connection failed",
  "error_code": "Error 691: Access denied",
  "network_status": "Connected",
  "attempted_action": "Restarted client",
  "attempt_count": 3,
  "unavailable_evidence": "Server logs are not shown in this screen."
}
```

- wall time: 6.42 s
- stop reason: `stop`
- 代理：HTTP/HTTPS/ALL proxy 清空，客户端 `trust_env=False`
- 结果：PASS

## 证据边界

截图解析结果不能直接标记为系统已验证事实。接入 M1 时应保留三层来源：

1. 用户提供的原始媒体；
2. 模型从媒体中读取出的观察；
3. 后续工具或人工验证结果。

视觉模型应输出可定位的证据区域、转写文本和不确定性。媒体中不可见的信息必须保持 unknown/unavailable，不能靠常识补全。
