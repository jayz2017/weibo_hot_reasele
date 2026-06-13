import asyncio
import logging
from pathlib import Path
from typing import List, Any
from core.base import BaseSkill
from models.comment_model import CommentModel
from utils.file_utils import generate_filename
from utils.weibo_url_utils import normalize_weibo_detail_url


class CommentProcessor(BaseSkill):
    """
    评论处理 Skill
    
    功能：
    1. 展开所有评论
    2. vue-recycle-scroller 过滤诊断与筛选
    3. 逐条提取评论文字 + 截图
    4. 构建 CommentModel 列表
    """

    def __init__(self, config: dict, logger: logging.Logger, *, image_handler=None):
        self.image_handler = image_handler
        super().__init__(config, logger)

    def _initialize(self):
        self.logger.info(f"[{self.name}] 评论处理器初始化完成")

    async def execute(self, page, keyword: str, article_index: int, safe_kw: str = None) -> List[CommentModel]:
        """
        在微博详情页截取评论
        
        策略：
        1. 使用 vue-recycle-scroller__item-view 定位所有元素
        2. 通过子元素 .wbpro-scroller-item 的 data-index 属性过滤
        3. 只截取 data-index > 0 的元素（data-index=0 是文章内容）
        4. 每条评论单独截图
        
        Args:
            page: Playwright Page 对象
            keyword: 关键词
            article_index: 文章索引
            safe_kw: 安全关键词（用于文件名）
            
        Returns:
            List[CommentModel]: 评论模型列表
        """
        if safe_kw is None:
            safe_kw = keyword
            
        comment_models = []

        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.7)")
            await asyncio.sleep(1)

            await self.image_handler._hide_navigation_bar(page)

            await self._expand_all_comments(page)

            for scroll_i in range(4):
                await page.evaluate("window.scrollBy(0, 800)")
                await asyncio.sleep(1)

            try:
                await page.wait_for_function("""
                    () => {
                        const items = document.querySelectorAll('.vue-recycle-scroller__item-view .wbpro-scroller-item[data-index]');
                        let count = 0;
                        items.forEach(item => { if (parseInt(item.getAttribute('data-index')) > 0) count++; });
                        return count > 2;
                    }
                """, timeout=8000)
            except Exception:
                self.logger.debug(f"      等待评论元素超时，继续尝试处理")

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
                        
                        d.pass_dataIndex = d.dataIndex > 0;
                        
                        const text = (el.innerText || '');
                        d.pass_recommend = !text.includes('推荐') && !text.includes('荐读');
                        
                        d.finalPass = d.pass_dataIndex && d.pass_recommend;
                        
                        details.push(d);
                    }
                    
                    return { totalVueItems: allItems.length, details: details };
                }
            """)
            
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

            comment_data_indices = []
            for d in filter_debug.get('details', []):
                if d.get('finalPass'):
                    comment_data_indices.append(d.get('dataIndex'))

            if not comment_data_indices:
                self.logger.warning(f"      未找到 data-index>0 的评论元素，尝试Fallback")
                await self._save_comment_area_fallback(page, keyword, article_index, safe_kw)
                return comment_models

            self.logger.info(f"      找到 {len(comment_data_indices)} 条评论（data-index>0）")

            max_comments = self.config.get('comment', {}).get('max_comments', 10)
            data_indices_to_process = comment_data_indices[:max_comments]

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

                            const userLink = target.querySelector('a[href*="weibo.com/u/"], a[href*="weibo.com/n/"], a[class*="name"], a[class*="user"], .wbpro-scroller-item a[href*="/u/"]');
                            data.author_name = userLink ? userLink.innerText.trim() : '';

                            const contentEl = target.querySelector('.WB_text, .txt, [class*="text"], [class*="content"]');
                            if (contentEl) {{
                                var text = contentEl.innerText.trim();
                                var colonIdx = text.indexOf(':');
                                if (colonIdx >= 0 && colonIdx < 30) {{
                                    if (!data.author_name) {{
                                        data.author_name = text.substring(0, colonIdx).trim();
                                    }}
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
                        self.image_handler.screenshot_dir / 'comments' / comment_screenshot_name
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
                                screenshot_saved = await self.image_handler.screenshot_clip(page, clip, comment_screenshot_path)
                                if screenshot_saved:
                                    self.logger.info(f"      📸 评论[{seq}] 截图已保存 data-index={data_index} clip=({clip['x']:.0f},{clip['y']:.0f},{clip['width']:.0f},{clip['height']:.0f})")

                    if not screenshot_saved:
                        self.logger.warning(f"      ⚠ 评论[{seq}] 截图失败，跳过 data-index={data_index}")
                        continue

                    comment = CommentModel(
                        article_url=normalize_weibo_detail_url(page.url),
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

        finally:
            await self.image_handler._restore_navigation_bar(page)

        return comment_models

    async def _expand_all_comments(self, page):
        """展开评论区所有折叠的评论"""
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

    async def _save_comment_area_fallback(self, page, keyword: str, article_index: int, safe_kw: str):
        """Fallback方案：对整个评论区进行区域截图"""
        comment_screenshot_name = generate_filename(safe_kw, f'comment_area_{article_index}', '.png')
        comment_screenshot_path = str(
            self.image_handler.screenshot_dir / 'comments' / comment_screenshot_name
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
            await self.image_handler.screenshot_clip(page, fallback_box, comment_screenshot_path)
        else:
            await self.image_handler.take_fullpage_fallback(page, comment_screenshot_path)

        self.logger.info(f"      📸 评论区整体截图(Fallback)已保存")
