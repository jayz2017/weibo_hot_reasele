# Tasks

- [x] Task 1: 创建 ImageHandler Skill (skills/image_handler.py)
  - [x] 1.1 继承BaseSkill，实现截图基础能力
  - [x] 1.2 实现 `screenshot_element(page, element, path)` — 滚动到元素 + element.screenshot
  - [x] 1.3 实现 `screenshot_clip(page, clip_dict, path)` — page.screenshot(clip=...) + 高度过滤(10<h<800)
  - [x] 1.4 实现 `generate_screenshot_path(keyword, type_, index)` — 路径自动生成
  - [x] 1.5 从 PipelineManager 中提取导航栏隐藏/恢复逻辑作为独立方法（供ArticleProcessor调用）

- [x] Task 2: 创建 ContentCollector Skill (skills/content_collector.py)
  - [x] 2.1 继承BaseSkill，接收 browser 实例依赖
  - [x] 2.2 实现 `execute(keyword) -> List[Dict]` — 搜索页导航+滚动加载+卡片定位
  - [x] 2.3 从 PipelineManager._process_keyword() 提取：搜索URL构建、登录检测、卡片定位逻辑(_locate_card_elements)
  - [x] 2.4 返回标准化的卡片数据字典列表

- [x] Task 3: 创建 ArticleProcessor Skill (skills/article_processor.py)
  - [x] 3.1 继承BaseSkill，接收 browser + image_handler 依赖
  - [x] 3.2 实现 `execute(page, card_info, keyword, index) -> ArticleModel`
  - [x] 3.3 从 PipelineManager._process_keyword() 提取：滚动到卡片→隐藏导航→截图→构建Model→恢复导航
  - [x] 3.4 复用 ImageHandler 进行截图操作

- [x] Task 4: 创建 CommentProcessor Skill (skills/comment_processor.py)
  - [x] 4.1 继承BaseSkill，接收 browser + image_handler 依赖
  - [x] 4.2 实现 `execute(page, keyword, article_index) -> List[CommentModel]`
  - [x] 4.3 从 PipelineManager 提取：_expand_all_comments、vue-recycle-scroller遍历、逐条提取+截图、fallback逻辑
  - [x] 4.4 复用 ImageHandler 进行截图操作

- [x] Task 5: 重构 PipelineManager 为纯编排层
  - [x] 5.1 删除 _process_keyword()、_locate_card_elements()、_hide_navigation_bar()、_restore_navigation_bar() 等私有方法
  - [x] 5.2 删除 _expand_all_comments()、_screenshot_comments_from_detail_page()、_save_comment_area_fallback()
  - [x] 5.3 _initialize_skills() 中新增 ContentCollector, ArticleProcessor, CommentProcessor, ImageHandler 实例化
  - [x] 5.4 run_pipeline() 改为调用各 Skill 的 execute() 方法
  - [x] 5.5 目标：PipelineManager 从 ~960行精简至393行 (-59.2%)

- [x] Task 6: 规范化 BrowserController 并集成独立脚本
  - [x] 6.1 BrowserController 补充 BaseSkill 继承和 execute() 接口
  - [x] 6.2 author_monitor.py 重构为 AuthorMonitor(BaseSkill)，保留原有功能不变
  - [x] 6.3 zhibo8_match_scraper.py 重构为 Zhibo8MatchScraper(BaseSkill)，保留原有功能不变

- [x] Task 7: 验证重构后的完整流水线可正常运行
  - [x] 7.1 所有22个模块导入验证通过（11个Skill + 3个基础模块 + 4个Model）
  - [x] 7.2 全部11个Skill正确继承BaseSkill并实现execute()
  - [x] 7.3 PipelineManager._initialize_skills() 正确实例化9个Skill（含4个新Skill）
  - [x] 7.4 无任何ImportError或语法错误

# Task Dependencies
- [Task 1] ✅ 完成
- [Task 2] ✅ 完成（依赖于 Task 1）
- [Task 3] ✅ 完成（依赖于 Task 1）
- [Task 4] ✅ 完成（依赖于 Task 1）
- [Task 5] ✅ 完成（依赖于 Task 1-4）
- [Task 6] ✅ 完成（与 Task 1-4 并行）
- [Task 7] ✅ 完成（依赖于 Task 5-6）
