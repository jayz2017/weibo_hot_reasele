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
                    const nameEl = document.querySelector('.ProfileHeader_name, .user_name, h1[class*="name"], [class*="UserName"], [class*="username"]');
                    if (nameEl) return nameEl.innerText.trim();
                    const metaEl = document.querySelector('meta[property="og:title"]');
                    if (metaEl && metaEl.content) return metaEl.content.trim();
                    const titleEl = document.querySelector('title');
                    if (titleEl) {
                        const text = titleEl.innerText || '';
                        if (text.includes('的微博')) {
                            return text.split('的微博')[0].trim();
                        }
                        if (text.includes('-')) {
                            const parts = text.split('-');
                            for (let i = 0; i < parts.length; i++) {
                                const p = parts[i].trim();
                                if (p && p !== '微博' && p !== '随时随地发现新鲜事' && !p.includes('weibo')) {
                                    return p;
                                }
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

    async def _screenshot_post_card(self, page, card_index: int, screenshot_path: str) -> bool:
        try:
            self.logger.info(f"    🔍 [文章截图] 开始 card_index={card_index}")

            hide_result = await page.evaluate("""
                () => {
                    const selectors = [
                        '.s-top', '.S_top', '.s-topbar', '.topbar', '.gn_header',
                        'header', '.header', '.Header',
                        '.ProfileHeader', '.profile-header',
                        '.ProfileHeader_new', '.profile-header_new',
                        '[class*="ProfileHeader"]', '[class*="profile-head"]',
                        '.wb-proj-header', '.wb-header',
                        'nav', '.global-nav', '.gn_nav',
                        '.main-top', '.top-bar',
                    ];
                    let hidden = 0;
                    for (const sel of selectors) {
                        try {
                            document.querySelectorAll(sel).forEach(el => {
                                el.style.setProperty('display', 'none', 'important');
                                el.style.setProperty('visibility', 'hidden', 'important');
                                el.style.setProperty('height', '0px', 'important');
                                el.style.setProperty('overflow', 'hidden', 'important');
                            });
                            hidden++;
                        } catch(e) {}
                    }

                    const allEls = document.querySelectorAll('*');
                    let stickyHidden = 0;
                    let profileHidden = 0;
                    for (const el of allEls) {
                        const style = window.getComputedStyle(el);
                        if (style.position === 'fixed' || style.position === 'sticky') {
                            const rect = el.getBoundingClientRect();
                            if (rect.y < 150 && rect.height > 20 && rect.height < 200) {
                                el.style.setProperty('display', 'none', 'important');
                                el.style.setProperty('visibility', 'hidden', 'important');
                                stickyHidden++;
                            }
                        }

                        const text = (el.innerText || '').trim();
                        if ((text.includes('返回') && text.length < 30) ||
                            (text.includes('关注') && text.includes('粉丝') && text.length < 200)) {
                            el.style.setProperty('display', 'none', 'important');
                            el.style.setProperty('visibility', 'hidden', 'important');
                            el.style.setProperty('height', '0px', 'important');
                            profileHidden++;
                        }
                    }

                    return { selectorsHidden: hidden, stickyHidden: stickyHidden, profileHidden: profileHidden };
                }
            """)
            self.logger.info(f"    🔍 [文章截图] 隐藏元素完成: {hide_result}")

            total_items = await page.evaluate("""
                () => document.querySelectorAll('.wbpro-scroller-item').length
            """)
            self.logger.info(f"    🔍 [文章截图] 页面共有 {total_items} 个 .wbpro-scroller-item 元素")

            await page.evaluate(f"""
                () => {{
                    const items = document.querySelectorAll('.wbpro-scroller-item');
                    if (items.length > {card_index}) items[{card_index}].scrollIntoView({{ behavior: 'instant', block: 'start' }});
                }}
            """)
            self.logger.info(f"    🔍 [文章截图] scrollIntoView(block='start') 完成，等待2秒渲染")
            await asyncio.sleep(2)

            await page.evaluate(f"""
                () => {{
                    const items = document.querySelectorAll('.wbpro-scroller-item');
                    if (items.length <= {card_index}) return;
                    const item = items[{card_index}];
                    const header = item.querySelector('header');
                    if (header) {{
                        header.style.setProperty('display', 'flex', 'important');
                        header.style.setProperty('visibility', 'visible', 'important');
                        header.style.setProperty('opacity', '1', 'important');
                        header.style.setProperty('min-height', '52px', 'important');
                    }}
                    const avatarDiv = item.querySelector('[class*="avatar"]');
                    if (avatarDiv) {{
                        avatarDiv.style.setProperty('display', 'inline-block', 'important');
                        avatarDiv.style.setProperty('visibility', 'visible', 'important');
                        avatarDiv.style.setProperty('width', '52px', 'important');
                        avatarDiv.style.setProperty('height', '52px', 'important');
                    }}
                    const avatarImg = item.querySelector('[class*="avatar"] img');
                    if (avatarImg) {{
                        avatarImg.style.setProperty('display', 'inline', 'important');
                        avatarImg.style.setProperty('visibility', 'visible', 'important');
                        avatarImg.style.setProperty('width', '52px', 'important');
                        avatarImg.style.setProperty('height', '52px', 'important');
                    }}
                }}
            """)
            self.logger.info(f"    🔍 [文章截图] 强制渲染 header/avatar CSS 完成，等待1秒")
            await asyncio.sleep(1)

            for wait_i in range(8):
                avatar_ok = await page.evaluate(f"""
                    () => {{
                        const items = document.querySelectorAll('.wbpro-scroller-item');
                        if (items.length <= {card_index}) return false;
                        const item = items[{card_index}];
                        const avatar = item.querySelector('[class*="avatar"] img');
                        if (!avatar) return true;
                        const rect = avatar.getBoundingClientRect();
                        return rect.width > 5 && rect.height > 5;
                    }}
                """)
                if avatar_ok:
                    self.logger.info(f"    ✅ [文章截图] 头像已渲染 (第{wait_i+1}次检查)")
                    break
                self.logger.debug(f"    ⏳ [文章截图] 头像未渲染，等待... (第{wait_i+1}次)")
                await asyncio.sleep(1)
            else:
                self.logger.warning(f"    ⚠️ [文章截图] 头像8次检查后仍未渲染，继续截图")

            screenshot_info = await page.evaluate(f"""
                () => {{
                    const items = document.querySelectorAll('.wbpro-scroller-item');
                    if (items.length <= {card_index}) return null;
                    const item = items[{card_index}];
                    const rect = item.getBoundingClientRect();

                    const articleEl = item.querySelector('article');
                    const artRect = articleEl ? articleEl.getBoundingClientRect() : null;

                    const avatar = item.querySelector('[class*="avatar"] img');
                    const avatarRect = avatar ? avatar.getBoundingClientRect() : null;

                    const header = item.querySelector('header');
                    const headerRect = header ? header.getBoundingClientRect() : null;

                    return {{
                        item: {{ x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) }},
                        article: artRect ? {{ x: Math.round(artRect.x), y: Math.round(artRect.y), w: Math.round(artRect.width), h: Math.round(artRect.height) }} : null,
                        avatar: avatarRect ? {{ x: Math.round(avatarRect.x), y: Math.round(avatarRect.y), w: Math.round(avatarRect.width), h: Math.round(avatarRect.height) }} : null,
                        header: headerRect ? {{ x: Math.round(headerRect.x), y: Math.round(headerRect.y), w: Math.round(headerRect.width), h: Math.round(headerRect.height) }} : null,
                    }};
                }}
            """)
            self.logger.info(f"    📐 [文章截图] 截图前状态: item={screenshot_info.get('item')}, article={screenshot_info.get('article')}")

            if not screenshot_info or not screenshot_info.get('item'):
                self.logger.warning(f"    ❌ [文章截图] screenshot_info 为空，无法截图")
                return False

            target_rect = screenshot_info.get('article') or screenshot_info['item']
            clip_x = max(0, int(target_rect['x']))
            clip_y = max(0, int(target_rect['y']))
            clip_w = int(target_rect['w'])
            clip_h = min(int(target_rect['h']), 5000)

            self.logger.info(f"    📐 [文章截图] 使用 clip 方式: x={clip_x} y={clip_y} w={clip_w} h={clip_h}")

            clip = {'x': clip_x, 'y': clip_y, 'width': clip_w, 'height': clip_h}
            await page.screenshot(path=screenshot_path, clip=clip)
            self.logger.info(f"    📸 [文章截图] page.screenshot(clip) 成功 → {screenshot_path}")
            return True

        except Exception as e:
            self.logger.error(f"    ❌ [文章截图] 异常: {e}", exc_info=True)
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
        processed_indices = set()

        try:
            self.logger.info(f"    🔍 [评论截图] 开始渐进式扫描评论区 (最多{self.max_comments_per_article}条)")

            max_scroll_rounds = 30
            no_new_count = 0

            for scroll_round in range(max_scroll_rounds):
                if len(processed_indices) >= self.max_comments_per_article:
                    self.logger.info(f"    🔍 [评论截图] 已达到最大评论数 {self.max_comments_per_article}，停止滚动")
                    break

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
                                        self.logger.info(f"    📐 [评论截图] data-index={di} 高度检查通过: h={ph:.0f}，执行 element.screenshot")
                                        await parent_el.screenshot(path=comment_screenshot_path)
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
