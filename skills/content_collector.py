import asyncio
import logging
from typing import Dict, List, Tuple, Any
from urllib.parse import quote
from pathlib import Path

from core.base import BaseSkill
from utils.file_utils import clean_filename, generate_filename, ensure_dir


class ContentCollector(BaseSkill):
    """
    微博内容采集 Skill
    
    功能：
    1. 根据关键词搜索微博内容
    2. 登录状态检测
    3. 微博卡片元素定位与数据提取
    4. 详情URL转换
    """

    def __init__(self, config: dict, logger: logging.Logger, *, browser=None, image_handler=None, path_manager=None):
        self.browser = browser
        self.image_handler = image_handler
        if path_manager is not None:
            self.path_manager = path_manager
            self.screenshot_dir = path_manager.base_dir
        else:
            from utils.screenshot_path_manager import ScreenshotPathManager
            self.path_manager = ScreenshotPathManager(config.get('screenshot', config.get('paths', {})), logger)
            self.screenshot_dir = self.path_manager.base_dir
        super().__init__(config, logger)

    def _initialize(self):
        self.logger.info(f"[{self.name}] 内容采集器初始化完成")

    async def execute(self, keyword: str, word_scheme: str = "") -> Tuple[List[Dict], bool]:
        """执行微博内容采集
        
        Args:
            keyword: 搜索关键词
            word_scheme: 超话词Scheme（可选）
            
        Returns:
            Tuple[List[Dict], bool]: (卡片数据列表, 是否需要登录)
        """
        search_page = None
        try:
            search_page = await self.browser.new_page()

            if word_scheme:
                topic_keyword = word_scheme.replace('#', '')
                search_url = f"https://s.weibo.com/weibo?q={quote(topic_keyword)}"
            else:
                search_url = f"https://s.weibo.com/weibo?q={quote(keyword)}"

            self.logger.info(f"[{self.name}] 🔍 搜索关键词: {keyword}")
            success = await self.browser.navigate_to(search_page, search_url, wait_for='domcontentloaded')

            if not success:
                self.logger.warning(f"[{self.name}] 搜索页访问失败")
                return [], False

            await asyncio.sleep(4)

            current_url = search_page.url
            if 'passport.weibo.com' in current_url or 'signin' in current_url:
                self.logger.warning(f"[{self.name}] ⚠ 被重定向到登录页，需要配置Cookie！")
                safe_kw = clean_filename(keyword[:20])
                screenshot_name = generate_filename(safe_kw, 'login_required', '.png')
                screenshot_path = str(
                    self.screenshot_dir / 'articles' / screenshot_name
                )
                await self.image_handler.take_fullpage_fallback(search_page, screenshot_path)
                return [{'keyword': keyword, 'login_required': True}], True

            await self.browser.scroll_to_load_more(search_page, scroll_count=3, delay=2000)
            await self.image_handler._hide_navigation_bar(search_page)

            card_elements_info = await self._locate_card_elements(search_page)

            if not card_elements_info:
                self.logger.warning(f"[{self.name}] 未找到微博卡片元素")
                return [], False

            self.logger.info(f"[{self.name}] 定位到 {len(card_elements_info)} 个微博卡片元素")

            return card_elements_info, False

        except Exception as e:
            self.logger.error(f"[{self.name}] 关键词处理异常: {e}")
            return [], False
        finally:
            if search_page:
                await self.image_handler._restore_navigation_bar(search_page)
                await self.browser.close_page(search_page)

    async def _locate_card_elements(self, page) -> list:
        """定位微博卡片元素并提取数据
        
        完整的JS evaluate代码从pipeline.py原样复制
        """
        try:
            cards_data = await page.evaluate("""
                () => {
                    const results = [];
                    let items = document.querySelectorAll('.wbpro-scroller-item');
                    let useWbproScroller = items.length > 0;
                    if (!useWbproScroller) {
                        items = document.querySelectorAll('.card-wrap');
                    }
                    for (let i = 0; i < items.length; i++) {
                        const item = items[i];
                        try {
                            const data = {};
                            data.index = i;
                            const card = useWbproScroller ? item.querySelector('.card-wrap') : item;
                            if (!card && useWbproScroller) continue;
                            const nameEl = card.querySelector('.name, .W_fb, a[nick-name]');
                            data.author_name = nameEl ? nameEl.innerText.trim() : '';
                            const txtEl = card.querySelector('.txt, p[node-type="feed_list_content"]');
                            data.content_text = txtEl ? txtEl.innerText.trim() : '';
                            const timeEl = card.querySelector('.from a:first-child');
                            data.publish_time = timeEl ? timeEl.innerText.trim() : '';
                            const actEls = card.querySelectorAll('.card-act li');
                            if (actEls.length >= 3) {
                                data.repost_count = parseInt(actEls[0]?.innerText) || 0;
                                data.comment_count = parseInt(actEls[1]?.innerText) || 0;
                                data.like_count = parseInt(actEls[2]?.innerText) || 0;
                            } else {
                                data.repost_count = 0;
                                data.comment_count = 0;
                                data.like_count = 0;
                            }
                            const linkEl = card.querySelector('.from a:last-child');
                            data.detail_url = linkEl ? linkEl.href : '';
                            if (!data.detail_url || data.detail_url.includes('app.weibo.com') || data.detail_url.startsWith('javascript:')) {
                                const detailLink = card.querySelector('a[href*="weibo.com/"][href*="/R"], a[href*="weibo.com/"][href*="/r"]');
                                if (detailLink) data.detail_url = detailLink.href;
                            }
                            if (!data.detail_url || data.detail_url.includes('app.weibo.com') || data.detail_url.startsWith('javascript:')) {
                                const timeLink = card.querySelector('.from a[href*="weibo.com/"]');
                                if (timeLink && !timeLink.href.includes('/u/') && !timeLink.href.includes('/p/')) {
                                    data.detail_url = timeLink.href;
                                }
                            }
                            if (!data.detail_url || data.detail_url.includes('app.weibo.com') || data.detail_url.startsWith('javascript:')) {
                                const anyLink = card.querySelector('a[href*="weibo.com/"]');
                                if (anyLink && !anyLink.href.includes('/u/') && !anyLink.href.includes('/p/')) {
                                    data.detail_url = anyLink.href;
                                }
                            }
                            if (data.content_text || data.author_name) {
                                results.push(data);
                            }
                        } catch(e) {}
                    }
                    return results;
                }
            """)
            return cards_data or []
        except Exception as e:
            self.logger.warning(f"[{self.name}] 卡片元素定位失败: {e}")
            return []

    @staticmethod
    def convert_detail_url(detail_url: str) -> str:
        """转换详情URL（app.weibo.com/t/feed/ → weibo.com/detail/）
        
        Args:
            detail_url: 原始详情URL
            
        Returns:
            str: 转换后的URL
        """
        if detail_url and 'app.weibo.com/t/feed/' in detail_url:
            feed_id = detail_url.split('/feed/')[-1].split('?')[0].split('#')[0]
            if feed_id:
                return f"https://weibo.com/detail/{feed_id}"
        return detail_url
