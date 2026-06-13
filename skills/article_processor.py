import asyncio
import logging
from typing import Any

from core.base import BaseSkill
from models.article_model import ArticleModel
from utils.date_utils import normalize_weibo_time, extract_weibo_title
from utils.weibo_url_utils import normalize_weibo_detail_url


class ArticleProcessor(BaseSkill):
    def __init__(self, config: dict, logger: logging.Logger, *, image_handler=None):
        self.image_handler = image_handler
        super().__init__(config, logger)

    def _initialize(self):
        self.logger.info(f"[{self.name}] 文章处理器初始化完成")

    async def execute(self, page, card_info: dict, keyword: str, article_index: int, safe_kw: str = None) -> ArticleModel:
        if safe_kw is None:
            safe_kw = keyword

        card_index = card_info['index']
        author_name = card_info.get('author_name', '')
        content_text = card_info.get('content_text', '')
        detail_url = normalize_weibo_detail_url(card_info.get('detail_url', ''))

        self.logger.info(f"    [{article_index + 1}] {author_name}: {content_text[:40]}...")
        self.logger.info(f"      detail_url=\"{detail_url}\"")

        await page.evaluate(f"""
            () => {{
                let targetEl = null;
                const items = document.querySelectorAll('.wbpro-scroller-item');
                if (items.length > {card_index}) targetEl = items[{card_index}];
                if (!targetEl) {{
                    const cards = document.querySelectorAll('.card-wrap');
                    if (cards.length > {card_index}) targetEl = cards[{card_index}];
                }}
                if (targetEl) targetEl.scrollIntoView({{ behavior: 'instant', block: 'start' }});
            }}
        """)
        await asyncio.sleep(1)

        article_screenshot_path = self.image_handler.generate_screenshot_path(
            safe_kw, 'articles', article_index
        )

        await self.image_handler._hide_navigation_bar(page)

        card_box = await page.evaluate(f"""
            () => {{
                let targetEl = null;
                const items = document.querySelectorAll('.wbpro-scroller-item');
                if (items.length > {card_index}) targetEl = items[{card_index}];
                if (!targetEl) {{
                    const cards = document.querySelectorAll('.card-wrap');
                    if (cards.length > {card_index}) targetEl = cards[{card_index}];
                }}
                if (!targetEl) return null;

                const commentAreas = targetEl.querySelectorAll(
                    '.card-comment, .WB_feed_repeat, [node-type="comment_list"], .repeat, .WB_feed_repeat_s_line, .wbpro-list'
                );
                commentAreas.forEach(el => {{
                    el.setAttribute('data-wb-comment-hidden', el.style.display || '');
                    el.style.display = 'none';
                }});

                const rect = targetEl.getBoundingClientRect();
                return {{ x: rect.x, y: rect.y, width: rect.width, height: rect.height }};
            }}
        """)

        if card_box and card_box['height'] > 50:
            clip = {
                'x': max(0, card_box['x']),
                'y': max(0, card_box['y']) + 50,
                'width': card_box['width'],
                'height': min(card_box['height'], 3000) - 50,
            }
            await self.image_handler.screenshot_clip(page, clip, article_screenshot_path)
            self.logger.info(f"    文章卡片截图已保存")
        else:
            await self.image_handler.take_fullpage_fallback(page, article_screenshot_path)

        await page.evaluate("""
            () => {
                document.querySelectorAll('[data-wb-comment-hidden]').forEach(el => {
                    el.style.display = el.getAttribute('data-wb-comment-hidden') || '';
                    el.removeAttribute('data-wb-comment-hidden');
                });
            }
        """)

        await self.image_handler._restore_navigation_bar(page)

        article = ArticleModel(
            keyword=keyword,
            title=extract_weibo_title(content_text),
            author_name=author_name,
            content_text=content_text[:2000],
            publish_time=normalize_weibo_time(card_info.get('publish_time', '')),
            repost_count=card_info.get('repost_count', 0),
            comment_count=card_info.get('comment_count', 0),
            like_count=card_info.get('like_count', 0),
            url=detail_url,
            screenshot_path=article_screenshot_path,
        )
        self.logger.info(f"    第{article_index + 1}条微博文章截图完成")

        return article
