# 灶边 · Cook Helper

Python 自建 Agent loop + React Web 厨房助手 MVP。默认推理模型为 qwen3.8-flash，使用 OpenAI 格式的 chat completions 和原生工具调用。

## 本地启动

需要 Python 3.11+、Node.js 20.19+。在 cook-helper 根目录执行：

    cp .env.example .env
    # 填写 API_KEY 和 API_URL
    ./scripts/dev.sh

已有 docs/.env 时不必复制。配置优先级：环境变量 > 根目录 .env > docs/.env。API_URL 可填写 API 根地址（例如 https://provider.example/v1）或完整 chat/completions 地址。密钥仅由后端读取。

打开 http://127.0.0.1:8000 ，接口文档位于 http://127.0.0.1:8000/docs 。启动脚本首次安装依赖、构建前端并启动后端。开发前端热更新：后端启动后另开终端执行 npm --prefix frontend run dev。

离线演示：设置 REASONING_PROVIDER=mock，界面会明确标注；默认使用真实 API，不在调用失败时自动切换到模拟模式。

## 当前能力

在对话中上传照片或说明已有食材。助手把食材写入本轮库存，只在身份有歧义时追问。用户可以随时发消息补充或更正。信息齐全后展示最多三张合规菜谱卡片，点击“选这道菜”即可查看步骤、待买清单和营养估算。

照片通过 Qwen 视觉接口识别。可数食材保留个数，无法数清则记录为数量未知。若库存与菜谱单位不同，清单会列出所需用量，并注明库存余量未知。营养使用菜谱用量计算全菜和每人的能量、蛋白质、脂肪和碳水。参考食材、FDC ID 及每 100 克数值见 data/nutrition.yaml，鸡蛋按每个可食部 50 克估算。[USDA SR Legacy 数据下载](https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_csv_2018-04.zip)

12 道 YAML 菜谱在 data/recipes，标准食材字典在 data/ingredients.yaml。匹配先硬过滤限制，再按种类覆盖 70% + 数量满足 30% 评分，主料/辅料/调料权重为 3/2/1。库存、偏好由服务器绑定，模型不能覆盖。Agent 在对话中调度识别、库存、匹配、待买清单和营养工具。工具结果回传给模型，助手气泡展示模型生成的 Markdown；菜谱卡片使用服务端匹配结果供用户选择。

## 检查

    PYTHONPATH=backend .venv/bin/python scripts/check_llm.py
    PYTHONPATH=backend .venv/bin/python scripts/check_vision.py
    PYTHONPATH=backend .venv/bin/pytest backend/tests -q
    npm --prefix frontend run build

check_llm 会产生少量真实 API 调用，验证文本、工具调用和工具结果回传。后端测试使用隔离的临时数据库和 mock。

## 运行与开发约定

当前为单用户本地应用，默认仅监听本机，无登录鉴权。SQLite 与压缩、清除元数据后的上传图片保存在 runtime，不提交 Git。服务重启时未完成任务会标记失败，可重新创建。取消后已发出的 API 请求可能仍完成，但不会覆盖取消状态。

第一版的功能、修复和文档都直接提交到 `version/v1`，按可验证的改动分次 commit。第一版验收后再发布到 `main`。开始第二个大版本时，从已验收的第一版创建 `version/v2`。

详见 [实施计划](docs/IMPLEMENTATION_PLAN.md) 和 [验证记录](docs/MVP_VALIDATION.md)。

## 视觉配置

默认复用推理模型地址和密钥，可独立配置 VISION_API_KEY、VISION_BASE_URL、VISION_MODEL、VISION_PROVIDER。未设置时分别继承推理配置。修改代码或配置后，停止原服务并重新运行 ./scripts/dev.sh，刷新页面后重新上传图片；旧任务保留原先结果。

视觉功能验证见 [P2 验证记录](docs/VISION_VALIDATION.md)。
