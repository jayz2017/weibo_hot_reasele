import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path

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
from utils.logger import setup_logger as _setup_logger
from utils.file_utils import clean_filename
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

            self.image_handler = ImageHandler(self.config, self.logger)
            self.content_collector = ContentCollector(self.config, self.logger, browser=self.browser, image_handler=self.image_handler)
            self.article_processor = ArticleProcessor(self.config, self.logger, image_handler=self.image_handler)
            self.comment_processor = CommentProcessor(self.config, self.logger, image_handler=self.image_handler)

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
                        article_comments = [c for c in cleaned_comments if hasattr(c, 'article_url') and c.article_url == article.url]
                        if article_comments:
                            comments_data = [c.to_dict() for c in article_comments]
                            self.mysql.save_comments_batch(comments_data, article_id)
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
                        article_comments = [c for c in comments if hasattr(c, 'article_url') and c.article_url == article.url]
                        if article_comments:
                            comments_data = [c.to_dict() for c in article_comments]
                            self.mysql.save_comments_batch(comments_data, article_id)
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
