import asyncio
import os
import re
import logging
import aiohttp
from pathlib import Path
from typing import Dict, Any, List, Optional

from models.zhibo8_comment_model import Zhibo8CommentModel
from utils.mysql_manager import MySQLManager
from utils.file_utils import generate_filename, clean_filename
from core.base import BaseSkill


class Zhibo8MatchScraper(BaseSkill):
    def __init__(self, config: Dict[str, Any], logger: logging.Logger, *, browser=None, mysql: Optional[MySQLManager] = None):
        super().__init__(config, logger)
        self.browser = browser
        self.mysql = mysql

        zhibo8_config = config.get('zhibo8', {})
        screenshot_dir = zhibo8_config.get('screenshot_dir', './data/screenshots/zhibo8_comments')
        self.screenshot_dir = Path(screenshot_dir)
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

        self.max_comments = zhibo8_config.get('max_comments', 30)
        self.wait_for_comments = zhibo8_config.get('wait_for_comments', 8000)

    async def scrape_match(self, url: str) -> Dict[str, Any]:
        self.logger.info(f"[Zhibo8] 开始爬取比赛评论: {url}")

        page = await self.browser.new_page()
        try:
            console_messages = []
            def on_console(msg):
                if msg.type in ('error', 'warning'):
                    console_messages.append(f"[{msg.type}] {msg.text[:200]}")
            page.on('console', on_console)

            await page.set_extra_http_headers({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
            })

            zhibo8_webuid = os.environ.get('ZBB_WEBUID', '')
            zhibo8_stype = os.environ.get('ZBB_STYPE', 'basketball')
            zhibo8_cookies = [
                {'name': 'ZBB_WEBUID', 'value': zhibo8_webuid, 'domain': '.zhibo8.com', 'path': '/'},
                {'name': 'isFirstAll', 'value': '1', 'domain': '.zhibo8.com', 'path': '/'},
                {'name': 'stype', 'value': zhibo8_stype, 'domain': '.zhibo8.com', 'path': '/'},
            ]
            await self.browser.context.add_cookies(zhibo8_cookies)
            self.logger.info(f"[Zhibo8] 已设置 {len(zhibo8_cookies)} 个直播吧Cookie")

            target_url = url
            for attempt in range(3):
                try:
                    if attempt > 0:
                        self.logger.info(f"[Zhibo8] 第{attempt+1}次尝试访问...")
                        await asyncio.sleep(3)
                    wait_strategy = 'networkidle' if attempt == 2 else 'domcontentloaded'
                    await page.goto(target_url, wait_until=wait_strategy, timeout=60000)
                    break
                except Exception as nav_err:
                    self.logger.warning(f"[Zhibo8] 导航失败({target_url[:60]}...): {nav_err}")
                    if attempt == 2:
                        try:
                            await page.goto(url, wait_until='load', timeout=90000)
                            break
                        except Exception as final_err:
                            self.logger.error(f"[Zhibo8] 所有重试均失败: {final_err}")
                            return {'url': url, 'error': f'页面访问失败', 'comments_total': 0, 'comments_processed': 0}

            current_url = page.url
            self.logger.info(f"[Zhibo8] 页面加载完成: {current_url[:80]}")
            await asyncio.sleep(3)

            page_content = await page.content()
            if len(page_content) < 500:
                self.logger.warning(f"[Zhibo8] 页面内容过短({len(page_content)}字符)，可能未正确加载")
            else:
                self.logger.info(f"[Zhibo8] 页面内容长度: {len(page_content)} 字符")

            match_info = await self._get_match_info(page, url)
            self.logger.info(f"[Zhibo8] 比赛信息: {match_info}")

            self.logger.info(f"[Zhibo8] 等待JavaScript渲染评论 (8秒)...")
            await asyncio.sleep(8)

            if console_messages:
                self.logger.info(f"[Zhibo8] 浏览器控制台消息 ({len(console_messages)}条):")
                for cm in console_messages[:15]:
                    self.logger.info(f"  {cm}")
            else:
                self.logger.info(f"[Zhibo8] 浏览器控制台: 无错误/警告")

            quick_check = await page.evaluate("""
                () => {
                    const pllist = document.getElementById('pllist');
                    if (!pllist) return { found: false };
                    const tables = pllist.querySelectorAll('table');
                    let dingCount = 0;
                    for (const t of tables) {
                        if (/顶\\s*\\(\\d+\\)/.test(t.innerText)) dingCount++;
                    }
                    return { found: true, totalTables: tables.length, commentTables: dingCount };
                }
            """)
            self.logger.info(f"[Zhibo8] 快速检查: {quick_check}")

            comments_data = await self._get_hot_comments(page, url, match_info)

            comments_with_screenshots = []
            for i, comment in enumerate(comments_data[:self.max_comments]):
                try:
                    safe_kw = clean_filename(match_info.get('match_title', f'match_{match_info.get("match_id", "")}'))
                    screenshot_name = generate_filename(safe_kw, f'comment_{i}', '.png')
                    screenshot_path = str(self.screenshot_dir / screenshot_name)

                    screenshot_ok = await self._screenshot_comment(page, i, screenshot_path)
                    if screenshot_ok:
                        comment.screenshot_path = screenshot_path
                    else:
                        comment.screenshot_path = ''

                    comments_with_screenshots.append(comment)

                    self.logger.info(f"  📋 [{i+1}] @{comment.author_name}: \"{comment.content_text[:50]}\" 顶={comment.like_count} 踩={comment.reply_count}")
                except Exception as e:
                    self.logger.warning(f"  ⚠️ [{i+1}] 评论处理异常: {e}")
                    comments_with_screenshots.append(comment)

            if self.mysql and comments_with_screenshots:
                dict_list = [c.to_dict() for c in comments_with_screenshots]
                saved_count = self.mysql.save_zhibo8_comments_batch(dict_list)
                self.logger.info(f"[Zhibo8] MySQL存储完成: {saved_count} 条")

            result = {
                'url': url,
                'match_info': match_info,
                'comments_total': len(comments_data),
                'comments_processed': len(comments_with_screenshots),
                'comments': [c.to_dict() for c in comments_with_screenshots],
            }

            return result

        except Exception as e:
            self.logger.error(f"[Zhibo8] 爬取异常: {e}", exc_info=True)
            return {'url': url, 'error': str(e), 'comments_total': 0, 'comments_processed': 0}
        finally:
            await self.browser.close_page(page)

    async def _get_match_info(self, page, url: str) -> Dict[str, Any]:
        try:
            info = await page.evaluate("""
                (matchUrl) => {
                    const result = {};

                    const titleEl = document.querySelector('title');
                    if (titleEl) result.page_title = titleEl.innerText.trim();

                    const scoreText = document.body.innerText || '';
                    const scoreMatch = scoreText.match(/(\\d+)\\s*[：:-]\\s*(\\d+)/);
                    if (scoreMatch) {
                        result.home_score = parseInt(scoreMatch[1]);
                        result.away_score = parseInt(scoreMatch[2]);
                    }

                    const teamMatch = scoreText.match(/([A-Za-z\\u4e00-\\u9fa5]+)\\s*\\d+\\s*[：:-]\\s*\\d+\\s*([A-Za-z\\u4e00-\\u9fa5]+)/);
                    if (teamMatch) {
                        result.home_team = teamMatch[1].trim();
                        result.away_team = teamMatch[2].trim();
                        result.match_title = `${result.home_team} ${result.home_score || '?'} vs ${result.away_team} ${result.away_score || '?'}`;
                    } else {
                        result.match_title = result.page_title || '';
                    }

                    const pathMatch = matchUrl.match(/match(\\d+)/);
                    result.match_id = pathMatch ? pathMatch[1] : '';

                    return result;
                }
            """, url)
            info['match_url'] = url
            return info
        except Exception as e:
            self.logger.warning(f"[Zhibo8] 获取比赛信息失败: {e}")
            return {'match_url': url, 'match_title': '', 'match_id': ''}

    async def _get_hot_comments(self, page, url: str, match_info: Dict) -> List[Zhibo8CommentModel]:
        try:
            raw_comments = await page.evaluate("""
                () => {
                    const results = [];

                    let container = document.getElementById('pllist');
                    if (!container) {
                        container = document.getElementById('hotpp');
                    }
                    if (!container) {
                        return { error: '未找到评论容器(#hotpp或#pllist)', found: false };
                    }

                    const containerRect = container.getBoundingClientRect();
                    const containerInfo = {
                        id: container.id,
                        tag: container.tagName,
                        childCount: container.children.length,
                        htmlLen: container.innerHTML.length,
                        rect: { x: Math.round(containerRect.x), y: Math.round(containerRect.y), w: Math.round(containerRect.width), h: Math.round(containerRect.height) }
                    };

                    const tables = container.querySelectorAll('table');
                    for (let i = 0; i < tables.length; i++) {
                        const table = tables[i];
                        const text = (table.innerText || '').trim();

                        if (!text || text.length < 8 || text.length > 3000) continue;
                        if (!/顶\\s*\\(\\d+\\)/.test(text)) continue;

                        const data = {};
                        data.index = results.length;

                        const dingMatch = text.match(/顶\\s*\\((\\d+)\\)/);
                        data.like_count = dingMatch ? parseInt(dingMatch[1]) : 0;

                        const caiMatch = text.match(/踩\\s*\\((\\d+)\\)/);
                        data.reply_count = caiMatch ? parseInt(caiMatch[1]) : 0;

                        const timeMatch = text.match(/(\\d{2}-\\d{2}\\s+\\d{2}:\\d{2})/);
                        data.publish_time = timeMatch ? timeMatch[1] : '';

                        const nameEl = table.querySelector('.name, .userPro .name, .left .name, [class*="name"]');
                        data.author_name = nameEl ? (nameEl.innerText || '').trim() : '';

                        if (!data.author_name) {
                            const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
                            for (let k = 0; k < Math.min(lines.length, 5); k++) {
                                const line = lines[k];
                                if (/^\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}$/.test(line)) continue;
                                if (/顶\\s*\\(\\d+\\)|踩\\s*\\(\\d+\\)/.test(line)) continue;
                                if (/^(举报|回复|收起|展开)$/.test(line)) continue;
                                if (line.length > 3 && line.length < 50 && !line.includes(' ')) {
                                    data.author_name = line;
                                    break;
                                }
                            }
                        }

                        const conEl = table.querySelector('.con, .content, [class*="con"], .contentPro .con');
                        data.content_text = conEl ? (conEl.innerText || '').trim() : '';

                        if (data.content_text) {
                            data.content_text = data.content_text
                                .replace(/\\s*举报\\s*$/, '')
                                .replace(/\\s*回复\\s*$/, '')
                                .replace(/\\s*\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}\\s*$/, '')
                                .trim();
                        }

                        if (!data.content_text || data.content_text.length < 3) {
                            const contentParts = [];
                            const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
                            let foundContent = false;
                            for (let j = 0; j < lines.length; j++) {
                                const line = lines[j];
                                if (/顶\\s*\\(\\d+\\)|踩\\s*\\(\\d+\\)/.test(line)) continue;
                                if (/^\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}$/.test(line)) continue;
                                if (/^(举报|回复|收起|展开)$/.test(line)) continue;
                                if (/下载最新直播吧/.test(line)) continue;
                                if (/查看图片$/.test(line)) continue;
                                if (!foundContent && line === data.author_name) { foundContent = true; continue; }
                                contentParts.push(line);
                            }
                            data.content_text = contentParts.join('\\n');
                        }

                        const rect = table.getBoundingClientRect();
                        data.rect = { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) };
                        data.tag = table.tagName;

                        if (data.content_text && data.content_text.length > 3 && data.like_count >= 0) {
                            results.push(data);
                        }
                    }

                    return { found: true, container: containerInfo, comments: results };
                }
            """)

            if not raw_comments:
                self.logger.warning("[Zhibo8] 评论提取返回空")
                return []

            if not raw_comments.get('found'):
                self.logger.warning(f"[Zhibo8] {raw_comments.get('error', '未知错误')}")
                return []

            container_info = raw_comments.get('container', {})
            self.logger.info(f"[Zhibo8] 热门评论容器: tag={container_info.get('tag')} cls={container_info.get('cls','')[:40]} children={container_info.get('childCount')} rect={container_info.get('rect')}")

            comments = []
            for rc in raw_comments.get('comments', []):
                comment = Zhibo8CommentModel(
                    match_url=url,
                    match_id=match_info.get('match_id', ''),
                    match_title=match_info.get('match_title', ''),
                    comment_id=f"{match_info.get('match_id', '')}_{rc.get('index', len(comments))}",
                    author_name=rc.get('author_name', ''),
                    content_text=rc.get('content_text', ''),
                    like_count=rc.get('like_count', 0),
                    reply_count=rc.get('reply_count', 0),
                    publish_time=rc.get('publish_time', ''),
                )
                comments.append(comment)

            return comments

        except Exception as e:
            self.logger.error(f"[Zhibo8] 提取评论失败: {e}", exc_info=True)
            return []

    def _extract_comments_from_html(self, raw_html: str, url: str, match_info: Dict) -> List[Zhibo8CommentModel]:
        comments = []
        try:
            hot_div_match = re.search(r'<div[^>]*id=["\']?hotpp["\']?[^>]*>(.*?)</div>\s*</div>\s*<div\s+id=["\']?re["\']?', raw_html, re.DOTALL)
            if not hot_div_match:
                alt_match = re.search(r'id=["\']?hotpp["\']?[^>]*>(.*?)</div>', raw_html, re.DOTALL)
                if alt_match:
                    hot_html = alt_match.group(1)
                else:
                    self.logger.info("[Zhibo8正则] 未找到#hotpp容器，使用全HTML搜索")
                    hot_html = raw_html
            else:
                hot_html = hot_div_match.group(1)

            if len(hot_html) < 200:
                self.logger.info(f"[Zhibo8正则] #hotpp内容过短({len(hot_html)}字符)，切换到全HTML搜索模式")
                hot_html = raw_html

            self.logger.info(f"[Zhibo8正则] 搜索区域长度: {len(hot_html)} 字符")

            comment_pattern = re.compile(
                r'<div[^>]*class=["\'][^"\']*comment[^"\']*["\'][^>]*>.*?'
                r'(?:<div[^>]*>)?(?:<a[^>]*>)?([^<]+?)</a>.*?'
                r'(\d{2}-\d{2}\s+\d{2}:\d{2})\s+'
                r'顶\s*\((\d+)\)\s*踩\s*\((\d+)\)',
                re.DOTALL
            )

            simple_pattern = re.compile(
                r'(?:吧友_\w+|[\w\u4e00-\u9fa5]+)\s+(\d{2}-\d{2}\s+\d{2}:\d{2})\s+顶\s*\((\d+)\)\s*踩\s*\((\d+)\)[\s\S]*?(.*?)(?=回复|查看回复|$)',
                re.DOTALL
            )

            matches = comment_pattern.findall(hot_html)
            if not matches:
                self.logger.debug(f"[Zhibo8正则] 主pattern未匹配，尝试简化pattern...")
                matches = simple_pattern.findall(hot_html)

            if not matches:
                line_pattern = re.compile(
                    r'([\w\u4e00-\u9fa5_]+)\s+(\d{2}-\d{2}\s+\d{2}:\d{2})\s+顶\s*\((\d+)\)\s*踩\s*\((\d+)\)'
                )
                dingcai_matches = list(line_pattern.finditer(hot_html))
                self.logger.info(f"[Zhibo8正则] 行级匹配到 {len(dingcai_matches)} 个顶/踩行")

                if len(dingcai_matches) == 0:
                    ding_pos = hot_html.find('顶(')
                    cao_pos = hot_html.find('踩(')
                    self.logger.info(f"[Zhibo8正则] '顶(' 位置: {ding_pos}, '踩(' 位置: {cao_pos}")

                    if ding_pos > -1:
                        context_start = max(0, ding_pos - 100)
                        context_end = min(len(hot_html), ding_pos + 150)
                        context = hot_html[context_start:context_end]
                        self.logger.info(f"[Zhibo8正则] '顶(' 上下文 ({ding_pos}):")
                        for ci, line in enumerate(context.split('\n')):
                            self.logger.info(f"    L{ci}: {line[:200]}")

                    if cao_pos > -1 and cao_pos != ding_pos + 1:
                        cs = max(0, cao_pos - 80)
                        ce = min(len(hot_html), cao_pos + 80)
                        self.logger.info(f"[Zhibo8正则] '踩(' 上下文 ({cao_pos}): {hot_html[cs:ce][:200]}")

                for mi, m in enumerate(dingcai_matches):
                    author = m.group(1).strip()
                    pub_time = m.group(2).strip()
                    like_count = int(m.group(3))
                    reply_count = int(m.group(4))

                    start_pos = m.end()
                    end_pos = dingcai_matches[mi + 1].start() if mi + 1 < len(dingcai_matches) else len(hot_html)
                    content_block = hot_html[start_pos:end_pos]

                    content_text = re.sub(r'<[^>]+>', '', content_block)
                    content_text = re.sub(r'&nbsp;', ' ', content_text)
                    content_text = re.sub(r'&lt;', '<', content_text)
                    content_text = re.sub(r'&gt;', '>', content_text)
                    content_text = re.sub(r'&amp;', '&', content_text)
                    content_text = '\n'.join([l.strip() for l in content_text.split('\n') if l.strip()])
                    for skip_word in ['回复', '查看回复', '举报', '下载最新直播吧']:
                        content_text = re.sub(r'^' + skip_word + r'.*', '', content_text).strip()
                    content_text = content_text.strip()

                    if not content_text or len(content_text) < 2:
                        continue

                    comment = Zhibo8CommentModel(
                        match_url=url,
                        match_id=match_info.get('match_id', ''),
                        match_title=match_info.get('match_title', ''),
                        comment_id=f"{match_info.get('match_id', '')}_regex_{mi}",
                        author_name=author,
                        content_text=content_text,
                        like_count=like_count,
                        reply_count=reply_count,
                        publish_time=pub_time,
                    )
                    comments.append(comment)
                    self.logger.info(f"  📋 [正则-{mi}] @{author}: \"{content_text[:60]}\" 顶={like_count} 踩={reply_count}")
            else:
                for mi, m in enumerate(matches):
                    author = m[0].strip()
                    pub_time = m[1]
                    like_count = int(m[2])
                    reply_count = int(m[3])

                    comment = Zhibo8CommentModel(
                        match_url=url,
                        match_id=match_info.get('match_id', ''),
                        match_title=match_info.get('match_title', ''),
                        comment_id=f"{match_info.get('match_id', '')}_regex_{mi}",
                        author_name=author,
                        content_text='',
                        like_count=like_count,
                        reply_count=reply_count,
                        publish_time=pub_time,
                    )
                    comments.append(comment)

            self.logger.info(f"[Zhibo8正则] 总共提取 {len(comments)} 条评论")
            return comments

        except Exception as e:
            self.logger.error(f"[Zhibo8正则] 提取异常: {e}", exc_info=True)
            return []

    async def _screenshot_comment(self, page, comment_index: int, screenshot_path: str) -> bool:
        try:
            element_handle = await page.evaluate_handle(f"""
                () => {{
                    const pllist = document.getElementById('pllist');
                    const container = pllist || document.body;
                    const allTables = container.querySelectorAll('table');
                    const candidates = [];

                    for (let i = 0; i < allTables.length; i++) {{
                        const el = allTables[i];
                        const text = (el.innerText || '').trim();
                        if (!text || text.length < 8) continue;
                        if (!/顶\\s*\\(\\d+\\)/.test(text)) continue;

                        candidates.push({{el: el, index: candidates.length}});
                    }}

                    if (candidates.length > {comment_index}) return candidates[{comment_index}].el;
                    return null;
                }}
            """)

            js_element = element_handle.as_element() if element_handle else None
            if not js_element:
                self.logger.debug(f"    [Zhibo8截图] 评论[{comment_index}] 未找到元素")
                return False

            box = await js_element.bounding_box()
            if not box or box.get('height', 0) < 10:
                self.logger.debug(f"    [Zhibo8截图] 评论[{comment_index}] 尺寸异常: {box}")
                return False

            await js_element.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)

            try:
                await js_element.screenshot(path=screenshot_path)
                self.logger.info(f"    📸 [Zhibo8截图] 评论[{comment_index}] 截图成功 (element.screenshot, h={box['height']:.0f})")
                return True
            except Exception as elem_err:
                self.logger.debug(f"    [Zhibo8截图] element.screenshot失败: {elem_err}, 尝试clip")

            clip = {
                'x': max(0, int(box['x'])),
                'y': max(0, int(box['y'])),
                'width': int(box['width']),
                'height': min(int(box['height']), 800),
            }
            await page.screenshot(path=screenshot_path, clip=clip)
            self.logger.info(f"    📸 [Zhibo8截图] 评论[{comment_index}] 截图成功 (page.screenshot clip)")
            return True

        except Exception as e:
            self.logger.warning(f"    ❌ [Zhibo8截图] 评论[{comment_index}] 异常: {e}")
            return False

    async def execute(self, *args, **kwargs) -> Dict[str, Any]:
        """执行技能：爬取比赛评论"""
        url = kwargs.get('url') or (args[0] if args else None)
        if not url:
            raise ValueError("Zhibo8MatchScraper.execute() 需要提供 url 参数")
        return await self.scrape_match(url)
