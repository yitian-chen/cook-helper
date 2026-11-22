# 灶边 · Cook Helper

Python 自建 Agent loop + React Web 厨房助手 v2。默认推理模型为 qwen3.8-flash，使用 OpenAI 格式的 chat completions 和原生工具调用。

## 本地启动

需要 Python 3.11+、Node.js 20.19+。在 cook-helper 根目录执行：

    cp .env.example .env
    # 填写 API_KEY 和 API_URL
    ./scripts/dev.sh

已有 docs/.env 时不必复制。配置优先级：环境变量 > 根目录 .env > docs/.env。API_URL 可填写 API 根地址（例如 https://provider.example/v1）或完整 chat/completions 地址。密钥仅由后端读取。

打开 http://127.0.0.1:8000 ，接口文档位于 http://127.0.0.1:8000/docs 。启动脚本首次安装依赖、构建前端并启动后端。开发前端热更新：后端启动后另开终端执行 npm --prefix frontend run dev。

离线演示：设置 REASONING_PROVIDER=mock，界面会明确标注；默认使用真实 API，不在调用失败时自动切换到模拟模式。

## 当前能力

上传厨房照片或告诉助手可用食材，系统会按人数搭配一套餐单。两人默认一主菜、一配菜和主食。可以说明“不吃主食”“想喝汤”，或用卡片和聊天替换一道菜。整餐按食材合并用量并扣除一次库存，已知存在但重量不明确的食材保留份量参考。助手根据工具结果给出购物建议和营养估算。缺少营养数据时显示未计入项。

点击「食材已备齐」或在对话中确认后，首次填写厨房配置，注明下厨人数、灶眼、锅具和设备数量。助手根据菜谱预设的操作时长、依赖及资源占用生成相对时间线。时间是估算值，大份量、火力和锅具大小会影响实际耗时。缺电饭锅时可使用汤锅煮饭。按时间线勾选完成的步骤，刷新后会恢复当前菜单、对话和进度。换菜、调整份量或厨房配置后会重新排程。

独立菜谱在 data/recipes，基础白米饭与 100 道菜的预设操作在 data/cooking_steps.yaml。v2 状态存放在原 SQLite 数据库的独立记录中，更新使用版本检查。前端通过 /api/v2/current 恢复，通过 /api/v2/turn 发送对话及菜单操作，/api/v2/kitchen 和 /api/v2/progress 保存厨房配置和步骤进度。

菜谱包含 92 道中餐和 8 道西餐。每道菜谱都有菜系 `cuisine` 和餐桌角色 `course`。菜系包括家常中餐、川菜、江浙菜、东北菜、鲁菜、粤菜和西餐。角色包括主菜、配菜、汤羹、主食和甜品。`tags` 记录家常、快手、辣味、酸甜等特征，其中快手指 20 分钟内。分类随匹配结果返回，留给后续偏好打分使用。部分新增食材尚无可用的营养参考值，估算时会列出未计入项。

## 检查

    PYTHONPATH=backend .venv/bin/python scripts/check_llm.py
    PYTHONPATH=backend .venv/bin/python scripts/check_vision.py
    PYTHONPATH=backend .venv/bin/pytest backend/tests -q
    npm --prefix frontend run build

check_llm 会产生少量真实 API 调用，验证文本、工具调用和工具结果回传。后端测试使用隔离的临时数据库和 mock。

## 运行与开发约定

当前为单用户本地应用，默认仅监听本机，无登录鉴权。SQLite 与压缩、清除元数据后的上传图片保存在 runtime，不提交 Git。服务重启时未完成任务会标记失败，可重新创建。取消后已发出的 API 请求可能仍完成，但不会覆盖取消状态。

当前开发分支为 `version/v2`，按可验证的开发阶段分次提交。

详见 [实施计划](docs/IMPLEMENTATION_PLAN.md) 和 [验证记录](docs/MVP_VALIDATION.md)。

## 视觉配置

默认复用推理模型地址和密钥，可独立配置 VISION_API_KEY、VISION_BASE_URL、VISION_MODEL、VISION_PROVIDER。未设置时分别继承推理配置。修改代码或配置后，停止原服务并重新运行 ./scripts/dev.sh，刷新页面后重新上传图片；旧任务保留原先结果。

视觉功能验证见 [P2 验证记录](docs/VISION_VALIDATION.md)。
