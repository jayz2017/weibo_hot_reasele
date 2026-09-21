# 微博热榜爬虫系统 (weibo_hot_reasele) - 项目结构技能树

## 项目概述
一个基于 Python 的微博热榜数据采集系统，提供热榜抓取、内容采集、评论截图、语义分析、作者监控等功能，
同时延伸支持直播吧(zhibo8.com)的比赛评论爬取。基于"Skill"架构设计 —— 每个功能模块是一个独立的 Skill。

---

## 一、项目架构树

```
weibo_hot_reasele/
│
├── main.py                         # 主入口 - 完整流水线
├── api_server.py                   # FastAPI Web服务
├── author_monitor_main.py          # 微博作者监控独立入口
├── keyword_analyze_main.py         # 热词全链路分析独立入口
├── semantic_analysis_main.py       # 语义分析回填入口
├── zhibo8_main.py                  # 直播吧评论爬取入口
├── config.yaml                     # 全局YAML配置
│
├── core/                           # 核心层
│   ├── base.py                     # BaseSkill 抽象基类
│   ├── config_manager.py           # ConfigManager 配置管理器
│   ├── exceptions.py               # 异常体系
│   ├── pipeline.py                 # PipelineManager 流水线编排器
│
├── skills/                         # 业务技能层
│   ├── hotsearch_crawler.py        # Skill-1: 热榜数据采集器
│   ├── data_filter.py              # Skill-2: 智能数据过滤器
│   ├── browser_controller.py       # Skill-3: 浏览器控制器(Playwright)
│   ├── content_collector.py        # Skill-4: 微博内容采集器
│   ├── article_processor.py        # Skill-5: 文章处理器
│   ├── comment_processor.py        # Skill-6: 评论处理器
│   ├── data_extractor.py           # Skill-7: 数据提取器
│   ├── storage_manager.py          # Skill-8: 数据存储管理器
│   ├── image_handler.py            # Skill-9: 图片处理器
│   ├── semantic_analyzer.py        # Skill-10: 语义分析器(jieba分词)
│   ├── author_monitor.py           # Skill-11: 作者监控器
│   └── zhibo8_match_scraper.py     # Skill-12: 直播吧比赛评论爬虫
│
├── models/                         # 数据模型层
│   ├── hotsearch_model.py          # HotSearchModel
│   ├── article_model.py            # ArticleModel
│   ├── comment_model.py            # CommentModel
│   └── zhibo8_comment_model.py     # Zhibo8CommentModel
│
├── utils/                          # 工具层
│   ├── logger.py                   # 日志配置
│   ├── http_client.py              # HTTP客户端
│   ├── mysql_manager.py            # MySQL连接池
│   ├── file_utils.py               # 文件工具
│   ├── date_utils.py               # 日期工具
│   ├── weibo_url_utils.py          # URL标准化
│   └── screenshot_path_manager.py  # 截图路径管理器
│
├── tests/                          # 测试层
│   └── test_weibo_comment_semantic_link.py
│
├── data/                           # 数据目录
│   ├── raw/                        # 原始热榜数据
│   ├── processed/                  # 处理后数据
│   ├── exports/                    # 导出文件(JSON/DOCX)
│   ├── logs/                       # 运行日志
│   └── screenshots/                # 项目内截图缓存
│
└── G:/weibo_hot_screenshots/       # 主截图盘(独立于项目目录)
    ├── articles/
    ├── comments/
    └── zhibo8_comments/
```
## 二、核心数据流 (Pipeline 流程)

`
1.热榜采集 -> 2.智能过滤 -> 3.浏览器初始化 -> 4.内容采集
(hotsearch_crawler)  (data_filter)  (browser_controller)  (content_collector)

5.文章处理+截图 -> 6.评论采集+截图 -> 7.语义分析 -> 8.存储导出
(article_processor)  (comment_processor)  (semantic_analyzer)  (storage_manager)
`

## 三、各Skills详细说明

### Core 核心层

| 模块 | 功能 | 关键方法/类 |
|------|------|-------------|
| base.py | Skill抽象基类,定义execute/validate_input/on_success/on_error生命周期 | BaseSkill |
| config_manager.py | YAML配置加载,点号嵌套访问 | ConfigManager |
| exceptions.py | CrawlerBaseException体系 | |
| pipeline.py | 流水线编排:初始化所有Skill,run_pipeline/run_single_keyword | PipelineManager |

