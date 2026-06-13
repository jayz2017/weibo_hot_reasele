import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from core.base import BaseSkill
from core.exceptions import CrawlerBaseException
from skills.hotsearch_crawler import HotSearchCrawler
from skills.data_filter import DataFilter
from skills.browser_controller import BrowserController
from skills.data_extractor import DataExtractor
from skills.storage_manager import StorageManager
from skills.image_handler import ImageHandler
from skills.content_collector import ContentCollector
from skills.article_processor import ArticleProcessor
from skills.comment_processor import CommentProcessor
from skills.semantic_analyzer import SemanticAnalyzer
from utils.logger import setup_logger as _setup_logger
from utils.file_utils import clean_filename
from utils.weibo_url_utils import same_weibo_detail_url
from models.article_model import ArticleModel
from models.comment_model import CommentModel
from utils.mysql_manager import MySQLManager


class PipelineManager:

    def __init__(self, config_path: str = "config.yaml"):
        from core.config_manager import ConfigManager
        self.config_manager = ConfigManager(config_path)
        self.config = self.config_manager.config

        log_dir = Path(self.config.get('paths', {}).get('log_dir', './data/logs'))
        log_dir.mkdir(parents=True, exist_ok=True)
        self.logger = _setup_logger(
            "Pipeline",
            level="INFO",
            log_file=str(log_dir / f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"),
        )

        self.skills: Dict[str, BaseSkill] = {}
        self.stats = {
            'total_hot_search': 0,
            'filtered_items': 0,
            'articles_processed': 0,
            'comments_processed': 0,
            'screenshots_taken': 0,
            'errors': [],
            'start_time': None,
            'end_time': None,
        }

        self._initialize_skills()

        paths_config = self.config.get('paths', {})
        self.screenshot_dir = Path(paths_config.get('screenshot_dir', './data/screenshots'))
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    def _initialize_skills(self):
        self.logger.info("正在初始化所有Skills...")
        try:
            self.crawler = HotSearchCrawler(self.config, self.logger)
            self.data_filter = DataFilter(self.config, self.logger)
            self.browser = BrowserController(self.config, self.logger)
            self.data_extractor = DataExtractor(self.config, self.logger)
            self.storage = StorageManager(self.config, self.logger)

            from utils.screenshot_path_manager import ScreenshotPathManager
            self.path_manager = ScreenshotPathManager(self.config.get('screenshot', self.config.get('paths', {})), self.logger)
            self.image_handler = ImageHandler(self.config, self.logger, path_manager=self.path_manager)
            self.content_collector = ContentCollector(self.config, self.logger, browser=self.browser, image_handler=self.image_handler, path_manager=self.path_manager)
            self.article_processor = ArticleProcessor(self.config, self.logger, image_handler=self.image_handler)
            self.comment_processor = CommentProcessor(self.config, self.logger, image_handler=self.image_handler)
            self.semantic_analyzer = SemanticAnalyzer(self.config, self.logger)

            mysql_config = self.config.get('mysql', {})
            if mysql_config.get('enabled', False):
                self.mysql = MySQLManager(mysql_config, self.logger)
                self.logger.info("[MySQL] 数据库连接初始化完成")
            else:
                self.mysql = None

            self.skills = {
                'crawler': self.crawler,
                'filter': self.data_filter,
                'browser': self.browser,
                'data_extractor': self.data_extractor,
                'storage': self.storage,
                'image_handler': self.image_handler,
                'content_collector': self.content_collector,
                'article_processor': self.article_processor,
                'comment_processor': self.comment_processor,
                'semantic_analyzer': self.semantic_analyzer,
            }
            self.logger.info(f"成功初始化 {len(self.skills)} 个Skills")
        except Exception as e:
            self.logger.error(f"Skills初始化失败: {e}")
            raise

    async def run_pipeline(self):
        self.stats['start_time'] = datetime.now().isoformat()
        self.logger.info("=" * 60)
        self.logger.info("🚀 开始执行微博热榜采集流水线")
        self.logger.info("=" * 60)

        try:
            self.logger.info("\n【Step 1/6】获取微博热榜数据...")
            hot_search_data = self.crawler.execute()
            self.stats['total_hot_search'] = len(hot_search_data)
            self.logger.info(f"✓ 获取到 {len(hot_search_data)} 条原始热榜数据")

            self.logger.info("\n【Step 2/6】过滤热榜数据...")
            filtered_data = self.data_filter.execute(hot_search_data)
            self.stats['filtered_items'] = len(filtered_data)
            self.logger.info(f"✓ 过滤后剩余 {len(filtered_data)} 条目标数据")

            if not filtered_data:
                self.logger.warning("⚠ 无符合条件的热榜数据，结束任务")
                return

            for item in filtered_data[:10]:
                self.logger.info(f"  📌 [{item.rank}] {item.word} (热度: {item.num:,})")

            self.logger.info("\n【Step 3/6】启动浏览器引擎...")
            await self.browser.start_browser()
            self.logger.info("✓ 浏览器启动成功")

            max_keywords = self.config.get('pipeline', {}).get('max_keywords', 5)
            all_articles = []
            all_comments = []

            keywords_to_process = filtered_data[:max_keywords]
            self.logger.info(f"\n【Step 4/6】开始处理 {len(keywords_to_process)} 个热搜关键词...")

            for idx, item in enumerate(keywords_to_process, 1):
                self.logger.info(f"\n  📝 [{idx}/{len(keywords_to_process)}] 处理热搜: {item.word}")
                try:
                    safe_kw = clean_filename(item.word[:20])
                    articles_per_kw = self.config.get('pipeline', {}).get('articles_per_keyword', 3)

                    cards_data, need_login = await self.content_collector.execute(
                        item.word, getattr(item, 'word_scheme', '')
                    )

                    if need_login:
                        self.logger.warning(f"    ⚠ 关键词需要登录，跳过")
                        continue

                    if not cards_data:
                        self.logger.warning(f"    未找到微博卡片，跳过")
                        continue

                    kw_articles = []
                    kw_comments = []

                    search_page = await self.browser.new_page()

                    if getattr(item, 'word_scheme', ''):
                        from urllib.parse import quote
                        topic_keyword = item.word_scheme.replace('#', '')
                        search_url = f"https://s.weibo.com/weibo?q={quote(topic_keyword)}"
                    else:
                        from urllib.parse import quote
                        search_url = f"https://s.weibo.com/weibo?q={quote(item.word)}"

                    nav_success = await self.browser.navigate_to(search_page, search_url, wait_for='domcontentloaded')
                    if not nav_success:
                        self.logger.warning(f"    搜索页访问失败，跳过")
                        await self.browser.close_page(search_page)
                        continue

                    await asyncio.sleep(4)
                    await self.browser.scroll_to_load_more(search_page, scroll_count=3, delay=2000)

                    for card_idx, card_info in enumerate(cards_data[:articles_per_kw]):
                        try:
                            article_model = await self.article_processor.execute(
                                search_page, card_info, item.word, card_idx, safe_kw
                            )
                            kw_articles.append(article_model)

                            detail_url = card_info.get('detail_url', '')
                            converted_url = ContentCollector.convert_detail_url(detail_url)

                            valid_url = (
                                converted_url and
                                'weibo.com/' in converted_url and
                                not converted_url.startswith('sinaweibo://')
                            )

                            if valid_url and card_info.get('comment_count', 0) > 0:
                                self.logger.info(f"      🔗 打开详情页获取评论区: {converted_url[:60]}...")
                                detail_page = None
                                try:
                                    detail_page = await self.browser.new_page()
                                    nav_success = await self.browser.navigate_to(
                                        detail_page, converted_url, wait_for='domcontentloaded'
                                    )

                                    if nav_success:
                                        await asyncio.sleep(5)
                                        page_comments = await self.comment_processor.execute(
                                            detail_page, item.word, card_idx, safe_kw
                                        )
                                        kw_comments.extend(page_comments)

                                        if len(page_comments) > 0:
                                            self.logger.info(f"      ✅ 成功截取 {len(page_comments)} 条评论")
                                        else:
                                            self.logger.warning(f"      ⚠ 未截取到评论")
                                    else:
                                        self.logger.warning(f"      详情页访问失败")

                                    await self.browser.close_page(detail_page)
                                except Exception as e:
                                    self.logger.warning(f"      详情页评论截取出错: {e}")
                                    if detail_page:
                                        await self.browser.close_page(detail_page)
                            else:
                                self.logger.debug(f"    第{card_idx+1}条微博无有效详情URL或无评论，跳过")

                        except Exception as e:
                            self.logger.warning(f"    第{card_idx+1}条微博处理失败: {e}")
                            continue

                    await self.browser.close_page(search_page)

                    all_articles.extend(kw_articles)
                    all_comments.extend(kw_comments)

                    self.stats['articles_processed'] += len(kw_articles)
                    self.stats['comments_processed'] += len(kw_comments)
                    self.stats['screenshots_taken'] += len(kw_articles) + len(kw_comments)

                    await asyncio.sleep(2)
                except Exception as e:
                    error_msg = f"处理 [{item.word}] 失败: {str(e)}"
                    self.logger.error(f"  ❌ {error_msg}")
                    self.stats['errors'].append(error_msg)
                    continue

            self.logger.info("\n【Step 6/6】数据清洗与存储...")
            cleaned_articles = self.data_extractor.execute(all_articles)
            cleaned_comments = self.data_extractor.execute(all_comments)

            for article in cleaned_articles:
                self.storage.save_article_data(article, article.keyword)
                if self.mysql:
                    try:
                        article_id = self.mysql.save_article(article.to_dict())
                        comment_rows = self._save_article_comments(article, article_id, cleaned_comments)
                        self._save_semantic_analysis(article, article_id, comment_rows=comment_rows)
                    except Exception as db_err:
                        self.logger.error(f"[MySQL] 文章存储失败: {db_err}")

            if cleaned_comments:
                self.storage.save_comments_data(cleaned_comments, "all_collected")

            self.storage.save_hot_search_data(filtered_data)

            if cleaned_articles:
                export_data = []
                for a in cleaned_articles:
                    row = self.data_extractor.format_article_for_export(a)
                    export_data.append(row)
                self.storage.export_to_csv(export_data, "articles_summary",
                    fieldnames=['标题', '作者', '发布时间', '关键词', '内容摘要', '转发数', '评论数', '点赞数', '截图路径', '采集时间'])

            if cleaned_comments:
                comment_export = []
                for c in cleaned_comments:
                    row = self.data_extractor.format_comment_for_export(c)
                    comment_export.append(row)
                self.storage.export_to_csv(comment_export, "comments_summary",
                    fieldnames=['评论者', '评论内容', '点赞数', '截图路径', '采集时间'])

            self.stats['end_time'] = datetime.now().isoformat()
            report_path = self.storage.generate_report(self.stats)

            self.logger.info(f"\n{'=' * 60}")
            self.logger.info("✅ 流水线执行完成！")
            self.logger.info(f"📊 统计摘要:")
            self.logger.info(f"   - 原始热榜: {self.stats['total_hot_search']} 条")
            self.logger.info(f"   - 过滤后: {self.stats['filtered_items']} 条")
            self.logger.info(f"   - 文章处理: {self.stats['articles_processed']} 篇")
            self.logger.info(f"   - 评论处理: {self.stats['comments_processed']} 条")
            self.logger.info(f"   - 截图生成: {self.stats['screenshots_taken']} 张")
            self.logger.info(f"   - 报告路径: {report_path}")
            self.logger.info(f"{'=' * 60}")

        except CrawlerBaseException as e:
            self.logger.error(f"❌ 流水线执行失败: {e}")
            raise
        finally:
            await self.browser.close_browser()
            self.crawler.close()
            if self.mysql:
                self.mysql.close()

    async def run_single_keyword(self, keyword: str):
        self.logger.info(f"单独处理关键词: {keyword}")
        await self.browser.start_browser()
        try:
            safe_kw = clean_filename(keyword[:20])
            articles_per_kw = self.config.get('pipeline', {}).get('articles_per_keyword', 3)

            cards_data, need_login = await self.content_collector.execute(keyword)

            if need_login:
                self.logger.warning(f"⚠ 关键词需要登录")
                return

            if not cards_data:
                self.logger.warning(f"未找到微博卡片")
                return

            search_page = await self.browser.new_page()

            from urllib.parse import quote
            search_url = f"https://s.weibo.com/weibo?q={quote(keyword)}"
            nav_success = await self.browser.navigate_to(search_page, search_url, wait_for='domcontentloaded')

            if not nav_success:
                self.logger.warning(f"搜索页访问失败")
                await self.browser.close_page(search_page)
                return

            await asyncio.sleep(4)
            await self.browser.scroll_to_load_more(search_page, scroll_count=3, delay=2000)

            articles = []
            comments = []

            for card_idx, card_info in enumerate(cards_data[:articles_per_kw]):
                try:
                    article_model = await self.article_processor.execute(
                        search_page, card_info, keyword, card_idx, safe_kw
                    )
                    articles.append(article_model)

                    detail_url = card_info.get('detail_url', '')
                    converted_url = ContentCollector.convert_detail_url(detail_url)

                    valid_url = (
                        converted_url and
                        'weibo.com/' in converted_url and
                        not converted_url.startswith('sinaweibo://')
                    )

                    if valid_url and card_info.get('comment_count', 0) > 0:
                        detail_page = None
                        try:
                            detail_page = await self.browser.new_page()
                            nav_success = await self.browser.navigate_to(
                                detail_page, converted_url, wait_for='domcontentloaded'
                            )

                            if nav_success:
                                await asyncio.sleep(5)
                                page_comments = await self.comment_processor.execute(
                                    detail_page, keyword, card_idx, safe_kw
                                )
                                comments.extend(page_comments)

                            await self.browser.close_page(detail_page)
                        except Exception as e:
                            self.logger.warning(f"详情页评论截取出错: {e}")
                            if detail_page:
                                await self.browser.close_page(detail_page)

                except Exception as e:
                    self.logger.warning(f"第{card_idx+1}条微博处理失败: {e}")
                    continue

            await self.browser.close_page(search_page)

            for article in articles:
                self.storage.save_article_data(article, keyword)
                if self.mysql:
                    try:
                        article_id = self.mysql.save_article(article.to_dict())
                        comment_rows = self._save_article_comments(article, article_id, comments)
                        self._save_semantic_analysis(article, article_id, comment_rows=comment_rows)
                    except Exception as db_err:
                        self.logger.error(f"[MySQL] 文章存储失败: {db_err}")
            if comments:
                self.storage.save_comments_data(comments, keyword)
            self.logger.info(f"✓ 单关键词处理完成: {keyword}")
            self.logger.info(f"  文章: {len(articles)} 篇, 评论: {len(comments)} 条")
        finally:
            await self.browser.close_browser()
            self.crawler.close()
            if self.mysql:
                self.mysql.close()

    def _get_article_comments(self, article: ArticleModel, comments: List[CommentModel]) -> List[CommentModel]:
        return [
            comment for comment in comments
            if hasattr(comment, 'article_url') and same_weibo_detail_url(comment.article_url, article.url)
        ]

    def _save_article_comments(
        self,
        article: ArticleModel,
        article_id: int,
        comments: List[CommentModel],
    ) -> List[Dict[str, Any]]:
        if not self.mysql:
            return []

        article_comments = self._get_article_comments(article, comments)
        if not article_comments:
            self.logger.info("[MySQL] 未找到匹配评论，跳过评论入库 article_id=%s url=%s", article_id, article.url)
            return []

        comments_data = [comment.to_dict() for comment in article_comments]
        comment_rows = self.mysql.save_comments_batch_return_rows(comments_data, article_id)
        self.logger.info(
            "[MySQL] 文章评论已关联 article_id=%s matched=%s db_rows=%s",
            article_id,
            len(article_comments),
            len(comment_rows),
        )
        return comment_rows

    def _save_semantic_analysis(
        self,
        article: ArticleModel,
        article_id: int,
        comment_rows: Optional[List[Dict[str, Any]]] = None,
    ):
        if not self.mysql or not self.semantic_analyzer or not self.semantic_analyzer.enabled:
            return

        article_analysis = self.semantic_analyzer.analyze_record(
            article,
            source_type='article',
            source_id=article_id,
            article_id=article_id,
            keyword=article.keyword,
        )
        self.mysql.save_semantic_analysis(article_analysis)

        if comment_rows is None:
            comment_rows = self.mysql.get_comments_by_article_id(article_id, limit=200)
        comment_analyses = [
            self.semantic_analyzer.analyze_record(
                row,
                source_type='comment',
                source_id=row.get('id', 0),
                article_id=article_id,
                keyword=article.keyword,
            )
            for row in comment_rows
        ]
        if comment_analyses:
            self.mysql.save_semantic_analysis_batch(comment_analyses)
            self.logger.info("[MySQL] 评论语义分析已保存 article_id=%s count=%s", article_id, len(comment_analyses))

    async def analyze_keyword_return(self, keyword: str, articles_per_kw: int = 3) -> Dict[str, Any]:
        """
        对单个热词执行全链路分析并返回结构化结果

        流程：搜索博文 → 文章截图 → 评论采集+截图 → 语义分析 → 汇总返回

        Args:
            keyword: 热词名称
            articles_per_kw: 每个关键词处理的文章数

        Returns:
            Dict: 包含 articles / comments / semantic_analysis / stats 的结构化结果
        """
        from collections import Counter

        result = {
            'keyword': keyword,
            'status': 'completed',
            'articles': [],
            'comments': [],
            'semantic_summary': {},
            'stats': {'articles_count': 0, 'comments_count': 0, 'screenshots_count': 0},
            'errors': [],
        }

        try:
            self.logger.info(f"[KeywordAnalyze] 开始分析热词: {keyword}")
            await self.browser.start_browser()

            safe_kw = clean_filename(keyword[:20])

            # Step 1: 搜索并采集微博卡片
            cards_data, need_login = await self.content_collector.execute(keyword)
            if need_login:
                result['status'] = 'failed'
                result['errors'].append('需要登录才能搜索')
                return result
            if not cards_data:
                result['status'] = 'partial'
                result['errors'].append(f'未找到关于「{keyword}」的微博卡片')
                return result

            self.logger.info(f"[KeywordAnalyze] 找到 {len(cards_data)} 条微博卡片")

            # Step 2: 打开搜索页（用于文章处理器定位元素）
            search_page = await self.browser.new_page()
            from urllib.parse import quote
            search_url = f"https://s.weibo.com/weibo?q={quote(keyword)}"
            nav_success = await self.browser.navigate_to(search_page, search_url, wait_for='domcontentloaded')
            if not nav_success:
                await self.browser.close_page(search_page)
                result['errors'].append('搜索页访问失败')
                return result

            await asyncio.sleep(4)
            await self.browser.scroll_to_load_more(search_page, scroll_count=3, delay=2000)

            articles = []
            comments = []

            # Step 3: 循环处理每条微博卡片
            for card_idx, card_info in enumerate(cards_data[:articles_per_kw]):
                try:
                    # 3a. 文章处理 + 截图
                    article_model = await self.article_processor.execute(
                        search_page, card_info, keyword, card_idx, safe_kw
                    )
                    articles.append(article_model)
                    self.logger.info(f"[KeywordAnalyze] [{card_idx+1}] 文章已处理: {article_model.author_name}")

                    # 3b. 进入详情页获取评论 + 截图
                    detail_url = card_info.get('detail_url', '')
                    converted_url = ContentCollector.convert_detail_url(detail_url)
                    valid_url = (
                        converted_url and
                        'weibo.com/' in converted_url and
                        not converted_url.startswith('sinaweibo://')
                    )

                    if valid_url and card_info.get('comment_count', 0) > 0:
                        detail_page = None
                        try:
                            detail_page = await self.browser.new_page()
                            nav_ok = await self.browser.navigate_to(
                                detail_page, converted_url, wait_for='domcontentloaded'
                            )
                            if nav_ok:
                                await asyncio.sleep(5)
                                page_comments = await self.comment_processor.execute(
                                    detail_page, keyword, card_idx, safe_kw
                                )
                                comments.extend(page_comments)
                                self.logger.info(f"[KeywordAnalyze] [{card_idx+1}] 获取 {len(page_comments)} 条评论")
                            else:
                                self.logger.warning(f"[KeywordAnalyze] 详情页访问失败")
                            await self.browser.close_page(detail_page)
                        except Exception as e:
                            self.logger.warning(f"[KeywordAnalyze] 详情页评论截取出错: {e}")
                            if detail_page:
                                await self.browser.close_page(detail_page)

                except Exception as e:
                    error_msg = f"第{card_idx+1}条微博处理失败: {str(e)}"
                    self.logger.warning(f"[KeywordAnalyze] {error_msg}")
                    result['errors'].append(error_msg)
                    continue

            await self.browser.close_page(search_page)

            # Step 4: 数据清洗与存储
            cleaned_articles = self.data_extractor.execute(articles)
            cleaned_comments = self.data_extractor.execute(comments)
            db_article_ids: Dict[int, int] = {}
            db_comment_rows_by_obj: Dict[int, Dict[str, Any]] = {}

            for article in cleaned_articles:
                self.storage.save_article_data(article, article.keyword)
                if self.mysql:
                    try:
                        article_id = self.mysql.save_article(article.to_dict())
                        db_article_ids[id(article)] = article_id
                        article_comments = self._get_article_comments(article, cleaned_comments)
                        comment_rows = self._save_article_comments(article, article_id, cleaned_comments)
                        for comment in article_comments:
                            for row in comment_rows:
                                if row.get('content_text', '') == comment.content_text:
                                    db_comment_rows_by_obj[id(comment)] = row
                                    break
                        self._save_semantic_analysis(article, article_id, comment_rows=comment_rows)
                    except Exception as db_err:
                        self.logger.error(f"[MySQL] 文章存储失败: {db_err}")
                        result['errors'].append(f'数据库写入失败: {db_err}')

            if cleaned_comments:
                self.storage.save_comments_data(cleaned_comments, keyword)

            # Step 5: 语义分析（对每篇文章和评论）
            article_semantics = []
            comment_semantics = []
            article_sem_by_obj: Dict[int, Dict[str, Any]] = {}
            comment_sem_by_obj: Dict[int, Dict[str, Any]] = {}

            if self.semantic_analyzer and self.semantic_analyzer.enabled:
                self.logger.info(f"[KeywordAnalyze] 开始语义分析...")
                for article in cleaned_articles:
                    try:
                        article_id = db_article_ids.get(id(article), 0)
                        analysis = self.semantic_analyzer.analyze_record(
                            article,
                            source_type='article',
                            source_id=article_id,
                            article_id=article_id,
                            keyword=keyword,
                        )
                        analysis.pop('analysis', None)  # 移除原始嵌套，减小体积
                        article_semantics.append(analysis)
                        article_sem_by_obj[id(article)] = analysis
                    except Exception as e:
                        self.logger.warning(f"[KeywordAnalyze] 文章语义分析失败: {e}")

                for comment in cleaned_comments:
                    try:
                        db_row = db_comment_rows_by_obj.get(id(comment), {})
                        article_id = int(db_row.get('article_id') or 0)
                        source_id = int(db_row.get('id') or 0)
                        analysis = self.semantic_analyzer.analyze_record(
                            comment,
                            source_type='comment',
                            source_id=source_id,
                            article_id=article_id,
                            keyword=keyword,
                        )
                        analysis.pop('analysis', None)
                        comment_semantics.append(analysis)
                        comment_sem_by_obj[id(comment)] = analysis
                    except Exception as e:
                        self.logger.warning(f"[KeywordAnalyze] 评论语义分析失败: {e}")

            # Step 6: 构建返回数据
            result_articles = []
            for i, a in enumerate(cleaned_articles):
                a_dict = a.to_dict()
                sem = article_sem_by_obj.get(id(a), article_semantics[i] if i < len(article_semantics) else None)
                a_dict['semantic'] = sem or {}
                result_articles.append(a_dict)

            result_comments = []
            for j, c in enumerate(cleaned_comments):
                c_dict = c.to_dict()
                sem = comment_sem_by_obj.get(id(c), comment_semantics[j] if j < len(comment_semantics) else None)
                c_dict['semantic'] = sem or {}
                result_comments.append(c_dict)

            # Step 7: 构建语义汇总统计
            semantic_summary = self._build_semantic_summary(article_semantics, comment_semantics)

            result['articles'] = result_articles
            result['comments'] = result_comments
            result['semantic_analysis'] = {
                'article_analysis': article_semantics,
                'comment_analysis': comment_semantics,
                'summary': semantic_summary,
            }
            result['stats'] = {
                'articles_count': len(result_articles),
                'comments_count': len(result_comments),
                'screenshots_count': len(result_articles) + len(result_comments),
            }

            if result['errors']:
                result['status'] = 'partial'

            self.logger.info(f"[KeywordAnalyze] 分析完成: {result['stats']}")

        except Exception as e:
            import traceback
            self.logger.error(f"[KeywordAnalyze] 分析失败: {e}\n{traceback.format_exc()}")
            result['status'] = 'failed'
            result['errors'].append(str(e))
        finally:
            try:
                await self.browser.close_browser()
            except Exception:
                pass
            try:
                self.crawler.close()
            except Exception:
                pass
            if self.mysql:
                try:
                    self.mysql.close()
                except Exception:
                    pass

        return result

    def _build_semantic_summary(self, article_semantics: List[Dict], comment_semantics: List[Dict]) -> Dict[str, Any]:
        """构建语义分析汇总统计"""
        from collections import Counter

        summary = {}

        # 情绪分布
        art_sentiments = Counter(a.get('sentiment_label', '未知') for a in article_semantics)
        cmt_sentiments = Counter(c.get('sentiment_label', '未知') for c in comment_semantics)
        summary['article_sentiment_dist'] = dict(art_sentiments)
        summary['comment_sentiment_dist'] = dict(cmt_sentiments)

        # 主要情绪分布
        art_emotions = Counter(a.get('primary_emotion', '中性') for a in article_semantics)
        cmt_emotions = Counter(c.get('primary_emotion', '中性') for c in comment_semantics)
        summary['article_emotion_dist'] = dict(art_emotions.most_common(8))
        summary['comment_emotion_dist'] = dict(cmt_emotions.most_common(8))

        # 立场分布
        all_stances = [s.get('stance_label', '中立') for s in (article_semantics + comment_semantics)]
        summary['stance_dist'] = dict(Counter(all_stances).most_common(6))

        # 价值标签 TOP
        value_counter = Counter()
        for s in article_semantics + comment_semantics:
            for v in s.get('value_labels', []):
                value_counter[v] += 1
        summary['top_values'] = [{'label': k, 'count': v} for k, v in value_counter.most_common(10)]

        # 观念标签 TOP
        concept_counter = Counter()
        for s in article_semantics + comment_semantics:
            for c in s.get('concept_labels', []):
                concept_counter[c] += 1
        summary['top_concepts'] = [{'label': k, 'count': v} for k, v in concept_counter.most_common(10)]

        # 高冲突评论筛选（冲突分 >= 0.4）
        high_conflict = [
            {'id': c.get('source_id'), 'conflict_score': c.get('conflict_score', {}),
             'stance': c.get('stance_label', ''), 'sentiment': c.get('sentiment_label', ''),
             'summary': c.get('summary', '')}
            for c in comment_semantics
            if isinstance(c.get('conflict_score'), (int, float)) and c.get('conflict_score', 0) >= 0.4
        ]
        # 也检查嵌套的 conflict.score
        high_conflict_v2 = []
        for c in comment_semantics:
            cs = c.get('conflict_score', {})
            score = cs.get('score', 0) if isinstance(cs, dict) else cs
            if isinstance(score, (int, float)) and score >= 0.4:
                high_conflict_v2.append({
                    'id': c.get('source_id'),
                    'conflict_score': score,
                    'level': cs.get('level', '') if isinstance(cs, dict) else '',
                    'stance': c.get('stance_label', ''),
                    'sentiment': c.get('sentiment_label', ''),
                    'summary': c.get('summary', ''),
                })
        summary['high_conflict_comments'] = high_conflict_v2 if high_conflict_v2 else high_conflict

        # 冲突分统计
        conflict_scores = []
        for s in comment_semantics:
            cs = s.get('conflict_score', {})
            if isinstance(cs, dict):
                conflict_scores.append(cs.get('score', 0))
            elif isinstance(cs, (int, float)):
                conflict_scores.append(cs)

        if conflict_scores:
            avg_conflict = sum(conflict_scores) / len(conflict_scores)
            max_conflict = max(conflict_scores)
            summary['conflict_stats'] = {
                'avg_score': round(avg_conflict, 4),
                'max_score': round(max_conflict, 4),
                'total_analyzed': len(conflict_scores),
            }
        else:
            summary['conflict_stats'] = {'avg_score': 0, 'max_score': 0, 'total_analyzed': 0}

        summary['total_articles_analyzed'] = len(article_semantics)
        summary['total_comments_analyzed'] = len(comment_semantics)

        return summary
