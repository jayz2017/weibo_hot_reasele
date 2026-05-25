import asyncio
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from models.article_model import ArticleModel
from models.comment_model import CommentModel
from utils.mysql_manager import MySQLManager
from utils.file_utils import generate_filename, clean_filename


class AuthorMonitor:
    def __init__(self, config: Dict[str, Any], browser_controller, mysql: Optional[MySQLManager], logger: logging.Logger):
        self.config = config
        self.browser = browser_controller
        self.mysql = mysql
        self.logger = logger

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

            await self._hide_navigation_bar(page)

            page_author_name = await page.evaluate("""
                () => {
                    const nameEl = document.querySelector('.ProfileHeader_name, .user_name, h1[class*="name"], [class*="UserName"]');
                    if (nameEl) return nameEl.innerText.trim();
                    const titleEl = document.querySelector('title');
                    if (titleEl) {
                        const text = titleEl.innerText || '';
                        const parts = text.split('-');
                        if (parts.length > 0) return parts[0].trim();
                    }
                    return '';
                }
            """)
            if not author_name:
                author_name = page_author_name
            self.logger.info(f"[AuthorMonitor] 作者名称: {author_name} (ID: {author_id})")

            existing_urls = self._get_existing_article_urls(author_id)

            recent_posts = await self._get_recent_posts(page, author_name)

            if not recent_posts:
                self.logger.info(f"[AuthorMonitor] 作者 {author_name} 最近{self.monitor_minutes}分钟内无更新")
                return articles, comments

            self.logger.info(f"[AuthorMonitor] 发现 {len(recent_posts)} 条最近更新")

            safe_kw = clean_filename(f"author_{author_id}")

            for i, post_info in enumerate(recent_posts[:self.max_articles_per_author]):
                try:
                    content_text = post_info.get('content_text', '')
                    detail_url = post_info.get('detail_url', '')
                    publish_time = post_info.get('publish_time', '')

                    if detail_url and detail_url in existing_urls:
                        self.logger.info(f"  ⏭ [{i+1}] 文章已存在，跳过: {detail_url[:60]}")
                        continue

                    self.logger.info(f"  📄 [{i+1}] {content_text[:50]}...")

                    article_screenshot_name = generate_filename(safe_kw, f'article_{i}', '.png')
                    article_screenshot_path = str(self.screenshot_dir / 'articles' / article_screenshot_name)
                    Path(article_screenshot_path).parent.mkdir(parents=True, exist_ok=True)

                    card_index = post_info.get('index', i)
                    screenshot_saved = await self._screenshot_post_card(page, card_index, article_screenshot_path)

                    if not screenshot_saved:
                        await self.browser.take_screenshot(page, article_screenshot_path, full_page=False)

                    article = ArticleModel(
                        keyword=f"author_monitor_{author_id}",
                        title=content_text[:100],
                        author_name=author_name,
                        author_id=author_id,
                        content_text=content_text[:2000],
                        publish_time=publish_time,
                        repost_count=post_info.get('repost_count', 0),
                        comment_count=post_info.get('comment_count', 0),
                        like_count=post_info.get('like_count', 0),
                        url=detail_url,
                        screenshot_path=article_screenshot_path,
                    )
                    articles.append(article)
                    self.logger.info(f"  ✅ 第{i+1}条微博文章截图完成")

                    if detail_url and 'weibo.com/' in detail_url and not detail_url.startswith('sinaweibo://'):
                        post_comments = await self._get_post_comments(
                            detail_url, author_id, i, safe_kw
                        )
                        comments.extend(post_comments)
                        if post_comments:
                            self.logger.info(f"  ✅ 获取到 {len(post_comments)} 条评论")

                except Exception as e:
                    self.logger.warning(f"  第{i+1}条微博处理失败: {e}")
                    continue

        except Exception as e:
            self.logger.error(f"[AuthorMonitor] 监控作者 {author_id} 异常: {e}")
        finally:
            if page:
                await self._restore_navigation_bar(page)
                await self.browser.close_page(page)

        return articles, comments

    async def _get_recent_posts(self, page, author_name: str = '') -> List[Dict]:
        cutoff_time = datetime.now() - timedelta(minutes=self.monitor_minutes)

        posts_data = await page.evaluate("""
            () => {
                const results = [];
                const items = document.querySelectorAll('.wbpro-scroller-item, .card-wrap, [class*="FeedItem"], [class*="feed_item"]');
                
                for (let i = 0; i < items.length; i++) {
                    const item = items[i];
                    try {
                        const data = {};
                        data.index = i;
                        
                        const card = item.querySelector('.card-wrap') || item;
                        
                        const txtEl = card.querySelector('.txt, p[node-type="feed_list_content"], [class*="text"], [class*="content"]');
                        data.content_text = txtEl ? txtEl.innerText.trim() : (item.innerText || '').substring(0, 200).trim();
                        
                        const timeEl = card.querySelector('.from a:first-child, [class*="time"], [class*="date"], time');
                        data.publish_time = timeEl ? timeEl.innerText.trim() : '';
                        
                        const headEl = card.querySelector('.from a:last-child, a[href*="weibo.com/"]');
                        data.detail_url = headEl ? headEl.href : '';
                        
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
                        
                        const actEls = card.querySelectorAll('.card-act li, [class*="action"] li, [class*="toolbar"] span');
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
            return []

        recent_posts = []
        for post in posts_data:
            pub_time_str = post.get('publish_time', '')
            if self._is_recent(pub_time_str):
                recent_posts.append(post)

        if not recent_posts:
            self.logger.info(f"[AuthorMonitor] 未找到最近{self.monitor_minutes}分钟内的更新，取前{self.max_articles_per_author}条")
            return posts_data[:self.max_articles_per_author]

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
                    pub_time = datetime.strptime(time_part, '%H:%M')
                    pub_datetime = now.replace(hour=pub_time.hour, minute=pub_time.minute, second=0)
                    return (now - pub_datetime).total_seconds() <= self.monitor_minutes * 60
            elif '小时前' in time_str:
                hours = int(''.join(c for c in time_str if c.isdigit()) or '0')
                return hours * 60 <= self.monitor_minutes
            else:
                try:
                    pub_time = datetime.strptime(time_str, '%m月%d日 %H:%M')
                    pub_datetime = pub_time.replace(year=now.year)
                    return (now - pub_datetime).total_seconds() <= self.monitor_minutes * 60
                except ValueError:
                    pass
        except Exception:
            pass
        return False

    async def _screenshot_post_card(self, page, card_index: int, screenshot_path: str) -> bool:
        try:
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
                    const rect = targetEl.getBoundingClientRect();
                    return {{ x: rect.x, y: rect.y, width: rect.width, height: rect.height }};
                }}
            """)

            if card_box and card_box.get('height', 0) > 50:
                clip = {
                    'x': max(0, card_box['x']),
                    'y': max(0, card_box['y']) + 50,
                    'width': card_box['width'],
                    'height': min(card_box['height'], 3000) - 50,
                }
                await page.screenshot(path=screenshot_path, clip=clip)
                return True

            try:
                element_handle = await page.query_selector(f'.wbpro-scroller-item:nth-child({card_index + 1}), .card-wrap:nth-child({card_index + 1})')
                if element_handle:
                    await element_handle.screenshot(path=screenshot_path)
                    return True
            except Exception:
                pass

            return False
        except Exception as e:
            self.logger.debug(f"[AuthorMonitor] 截图失败: {e}")
            return False

    async def _get_post_comments(self, detail_url: str, author_id: str,
                                  article_index: int, safe_kw: str) -> list:
        comment_models = []
        detail_page = None

        try:
            if 'app.weibo.com/t/feed/' in detail_url:
                feed_id = detail_url.split('/feed/')[-1].split('?')[0].split('#')[0]
                if feed_id:
                    detail_url = f"https://weibo.com/detail/{feed_id}"

            if detail_url.startswith('sinaweibo://'):
                return comment_models

            detail_page = await self.browser.new_page()
            nav_success = await self.browser.navigate_to(detail_page, detail_url, wait_for='domcontentloaded')

            if not nav_success:
                return comment_models

            await asyncio.sleep(5)
            await self._hide_navigation_bar(detail_page)

            await detail_page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.7)")
            await asyncio.sleep(2)

            for scroll_i in range(3):
                await detail_page.evaluate(f"window.scrollBy(0, {300 + scroll_i * 200})")
                await asyncio.sleep(1)

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
                    self.logger.info(f"    评论区已加载，发现 {comment_count} 条评论元素")
                    break
                await asyncio.sleep(2)

            comment_models = await self._screenshot_comments_from_detail_page(
                detail_page, f"author_{author_id}", article_index, safe_kw
            )

        except Exception as e:
            self.logger.warning(f"[AuthorMonitor] 获取评论失败: {e}")
        finally:
            if detail_page:
                await self.browser.close_page(detail_page)

        return comment_models

    async def _screenshot_comments_from_detail_page(self, page, keyword: str,
                                                      article_index: int, safe_kw: str) -> list:
        comment_models = []

        try:
            filter_debug = await page.evaluate("""
                () => {
                    const allItems = document.querySelectorAll('.vue-recycle-scroller__item-view');
                    const details = [];
                    for (let i = 0; i < allItems.length; i++) {
                        const el = allItems[i];
                        const scrollerItem = el.querySelector('.wbpro-scroller-item');
                        const d = {
                            vueIndex: i,
                            dataIndex: scrollerItem ? (parseInt(scrollerItem.getAttribute('data-index')) || 0) : -1,
                            pass_dataIndex: false,
                            pass_recommend: false,
                            finalPass: false
                        };
                        d.pass_dataIndex = d.dataIndex > 0;
                        const text = (el.innerText || '');
                        d.pass_recommend = !text.includes('推荐') && !text.includes('荐读');
                        d.finalPass = d.pass_dataIndex && d.pass_recommend;
                        details.push(d);
                    }
                    return { totalVueItems: allItems.length, details: details };
                }
            """)

            comment_data_indices = []
            for d in filter_debug.get('details', []):
                if d.get('finalPass'):
                    comment_data_indices.append(d.get('dataIndex'))

            if not comment_data_indices:
                self.logger.info(f"    未找到评论元素")
                return comment_models

            self.logger.info(f"    找到 {len(comment_data_indices)} 条评论")

            data_indices_to_process = comment_data_indices[:self.max_comments_per_article]

            for seq, target_data_index in enumerate(data_indices_to_process):
                try:
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

                            const rect = el.getBoundingClientRect();
                            data.visible = rect.height > 20 && rect.y > -rect.height && rect.y < window.innerHeight;

                            return data;
                        }}
                    """)

                    if not comment_data:
                        continue

                    author = comment_data.get('author_name', '')
                    content = comment_data.get('content_text', '')
                    is_visible = comment_data.get('visible', False)

                    self.logger.info(f"    📋 评论[{seq}] data-index={target_data_index} author=\"{author}\" content=\"{content[:60]}\"")

                    if not content.strip():
                        self.logger.info(f"    ⏭ 评论[{seq}] 无文字内容，跳过")
                        continue

                    comment_screenshot_name = generate_filename(safe_kw, f'comment_{article_index}_{seq}', '.png')
                    comment_screenshot_path = str(self.screenshot_dir / 'comments' / comment_screenshot_name)
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
                                    self.logger.info(f"    📸 评论[{seq}] 截图已保存 (element.screenshot)")
                        except Exception:
                            pass

                    if not screenshot_saved:
                        try:
                            await page.screenshot(path=comment_screenshot_path, full_page=False)
                            screenshot_saved = True
                        except Exception:
                            pass

                    if not screenshot_saved:
                        continue

                    comment = CommentModel(
                        article_url=page.url,
                        comment_id=f"comment_author_{keyword[:10]}_{article_index}_{seq}",
                        content_text=comment_data.get('content_text', ''),
                        author_name=author,
                        like_count=comment_data.get('like_count', 0),
                        screenshot_path=comment_screenshot_path,
                    )
                    comment_models.append(comment)

                except Exception as e:
                    self.logger.debug(f"    评论[{seq}]处理失败: {e}")
                    continue

        except Exception as e:
            self.logger.warning(f"[AuthorMonitor] 评论截图异常: {e}")

        return comment_models

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
