# 厨房台面测试图

文件：`../kitchen-counter-test.png`。使用内置 image_gen 生成，人工目视核对：番茄 2 个、鸡蛋 3 个、土豆 2 个、胡萝卜 1 根、黄瓜 1 根。无重量标注，不能据此推断克数。

当前 MVP 视觉工具仍返回固定 mock 数据，上传该图片不会自动识别全部食材；可手动修正库存，后续用作视觉 API 测试样例。

## 最终生成提示词

Use case: photorealistic-natural. Generate a realistic smartphone photograph for testing a kitchen ingredient recognition app. A clean ordinary home kitchen countertop, slightly oblique overhead view, soft window daylight, realistic textures. On the counter clearly separated and fully visible: exactly 2 whole red tomatoes, exactly 3 whole chicken eggs, exactly 2 whole potatoes, exactly 1 carrot and exactly 1 cucumber. Arrange naturally with modest gaps so each object is easy to count. No other food, no bowls hiding ingredients, no people, no text, no labels, no watermark. Landscape composition, all ingredients in focus, not a collage.
