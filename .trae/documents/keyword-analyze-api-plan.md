# 方案：新增热词博文全链路分析接口

## 摘要

新增一个独立的 API 接口，接收热词名称作为入参，自动完成「搜索博文 → 截图 → 采集评论 → 评论截图 → 语义分析标注」的全链路流程，返回结构化的分析结果。

## 现状分析

### 已有能力（可复用）

| 模块 | 文件 | 功能 |
|------|------|------|
| ContentCollector | [skills/content_collector.py](skills/content_collector.py) | 根据关键词搜索微博，提取卡片数据（作者、内容、URL等） |
| ArticleProcessor | [skills/article_processor.py](skills/article_processor.py) | 对每条博文截图，生成 ArticleModel |
| CommentProcessor | [skills/comment_processor.py](skills/comment_processor.py) | 进入详情页展开评论，逐条截图，生成 CommentModel |
| SemanticAnalyzer | [skills/semantic_analyzer.py](skills/semantic_analyzer.py) | 8维语义分析：情绪/立场/价值观/观念/观点/冲突分 |
| PipelineManager | [core/pipeline.py#L303-L400](core/pipeline.py#L303-L400) | 已有 `run_single_keyword(keyword)` 方法，串联上述全部 Skill |
| BrowserController | skills/browser_controller.py | Playwright 浏览器管理 |
| ImageHandler | skills/image_handler.py | 截图工具 |
| StorageManager | skills/storage_manager.py | 数据持久化（JSON文件+MySQL） |

### 关键发现

`PipelineManager.run_single_keyword()` （[core/pipeline.py#L303](core/pipeline.py#L303)）已经完整实现了单关键词的全链路处理：
1. 搜索 → 2. 卡片提取 → 3. 文章截图 → 4. 详情页评论截图 → 5. MySQL存储 → 6. 语义分析

**但该方法的问题是：不返回数据给调用者**，只做存储。API 接口需要在此基础上增加结果返回能力。

### 现有 API 模式

参考 [api_server.py#L357-L376](api_server.py#L357-L376) 的流水线接口模式：
- 使用 `BackgroundTasks` 异步执行
- 通过全局 `_pipeline_status` 跟踪状态
- 返回立即响应 + 后台执行

---

## 实现方案

### 新增接口

```
POST /api/keyword/analyze
参数: keyword (string, required) - 热词名称
      articles_per_kw (int, optional, default=3) - 每个关键词处理的文章数
      max_comments (int, optional, default=15) - 每篇文章最大评论数
返回: {
  code: 200,
  message: "success",
  data: {
    keyword: "xxx",
    status: "completed",
    articles: [...],        // 文章列表（含截图路径）
    comments: [...],        // 评论列表（含截图路径）
    semantic_analysis: {    // 语义分析汇总
      article_analysis: [...],
      comment_analysis: [...],
      summary: {...}
    },
    stats: { articles_count, comments_count, screenshots_count }
  }
}
```

### 修改的文件

#### 1. `api_server.py` — 新增接口路由和请求/响应模型

**改动内容：**
- 新增 Pydantic 请求模型 `KeywordAnalyzeRequest`（keyword, articles_per_kw, max_comments）
- 新增 Pydantic 响应模型 `KeywordAnalyzeResult` / `SemanticSummaryItem`
- 新增 `POST /api/keyword/analyze` 路由处理函数
- 新增异步执行函数 `_run_keyword_analyze_async()`，复用 PipelineManager 的 Skill 实例
- 在根路由 endpoints 列表中注册新接口

**核心逻辑：**
```python
@app.post("/api/keyword/analyze", tags=["热词分析"])
async def analyze_keyword(req: KeywordAnalyzeRequest, bg_tasks: BackgroundTasks):
    # 复用已初始化的 browser / content_collector / article_processor
    # / comment_processor / semantic_analyzer / storage / mysql
    # 执行全链路并返回结构化结果
```

#### 2. `core/pipeline.py` — 新增 `analyze_keyword_return()` 方法

**改动内容：**
- 在 `PipelineManager` 中新增 `async def analyze_keyword_return(self, keyword: str, **options) -> Dict` 方法
- 基于 `run_single_keyword()` 的逻辑改造，关键区别：
  - **保留所有中间结果**（articles 列表、comments 列表），不丢弃
  - **对每条文章和评论执行 SemanticAnalyzer.analyze_record()**
  - **将语义标签回填到文章/评论数据中**
  - **返回完整的结构化字典**而非仅写入数据库
- 仍然执行存储操作（MySQL + 文件），确保数据不丢失

**方法签名与返回值：**
```python
async def analyze_keyword_return(
    self,
    keyword: str,
    articles_per_kw: int = 3,
    max_comments: int = 15,
) -> Dict[str, Any]:
    """
    Returns:
        {
            "keyword": str,
            "status": "completed" | "partial" | "failed",
            "articles": [{...ArticleModel.to_dict(), semantic: {...}}, ...],
            "comments": [{...CommentModel.to_dict(), semantic: {...}}, ...],
            "semantic_summary": {
                "article_sentiment_dist": {"正向": N, "负向": N, "中性": N},
                "comment_sentiment_dist": {...},
                "top_values": [{"label": "公平正义", "count": N}, ...],
                "top_stances": [{"label": "支持", "count": N}, ...],
                "high_conflict_comments": [...],
            },
            "stats": {"articles": N, "comments": N, "screenshots": N},
            "errors": [str, ...]
        }
    """
```

#### 3. `config.yaml` — 可选：添加 keyword_analyze 配置节

```yaml
keyword_analyze:
  default_articles_per_kw: 3     # 默认每个关键词处理文章数
  default_max_comments: 15       # 默认每篇文章最大评论数
  timeout_seconds: 300           # 单次分析超时时间
```

---

## 数据流

```
用户输入热词
    │
    ▼
POST /api/keyword/analyze  { keyword: "xxx" }
    │
    ▼
┌─────────────────────────────────────────────┐
│  analyze_keyword_return(keyword)             │
│                                             │
│  1️⃣ ContentCollector.execute(keyword)       │
│     → 访问 s.weibo.com/weibo?q=keyword       │
│     → 提取微博卡片列表 cards_data[]          │
│                                             │
│  2️⃣ 循环处理每条卡片 (前N条):               │
│     ├─ ArticleProcessor.execute()           │
│     │   → 定位卡片元素 → clip截图            │
│     │   → 返回 ArticleModel                 │
│     │                                       │
│     ├─ CommentProcessor.execute() [有评论时] │
│     │   → 打开详情页 → 展开评论              │
│     │   → 逐条截取评论                       │
│     │   → 返回 List[CommentModel]            │
│     │                                       │
│     └─ 存储到 MySQL + JSON                   │
│                                             │
│  3️⃣ SemanticAnalyzer 全量分析               │
│     ├─ 对每篇文章: analyze_record(article)   │
│     │   → sentiment / stance / values /     │
│     │     concepts / conflict / viewpoints   │
│     │                                       │
│     └─ 对每条评论: analyze_record(comment)   │
│         → 同上8维语义分析                    │
│                                             │
│  4️⃣ 汇总语义统计                           │
│     → 情绪分布 / 价值标签TOP / 立场分布      │
│     → 高冲突评论筛选                         │
│                                             │
│  5️⃣ 返回完整结果字典                        │
└─────────────────────────────────────────────┘
    │
    ▼
JSON Response → 前端/Swagger 展示
```

## 语义分析输出字段说明

对每条文章/评论，SemanticAnalyzer 返回以下维度：

| 维度 | 字段 | 说明 |
|------|------|------|
| 情绪倾向 | sentiment_label | 正向/负向/中性/复杂 |
| 情绪分数 | sentiment_score | -1.0 ~ 1.0 |
| 主要情绪 | primary_emotion | 喜悦/期待/愤怒/失望/焦虑/质疑/嘲讽/共鸣 |
| 立场 | stance_label | 支持/反对/质疑/建议/观望/中立 |
| 立场极性 | stance_polarity | -0.8 ~ 0.8 |
| 价值标签 | value_labels | TOP3: 公平正义/效率结果/真实可信/... |
| 观念标签 | concept_labels | TOP3: 结果导向/规则底线/个人选择/... |
| 冲突分 | conflict_score | 0 ~ 1.0 (高/中/低) |
| 关键词 | keywords | jieba 分词 TOP-K |
| 观点句 | viewpoints | 明确表达观点的核心句子 |

## 验证步骤

1. 启动服务 `uvicorn api_server:app --port 9000`
2. 访问 http://localhost:9000/docs 确认新接口出现在 Swagger 中
3. 用 Swagger UI 或 curl 调用：
   ```bash
   curl -X POST "http://localhost:9000/api/keyword/analyze" \
     -H "Content-Type: application/json" \
     -d '{"keyword": "高考", "articles_per_kw": 2}'
   ```
4. 验证返回数据包含 articles、comments、semantic_analysis 三个主要部分
5. 验证截图文件已保存到 G:/weibo_hot_screenshots/ 对应目录
6. 验证 MySQL 中已写入对应的文章、评论、语义分析记录

## 假设与决策

1. **浏览器复用**：API 服务启动时不启动浏览器（保持轻量），在收到分析请求时临时创建 PipelineManager 实例并启动浏览器，完成后关闭
2. **超时控制**：单次分析设置默认超时 300 秒，避免长时间占用资源
3. **并发限制**：同一时间只允许一次关键词分析任务（通过全局锁控制），避免浏览器冲突
4. **错误容错**：某篇文章或评论处理失败不影响整体流程，记录到 errors 列表中继续执行
5. **语义分析始终启用**：只要 config.yaml 中 `semantic_analysis.enabled=true`（当前配置为 true），就执行全量语义分析
