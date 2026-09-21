# weibo_hot_reasele - Codex 项目技能描述

此技能文件用于 Codex 快速理解和修改"微博热榜爬虫系统"项目。
当你被问到需要修改此项目时，请按以下层级理解并定位代码。

---

## 项目概述

基于 Python 的微博热榜数据采集系统，含热榜抓取、内容采集、评论截图、语义分析、作者监控、直播吧评论爬取。
采用 Skill 架构：每个功能是一个独立模块，继承自 `core/base.py` 的 `BaseSkill`。

---

## 目录结构速查

| 路径 | 说明 |
|------|------|
| main.py | 主入口（完整流水线） |
| api_server.py | FastAPI Web服务 |
| config.yaml | 全局YAML配置 |
| core/ | 核心层（BaseSkill, ConfigManager, exceptions, pipeline） |
| skills/ | 12个业务Skill |
| models/ | 4个数据模型 |
| utils/ | 7个工具模块 |
| data/ | 数据/日志/截图目录 |

---

## 各文件职责定位（快速定位修改目标）

### 入口文件

| 文件 | 功能 | 依赖的核心文件 |
|------|------|---------------|
| main.py | 完整流水线/单关键词/仅热榜 | core/pipeline.py, skills/hotsearch_crawler.py, skills/data_filter.py |
| api_server.py | FastAPI REST API | skills/hotsearch_crawler.py, skills/data_filter.py, utils/mysql_manager.py |
| author_monitor_main.py | 微博作者监控独立入口 | skills/author_monitor.py, skills/browser_controller.py, utils/mysql_manager.py |
| keyword_analyze_main.py | 热词全链路分析入口 | core/pipeline.py (run_full_hotword_analyze) |
| semantic_analysis_main.py | 语义分析回填（读MySQL→分析→写回） | skills/semantic_analyzer.py, utils/mysql_manager.py |
| zhibo8_main.py | 直播吧评论爬取入口 | skills/zhibo8_match_scraper.py |

### Skills 业务模块（按流水线顺序）

| # | 文件名 | 类名 | 核心方法 | 修改场景 |
|---|--------|------|----------|----------|
| 1 | skills/hotsearch_crawler.py | HotSearchCrawler | execute() | 改热榜API地址/请求逻辑 |
| 2 | skills/data_filter.py | DataFilter | execute() | 改过滤规则/黑名单/热度阈值 |
| 3 | skills/browser_controller.py | BrowserController | open_browser()/new_page()/close_browser() | 改浏览器行为/Cookie/UserAgent |
| 4 | skills/content_collector.py | ContentCollector | collect_articles_for_keyword() | 改内容采集逻辑/选择器 |
| 5 | skills/article_processor.py | ArticleProcessor | execute()/process_article_detail() | 改文章处理流程/URL规范化 |
| 6 | skills/comment_processor.py | CommentProcessor | execute()/process_comments() | 改评论处理/排序/截图 |
| 7 | skills/data_extractor.py | DataExtractor | execute()/extract_article_data() | 改解析逻辑 |
| 8 | skills/storage_manager.py | StorageManager | save_json()/save_docx() | 改存储格式/导出 |
| 9 | skills/image_handler.py | ImageHandler | screenshot_element()/screenshot_clip() | 改截图逻辑 |
| 10 | skills/semantic_analyzer.py | SemanticAnalyzer | analyze() | 改分词/情感/立场分析 |
| 11 | skills/author_monitor.py | AuthorMonitor | monitor_author() | 改作者监控逻辑 |
| 12 | skills/zhibo8_match_scraper.py | Zhibo8MatchScraper | scrape_match_comments() | 改直播吧爬虫 |

### Core 核心文件

| 文件 | 类 | 关键点 |
|------|----|--------|
| core/base.py | BaseSkill | 所有Skill的抽象基类, execute()必须实现 |
| core/config_manager.py | ConfigManager | 加载config.yaml, get(key)点号嵌套访问 |
| core/exceptions.py | CrawlerBaseException体系 | 7种异常类型 |
| core/pipeline.py | PipelineManager | 初始化所有Skill, 编排6步流程, 含run_full_hotword_analyze |

### Models 数据模型

| 文件 | 类 | 关键字段 |
|------|----|----------|
| models/hotsearch_model.py | HotSearchModel | icon_desc, word, num, rank, category |
| models/article_model.py | ArticleModel | url, title, author, content, screenshot_path |
| models/comment_model.py | CommentModel | article_url, comment_id, content, screenshot_path |
| models/zhibo8_comment_model.py | Zhibo8CommentModel | match_url, match_id, comment_id |

### Utils 工具文件

| 文件 | 主要功能 | 修改场景 |
|------|----------|----------|
| utils/logger.py | setup_logger() 统一日志 | 改日志格式/级别 |
| utils/http_client.py | HTTPClient GET/POST重试 | 改请求行为/UA池 |
| utils/mysql_manager.py | MySQLManager连接池 | 改MySQL配置/表结构 |
| utils/file_utils.py | ensure_dir/save_json/load_json/clean_filename | 改文件操作逻辑 |
| utils/date_utils.py | extract_weibo_title/normalize_weibo_time | 改时间解析/话题提取 |
| utils/weibo_url_utils.py | normalize_weibo_detail_url/same_weibo_detail_url | 改URL规范化规则 |
| utils/screenshot_path_manager.py | ScreenshotPathManager | 改截图路径策略 |

---

## 修改指引（常见修改场景）

### 场景1：修改热榜过滤规则
- 修改 `skills/data_filter.py` 中的 DataFilter.execute()
- 或修改 `config.yaml` 中 filter.blacklist_keywords / filter.allowed_icons / filter.min_hot_num

### 场景2：修改截图行为
- 截图预览: `skills/image_handler.py`
- 截图时机/路径: `config.yaml` screenshot 节点 + `utils/screenshot_path_manager.py`

### 场景3：增减数据模型字段
- 修改对应 `models/xxx_model.py` 中的 dataclass
- 同步修改 `utils/mysql_manager.py` 中的建表SQL（_ensure_tables方法）

### 场景4：修改语义分析逻辑
- 主逻辑在 `skills/semantic_analyzer.py`
- 参数在 `config.yaml` semantic_analysis 节点

### 场景5：增加新的 Skill
- 在 `skills/` 下创建新文件, 继承 `core/base.py` 的 BaseSkill
- 在 `core/pipeline.py` 的 _initialize_skills 中注册
- 在 `config.yaml` 中添加对应配置节

### 场景6：修改主流程逻辑
- `core/pipeline.py` 的 run_pipeline() 方法

### 场景7：API 修改
- `api_server.py`

---

## 关键依赖

| 依赖 | 用途 | 版本参考 |
|------|------|----------|
| playwright | 浏览器自动化,截图 | latest |
| requests | HTTP请求热榜API | >=2.28 |
| fastapi+uvicorn | Web服务 | latest |
| pymysql+dbutils | MySQL连接池 | latest |
| jieba | 中文分词 | >=0.42 |
| python-docx | DOCX导出 | >=0.8 |
| pyyaml | YAML配置加载 | >=6.0 |
| pydantic | API数据校验 | v2 |

---

## 配置项速查 (config.yaml)

- crawler.hotsearch_api: 热榜API地址
- filter.allowed_icons: 允许的标签("新","热","新热")
- filter.blacklist_keywords: 过滤的关键词黑名单
- browser.cookie: 微博Cookie(重要,空时功能受限)
- screenshot.base_dir: 截图主目录(当前为G:/weibo_hot_screenshots)
- mysql.*: 数据库配置
- zhibo8.*: 直播吧爬虫配置
