import asyncio
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path
from core.base import BaseSkill
from core.exceptions import ScreenshotError
from models.article_model import ArticleModel
from skills.browser_controller import BrowserController
from utils.file_utils import clean_filename, generate_filename


class ArticleScreenshot(BaseSkill):

    def __init__(self, config: Dict[str, Any], logger: logging.Logger,
                 browser_controller: Optional[BrowserController] = None):
        self.browser = browser_controller
        storage_config = config.get('storage', {})
        self.screenshot_dir = Path(storage_config.get('base_dir', './data')) / 'screenshots' / 'articles'
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        super().__init__(config, logger)

    def _initialize(self):
        self.logger.info(f"[{self.name}] 文章截图处理器初始化完成")
        self.logger.info(f"[{self.name}] 截图保存目录: {self.screenshot_dir}")

    def create_article_from_search(self, keyword: str, screenshot_path: str = "") -> ArticleModel:
        """
        当无法从搜索页提取卡片数据时，创建一个基于搜索页截图的文章记录
        """
        return ArticleModel(
            keyword=keyword,
            title=f"搜索结果: {keyword}",
            content_text=f"关键词 {keyword} 的微博搜索结果页面截图",
            screenshot_path=screenshot_path,
        )

    async def process_article(self, article_url: str, keyword: str) -> ArticleModel:
        """
        处理单篇文章：打开页面 → 等待渲染 → 截图 → 提取数据
        """
        if not self.browser:
            raise ScreenshotError("浏览器控制器未初始化")

        self.logger.info(f"[{self.name}] 开始处理文章: {article_url[:50]}...")

        page = None
        try:
            page = await self.browser.new_page()

            success = await self.browser.navigate_to(page, article_url)
            if not success:
                raise ScreenshotError(f"无法访问文章页面: {article_url}")

            await asyncio.sleep(3)

            article_data = await self._extract_article_data(page, keyword)

            safe_keyword = clean_filename(keyword)[:30]
            screenshot_name = generate_filename(safe_keyword, 'article', '.png')
            screenshot_path = str(self.screenshot_dir / screenshot_name)

            await self._capture_article_screenshot(page, screenshot_path)

            article_data.screenshot_path = screenshot_path
            article_data.url = article_url

            self.logger.info(f"[{self.name}] 文章处理完成，截图已保存: {screenshot_path}")
            self.on_success(article_data)

            return article_data

        except Exception as e:
            self.logger.error(f"[{self.name}] 文章处理失败: {e}")
            raise ScreenshotError(f"文章截图失败: {e}")
        finally:
            if page:
                await self.browser.close_page(page)

    async def _extract_article_data(self, page, keyword: str) -> ArticleModel:
        """从页面提取文章文本数据，适配多种微博页面结构"""
        try:
            data = await page.evaluate("""
                () => {
                    const result = {};

                    // 微博新版页面
                    const authorEl = document.querySelector(
                        '.wbpro-feed-content .info .name, ' +
                        '.head-info .name, ' +
                        '.content .name a, ' +
                        '.W_fb.wbuser, ' +
                        'a[nick-name]'
                    );
                    result.author_name = authorEl ? authorEl.innerText.trim() : '';

                    const timeEl = document.querySelector(
                        '.wbpro-feed-content .from .time, ' +
                        '.head-info .from a, ' +
                        '.content .from a, ' +
                        '.wbtime, ' +
                        'a[node-type="feed_list_item_date"]'
                    );
                    result.publish_time = timeEl ? timeEl.innerText.trim() : '';

                    const contentEl = document.querySelector(
                        '.wbpro-feed-content .wbpro-feed-text, ' +
                        '.content p[node-type="feed_list_content"], ' +
                        '.txt[node-type="feed_list_content"], ' +
                        'p[node-type="feed_list_content"]'
                    );
                    result.content_text = contentEl ? contentEl.innerText.trim() : '';

                    // 互动数据 - 多种选择器
                    const actEls = document.querySelectorAll(
                        '.wbpro-feed-action .item, ' +
                        '.card-act li, ' +
                        '.act li'
                    );
                    const actTexts = Array.from(actEls).map(el => el.innerText.trim());
                    result.repost_count = parseInt(actTexts[0]) || 0;
                    result.comment_count = parseInt(actTexts[1]) || 0;
                    result.like_count = parseInt(actTexts[2]) || 0;

                    return result;
                }
            """)

            return ArticleModel(
                author_name=data.get('author_name', ''),
                publish_time=data.get('publish_time', ''),
                content_text=data.get('content_text', '')[:2000],
                repost_count=data.get('repost_count', 0),
                comment_count=data.get('comment_count', 0),
                like_count=data.get('like_count', 0),
                keyword=keyword,
                title=data.get('content_text', '')[:100],
            )

        except Exception as e:
            self.logger.warning(f"[{self.name}] 数据提取部分失败: {e}")
            return ArticleModel(keyword=keyword)

    async def _capture_article_screenshot(self, page, save_path: str):
        """截取文章的可视化渲染截图，优先精准卡片截图，降级为全屏"""
        try:
            card_selectors = [
                '.card-wrap',
                '.wbpro-feed-content',
                '[node-type="feed_content"]',
                '.WB_feed_type',
                '.content',
            ]

            for selector in card_selectors:
                card_el = await page.query_selector(selector)
                if card_el:
                    card_box = await card_el.bounding_box()
                    if card_box and card_box['height'] > 50:
                        clip = {
                            'x': max(0, card_box['x'] - 10),
                            'y': max(0, card_box['y'] - 10),
                            'width': card_box['width'] + 20,
                            'height': min(card_box['height'] + 20, 3000),
                        }
                        success = await self.browser.take_screenshot(
                            page, save_path, full_page=False, clip=clip
                        )
                        if success:
                            return

            self.logger.debug(f"[{self.name}] 未找到精确卡片元素，使用全屏截图")
            await self.browser.take_screenshot(page, save_path, full_page=False)

        except Exception as e:
            self.logger.error(f"[{self.name}] 截图操作异常: {e}")
            raise

    async def batch_process(self, articles: List[tuple]) -> List[ArticleModel]:
        results = []
        total = len(articles)
        for i, (url, keyword) in enumerate(articles, 1):
            self.logger.info(f"[{self.name}] 处理进度: {i}/{total}")
            try:
                article = await self.process_article(url, keyword)
                results.append(article)
                await asyncio.sleep(2)
            except Exception as e:
                self.logger.error(f"[{self.name}] 第{i}篇文章处理失败: {e}")
                continue
        self.logger.info(f"[{self.name}] 批量处理完成: 成功 {len(results)}/{total}")
        return results

    def execute(self, *args, **kwargs):
        self.logger.warning(f"[{self.name}] 请使用异步方法 process_article()")
