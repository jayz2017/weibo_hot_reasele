# 截图DOM选择器优化计划

## 一、问题分析

### 当前状态
- **文章截图**：使用 `.card-wrap` 作为截图元素，但顶部包含导航栏
- **评论截图**：使用 `.card-wrap` 内的子元素或整页截图，不够精准

### 目标状态
- **文章截图**：使用 `class="vue-recycle-scroller__item-view"` 作为最外围元素
- **评论截图**：使用 `div class="wbpro-list"` 作为最外围元素

---

## 二、需要修改的位置

### 文件：`core/pipeline.py`

#### 修改点 1：`_process_keyword()` 方法中的文章截图逻辑（约第264-342行）

**当前代码问题**：
```javascript
// ❌ 当前使用 .card-wrap
const cards = document.querySelectorAll('.card-wrap');
const card = cards[{card_index}];
const rect = card.getBoundingClientRect();
```

**应改为**：
```javascript
// ✅ 改为使用 .vue-recycle-scroller__item-view
const items = document.querySelectorAll('.vue-recycle-scroller__item-view');
const item = items[{card_index}];
const rect = item.getBoundingClientRect();
```

**涉及的具体位置**：
1. 第276-281行：滚动到卡片位置 → `.card-wrap` → `.vue-recycle-scroller__item-view`
2. 第291-298行：隐藏评论区元素 → `.card-wrap` → `.vue-recycle-scroller__item-view`
3. 第302-315行：获取bounding box进行截图 → `.card-wrap` → `.vue-recycle-scroller__item-view`
4. 第333-341行：恢复评论区显示 → `.card-wrap` → `.vue-recycle-scroller__item-view`

#### 修改点 2：`_locate_card_elements()` 方法（约第398-448行）

**当前代码问题**：
```javascript
// ❌ 当前使用 .card-wrap 定位卡片
const cards = document.querySelectorAll('.card-wrap');
```

**应改为**：
```javascript
// ✅ 改为使用 .vue-recycle-scroller__item-view
const items = document.querySelectorAll('.vue-recycle-scroller__item-view');
// 内部查找 .card-wrap 获取数据（因为数据在 .card-wrap 内）
const card = item.querySelector('.card-wrap');
```

#### 修改点 3：点击评论按钮（约第360-373行）

**当前代码**：
```javascript
const cards = document.querySelectorAll('.card-wrap');
const card = cards[{card_index}];
```

**应改为**：
```javascript
const items = document.querySelectorAll('.vue-recycle-scroller__item-view');
const item = items[{card_index}];
const card = item.querySelector('.card-wrap'); // 在item内找card-wrap
```

#### 修改点 4：`_screenshot_individual_comments()` 方法 - 评论区整体截图（约第496-504行）

**当前代码**：当找不到评论时，截取整页

**应改为**：使用 `wbpro-list` 作为评论区的外围容器进行精准截图

```javascript
// ✅ 使用 wbpro-list 截取评论区
const commentListEl = document.querySelector('.wbpro-list');
if (commentListEl) {
    const rect = commentListEl.getBoundingClientRect();
    // 使用 rect 进行 clip 截图
}
```

#### 修改点 5：`_screenshot_individual_comments()` 方法 - 逐条评论截图（约第460-494行）

**当前代码**：
```javascript
const cards = document.querySelectorAll('.card-wrap');
const card = cards[{card_index}];
const commentEls = card.querySelectorAll(...);
```

**应改为**：
```javascript
// 在 wbpro-list 内查找评论，或在 vue-recycle-scroller__item-view 内查找
const items = document.querySelectorAll('.vue-recycle-scroller__item-view');
const item = items[{card_index}];

// 方案A: 直接在 wbpro-list 中查找所有评论
const commentListEl = document.querySelector('.wbpro-list');
if (commentListEl) {
    const commentEls = commentListEl.querySelectorAll('.list_li, .WB_text, ...');
}

// 方案B: 或在 item 内查找 card-comment 区域
const commentArea = item.querySelector('.card-comment, .wbpro-list, [class*="comment"]');
```

#### 修改点 6：滚动到评论位置（约第512-522行）

**当前代码**：在 `.card-wrap` 内滚动

**应改为**：在 `wbpro-list` 内或使用评论元素的直接滚动

---

## 三、实施步骤

### Step 1：修改 `_locate_card_elements()` 方法
- 选择器从 `.card-wrap` 改为 `.vue-recycle-scroller__item-view`
- 数据提取仍通过 `item.querySelector('.card-wrap')` 获取

### Step 2：修改 `_process_keyword()` 中的文章截图部分
- 所有 `.card-wrap` 引用改为 `.vue-recycle-scroller__item-view`
- 滚动、隐藏评论区、获取bounding box、恢复显示全部更新

### Step 3：修改评论按钮点击逻辑
- 在 `.vue-recycle-scroller__item-view` 内查找 `.card-wrap` 再找按钮

### Step 4：重写 `_screenshot_individual_comments()` 的评论区定位逻辑
- 优先使用 `.wbpro-list` 作为评论容器
- 如果 `.wbpro-list` 不存在，降级为在 `.vue-recycle-scroller__item-view` 内查找评论区域

### Step 5：运行测试验证效果
- 运行 `python main.py`
- 检查生成的截图是否符合预期

---

## 四、关键代码变更预览

### 文章截图核心变更
```python
# Before
card_box = await page.evaluate(f"""
    () => {{
        const cards = document.querySelectorAll('.card-wrap');
        const card = cards[{card_index}];
        ...
    }}
""")

# After  
card_box = await page.evaluate(f"""
    () => {{
        const items = document.querySelectorAll('.vue-recycle-scroller__item-view');
        const item = items[{card_index}];
        if (!item) return null;
        const rect = item.getBoundingClientRect();
        return {{ x: rect.x, y: rect.y, width: rect.width, height: rect.height }};
    }}
""")
```

### 评论截图核心变更
```python
# Before: 整页截图或 .card-wrap 内查找
await self.browser.take_screenshot(page, comment_screenshot_path, full_page=False)

# After: 精准截取 wbpro-list 区域
comment_list_box = await page.evaluate("""
    () => {
        const listEl = document.querySelector('.wbpro-list');
        if (!listEl) return null;
        const rect = listEl.getBoundingClientRect();
        return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
    }
""")

if comment_list_box:
    await page.screenshot(path=comment_screenshot_path, clip=comment_list_box)
```

---

## 五、预期效果

### 文章截图
- ✅ 只包含单条微博内容（作者头像+昵称+正文+配图）
- ✅ 不包含页面导航栏
- ✅ 不包含评论区
- ✅ 每条微博独立一张图片

### 评论截图
- ✅ 精准截取 `.wbpro-list` 容器区域
- ✅ 包含完整的评论列表
- ✅ 不包含无关的页面元素
