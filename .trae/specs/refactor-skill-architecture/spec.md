# 项目Skills架构重构 Spec

## Why

当前项目虽然目录结构基本符合设计文档，但 **实际代码并未按照设计文档的"每个功能一个独立Skill"原则组织**。核心问题是 `PipelineManager`（[pipeline.py](file:///e:\haochenkeji\weibo_hot_reasele\core\pipeline.py)）演变成了一个 **~960行的上帝类**，将本应属于 ContentCollector、ArticleProcessor、CommentProcessor、ImageHandler 的全部逻辑内聚其中，导致：

1. **职责不清**：PipelineManager 同时负责页面导航、卡片定位、文章截图、评论截图、导航栏隐藏、评论展开等10+种不同职责
2. **无法独立运行**：各个功能模块无法脱离Pipeline单独调用和测试
3. **扩展困难**：新增数据源（如zhibo8）只能通过创建独立脚本（`zhibo8_main.py` + `author_monitor_main.py`），无法复用已有Skills
4. **违反BaseSkill契约**：部分现有Skill未继承BaseSkill或未实现execute()接口

## What Changes

### 现状分析

| 设计文档规划的7个Skill | 当前状态 | 问题 |
|----------------------|---------|------|
| Skill 1: HotSearchCrawler | ✅ 已实现，继承BaseSkill | 正常 |
| Skill 2: DataFilter | ✅ 已实现，继承BaseSkill | 正常 |
| **Skill 3: ContentCollector** | **❌ 缺失** | 逻辑散落在PipelineManager._process_keyword() |
| **Skill 4: ArticleProcessor** | **❌ 缺失** | 逻辑散落在PipelineManager._process_keyword() |
| **Skill 5: CommentProcessor** | **❌ 缺失** | 逻辑散落在PipelineManager._screenshot_comments_from_detail_page() |
| **Skill 6: ImageHandler** | **❌ 缺失** | 截图逻辑分散在多处 |
| Skill 7: StorageManager | ✅ 已实现，继承BaseSkill | 正常 |

### 重构计划

#### 1. 新建 `skills/content_collector.py` — ContentCollector(BaseSkill)
从 `PipelineManager._process_keyword()` 中提取：
- 搜索页导航与加载 (`navigate_to`, `scroll_to_load_more`)
- 微博卡片元素定位 (`_locate_card_elements`)
- 文章详情URL提取与转换
- 登录检测与重定向处理
- **接口**: `async execute(keyword: str) -> List[Dict]` 返回卡片数据列表

#### 2. 新建 `skills/article_processor.py` — ArticleProcessor(BaseSkill)
从 `PipelineManager._process_keyword()` 中提取：
- 单篇文章截图（搜索页卡片截图）
- 导航栏隐藏/恢复 (`_hide_navigation_bar`, `_restore_navigation_bar`)
- ArticleModel 构建与封装
- **接口**: `async execute(page, card_info, keyword) -> ArticleModel`

#### 3. 新建 `skills/comment_processor.py` — CommentProcessor(BaseSkill)
从 `PipelineManager._screenshot_comments_from_detail_page()` 中提取：
- 详情页评论区定位与展开 (`_expand_all_comments`)
- vue-recycle-scroller 虚拟滚动评论遍历
- 逐条评论文字提取 + 截图
- Fallback 整体截图策略
- **接口**: `async execute(page, keyword, article_index) -> List[CommentModel]`

#### 4. 新建 `skills/image_handler.py` — ImageHandler(BaseSkill)
整合分散在各处的截图逻辑：
- element.screenshot 封装（含视口滚动到元素）
- page.screenshot(clip=...) 封装
- 全页截图 fallback
- 截图路径自动生成与管理
- 高度过滤（拒绝>800px的异常截图）
- **接口**: `screenshot_element(page, element, path) -> bool`, `screenshot_clip(page, clip, path) -> bool`

#### 5. 重构 `core/pipeline.py` — PipelineManager 精简
- 删除所有私有方法（`_process_keyword`, `_locate_card_elements`, `_hide_navigation_bar`, `_restore_navigation_bar`, `_expand_all_comments`, `_screenshot_comments_from_detail_page`, `_save_comment_area_fallback`）
- 改为调用各Skill的 `execute()` 方法编排流程
- 目标：从 ~960行精简至 ~150行纯编排代码

#### 6. 规范化现有Skill — 统一BaseSkill契约
- `BrowserController`: 补充BaseSkill继承，实现execute()
- `ArticleScreenshot`: 合并入ImageHandler + ArticleProcessor
- `CommentScreenshot`: 合并入ImageHandler + CommentProcessor
- `DataExtractor`: 确认BaseSkill继承正确

#### 7. 集成独立脚本到Skill体系
- `author_monitor.py` → 重构为继承BaseSkill的 `AuthorMonitorSkill`
- `zhibo8_match_scraper.py` → 重构为继承BaseSkill的 `Zhibo8MatchScraperSkill`
- 两者均可被PipelineManager按需调度

## Impact

- Affected specs: 所有涉及微博热榜采集流程的功能
- Affected code:
  - [core/pipeline.py](file:///e:\haochenkeji\weibo_hot_reasele\core\pipeline.py) — 主要重构目标，大幅精简
  - [skills/](file:///e:\haochenkeji\weibo_hot_reasele\skills\) 目录 — 新增4个Skill文件，修改3个现有文件
  - [author_monitor_main.py](file:///e:\haochenkeji\weibo_hot_reasele\author_monitor_main.py) — 调整为使用新Skill
  - [zhibo8_main.py](file:///e:\haochenkeji\weibo_hot_reasele\zhibo8_main.py) — 调整为使用新Skill
  - [main.py](file:///e:\haochenkeji\weibo_hot_reasele\main.py) — 无需改动（通过PipelineManager间接受益）

## ADDED Requirements

### Requirement: ContentCollector Skill
系统 SHALL 提供 `ContentCollector` 类，继承自 `BaseSkill`，负责微博热搜关键词的内容搜索与采集。

#### Scenario: 搜索关键词并返回文章卡片数据
- **WHEN** 调用 `ContentCollector.execute(keyword="某热搜词")`
- **THEN** 系统访问微博搜索页，滚动加载内容，定位微博卡片元素，返回包含 author_name, content_text, detail_url, publish_time, repost_count, comment_count, like_count 的字典列表

#### Scenario: 搜索页需要登录
- **WHEN** 搜索页重定向到登录页
- **THEN** 返回特殊标记的ArticleModel，提示需要配置Cookie

### Requirement: ArticleProcessor Skill
系统 SHALL 提供 `ArticleProcessor` 类，继承自 `BaseSkill`，负责单篇文章的数据处理与截图。

#### Scenario: 处理文章卡片并截图
- **WHEN** 调用 `ArticleProcessor.execute(page, card_info, keyword)`
- **THEN** 隐藏导航栏 → 滚动到卡片位置 → 截图 → 构建ArticleModel → 恢复导航栏 → 返回ArticleModel

### Requirement: CommentProcessor Skill
系统 SHALL 提供 `CommentProcessor` 类，继承自 `BaseSkill`，负责文章评论区数据提取与逐条截图。

#### Scenario: 提取并截图评论区
- **WHEN** 调用 `CommentProcessor.execute(detail_page, keyword, article_index)`
- **THEN** 滚动到评论区 → 展开所有评论 → 逐条滚动+提取文字+截图 → 返回List[CommentModel]

### Requirement: ImageHandler Skill
系统 SHALL 提供 `ImageHandler` 类，继承自 `BaseSkill`，统一管理所有截图操作。

#### Scenario: 元素截图
- **WHEN** 调用 `ImageHandler.screenshot_element(page, element, path)`
- **THEN** 滚动元素到视口 → 调用element.screenshot → 返回成功/失败

#### Scenario: Clip区域截图
- **WHEN** 调用 `ImageHandler.screenshot_clip(page, clip_dict, path)`
- **THEN** 使用page.screenshot(clip=...)截图 → 高度校验(10<h<800) → 返回成功/失败

### Requirement: PipelineManager 精简
重构后的 `PipelineManager` SHALL 只保留流水线编排逻辑，不包含任何具体业务实现。

#### Scenario: 通过Skill编排执行完整流程
- **WHEN** 调用 `PipelineManager.run_pipeline()`
- **THEN** 按 Step1→Step6 顺序依次调用各Skill的execute()方法，不直接操作浏览器DOM

## MODIFIED Requirements

### Requirement: BrowserController 规范化
`BrowserController` SHALL 继承 `BaseSkill` 并实现 `execute()` 方法用于启动浏览器。

### Requirement: 独立脚本集成
`AuthorMonitor` 和 `Zhibo8MatchScraper` SHALL 重构为继承 `BaseSkill` 的标准Skill，可通过PipelineManager或其他入口统一调度。
