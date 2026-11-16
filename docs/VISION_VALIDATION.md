# P2 Qwen 视觉接入验证

日期：2026-11-16。分支：feat/vision-provider（基于尚未合并的 feat/mvp）。

## 实现

recognize_ingredients 读取服务端图片 ID 对应的清洗后 JPEG，通过 OpenAI 格式 image_url/data URL 调用 qwen3.8-flash。默认复用已有 .env 配置，允许 VISION_* 独立覆盖。

输出执行 JSON/Pydantic 校验、标准 ID 校验、重复项与单位检查。只允许可见个数或未知数量，不接受照片估算克数。未知食材与遮挡信息通过 uncertain 展示。工具仍在 Agent loop 内调用，识别后暂停等待用户确认。

前端状态由健康接口提供，真实调用模式不再显示“未接入视觉 API”。显式 mock 可用于离线测试；真实服务异常不会转成假识别结果。

## 已通过

- 上传 kitchen-counter-test.png，真实 Qwen 返回番茄 2、鸡蛋 3、土豆 2、胡萝卜 1、黄瓜 1；与人工标注完全一致。
- 通过确认接口继续匹配，任务 completed，返回黄瓜炒蛋、番茄炒蛋、土豆炒胡萝卜。
- 后端测试 12 项通过：既有 MVP 回归及视觉非法输出、空结果、不确定项、实际图片载荷、路径拒绝、服务失败不回退。
- 前端类型检查和生产构建通过。
- 真实联调使用 TestClient 与隔离临时数据库，没有启动或占用用户的本地服务。

以上是单张合成测试图的验证，不代表真实复杂厨房场景的准确率。浏览器视觉与交互验收仍由用户启动后检查。

## 重启与复测

停止旧进程，再从项目根目录执行 ./scripts/dev.sh，刷新页面并重新上传照片。旧任务不会被自动重新识别。

手动复测真实 API（产生 API 用量）：

    PYTHONPATH=backend .venv/bin/python scripts/check_vision.py