### Skills 业务技能层

| 编号 | Skill | 文件 | 核心职责 | 输入 | 输出 |
|------|-------|------|----------|------|------|
| 1 | HotSearchCrawler | hotsearch_crawler.py | 调用微博API获取热榜JSON | 无 | List[HotSearchModel] |
| 2 | DataFilter | data_filter.py | icon_desc过滤,黑名单,热度阈值,TOP N | List[HotSearchModel] | List[HotSearchModel] |
| 3 | BrowserController | browser_controller.py | Playwright浏览器管理 | config | Browser |
| 4 | ContentCollector | content_collector.py | 搜索微博关键字 - 打开详情 - 提取内容 | keyword列表 | 文章+评论 |
| 5 | ArticleProcessor | article_processor.py | 文章清洗/字段标准化/截图 | ArticleModel | ArticleModel+png |
| 6 | CommentProcessor | comment_processor.py | 评论清洗/排序/截图/去重 | List[CommentModel] | List[CommentModel] |
| 7 | DataExtractor | data_extractor.py | HTML/文本提取结构化内容 | 文本 | Dict |
| 8 | StorageManager | storage_manager.py | JSON/DOCX导出/备份/清理 | 任意数据 | 文件 |
| 9 | ImageHandler | image_handler.py | 元素截图/区域截图/导航栏隐藏 | page+selector | png路径 |
| 10 | SemanticAnalyzer | semantic_analyzer.py | jieba分词+TF-IDF/情感/立场/冲突/价值观分析 | 文本 | 8维语义结果 |
| 11 | AuthorMonitor | author_monitor.py | 从MySQL读取作者,监控新博文,截图存MySQL | 作者ID | MySQL |
| 12 | Zhibo8MatchScraper | zhibo8_match_scraper.py | 直播吧评论爬取(aiohttp+Playwright) | 比赛URL | MySQL+截图 |

### Models 数据模型

| 模型 | 关键字段 |
|------|----------|
| HotSearchModel | icon_desc, word, num(热度), rank, category |
| ArticleModel | url, title, author_name, content_text, publish_time, repost/comment/like_count, screenshot_path |
| CommentModel | article_url, comment_id, content_text, author_name, like_count, screenshot_path |
| Zhibo8CommentModel | match_url, match_id, match_title, comment_id, author_name, content_text, like_count |

### Utils 工具层

| 工具 | 功能说明 |
|------|----------|
| logger.py | 统一日志配置(控制台+文件) |
| http_client.py | requests封装+重试+随机UA |
| mysql_manager.py | DBUtils连接池+pymysql+自动建表 |
| file_utils.py | JSON读写/文件名生成/路径处理 |
| date_utils.py | #话题#提取/微博相对时间标准化 |
| weibo_url_utils.py | URL统一化/URL相等判断 |
| screenshot_path_manager.py | 截图路径统一管理,从config读取 |

## 四、配置树 (config.yaml)

- app, paths, crawler, filter: 基础配置
- browser: headless/viewport/Cookie
- storage: 存储目录/导出格式/备份
- screenshot: 截图盘路径(G:/)/子目录/格式/质量/延迟
- comment/author_monitor/extract: 各模块参数
- semantic_analysis: jieba分词参数
- pipeline/keyword_analyze: 流水线参数
- mysql: 数据库连接
- zhibo8: 直播吧爬虫参数

## 五、运行模式

1. 完整流水线: python main.py
2. 单关键词: python main.py -k 关键词
3. 仅热榜: python main.py -m hotsearch
4. API服务: python api_server.py
5. 热词全链路: python keyword_analyze_main.py -k 热词
6. 作者监控: python author_monitor_main.py -id 123456
7. 语义分析回填: python semantic_analysis_main.py -k xxx
8. 直播吧评论: python zhibo8_main.py --url 比赛URL
9. 有头模式调试: 任何入口 + --headless false

## 六、依赖关系

Playwright(browser_controller) -> content_collector, article_processor, comment_processor, image_handler
requests(http_client) -> hotsearch_crawler
pymysql+DBUtils(mysql_manager) -> 所有MySQL存储
jieba(semantic_analyzer) -> 语义分析
