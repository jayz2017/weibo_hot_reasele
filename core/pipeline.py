import asyncio
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from core.base import BaseSkill
from core.exceptions import CrawlerBaseException
from skills.hotsearch_crawler import HotSearchCrawler
from skills.data_filter import DataFilter
from skills.browser_controller import BrowserController
from skills.article_screenshot import ArticleScreenshot
from skills.comment_screenshot import CommentScreenshot
from skills.data_extractor import DataExtractor
from skills.storage_manager import StorageManager
from utils.logger import setup_logger as _setup_logger
from utils.file_utils import clean_filename, generate_filename
from utils.http_client import HTTPClient
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
            self.article_screenshot = ArticleScreenshot(self.config, self.logger, self.browser)
            self.comment_screenshot = CommentScreenshot(self.config, self.logger, self.browser)
            self.data_extractor = DataExtractor(self.config, self.logger)
            self.storage = StorageManager(self.config, self.logger)

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
                'article_screenshot': self.article_screenshot,
                'comment_screenshot': self.comment_screenshot,
                'data_extractor': self.data_extractor,
                'storage': self.storage,
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
                    articles, comments = await self._process_keyword(item.word, getattr(item, 'word_scheme', ''))
                    all_articles.extend(articles)
                    all_comments.extend(comments)

                    self.stats['articles_processed'] += len(articles)
                    self.stats['comments_processed'] += len(comments)
                    self.stats['screenshots_taken'] += len(articles) + len(comments)

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

    async def _process_keyword(self, keyword: str, word_scheme: str = ""):
        """
        处理单个热搜关键词：
        
        流程：
        1. 搜索页 → 截取文章卡片（wbpro-scroller-item）
        2. 获取每篇文章的详情URL
        3. 打开详情页 → 截取评论区（vue-recycle-scroller__item-view）
        """
        articles = []
        comments = []

        search_page = None
        try:
            search_page = await self.browser.new_page()

            if word_scheme:
                topic_keyword = word_scheme.replace('#', '')
                search_url = f"https://s.weibo.com/weibo?q={quote(topic_keyword)}"
            else:
                search_url = f"https://s.weibo.com/weibo?q={quote(keyword)}"

            self.logger.info(f"    🔍 搜索关键词: {keyword}")
            success = await self.browser.navigate_to(search_page, search_url, wait_for='domcontentloaded')

            if not success:
                self.logger.warning(f"    搜索页访问失败，跳过")
                return articles, comments

            await asyncio.sleep(4)

            current_url = search_page.url
            if 'passport.weibo.com' in current_url or 'signin' in current_url:
                self.logger.warning(f"    ⚠ 被重定向到登录页，需要配置Cookie！")
                safe_kw = clean_filename(keyword[:20])
                screenshot_name = generate_filename(safe_kw, 'login_required', '.png')
                screenshot_path = str(
                    self.screenshot_dir / 'articles' / screenshot_name
                )
                await self.browser.take_screenshot(search_page, screenshot_path, full_page=False)
                article = ArticleModel(
                    keyword=keyword,
                    title=f"[需要Cookie] {keyword}",
                    content_text="微博搜索页需要登录Cookie才能访问",
                    screenshot_path=screenshot_path,
                )
                articles.append(article)
                return articles, comments

            await self.browser.scroll_to_load_more(search_page, scroll_count=3, delay=2000)
            await self._hide_navigation_bar(search_page)

            safe_kw = clean_filename(keyword[:20])
            articles_per_kw = self.config.get('pipeline', {}).get('articles_per_keyword', 3)

            card_elements_info = await self._locate_card_elements(search_page)

            if not card_elements_info:
                self.logger.warning(f"    未找到微博卡片元素，保存整页截图")
                search_screenshot_name = generate_filename(safe_kw, 'search_page', '.png')
                search_screenshot_path = str(
                    self.screenshot_dir / 'articles' / search_screenshot_name
                )
                await self.browser.take_screenshot(search_page, search_screenshot_path, full_page=True)
                article = ArticleModel(
                    keyword=keyword,
                    title=f"搜索结果: {keyword}",
                    content_text=f"关键词 {keyword} 的微博搜索结果页面截图",
                    screenshot_path=search_screenshot_path,
                )
                articles.append(article)
                return articles, comments

            self.logger.info(f"    定位到 {len(card_elements_info)} 个微博卡片元素")

            for i, card_info in enumerate(card_elements_info[:articles_per_kw]):
                try:
                    card_index = card_info['index']
                    author_name = card_info.get('author_name', '')
                    content_text = card_info.get('content_text', '')
                    detail_url = card_info.get('detail_url', '')

                    self.logger.info(f"    📄 [{i+1}] {author_name}: {content_text[:40]}...")
                    self.logger.info(f"      🔗 detail_url=\"{detail_url}\"")

                    # ===== Step A: 在搜索页截取文章卡片 =====
                    await search_page.evaluate(f"""
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

                    article_screenshot_name = generate_filename(safe_kw, f'article_{i}', '.png')
                    article_screenshot_path = str(
                        self.screenshot_dir / 'articles' / article_screenshot_name
                    )

                    card_box = await search_page.evaluate(f"""
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

                    Path(article_screenshot_path).parent.mkdir(parents=True, exist_ok=True)

                    if card_box and card_box['height'] > 50:
                        clip = {
                            'x': max(0, card_box['x']),
                            'y': max(0, card_box['y']) + 50,
                            'width': card_box['width'],
                            'height': min(card_box['height'], 3000) - 50,
                        }
                        await search_page.screenshot(path=article_screenshot_path, clip=clip)
                        self.logger.info(f"    📸 文章卡片截图已保存")
                    else:
                        await self.browser.take_screenshot(search_page, article_screenshot_path, full_page=False)

                    await search_page.evaluate("""
                        () => {
                            document.querySelectorAll('[data-wb-comment-hidden]').forEach(el => {
                                el.style.display = el.getAttribute('data-wb-comment-hidden') || '';
                                el.removeAttribute('data-wb-comment-hidden');
                            });
                        }
                    """)

                    article = ArticleModel(
                        keyword=keyword,
                        title=content_text[:100],
                        author_name=author_name,
                        content_text=content_text[:2000],
                        publish_time=card_info.get('publish_time', ''),
                        repost_count=card_info.get('repost_count', 0),
                        comment_count=card_info.get('comment_count', 0),
                        like_count=card_info.get('like_count', 0),
                        url=detail_url,
                        screenshot_path=article_screenshot_path,
                    )
                    articles.append(article)
                    self.logger.info(f"    ✅ 第{i+1}条微博文章截图完成")

                    # ===== Step B: 打开详情页获取评论区 =====
                    detail_url_to_use = detail_url

                    if detail_url and 'app.weibo.com/t/feed/' in detail_url:
                        feed_id = detail_url.split('/feed/')[-1].split('?')[0].split('#')[0]
                        if feed_id:
                            detail_url_to_use = f"https://weibo.com/detail/{feed_id}"
                            self.logger.info(f"      🔄 转换URL: {detail_url} → {detail_url_to_use}")

                    valid_url = (
                        detail_url_to_use and 
                        'weibo.com/' in detail_url_to_use and 
                        not detail_url_to_use.startswith('sinaweibo://')
                    )

                    if valid_url:
                        self.logger.info(f"      🔗 打开详情页获取评论区: {detail_url_to_use[:60]}...")
                        detail_page = None
                        try:
                            detail_page = await self.browser.new_page()
                            nav_success = await self.browser.navigate_to(detail_page, detail_url_to_use, wait_for='domcontentloaded')

                            if nav_success:
                                await asyncio.sleep(5)
                                await self._hide_navigation_bar(detail_page)

                                # 滚动到评论区
                                await detail_page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.7)")
                                await asyncio.sleep(2)

                                # 展开评论区
                                await self._expand_all_comments(detail_page)
                                await asyncio.sleep(3)

                                # 多次滚动触发懒加载
                                for scroll_i in range(3):
                                    await detail_page.evaluate(f"window.scrollBy(0, {300 + scroll_i * 200})")
                                    await asyncio.sleep(1)

                                # 等待评论区加载完成：检查 data-index>0 的元素数量
                                for wait_i in range(5):
                                    comment_count = await detail_page.evaluate("""
                                        () => {
                                            const items = document.querySelectorAll('.vue-recycle-scroller__item-view');
                                            let count = 0;
                                            for (const el of items) {
                                                const scrollerItem = el.querySelector('.wbpro-scroller-item');
                                                if (scrollerItem) {
                                                    const di = parseInt(scrollerItem.getAttribute('data-index')) || 0;
                                                    if (di > 0) count++;
                                                }
                                            }
                                            return count;
                                        }
                                    """)
                                    if comment_count > 0:
                                        self.logger.info(f"      评论区已加载，发现 {comment_count} 条评论元素")
                                        break
                                    self.logger.debug(f"      等待评论区加载... ({wait_i+1}/5)")
                                    await asyncio.sleep(2)

                                # 截取评论区
                                page_comments = await self._screenshot_comments_from_detail_page(
                                    detail_page, keyword, i, safe_kw
                                )
                                comments.extend(page_comments)

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
                        self.logger.debug(f"    第{i+1}条微博无有效详情URL，跳过评论")

                except Exception as e:
                    self.logger.warning(f"    第{i+1}条微博处理失败: {e}")
                    continue

        except Exception as e:
            self.logger.error(f"    关键词处理异常: {e}")
        finally:
            if search_page:
                await self._restore_navigation_bar(search_page)
                await self.browser.close_page(search_page)

        return articles, comments

    async def _hide_navigation_bar(self, page):
        await page.evaluate("""
            () => {
                const navSelectors = [
                    '.s-top', '.S_top', '.s-topbar', '.topbar',
                    '.gn_header', '.gnb', '.S_bg2',
                    'header', '.header', '[class*="topbar"]',
                    '[class*="Topbar"]', '[class*="header"]', '[class*="Header"]',
                    '.s-fram-nav', '.s-nav', '.m-con-top',
                    '.pl-bread', '.s-top-nav',
                    '[id*="nav"]', '[id*="header"]', '[id*="top"]',
                    '.search-header', '.search-bar', '.search-nav',
                    '#pl_top_common', '#pl_top_banner',
                    '.navbar', '.nav-bar', '.top-nav',
                    '[class*="navbar"]', '[class*="Navbar"]',
                    '[class*="global"]', '[class*="Global"]',
                    '.gn-topbar', '.tb-head', '.tbt',
                    '.icon_new', '.new_icon', '.tag-new',
                    '[class*="icon_new"]', '[class*="new-icon"]',
                    '[class*="tag-new"]', '[class*="TagNew"]',
                    'span[class*="NEW"]', '.NEW',
                    '.user-avatar', '.avatar',
                    '[class*="user-avatar"]', '[class*="UserAvatar"]',
                    '[class*="avatar-img"]', '[class*="Avatar"]',
                    '.profile-photo', '.user-photo',
                    '.search-user-info', '.user-bar',
                    '.top-nav .user', '.header-right',
                    '[class*="user-info"]', '[class*="UserInfo"]',
                    '[class*="login-user"]', '[class*="LoginUser"]',
                    '.person-box', '.account-wrap',
                    '.fixed-top', '.sticky-top',
                ];

                for (const sel of navSelectors) {
                    try {
                        document.querySelectorAll(sel).forEach(el => {
                            if (!el.hasAttribute('data-wb-nav-hidden')) {
                                el.setAttribute('data-wb-nav-hidden', JSON.stringify({
                                    display: el.style.display,
                                    visibility: el.style.visibility,
                                    position: el.style.position,
                                    height: el.style.height
                                }));
                            }
                            el.style.setProperty('display', 'none', 'important');
                            el.style.setProperty('visibility', 'hidden', 'important');
                            el.style.setProperty('height', '0px', 'important');
                            el.style.setProperty('overflow', 'hidden', 'important');
                            el.style.setProperty('position', 'absolute', 'important');
                        });
                    } catch(e) {}
                }
            }
        """)

    async def _restore_navigation_bar(self, page):
        await page.evaluate("""
            () => {
                document.querySelectorAll('[data-wb-nav-hidden]').forEach(el => {
                    const prevStr = el.getAttribute('data-wb-nav-hidden');
                    if (prevStr) {
                        try {
                            const prev = JSON.parse(prevStr);
                            el.style.setProperty('display', prev.display || '', 'important');
                            el.style.setProperty('visibility', prev.visibility || '', 'important');
                            el.style.setProperty('height', prev.height || '', 'important');
                            el.style.setProperty('overflow', '', 'important');
                            el.style.setProperty('position', prev.position || '', 'important');
                        } catch(e) {
                            el.style.setProperty('display', '', 'important');
                            el.style.setProperty('visibility', '', 'important');
                            el.style.setProperty('height', '', 'important');
                        }
                        el.removeAttribute('data-wb-nav-hidden');
                    }
                });
            }
        """)

    async def _locate_card_elements(self, page) -> list:
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
            self.logger.warning(f"    卡片元素定位失败: {e}")
            return []

    async def _expand_all_comments(self, page):
        try:
            for _ in range(3):
                expanded = await page.evaluate("""
                    () => {
                        let clicked = false;
                        const expandSelectors = [
                            '[action-type="click_more_comment"]',
                            '.more_comments', '.comment_expand',
                            '[class*="expand"]', '.WB_feed_expand'
                        ];
                        for (const sel of expandSelectors) {
                            try {
                                const btns = document.querySelectorAll(sel);
                                for (const btn of btns) {
                                    if (btn.offsetParent !== null && btn.innerText.includes('展开')) {
                                        btn.click();
                                        clicked = true;
                                    }
                                }
                            } catch(e) {}
                        }
                        const moreLinks = document.querySelectorAll('a');
                        for (const link of moreLinks) {
                            const text = link.innerText || '';
                            if ((text.includes('更多评论') || text.includes('全部评论') || 
                                 text.includes('查看更多')) && link.offsetParent !== null) {
                                link.click();
                                clicked = true;
                            }
                        }
                        return clicked;
                    }
                """)
                if expanded:
                    await asyncio.sleep(1.5)
        except Exception as e:
            self.logger.debug(f"      展开评论区时出错: {e}")

    async def _screenshot_comments_from_detail_page(self, page, keyword: str,
                                                     article_index: int, safe_kw: str) -> list:
        """
        在微博详情页截取评论
        
        策略：
        1. 使用 vue-recycle-scroller__item-view 定位所有元素
        2. 通过子元素 .wbpro-scroller-item 的 data-index 属性过滤
        3. 只截取 data-index > 0 的元素（data-index=0 是文章内容）
        4. 每条评论单独截图
        """
        comment_models = []

        try:
            # ===== 遍历所有元素，记录每个元素的过滤判断详情 =====
            filter_debug = await page.evaluate("""
                () => {
                    const allItems = document.querySelectorAll('.vue-recycle-scroller__item-view');
                    
                    const details = [];
                    for (let i = 0; i < allItems.length; i++) {
                        const el = allItems[i];
                        const scrollerItem = el.querySelector('.wbpro-scroller-item');
                        const rect = el.getBoundingClientRect();
                        
                        const d = {
                            vueIndex: i,
                            hasScrollerItem: !!scrollerItem,
                            dataIndex: scrollerItem ? (parseInt(scrollerItem.getAttribute('data-index')) || 0) : -1,
                            elTop: Math.round(rect.top),
                            elHeight: Math.round(rect.height),
                            textPreview: (el.innerText || '').substring(0, 40).replace(/\\n/g, ' '),
                            pass_dataIndex: false,
                            pass_recommend: false,
                            finalPass: false
                        };
                        
                        // 判断1: data-index > 0
                        d.pass_dataIndex = d.dataIndex > 0;
                        
                        // 判断2: 非推荐内容
                        const text = (el.innerText || '');
                        d.pass_recommend = !text.includes('推荐') && !text.includes('荐读');
                        
                        d.finalPass = d.pass_dataIndex && d.pass_recommend;
                        
                        details.push(d);
                    }
                    
                    return { totalVueItems: allItems.length, details: details };
                }
            """)
            
            # 输出详细的过滤日志
            self.logger.info(f"      ===== 评论区过滤诊断 =====")
            self.logger.info(f"      总 vue-recycle-scroller__item-view 元素数: {filter_debug.get('totalVueItems', 0)}")
            
            for d in filter_debug.get('details', []):
                status = "✅通过" if d.get('finalPass') else "❌过滤"
                reasons = []
                if not d.get('pass_dataIndex'):
                    reasons.append(f"data-index={d.get('dataIndex')}<=0")
                if not d.get('pass_recommend'):
                    reasons.append("含推荐/荐读")
                
                reason_str = " | " + ", ".join(reasons) if reasons else ""
                self.logger.info(
                    f"      [{d.get('vueIndex')}] {status} "
                    f"data-index={d.get('dataIndex')} "
                    f"top={d.get('elTop')} h={d.get('elHeight')} "
                    f"text=\"{d.get('textPreview', '')}\""
                    f"{reason_str}"
                )
            self.logger.info(f"      ===== 诊断结束 =====")

            # ===== 筛选符合条件的评论 data-index 列表 =====
            comment_data_indices = []
            for d in filter_debug.get('details', []):
                if d.get('finalPass'):
                    comment_data_indices.append(d.get('dataIndex'))

            if not comment_data_indices:
                self.logger.warning(f"      未找到 data-index>0 的评论元素，尝试Fallback")
                await self._save_comment_area_fallback(page, keyword, article_index, safe_kw)
                return comment_models

            self.logger.info(f"      找到 {len(comment_data_indices)} 条评论（data-index>0）")

            # 逐条提取评论文字 + 截图（基于data-index定位，避免虚拟滚动导致索引错位）
            max_comments = self.config.get('comment', {}).get('max_comments', 10)
            data_indices_to_process = comment_data_indices[:max_comments]

            for seq, target_data_index in enumerate(data_indices_to_process):
                try:
                    # 滚动到该评论（通过data-index属性定位）
                    await page.evaluate(f"""
                        () => {{
                            const allItems = document.querySelectorAll('.vue-recycle-scroller__item-view .wbpro-scroller-item');
                            for (const item of allItems) {{
                                const di = parseInt(item.getAttribute('data-index')) || 0;
                                if (di === {target_data_index}) {{
                                    item.scrollIntoView({{ behavior: 'instant', block: 'center' }});
                                    break;
                                }}
                            }}
                        }}
                    """)
                    await asyncio.sleep(1.5)

                    # 提取评论文字（通过data-index重新定位）
                    comment_data = await page.evaluate(f"""
                        () => {{
                            const allItems = document.querySelectorAll('.vue-recycle-scroller__item-view');
                            let el = null;
                            for (const item of allItems) {{
                                const scrollerItem = item.querySelector('.wbpro-scroller-item');
                                const di = parseInt(scrollerItem?.getAttribute('data-index')) || 0;
                                if (di === {target_data_index}) {{
                                    el = item;
                                    break;
                                }}
                            }}
                            if (!el) return null;

                            const data = {{}};

                            const scrollerItem = el.querySelector('.wbpro-scroller-item');
                            const target = scrollerItem || el;
                            data.data_index = parseInt(scrollerItem?.getAttribute('data-index')) || 0;

                            const fullText = target.innerText || '';

                            const userLink = target.querySelector('a[href*="weibo.com/u/"], a[href*="weibo.com/n/"]');
                            data.author_name = userLink ? userLink.innerText.trim() : '';

                            const contentEl = target.querySelector('.WB_text, .txt, [class*="text"], [class*="content"]');
                            if (contentEl) {{
                                var text = contentEl.innerText.trim();
                                var colonIdx = text.indexOf(':');
                                if (colonIdx >= 0 && colonIdx < 30) {{
                                    text = text.substring(colonIdx + 1).trim();
                                }}
                                data.content_text = text;
                            }} else {{
                                var lines = fullText.split('\\n').filter(function(l) {{ return l.trim(); }});
                                if (lines.length >= 2) {{
                                    for (var k = 0; k < lines.length; k++) {{
                                        var ci = lines[k].indexOf(':');
                                        if (ci >= 0 && ci < 30) {{
                                            data.content_text = lines[k].substring(ci + 1).trim();
                                            if (!data.author_name) {{
                                                data.author_name = lines[k].substring(0, ci).trim();
                                            }}
                                            break;
                                        }}
                                    }}
                                    if (!data.content_text) {{
                                        data.content_text = lines.slice(1).join(' ').substring(0, 500);
                                    }}
                                }} else {{
                                    data.content_text = fullText.substring(0, 200);
                                }}
                            }}

                            const likeEl = target.querySelector('[class*="like"] em, [class*="like"] span, .count');
                            data.like_count = likeEl ? parseInt(likeEl.innerText) || 0 : 0;

                            const replyEls = target.querySelectorAll('[class*="reply"], [class*="child"]');
                            data.reply_count = replyEls.length;

                            const rect = el.getBoundingClientRect();
                            data.visible = rect.height > 20 && rect.y > -rect.height && rect.y < window.innerHeight;
                            data.box = {{
                                x: rect.x,
                                y: rect.y,
                                width: rect.width,
                                height: Math.min(rect.height, 2000)
                            }};

                            return data;
                        }}
                    """)

                    if not comment_data:
                        self.logger.warning(f"      ⚠ 评论[{seq}] data-index={target_data_index} 提取数据为null，跳过")
                        continue

                    data_index = comment_data.get('data_index', '?')
                    author = comment_data.get('author_name', '')
                    content = comment_data.get('content_text', '')[:80]
                    box_h = comment_data.get('box', {}).get('height', 0)
                    is_visible = comment_data.get('visible', False)

                    self.logger.info(f"      📋 评论[{seq}] data-index={target_data_index} author=\"{author}\" content=\"{content}\"")

                    if not content.strip():
                        self.logger.info(f"      ⏭ 评论[{seq}] 无文字内容，跳过截图")
                        continue

                    comment_screenshot_name = generate_filename(safe_kw, f'comment_{article_index}_{seq}', '.png')
                    comment_screenshot_path = str(
                        self.screenshot_dir / 'comments' / comment_screenshot_name
                    )

                    Path(comment_screenshot_path).parent.mkdir(parents=True, exist_ok=True)

                    screenshot_saved = False

                    if is_visible:
                        try:
                            element_handle = await page.query_selector(f'.vue-recycle-scroller__item-view .wbpro-scroller-item[data-index="{target_data_index}"]')
                            if element_handle:
                                parent_handle = await element_handle.evaluate_handle('el => el.closest(".vue-recycle-scroller__item-view")')
                                if parent_handle:
                                    await parent_handle.screenshot(path=comment_screenshot_path)
                                    screenshot_saved = True
                                    self.logger.info(f"      📸 评论[{seq}] 截图已保存 data-index={data_index} (element.screenshot)")
                        except Exception as elem_err:
                            self.logger.debug(f"      element.screenshot失败: {elem_err}, 尝试clip方式")

                    if not screenshot_saved:
                        box = comment_data.get('box')
                        if box and box.get('height', 0) > 20 and box.get('y', -999) > -box.get('height', 0):
                            clip_y = max(60, box['y'])
                            clip_height = box['height'] - (clip_y - box['y']) if clip_y > box['y'] else box['height']
                            if clip_height > 20:
                                clip = {
                                    'x': max(0, box['x']),
                                    'y': clip_y,
                                    'width': box['width'],
                                    'height': clip_height,
                                }
                                await page.screenshot(path=comment_screenshot_path, clip=clip)
                                screenshot_saved = True
                                self.logger.info(f"      📸 评论[{seq}] 截图已保存 data-index={data_index} clip=({clip['x']:.0f},{clip['y']:.0f},{clip['width']:.0f},{clip['height']:.0f})")

                    if not screenshot_saved:
                        self.logger.warning(f"      ⚠ 评论[{seq}] 截图失败，跳过 data-index={data_index}")
                        continue

                    comment = CommentModel(
                        article_url=page.url,
                        comment_id=f"comment_{keyword[:10]}_{article_index}_{seq}",
                        content_text=comment_data.get('content_text', ''),
                        author_name=comment_data.get('author_name', ''),
                        like_count=comment_data.get('like_count', 0),
                        screenshot_path=comment_screenshot_path,
                    )
                    comment_models.append(comment)

                except Exception as e:
                    self.logger.debug(f"      第{seq+1}条评论截图失败: {e}")
                    continue

            self.logger.info(f"      📸 共截取 {len(comment_models)} 条评论")

        except Exception as e:
            self.logger.warning(f"      详情页评论截图处理异常: {e}")

        return comment_models

    async def _save_comment_area_fallback(self, page, keyword: str, article_index: int, safe_kw: str):
        comment_screenshot_name = generate_filename(safe_kw, f'comment_area_{article_index}', '.png')
        comment_screenshot_path = str(
            self.screenshot_dir / 'comments' / comment_screenshot_name
        )

        fallback_box = await page.evaluate("""
            () => {
                const selectors = ['.wbpro-list', '.WB_feed_repeat', '[node-type="comment_list"]'];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const rect = el.getBoundingClientRect();
                        if (rect.height > 50) {
                            return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
                        }
                    }
                }
                return null;
            }
        """)

        Path(comment_screenshot_path).parent.mkdir(parents=True, exist_ok=True)

        if fallback_box and fallback_box.get('height', 0) > 50:
            await page.screenshot(path=comment_screenshot_path, clip=fallback_box)
        else:
            await self.browser.take_screenshot(page, comment_screenshot_path, full_page=False)

        self.logger.info(f"      📸 评论区整体截图(Fallback)已保存")

    async def run_single_keyword(self, keyword: str):
        self.logger.info(f"单独处理关键词: {keyword}")
        await self.browser.start_browser()
        try:
            articles, comments = await self._process_keyword(keyword)
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
