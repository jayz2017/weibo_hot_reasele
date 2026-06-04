import asyncio
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from models.article_model import ArticleModel
from models.comment_model import CommentModel
from utils.mysql_manager import MySQLManager
from utils.file_utils import generate_filename, clean_filename
from core.base import BaseSkill


class AuthorMonitor(BaseSkill):
    def __init__(self, config: Dict[str, Any], logger: logging.Logger, *, browser=None, mysql: Optional[MySQLManager] = None, image_handler=None):
        super().__init__(config, logger)
        self.browser = browser
        self.mysql = mysql
        self.image_handler = image_handler

        paths_config = config.get('paths', {})
        self.screenshot_dir = Path(paths_config.get('screenshot_dir', './data/screenshots'))
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

        self.monitor_minutes = config.get('author_monitor', {}).get('monitor_minutes', 30)
        self.max_articles_per_author = config.get('author_monitor', {}).get('max_articles_per_author', 5)
        self.max_comments_per_article = config.get('comment', {}).get('max_comments', 10)

    async def monitor_all_authors(self) -> Dict[str, Any]:
        authors = self._get_author_ids_from_db()
        if not authors:
            self.logger.info("[AuthorMonitor] wb_look_article_info表中无作者，跳过监控")
            return {"authors_monitored": 0, "articles": 0, "comments": 0}

        self.logger.info(f"[AuthorMonitor] 开始监控 {len(authors)} 位作者")

        total_articles = 0
        total_comments = 0

        for author_info in authors:
            author_id = author_info['author_id']
            author_name = author_info.get('author_name', '')
            try:
                self.logger.info(f"[AuthorMonitor] 监控作者: {author_name}(ID:{author_id})")
                articles, comments = await self._monitor_author(author_id, author_name)
                total_articles += len(articles)
                total_comments += len(comments)

                if self.mysql:
                    for article in articles:
                        try:
                            article_id = self.mysql.save_article(article.to_dict())
                            article_comments = [c for c in comments if hasattr(c, 'article_url') and c.article_url == article.url]
                            if article_comments:
                                comments_data = [c.to_dict() for c in article_comments]
                                self.mysql.save_comments_batch(comments_data, article_id)
                        except Exception as db_err:
                            self.logger.error(f"[AuthorMonitor] MySQL存储失败: {db_err}")

                await asyncio.sleep(3)
            except Exception as e:
                self.logger.error(f"[AuthorMonitor] 监控作者 {author_id} 失败: {e}")
                continue

        result = {
            "authors_monitored": len(authors),
            "articles": total_articles,
            "comments": total_comments
        }
        self.logger.info(f"[AuthorMonitor] 监控完成: {result}")
        return result

    async def monitor_author(self, author_id: str) -> Dict[str, Any]:
        articles, comments = await self._monitor_author(author_id)

        if self.mysql:
            for article in articles:
                try:
                    article_id = self.mysql.save_article(article.to_dict())
                    article_comments = [c for c in comments if hasattr(c, 'article_url') and c.article_url == article.url]
                    if article_comments:
                        comments_data = [c.to_dict() for c in article_comments]
                        self.mysql.save_comments_batch(comments_data, article_id)
                except Exception as db_err:
                    self.logger.error(f"[AuthorMonitor] MySQL存储失败: {db_err}")

        return {"author_id": author_id, "articles": len(articles), "comments": len(comments)}

    def _get_author_ids_from_db(self) -> List[Dict[str, str]]:
        if not self.mysql:
            self.logger.warning("[AuthorMonitor] MySQL未启用，无法获取作者ID")
            return []

        try:
            with self.mysql.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT article_id, article_name, article_zone_type FROM wb_look_article_info WHERE article_id IS NOT NULL AND article_id != ''")
                rows = cursor.fetchall()
                authors = []
                for row in rows:
                    authors.append({
                        'author_id': row['article_id'],
                        'author_name': row.get('article_name', ''),
                        'zone_type': row.get('article_zone_type', ''),
                    })
                self.logger.info(f"[AuthorMonitor] 从wb_look_article_info获取到 {len(authors)} 个作者")
                return authors
        except Exception as e:
            self.logger.error(f"[AuthorMonitor] 获取作者ID失败: {e}")
            return []

    def _get_existing_article_urls(self, author_id: str) -> set:
        if not self.mysql:
            return set()
        try:
            with self.mysql.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT url FROM wb_article WHERE author_id = %s AND url != ''", (author_id,))
                rows = cursor.fetchall()
                urls = {row['url'] for row in rows}
                self.logger.info(f"[AuthorMonitor] 作者{author_id}已有 {len(urls)} 篇文章记录")
                return urls
        except Exception as e:
            self.logger.error(f"[AuthorMonitor] 获取已有文章URL失败: {e}")
            return set()

    async def _monitor_author(self, author_id: str, author_name: str = '') -> tuple:
        articles = []
        comments = []

        profile_url = f"https://weibo.com/u/{author_id}"
        self.logger.info(f"[AuthorMonitor] 访问作者主页: {profile_url}")

        page = None
        try:
            page = await self.browser.new_page()
            nav_success = await self.browser.navigate_to(page, profile_url, wait_for='domcontentloaded')

            if not nav_success:
                self.logger.warning(f"[AuthorMonitor] 作者主页访问失败: {author_id}")
                return articles, comments

            await asyncio.sleep(5)

            current_url = page.url
            if 'passport.weibo.com' in current_url or 'signin' in current_url:
                self.logger.warning(f"[AuthorMonitor] 被重定向到登录页，需要Cookie")
                return articles, comments

            # 获取作者名称
            page_author_name = await page.evaluate("""
                () => {
                    const nameEl = document.querySelector('.ProfileHeader_name, .user_name, h1[class*="name"], [class*="UserName"], [class*="username"]');
                    if (nameEl) return nameEl.innerText.trim();
                    const metaEl = document.querySelector('meta[property="og:title"]');
                    if (metaEl && metaEl.content) return metaEl.content.trim();
                    const titleEl = document.querySelector('title');
                    if (titleEl) {
                        const text = titleEl.innerText || '';
                        if (text.includes('的微博')) return text.split('的微博')[0].trim();
                        if (text.includes('-')) {
                            const parts = text.split('-');
                            for (let i = 0; i < parts.length; i++) {
                                const p = parts[i].trim();
                                if (p && p !== '微博' && p !== '随时随地发现新鲜事' && !p.includes('weibo')) return p;
                            }
                        }
                    }
                    return '';
                }
            """)
            if not author_name:
                author_name = page_author_name
            self.logger.info(f"[AuthorMonitor] 作者名称: {author_name} (ID: {author_id})")

            existing_urls = self._get_existing_article_urls(author_id)

            # 在主页获取最近微博列表（仅提取信息，不截图）
            recent_posts = await self._get_recent_posts(page, author_name)

            if not recent_posts:
                self.logger.info(f"[AuthorMonitor] 作者 {author_name} 最近{self.monitor_minutes}分钟内无更新")
                return articles, comments

            self.logger.info(f"[AuthorMonitor] 发现 {len(recent_posts)} 条最近更新，逐条进入详情页截图")

            safe_kw = clean_filename(f"author_{author_id}")

            for i, post_info in enumerate(recent_posts[:self.max_articles_per_author]):
                try:
                    content_text = post_info.get('content_text', '')
                    detail_url = post_info.get('detail_url', '')
                    publish_time = post_info.get('publish_time', '')

                    if not detail_url or detail_url.startswith('sinaweibo://'):
                        self.logger.info(f"  ⏭ [{i+1}] 无有效详情URL，跳过")
                        continue

                    if detail_url in existing_urls:
                        self.logger.info(f"  ⏭ [{i+1}] 文章已存在，跳过: {detail_url[:60]}")
                        continue

                    self.logger.info(f"  📄 [{i+1}] 进入详情页: {detail_url[:80]}")

                    # 进入详情页，截图文章内容 + 评论
                    article_screenshot_name = generate_filename(safe_kw, f'article_{i}', '.png')
                    article_screenshot_path = str(self.screenshot_dir / 'articles' / article_screenshot_name)
                    Path(article_screenshot_path).parent.mkdir(parents=True, exist_ok=True)

                    article_data, post_comments = await self._process_detail_page(
                        detail_url, author_id, author_name, i, safe_kw,
                        article_screenshot_path
                    )

                    if article_data:
                        article = ArticleModel(
                            keyword=f"author_monitor_{author_id}",
                            title=content_text[:100],
                            author_name=author_name,
                            author_id=author_id,
                            content_text=article_data.get('content_text', content_text)[:2000],
                            publish_time=publish_time,
                            repost_count=post_info.get('repost_count', 0),
                            comment_count=post_info.get('comment_count', 0),
                            like_count=post_info.get('like_count', 0),
                            url=detail_url,
                            screenshot_path=article_screenshot_path,
                        )
                        articles.append(article)
                        self.logger.info(f"  ✅ 第{i+1}条微博详情页截图完成")

                    if post_comments:
                        comments.extend(post_comments)
                        self.logger.info(f"  ✅ 获取到 {len(post_comments)} 条评论")

                except Exception as e:
                    self.logger.warning(f"  第{i+1}条微博处理失败: {e}")
                    continue

        except Exception as e:
            self.logger.error(f"[AuthorMonitor] 监控作者 {author_id} 异常: {e}")
        finally:
            if page:
                await self.browser.close_page(page)

        return articles, comments

    async def _process_detail_page(self, detail_url: str, author_id: str,
                                    author_name: str, article_index: int,
                                    safe_kw: str, article_screenshot_path: str) -> tuple:
        """进入微博详情页，截图文章内容 + 获取评论截图"""
        article_data = None
        comment_models = []
        detail_page = None

        try:
            # 处理特殊URL格式
            if 'app.weibo.com/t/feed/' in detail_url:
                feed_id = detail_url.split('/feed/')[-1].split('?')[0].split('#')[0]
                if feed_id:
                    detail_url = f"https://weibo.com/detail/{feed_id}"

            detail_page = await self.browser.new_page()
            nav_success = await self.browser.navigate_to(detail_page, detail_url, wait_for='domcontentloaded')

            if not nav_success:
                self.logger.warning(f"    详情页访问失败: {detail_url[:60]}")
                return article_data, comment_models

            await asyncio.sleep(5)

            # 隐藏导航栏
            await self.image_handler._hide_navigation_bar(detail_page)
            light_stats = await self._prepare_light_screenshot_surface(detail_page)
            self.logger.info(f"    [light-screenshot] initial DOM cleanup: {light_stats}")

            # 等待文章内容加载
            for wait_i in range(10):
                content_loaded = await detail_page.evaluate("""
                    () => {
                        // 详情页的文章内容区域
                        const detailContent = document.querySelector('.detail-content, .weibo-detail, [class*="detail"]');
                        if (detailContent) return true;
                        // 或者有 vue-recycle-scroller（评论列表）
                        const scroller = document.querySelector('.vue-recycle-scroller');
                        if (scroller) return true;
                        // 或者有文章主体
                        const article = document.querySelector('article');
                        if (article) return true;
                        return false;
                    }
                """)
                if content_loaded:
                    break
                await asyncio.sleep(1)

            # 滚动到页面顶部，确保文章内容可见
            await detail_page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(2)

            # 强制加载所有懒加载图片
            await detail_page.evaluate("""
                () => {
                    document.querySelectorAll('img').forEach(img => {
                        img.setAttribute('loading', 'eager');
                        img.setAttribute('decoding', 'auto');
                        const dataSrc = img.getAttribute('data-src') || img.getAttribute('data-original');
                        if (dataSrc && !img.src) img.src = dataSrc;
                    });
                }
            """)
            await asyncio.sleep(2)

            # 提取文章内容文本
            article_content = await detail_page.evaluate("""
                () => {
                    const selectors = [
                        '.detail-content .txt', '.detail-content .content',
                        '.weibo-detail .txt', '.weibo-detail .content',
                        'article .txt', 'article .content',
                        'article [class*="text"]', 'article [class*="content"]',
                        '.WB_text', '.txt[node-type="feed_list_content"]',
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el && el.innerText.trim().length > 10) return el.innerText.trim();
                    }
                    // fallback: 取 article 内所有文本
                    const article = document.querySelector('article');
                    if (article) return article.innerText.trim().substring(0, 2000);
                    return '';
                }
            """)

            # 截图文章内容区域
            screenshot_saved = await self._screenshot_detail_article(detail_page, article_screenshot_path)

            if not screenshot_saved:
                # fallback: 截取整个页面可见区域
                await detail_page.screenshot(path=article_screenshot_path)
                self.logger.info(f"    📸 [文章截图] fallback: 整页截图 → {article_screenshot_path}")

            article_data = {'content_text': article_content or ''}

            # 获取评论截图
            comment_models = await self._screenshot_comments_from_detail_page(
                detail_page, f"author_{author_id}", article_index, safe_kw
            )

        except Exception as e:
            self.logger.warning(f"    详情页处理失败: {e}")
        finally:
            if detail_page:
                await self.image_handler._restore_navigation_bar(detail_page)
                await self.browser.close_page(detail_page)

        return article_data, comment_models

    async def _prepare_light_screenshot_surface(self, page, target_selector: str = '') -> Dict[str, Any]:
        """Force white screenshot backgrounds while preserving the page's foreground styles."""
        try:
            return await page.evaluate("""
                (targetSelector) => {
                    const stats = {
                        targetFound: false,
                        backgroundElements: 0,
                        filtersCleared: 0,
                        hiddenOverlays: 0
                    };

                    const styleId = 'codex-force-light-screenshot-style';
                    let style = document.getElementById(styleId);
                    if (!style) {
                        style = document.createElement('style');
                        style.id = styleId;
                        document.head.appendChild(style);
                    }
                    style.textContent = `
                        html, body {
                            background: #ffffff !important;
                            color-scheme: light !important;
                        }
                        html::before, html::after, body::before, body::after {
                            content: none !important;
                            display: none !important;
                            background: transparent !important;
                        }
                        .vue-recycle-scroller,
                        .vue-recycle-scroller__item-view,
                        .wbpro-scroller-item,
                        article,
                        main,
                        .detail-content,
                        .weibo-detail {
                            background: #ffffff !important;
                            background-color: #ffffff !important;
                            background-image: none !important;
                        }
                    `;

                    function parseColor(value) {
                        if (!value || value === 'transparent') return null;
                        const match = value.match(/rgba?\\(([^)]+)\\)/);
                        if (!match) return null;
                        const parts = match[1].split(',').map(p => p.trim());
                        if (parts.length < 3) return null;
                        return {
                            r: Number.parseFloat(parts[0]),
                            g: Number.parseFloat(parts[1]),
                            b: Number.parseFloat(parts[2]),
                            a: parts.length >= 4 ? Number.parseFloat(parts[3]) : 1
                        };
                    }

                    function isDark(color) {
                        return color && color.a > 0.05 && (color.r + color.g + color.b) < 260;
                    }

                    function hasVisualEffect(value) {
                        return Boolean(value && value !== 'none');
                    }

                    function forceLight(el) {
                        if (!el || !el.style) return;
                        const cs = window.getComputedStyle(el);
                        const bg = parseColor(cs.backgroundColor);

                        if (el === document.documentElement || el === document.body || isDark(bg) || (bg && bg.a > 0 && bg.a < 1)) {
                            el.style.setProperty('background-color', '#ffffff', 'important');
                            el.style.setProperty('background-image', 'none', 'important');
                        }
                        stats.backgroundElements += 1;
                    }

                    const containerSelector = '.vue-recycle-scroller, .vue-recycle-scroller__item-view, .wbpro-scroller-item, article, main, .detail-content, .weibo-detail';
                    const contentRoot = targetSelector
                        ? document.querySelector(targetSelector)
                        : (document.querySelector('.vue-recycle-scroller') || document.querySelector('article') || document.body);
                    stats.targetFound = Boolean(contentRoot);

                    const targets = new Set([document.documentElement, document.body]);
                    if (contentRoot) {
                        targets.add(contentRoot);
                        contentRoot.querySelectorAll(containerSelector).forEach(el => targets.add(el));
                    } else {
                        document.querySelectorAll(containerSelector).forEach(el => targets.add(el));
                    }
                    targets.forEach(forceLight);

                    const vw = Math.max(document.documentElement.clientWidth || 0, window.innerWidth || 0);
                    const vh = Math.max(document.documentElement.clientHeight || 0, window.innerHeight || 0);
                    const contentSelector = '.vue-recycle-scroller, .vue-recycle-scroller__item-view, .wbpro-scroller-item, article, main, .detail-content, .weibo-detail';
                    document.querySelectorAll('*').forEach(el => {
                        const cs = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        if (!rect.width || !rect.height || !vw || !vh) return;

                        const areaRatio = (rect.width * rect.height) / (vw * vh);
                        const position = cs.position;
                        const bg = parseColor(cs.backgroundColor);
                        const name = `${el.id || ''} ${el.className || ''}`.toLowerCase();
                        const likelyOverlayName = /mask|overlay|modal|dialog|popup|shade|layer|dark|passport|login/.test(name);
                        const hasDarkPaint = isDark(bg) || (bg && bg.a > 0.1 && bg.a < 1);
                        const hasBackdrop = hasVisualEffect(cs.backdropFilter) || hasVisualEffect(cs.webkitBackdropFilter);
                        const hasFilter = hasVisualEffect(cs.filter);
                        const coversScreen = areaRatio > 0.35 && rect.width > vw * 0.65 && rect.height > vh * 0.35;
                        const isFloating = position === 'fixed' || position === 'sticky' || position === 'absolute';
                        const isContent = Boolean(el.closest(contentSelector));

                        if (!isContent && isFloating && coversScreen && (likelyOverlayName || hasDarkPaint || hasBackdrop || hasFilter)) {
                            el.setAttribute('data-wb-hidden', '1');
                            el.style.setProperty('display', 'none', 'important');
                            el.style.setProperty('visibility', 'hidden', 'important');
                            stats.hiddenOverlays += 1;
                        }
                    });

                    return stats;
                }
            """, target_selector or '')
        except Exception as e:
            self.logger.warning(f"    [light-screenshot] DOM cleanup failed: {e}")
            return {"targetFound": False, "backgroundElements": 0, "filtersCleared": 0, "hiddenOverlays": 0, "error": str(e)}

    async def _screenshot_detail_article(self, page, screenshot_path: str) -> bool:
        """在详情页中截图文章内容区域"""
        try:
            light_stats = await self._prepare_light_screenshot_surface(
                page,
                'article, .detail-content, .weibo-detail, .wbpro-scroller-item[data-index="0"]'
            )
            self.logger.info(f"    [light-screenshot] article DOM cleanup: {light_stats}")

            # 只强制截图容器背景为白色，保留文字、图标等原始颜色
            await page.evaluate("""
                () => {
                    const selectors = [
                        'html', 'body', 'article', '.detail-content',
                        '.weibo-detail', '.wbpro-scroller-item[data-index="0"]'
                    ];
                    for (const sel of selectors) {
                        try {
                            document.querySelectorAll(sel).forEach(el => {
                                el.style.setProperty('background-color', '#ffffff', 'important');
                                el.style.setProperty('background-image', 'none', 'important');
                            });
                        } catch(e) {
                            // Ignore invalid selectors on page variants.
                        }
                    }
                }
            """)

            # 隐藏所有固定/粘性元素
            await page.evaluate("""
                () => {
                    const navSelectors = [
                        '.s-top', '.S_top', '.s-topbar', '.topbar',
                        '.gn_header', '.gnb', '.S_bg2',
                        'nav', '.global-nav', '.gn_nav',
                        '.main-top', '.top-bar',
                        '.fixed-top', '.sticky-top',
                        '[class*="topbar"]', '[class*="Topbar"]',
                    ];
                    for (const sel of navSelectors) {
                        try {
                            document.querySelectorAll(sel).forEach(el => {
                                el.setAttribute('data-wb-hidden', '1');
                                el.style.setProperty('display', 'none', 'important');
                            });
                        } catch(e) {}
                    }
                    // 隐藏所有 fixed/sticky 元素
                    document.querySelectorAll('*').forEach(el => {
                        const style = window.getComputedStyle(el);
                        if (style.position === 'fixed' || style.position === 'sticky') {
                            el.setAttribute('data-wb-hidden', '1');
                            el.style.setProperty('display', 'none', 'important');
                        }
                    });
                }
            """)

            # 查找文章内容区域并截图
            clip_info = await page.evaluate("""
                () => {
                    // 优先查找详情页的文章区域
                    const selectors = [
                        '.detail-content', '.weibo-detail',
                        'article', '.wbpro-scroller-item[data-index="0"]',
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el) {
                            const rect = el.getBoundingClientRect();
                            if (rect.height > 50 && rect.width > 100) {
                                return {
                                    x: Math.round(rect.x),
                                    y: Math.round(rect.y),
                                    w: Math.round(rect.width),
                                    h: Math.round(rect.height)
                                };
                            }
                        }
                    }
                    return null;
                }
            """)

            if clip_info and clip_info['h'] > 0:
                clip = {
                    'x': clip_info['x'],
                    'y': clip_info['y'],
                    'width': clip_info['w'],
                    'height': min(clip_info['h'], 5000),
                }
                self.logger.info(f"    📐 [详情页截图] clip={clip}")
                await page.screenshot(path=screenshot_path, clip=clip)
                self.logger.info(f"    📸 [详情页截图] 成功 → {screenshot_path}")
            else:
                # 找不到文章区域，截取视口上半部分
                viewport = page.viewport_size
                clip = {'x': 0, 'y': 0, 'width': viewport['width'], 'height': min(viewport['height'], 1500)}
                self.logger.info(f"    📐 [详情页截图] fallback clip={clip}")
                await page.screenshot(path=screenshot_path, clip=clip)

            # 恢复隐藏的元素
            await page.evaluate("""
                () => {
                    document.querySelectorAll('[data-wb-hidden="1"]').forEach(el => {
                        el.style.removeProperty('display');
                        el.removeAttribute('data-wb-hidden');
                    });
                }
            """)

            return True

        except Exception as e:
            self.logger.error(f"    ❌ [详情页截图] 异常: {e}")
            return False

    async def _get_recent_posts(self, page, author_name: str = '') -> List[Dict]:
        posts_data = await page.evaluate("""
            () => {
                const results = [];
                const items = document.querySelectorAll('.wbpro-scroller-item');
                
                for (let i = 0; i < items.length; i++) {
                    const item = items[i];
                    try {
                        const data = {};
                        data.index = i;
                        
                        const txtEl = item.querySelector('.txt, p[node-type="feed_list_content"], [class*="text"], [class*="content"]');
                        data.content_text = txtEl ? txtEl.innerText.trim() : (item.innerText || '').substring(0, 200).trim();
                        
                        let timeText = '';
                        const headInfoEl = item.querySelector('.head-info, [class*="head-info"], [class*="HeadInfo"]');
                        if (headInfoEl) {
                            timeText = headInfoEl.innerText.trim();
                        }
                        if (!timeText) {
                            const timeEl = item.querySelector('a[href*="weibo.com/"] [class*="time"], a[class*="time"], [class*="time"], [class*="date"], time, .from a:first-child');
                            if (timeEl) timeText = timeEl.innerText.trim();
                        }
                        if (!timeText) {
                            const allLinks = item.querySelectorAll('a');
                            for (const link of allLinks) {
                                const href = link.href || '';
                                const text = link.innerText.trim();
                                if (href && !href.includes('/u/') && !href.includes('/p/') && 
                                    !href.includes('/follow') && !href.includes('/fans') &&
                                    (text.includes('分钟前') || text.includes('刚刚') || text.includes('秒前') ||
                                     text.includes('小时前') || text.includes('今天') || text.includes('月'))) {
                                    timeText = text;
                                    break;
                                }
                            }
                        }
                        if (!timeText) {
                            const spans = item.querySelectorAll('span, a');
                            for (const sp of spans) {
                                const t = sp.innerText.trim();
                                if (t && (t.includes('分钟前') || t.includes('刚刚') || t.includes('秒前') ||
                                          t.includes('小时前') || t.includes('今天') || /\\d+月\\d+日/.test(t))) {
                                    timeText = t;
                                    break;
                                }
                            }
                        }
                        data.publish_time = timeText;
                        
                        let detailUrl = '';
                        const timeLinks = item.querySelectorAll('a[href*="weibo.com/"]');
                        for (const link of timeLinks) {
                            const href = link.href || '';
                            if (href.includes('/u/') || href.includes('/p/') || 
                                href.includes('/follow') || href.includes('/fans') ||
                                href.includes('/signup') || href.includes('/login') ||
                                href.startsWith('javascript:')) continue;
                            if (href.match(/weibo\\.com\\/\\d+\\/[a-zA-Z0-9]+/)) {
                                detailUrl = href;
                                break;
                            }
                        }
                        if (!detailUrl) {
                            for (const link of timeLinks) {
                                const href = link.href || '';
                                if (href.includes('/u/') || href.includes('/p/') || 
                                    href.includes('/follow') || href.includes('/fans') ||
                                    href.startsWith('javascript:')) continue;
                                const text = link.innerText.trim();
                                if (text && (text.includes('分钟前') || text.includes('刚刚') || text.includes('秒前') ||
                                             text.includes('小时前') || text.includes('今天') || text.includes('月'))) {
                                    detailUrl = href;
                                    break;
                                }
                            }
                        }
                        if (!detailUrl) {
                            const fromLink = item.querySelector('.from a:last-child, a[href*="weibo.com/"]');
                            if (fromLink && !fromLink.href.includes('/u/') && !fromLink.href.includes('/p/')) {
                                detailUrl = fromLink.href;
                            }
                        }
                        data.detail_url = detailUrl;
                        
                        const actEls = item.querySelectorAll('.card-act li, [class*="action"] li, [class*="toolbar"] span, [class*="woo-like"], [class*="interaction"]');
                        if (actEls.length >= 3) {
                            data.repost_count = parseInt(actEls[0]?.innerText) || 0;
                            data.comment_count = parseInt(actEls[1]?.innerText) || 0;
                            data.like_count = parseInt(actEls[2]?.innerText) || 0;
                        } else {
                            data.repost_count = 0;
                            data.comment_count = 0;
                            data.like_count = 0;
                        }
                        
                        if (data.content_text) {
                            results.push(data);
                        }
                    } catch(e) {}
                }
                return results;
            }
        """)

        if not posts_data:
            self.logger.info(f"[AuthorMonitor] 作者主页未解析到任何微博内容")
            return []

        self.logger.info(f"[AuthorMonitor] 作者主页解析到 {len(posts_data)} 条微博，开始时间过滤(≤{self.monitor_minutes}分钟)")

        recent_posts = []
        for post in posts_data:
            pub_time_str = post.get('publish_time', '')
            content_preview = post.get('content_text', '')[:40]
            detail_url = post.get('detail_url', '')
            is_recent = self._is_recent(pub_time_str)

            self.logger.info(f"  📋 微博[{post.get('index', '?')}] time=\"{pub_time_str}\" recent={is_recent} url={detail_url[:60] if detail_url else 'N/A'}")
            self.logger.info(f"     内容: {content_preview}...")

            if is_recent:
                recent_posts.append(post)

        if not recent_posts:
            self.logger.info(f"[AuthorMonitor] 最近{self.monitor_minutes}分钟内无更新，跳过该作者")
            return []

        self.logger.info(f"[AuthorMonitor] 筛选出 {len(recent_posts)} 条最近{self.monitor_minutes}分钟内的更新")
        return recent_posts

    def _is_recent(self, time_str: str) -> bool:
        if not time_str:
            return False
        try:
            now = datetime.now()
            if '分钟前' in time_str:
                minutes = int(''.join(c for c in time_str if c.isdigit()) or '0')
                return minutes <= self.monitor_minutes
            elif '刚刚' in time_str or '秒前' in time_str:
                return True
            elif '今天' in time_str:
                time_part = time_str.replace('今天', '').strip()
                if time_part:
                    try:
                        pub_time = datetime.strptime(time_part, '%H:%M')
                    except ValueError:
                        pub_time = datetime.strptime(time_part, '%H:%M:%S')
                    pub_datetime = now.replace(hour=pub_time.hour, minute=pub_time.minute, second=0)
                    return (now - pub_datetime).total_seconds() <= self.monitor_minutes * 60
            elif '小时前' in time_str:
                hours = int(''.join(c for c in time_str if c.isdigit()) or '0')
                return hours * 60 <= self.monitor_minutes
            elif '昨天' in time_str:
                return False
            else:
                try:
                    pub_time = datetime.strptime(time_str, '%m月%d日 %H:%M')
                    pub_datetime = pub_time.replace(year=now.year)
                    return (now - pub_datetime).total_seconds() <= self.monitor_minutes * 60
                except ValueError:
                    pass
                try:
                    pub_time = datetime.strptime(time_str, '%m月%d日 %H:%M:%S')
                    pub_datetime = pub_time.replace(year=now.year)
                    return (now - pub_datetime).total_seconds() <= self.monitor_minutes * 60
                except ValueError:
                    pass
                try:
                    pub_time = datetime.strptime(time_str, '%Y年%m月%d日 %H:%M')
                    return (now - pub_time).total_seconds() <= self.monitor_minutes * 60
                except ValueError:
                    pass
                try:
                    import re
                    match = re.search(r'(\d+)月(\d+)日\s*(\d+):(\d+)', time_str)
                    if match:
                        month, day, hour, minute = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                        pub_datetime = now.replace(month=month, day=day, hour=hour, minute=minute, second=0)
                        return (now - pub_datetime).total_seconds() <= self.monitor_minutes * 60
                except Exception:
                    pass
                try:
                    import re
                    match = re.search(r'(\d+)-(\d+)\s+(\d+):(\d+)', time_str)
                    if match:
                        month, day, hour, minute = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                        pub_datetime = now.replace(month=month, day=day, hour=hour, minute=minute, second=0)
                        return (now - pub_datetime).total_seconds() <= self.monitor_minutes * 60
                except Exception:
                    pass
        except Exception:
            pass
        return False

    async def _screenshot_comments_from_detail_page(self, page, keyword: str,
                                                      article_index: int, safe_kw: str) -> list:
        comment_models = []
        processed_indices = set()

        try:
            self.logger.info(f"    🔍 [评论截图] 开始渐进式扫描评论区 (最多{self.max_comments_per_article}条)")

            # 只清理截图背景和遮罩，不改文字/图标/徽标颜色。
            await asyncio.sleep(0.5)
            light_stats = await self._prepare_light_screenshot_surface(page, '.vue-recycle-scroller')
            self.logger.info(f"    [light-screenshot] comments DOM cleanup: {light_stats}")

            max_scroll_rounds = 30
            no_new_count = 0

            for scroll_round in range(max_scroll_rounds):
                if len(processed_indices) >= self.max_comments_per_article:
                    self.logger.info(f"    🔍 [评论截图] 已达到最大评论数 {self.max_comments_per_article}，停止滚动")
                    break

                await self._prepare_light_screenshot_surface(page, '.vue-recycle-scroller')
                visible_comments = await page.evaluate("""
                    () => {
                        const vh = window.innerHeight;
                        const results = [];
                        const allItems = document.querySelectorAll('.vue-recycle-scroller__item-view');
                        for (const item of allItems) {
                            const scrollerItem = item.querySelector('.wbpro-scroller-item');
                            if (!scrollerItem) continue;
                            const di = parseInt(scrollerItem.getAttribute('data-index')) || 0;
                            if (di <= 0) continue;
                            const rect = item.getBoundingClientRect();
                            const inViewport = rect.height > 10 && rect.top < vh && rect.bottom > 0;
                            if (!inViewport) continue;
                            const text = (item.innerText || '');
                            if (text.includes('推荐') || text.includes('荐读')) continue;

                            const data = {};
                            data.data_index = di;
                            data.rect = { x: rect.x, y: rect.y, w: rect.width, h: rect.height };

                            const userLink = item.querySelector('a[href*="weibo.com/u/"], a[href*="weibo.com/n/"]');
                            data.author_name = userLink ? userLink.innerText.trim() : '';

                            const contentEl = scrollerItem.querySelector('.WB_text, .txt, [class*="text"], [class*="content"]');
                            if (contentEl) {
                                var t = contentEl.innerText.trim();
                                var ci = t.indexOf(':');
                                if (ci >= 0 && ci < 30) t = t.substring(ci + 1).trim();
                                data.content_text = t;
                            } else {
                                var lines = text.split('\\n').filter(function(l) { return l.trim(); });
                                if (lines.length >= 2) {
                                    for (var k = 0; k < lines.length; k++) {
                                        var ci = lines[k].indexOf(':');
                                        if (ci >= 0 && ci < 30) {
                                            data.content_text = lines[k].substring(ci + 1).trim();
                                            if (!data.author_name) data.author_name = lines[k].substring(0, ci).trim();
                                            break;
                                        }
                                    }
                                    if (!data.content_text) data.content_text = lines.slice(1).join(' ').substring(0, 500);
                                } else {
                                    data.content_text = text.substring(0, 200);
                                }
                            }

                            const likeEl = scrollerItem.querySelector('[class*="like"] em, [class*="like"] span, .count');
                            data.like_count = likeEl ? parseInt(likeEl.innerText) || 0 : 0;

                            results.push(data);
                        }
                        return results;
                    }
                """)

                new_in_this_round = 0
                for cd in (visible_comments or []):
                    di = cd.get('data_index')
                    if di in processed_indices:
                        continue

                    processed_indices.add(di)
                    new_in_this_round += 1

                    content = cd.get('content_text', '')
                    author = cd.get('author_name', '')
                    rect_info = cd.get('rect', {})

                    self.logger.info(f"    📋 [评论截图] 第{scroll_round+1}轮发现 data-index={di} rect={rect_info} author=\"{author}\" content=\"{content[:60]}\"")

                    if not content.strip():
                        self.logger.info(f"    ⏭ [评论截图] data-index={di} 无文字内容，跳过")
                        continue

                    seq = len(processed_indices) - 1
                    comment_screenshot_name = generate_filename(safe_kw, f'comment_{article_index}_{seq}', '.png')
                    comment_screenshot_path = str(self.screenshot_dir / 'comments' / comment_screenshot_name)
                    Path(comment_screenshot_path).parent.mkdir(parents=True, exist_ok=True)

                    screenshot_saved = False
                    try:
                        element_handle = await page.query_selector(f'.vue-recycle-scroller__item-view .wbpro-scroller-item[data-index="{di}"]')
                        if element_handle:
                            parent_handle = await element_handle.evaluate_handle('el => el.closest(".vue-recycle-scroller__item-view")')
                            if parent_handle:
                                parent_el = parent_handle.as_element()
                                parent_box = await parent_el.bounding_box() if parent_el else None
                                self.logger.info(f"    📐 [评论截图] data-index={di} parent bounding_box: {parent_box}")

                                if parent_box:
                                    ph = parent_box['height']
                                    if 10 < ph < 800:
                                        self.logger.info(f"    📐 [评论截图] data-index={di} 高度检查通过: h={ph:.0f}，执行 page.screenshot(clip)")
                                        # 滚动到元素位置确保在视口内
                                        await element_handle.scroll_into_view_if_needed()
                                        await asyncio.sleep(0.3)
                                        await self._prepare_light_screenshot_surface(page, '.vue-recycle-scroller')
                                        # 重新获取 bounding_box（滚动后位置变化）
                                        parent_box = await parent_el.bounding_box()
                                        if not parent_box:
                                            self.logger.warning(f"    ❌ [评论截图] data-index={di} 滚动后 bounding_box 为空")
                                            continue
                                        # 使用 page.screenshot(clip) 以支持浅色主题
                                        clip = self._clip_to_viewport(parent_box, page.viewport_size)
                                        if not clip:
                                            self.logger.warning(f"    ❌ [评论截图] data-index={di} clip 超出视口: {parent_box}")
                                            continue
                                        await page.screenshot(path=comment_screenshot_path, clip=clip)
                                        screenshot_saved = True
                                        self.logger.info(f"    📸 [评论截图] data-index={di} element.screenshot 成功 → {comment_screenshot_path}")
                                    else:
                                        self.logger.info(f"    ⏭ [评论截图] data-index={di} 高度检查未通过: h={ph:.0f} (需 10 < h < 800)")
                                else:
                                    self.logger.warning(f"    ❌ [评论截图] data-index={di} parent bounding_box 为空")
                            else:
                                self.logger.warning(f"    ❌ [评论截图] data-index={di} 未找到父元素")
                        else:
                            self.logger.warning(f"    ❌ [评论截图] data-index={di} 未找到 .wbpro-scroller-item")
                    except Exception as e:
                        self.logger.warning(f"    ❌ [评论截图] data-index={di} element.screenshot 异常: {e}")

                    if not screenshot_saved:
                        self.logger.info(f"    ⏭ [评论截图] data-index={di} 截图未保存，跳过")
                        continue

                    comment = CommentModel(
                        article_url=page.url,
                        comment_id=f"comment_author_{keyword[:10]}_{article_index}_{seq}",
                        content_text=content,
                        author_name=author,
                        like_count=cd.get('like_count', 0),
                        screenshot_path=comment_screenshot_path,
                    )
                    comment_models.append(comment)

                self.logger.info(f"    🔍 [评论截图] 第{scroll_round+1}轮完成: 新发现{new_in_this_round}条, 累计处理{len(processed_indices)}条, 截图{len(comment_models)}条")

                if new_in_this_round == 0:
                    no_new_count += 1
                    if no_new_count >= 3:
                        self.logger.info(f"    🔍 [评论截图] 连续{no_new_count}轮无新评论，停止滚动")
                        break
                else:
                    no_new_count = 0

                await page.mouse.wheel(0, 500)
                await asyncio.sleep(1.5)

        except Exception as e:
            self.logger.error(f"    ❌ [评论截图] 整体异常: {e}", exc_info=True)

        self.logger.info(f"    🔍 [评论截图] 最终结果: 扫描{len(processed_indices)}条, 截图{len(comment_models)}条")
        return comment_models

    def _clip_to_viewport(self, box: Dict[str, Any], viewport: Optional[Dict[str, int]]) -> Optional[Dict[str, int]]:
        """Clamp a Playwright bounding box to the current viewport."""
        if not box or not viewport:
            return None

        viewport_width = int(viewport.get('width') or 0)
        viewport_height = int(viewport.get('height') or 0)
        if viewport_width <= 0 or viewport_height <= 0:
            return None

        left = int(round(box.get('x', 0)))
        top = int(round(box.get('y', 0)))
        right = int(round(left + box.get('width', 0)))
        bottom = int(round(top + box.get('height', 0)))

        left = max(0, left)
        top = max(0, top)
        right = min(viewport_width, right)
        bottom = min(viewport_height, bottom)

        width = right - left
        height = bottom - top
        if width <= 1 or height <= 1:
            return None

        return {'x': left, 'y': top, 'width': width, 'height': height}

    async def execute(self, *args, **kwargs) -> Dict[str, Any]:
        """执行技能：监控所有作者"""
        return await self.monitor_all_authors()
