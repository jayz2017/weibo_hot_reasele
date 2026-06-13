import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic.types import constr

from core.config_manager import ConfigManager
from skills.hotsearch_crawler import HotSearchCrawler
from skills.data_filter import DataFilter
from utils.mysql_manager import MySQLManager
from utils.logger import setup_logger as _setup_logger


app = FastAPI(
    title="微博热榜数据采集系统 API",
    description="""
## 功能模块

### 1. 热榜数据 (Hot Search)
- 获取微博实时热榜数据
- 按关键词/热度/标签过滤

### 2. 文章数据 (Articles)
- 查询已采集的文章记录
- 支持按作者、关键词、时间范围筛选

### 3. 评论数据 (Comments)  
- 查询文章评论记录
- 支持按文章ID/URL筛选

### 4. 直播吧评论 (Zhibo8)
- 查询直播吧比赛热门评论
- 支持按比赛ID筛选

### 5. 流水线控制 (Pipeline)
- 触发完整采集流水线
- 异步执行状态查询

### 6. 热词分析 (Keyword Analyze)
- 输入热词，自动搜索博文、截图、采集评论
- 评论语义分析（情绪/立场/价值观/观点/冲突分）
- 返回结构化全量结果

## 技术栈
- **框架**: FastAPI + Uvicorn
- **数据库**: MySQL (pymysql)
- **文档**: Swagger UI (自动生成)
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

config_manager = None
config = None
logger = None
mysql = None
crawler = None
data_filter = None


@app.on_event("startup")
async def startup_event():
    global config_manager, config, logger, mysql, crawler, data_filter

    config_manager = ConfigManager("config.yaml")
    config = config_manager.config
    logger = _setup_logger("APIServer", level="INFO")

    mysql_config = config.get('mysql', {})
    if mysql_config.get('enabled', False):
        mysql = MySQLManager(mysql_config, logger)

    crawler = HotSearchCrawler(config, logger)
    data_filter = DataFilter(config, logger)


class ResponseModel(BaseModel):
    code: int = Field(200, description="状态码")
    message: str = Field("success", description="响应消息")
    data: Any = Field(None, description="响应数据")
    timestamp: str = Field(default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))


class HotSearchItem(BaseModel):
    rank: int = Field(..., description="排名")
    title: str = Field(..., description="标题")
    hot_num: int = Field(..., description="热度值")
    icon: Optional[str] = Field(None, description="标签: 新/热/新热")
    label: Optional[str] = Field(None, description="分类标签")
    word_scheme: Optional[str] = Field(None, description="搜索词编码")


class HotSearchPageItem(BaseModel):
    rank: int = Field(..., description="排名")
    word: str = Field(..., description="热点内容")
    num: int = Field(..., description="热度值")
    label: Optional[str] = Field(None, description="标签（新/热/沸等）")
    category: Optional[str] = Field(None, description="分类")


class ArticleItem(BaseModel):
    id: int
    url: str
    title: str = ""
    author_name: str = ""
    author_id: str = ""
    content_text: str = ""
    publish_time: str = ""
    repost_count: int = 0
    comment_count: int = 0
    like_count: int = 0
    keyword: str = ""
    screenshot_path: str = ""
    created_at: str = ""


class CommentItem(BaseModel):
    id: int
    article_id: int = 0
    article_url: str = ""
    comment_id: str = ""
    content_text: str = ""
    author_name: str = ""
    author_id: str = ""
    like_count: int = 0
    screenshot_path: str = ""
    created_at: str = ""


class Zhibo8CommentItem(BaseModel):
    id: int
    match_url: str = ""
    match_id: str = ""
    match_title: str = ""
    comment_id: str = ""
    author_name: str = ""
    content_text: str = ""
    like_count: int = 0
    reply_count: int = 0
    publish_time: str = ""
    screenshot_path: str = ""
    created_at: str = ""


class PipelineStatus(BaseModel):
    status: str = "idle"
    running: bool = False
    last_run: Optional[str] = None
    stats: Dict[str, Any] = {}


class KeywordAnalyzeRequest(BaseModel):
    keyword: str = Field(..., description="热词名称", min_length=1, max_length=100)
    articles_per_kw: int = Field(3, ge=1, le=10, description="每个关键词处理的文章数")


_pipeline_status = {
    "status": "idle",
    "running": False,
    "last_run": None,
    "stats": {},
}

_keyword_analyze_status = {
    "running": False,
    "keyword": None,
    "status": "idle",
    "result": None,
    "started_at": None,
}


@app.get("/", tags=["Root"])
async def root():
    return {
        "service": "微博热榜数据采集系统 API",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": [
            {"path": "/api/health", "method": "GET", "desc": "健康检查"},
            {"path": "/api/hotsearch", "method": "GET", "desc": "获取热榜数据"},
            {"path": "/api/hotsearch/filter", "method": "GET", "desc": "过滤后的热词"},
            {"path": "/api/hotsearch/page", "method": "GET", "desc": "热搜页面热点列表（含热度）"},
            {"path": "/api/articles", "method": "GET", "desc": "查询文章列表"},
            {"path": "/api/articles/{article_id}", "method": "GET", "desc": "查询单篇文章"},
            {"path": "/api/comments", "method": "GET", "desc": "查询评论列表"},
            {"path": "/api/semantic/articles/{article_id}", "method": "GET", "desc": "查询文章与评论语义标注"},
            {"path": "/api/semantic/viewpoints", "method": "GET", "desc": "查询热点观点冲突池"},
            {"path": "/api/zhibo8/comments", "method": "GET", "desc": "查询直播吧评论"},
            {"path": "/api/zhibo8/matches", "method": "GET", "desc": "查询比赛列表"},
            {"path": "/api/pipeline/run", "method": "POST", "desc": "触发采集流水线"},
            {"path": "/api/pipeline/status", "method": "GET", "desc": "流水线状态"},
            {"path": "/api/keyword/analyze", "method": "POST", "desc": "热词全链路分析（博文+评论+语义）"},
            {"path": "/api/keyword/status", "method": "GET", "desc": "热词分析任务状态"},
        ],
    }


@app.get("/api/health", tags=["系统"], response_model=ResponseModel)
async def health_check():
    db_status = "connected" if mysql else "disabled"
    try:
        if mysql:
            with mysql.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
    except Exception as e:
        db_status = f"error: {str(e)[:50]}"
    return ResponseModel(data={"database": db_status, "status": "ok"})


@app.get("/api/hotsearch", tags=["热榜数据"], response_model=ResponseModel)
async def get_hotsearch(
    top_n: int = Query(50, ge=1, le=100, description="返回前N条"),
):
    raw_data = crawler.execute()
    items = raw_data[:top_n]
    result = []
    for item in items:
        result.append({
            "rank": item.rank,
            "title": item.word,
            "hot_num": item.num,
            "icon": item.icon,
            "label": item.category,
            "word_scheme": item.word_scheme,
        })
    return ResponseModel(data={"total": len(result), "items": result})


@app.get("/api/hotsearch/filter", tags=["热榜数据"], response_model=ResponseModel)
async def get_filtered_keywords(
    top_n: int = Query(20, ge=1, le=50, description="返回前N条"),
):
    raw_data = crawler.execute()
    filtered = data_filter.execute(raw_data)
    result = filtered[:top_n]
    return ResponseModel(data={
        "total_raw": len(raw_data),
        "total_filtered": len(filtered),
        "items": result,
    })


@app.get("/api/hotsearch/page", tags=["热榜数据"], response_model=ResponseModel)
async def get_hot_search_page(
    top_n: int = Query(50, ge=1, le=100, description="返回前N条"),
):
    """从微博热搜页面获取热点列表（含完整热度值）"""
    items = crawler.fetch_hot_search_page()
    result = items[:top_n]
    return ResponseModel(data={
        "source": "https://weibo.com/hot/search",
        "total": len(result),
        "items": result,
    })


@app.get("/api/articles", tags=["文章数据"], response_model=ResponseModel)
async def list_articles(
    keyword: Optional[str] = Query(None, description="按关键词筛选"),
    author_name: Optional[str] = Query(None, description="按作者名筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
):
    if not mysql:
        raise HTTPException(status_code=503, detail="MySQL未启用")
    total, rows = mysql.get_articles(keyword=keyword, author_name=author_name, page=page, page_size=page_size)
    return ResponseModel(data={"total": total, "page": page, "page_size": page_size, "items": rows})


@app.get("/api/articles/{article_id}", tags=["文章数据"], response_model=ResponseModel)
async def get_article(article_id: int):
    if not mysql:
        raise HTTPException(status_code=503, detail="MySQL未启用")
    row = mysql.get_article_by_id(article_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"文章 {article_id} 不存在")
    comments = mysql.get_comments_by_article_id(article_id)
    row['comments'] = comments
    return ResponseModel(data=row)


@app.get("/api/comments", tags=["评论数据"], response_model=ResponseModel)
async def list_comments(
    article_url: Optional[str] = Query(None, description="按文章URL筛选"),
    author_name: Optional[str] = Query(None, description="按评论者名称筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if not mysql:
        raise HTTPException(status_code=503, detail="MySQL未启用")
    total, rows = mysql.get_comments(article_url=article_url, author_name=author_name, page=page, page_size=page_size)
    return ResponseModel(data={"total": total, "page": page, "page_size": page_size, "items": rows})


@app.get("/api/semantic/articles/{article_id}", tags=["语义分析"], response_model=ResponseModel)
async def get_article_semantics(article_id: int):
    if not mysql:
        raise HTTPException(status_code=503, detail="MySQL未启用")
    article = mysql.get_article_by_id(article_id)
    if not article:
        raise HTTPException(status_code=404, detail=f"文章 {article_id} 不存在")
    analyses = mysql.get_semantic_analysis_by_article_id(article_id)
    return ResponseModel(data={
        "article": article,
        "total": len(analyses),
        "items": analyses,
    })


@app.get("/api/semantic/viewpoints", tags=["语义分析"], response_model=ResponseModel)
async def list_semantic_viewpoints(
    keyword: Optional[str] = Query(None, description="按热点关键词筛选"),
    article_id: Optional[int] = Query(None, description="按文章ID筛选"),
    min_conflict_score: float = Query(0.0, ge=0.0, le=1.0, description="最低冲突分"),
    limit: int = Query(30, ge=1, le=100, description="返回条数"),
):
    if not mysql:
        raise HTTPException(status_code=503, detail="MySQL未启用")
    rows = mysql.get_hotspot_viewpoints(
        keyword=keyword,
        article_id=article_id,
        limit=limit,
        min_conflict_score=min_conflict_score,
    )
    summary = mysql.get_hotspot_semantic_summary(keyword=keyword, article_id=article_id)
    return ResponseModel(data={
        "summary": summary,
        "total": len(rows),
        "items": rows,
    })


@app.get("/api/zhibo8/comments", tags=["直播吧"], response_model=ResponseModel)
async def list_zhibo8_comments(
    match_url: Optional[str] = Query(None, description="按比赛URL筛选"),
    match_id: Optional[str] = Query(None, description="按比赛ID筛选"),
    min_likes: int = Query(0, ge=0, description="最低点赞数"),
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
):
    if not mysql:
        raise HTTPException(status_code=503, detail="MySQL未启用")
    total, rows = mysql.get_zhibo8_comments(match_url=match_url, match_id=match_id, min_likes=min_likes, page=page, page_size=page_size)
    return ResponseModel(data={"total": total, "page": page, "page_size": page_size, "items": rows})


@app.get("/api/zhibo8/matches", tags=["直播吧"], response_model=ResponseModel)
async def list_zhibo8_matches(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if not mysql:
        raise HTTPException(status_code=503, detail="MySQL未启用")
    total, rows = mysql.get_zhibo8_matches(page=page, page_size=page_size)
    return ResponseModel(data={"total": total, "page": page, "page_size": page_size, "items": rows})


async def _run_pipeline_async():
    global _pipeline_status
    try:
        from core.pipeline import PipelineManager
        _pipeline_status["running"] = True
        _pipeline_status["status"] = "running"

        pm = PipelineManager("config.yaml")
        await pm.run_pipeline()

        _pipeline_status["stats"] = pm.stats
        _pipeline_status["last_run"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"[API] 流水线执行完成: {pm.stats}")
    except Exception as e:
        logger.error(f"[API] 流水线执行失败: {e}")
        _pipeline_status["status"] = f"error: {str(e)}"
    finally:
        _pipeline_status["running"] = False
        _pipeline_status["status"] = "completed"


@app.post("/api/pipeline/run", tags=["流水线"], response_model=ResponseModel)
async def run_pipeline(background_tasks: BackgroundTasks):
    global _pipeline_status
    if _pipeline_status["running"]:
        raise HTTPException(status_code=409, detail="流水线正在运行中，请稍后再试")

    background_tasks.add_task(_run_pipeline_async)
    return ResponseModel(message="流水线已启动（后台异步执行）", data=_pipeline_status)


@app.get("/api/pipeline/status", tags=["流水线"], response_model=ResponseModel)
async def pipeline_status():
    return ResponseModel(data=_pipeline_status)


async def _run_keyword_analyze_async(req: KeywordAnalyzeRequest):
    global _keyword_analyze_status
    try:
        _keyword_analyze_status["running"] = True
        _keyword_analyze_status["keyword"] = req.keyword
        _keyword_analyze_status["status"] = "running"
        _keyword_analyze_status["started_at"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        _keyword_analyze_status["result"] = None

        from core.pipeline import PipelineManager
        pm = PipelineManager("config.yaml")
        result = await pm.analyze_keyword_return(
            keyword=req.keyword,
            articles_per_kw=req.articles_per_kw,
        )

        _keyword_analyze_status["result"] = result
        _keyword_analyze_status["status"] = result.get("status", "completed")
        logger.info(f"[API] 热词分析完成: {req.keyword} -> {result['stats']}")
    except Exception as e:
        logger.error(f"[API] 热词分析失败: {e}")
        _keyword_analyze_status["status"] = f"error: {str(e)}"
    finally:
        _keyword_analyze_status["running"] = False


@app.post("/api/keyword/analyze", tags=["热词分析"], response_model=ResponseModel)
async def analyze_keyword(req: KeywordAnalyzeRequest, background_tasks: BackgroundTasks):
    """
    对热词执行全链路分析

    流程：搜索微博博文 → 文章内容截图 → 进入详情页采集评论 → 评论截图
         → 8维语义分析（情绪/立场/价值观/观念/观点/冲突分）→ 返回结构化结果
    """
    global _keyword_analyze_status
    if _keyword_analyze_status["running"]:
        raise HTTPException(status_code=409, detail="已有热词分析任务正在运行中，请稍后再试")

    background_tasks.add_task(_run_keyword_analyze_async, req)
    return ResponseModel(
        message=f"热词「{req.keyword}」分析已启动（后台异步执行）",
        data={"keyword": req.keyword, "articles_per_kw": req.articles_per_kw, "status": "running"},
    )


@app.get("/api/keyword/status", tags=["热词分析"], response_model=ResponseModel)
async def keyword_analyze_status():
    """查询热词分析任务的执行状态和结果"""
    status_data = dict(_keyword_analyze_status)
    # 如果有结果且数据量大，只返回摘要信息
    result = status_data.get("result")
    if isinstance(result, dict) and result.get("stats"):
        status_data["result_summary"] = {
            "keyword": result.get("keyword"),
            "status": result.get("status"),
            "stats": result.get("stats"),
            "errors_count": len(result.get("errors", [])),
            "semantic_summary_keys": list(result.get("semantic_analysis", {}).get("summary", {}).keys()) if result.get("semantic_analysis") else [],
        }
    return ResponseModel(data=status_data)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000, reload=False)
