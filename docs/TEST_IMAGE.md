# 厨房台面测试图

文件：`../kitchen-counter-test.png`。使用内置 image_gen 生成，人工目视核对：番茄 2 个、鸡蛋 3 个、土豆 2 个、胡萝卜 1 根、黄瓜 1 根。无重量标注，不能据此推断克数。

视觉工具现已接入 Qwen；此图真实识别的五类食材及数量与上述人工标注一致。可用于 scripts/check_vision.py 集成检查。

## 最终生成提示词

Use case: photorealistic-natural. Generate a realistic smartphone photograph for testing a kitchen ingredient recognition app. A clean ordinary home kitchen countertop, slightly oblique overhead view, soft window daylight, realistic textures. On the counter clearly separated and fully visible: exactly 2 whole red tomatoes, exactly 3 whole chicken eggs, exactly 2 whole potatoes, exactly 1 carrot and exactly 1 cucumber. Arrange naturally with modest gaps so each object is easy to count. No other food, no bowls hiding ingredients, no people, no text, no labels, no watermark. Landscape composition, all ingredients in focus, not a collage.
