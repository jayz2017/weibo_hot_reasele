import asyncio
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path
from core.base import BaseSkill
from core.exceptions import ScreenshotError
from models.comment_model import CommentModel
from skills.browser_controller import BrowserController
from utils.file_utils import clean_filename, generate_filename

class CommentScreenshot(BaseSkill):
    """
    评论截图处理器 Skill 5 ⭐核心功能
    
    功能：
    1. 在文章页面滚动到评论区
    2. 加载评论列表（处理动态加载）
    3. 对每条评论（或评论组）进行截图：
       - 评论者名称和头像
       - 评论文本内容
       - 评论中的图片
       - 点赞数和时间
       - 楼中楼回复关系
    4. 提取评论文本用于结构化存储
    """
    
    def __init__(self, config: Dict[str, Any], logger: logging.Logger,
                 browser_controller: Optional[BrowserController] = None):
        self.browser = browser_controller
        storage_config = config.get('storage', {})
        self.screenshot_dir = Path(storage_config.get('base_dir', './data')) / 'screenshots' / 'comments'
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        
        comment_config = config.get('comment', {})
        self.max_comments = comment_config.get('max_comments', 50)
        self.load_more_times = comment_config.get('load_more_times', 5)
        super().__init__(config, logger)
    
    def _initialize(self):
        self.logger.info(f"[{self.name}] 评论截图处理器初始化完成")
        self.logger.info(f"[{self.name}] 最大评论数: {self.max_comments}, 加载次数: {self.load_more_times}")
    
    async def process_comments(self, article_url: str, article_id: str = "") -> List[CommentModel]:
        """
        处理文章的所有评论
        
        Args:
            article_url: 文章URL
            article_id: 文章ID（可选）
            
        Returns:
            List[CommentModel]: 评论数据列表（含截图路径）
        """
        if not self.browser:
            raise ScreenshotError("浏览器控制器未初始化")
        
        self.logger.info(f"[{self.name}] 开始处理评论区: {article_url[:50]}...")
        
        page = None
        comments = []
        
        try:
            page = await self.browser.new_page()
            
            # 导航到文章页
            success = await self.browser.navigate_to(page, article_url)
            if not success:
                raise ScreenshotError(f"无法访问文章页面")
            
            # 等待页面加载
            await asyncio.sleep(3)
            
            # 滚动到评论区并加载更多评论
            await self._scroll_to_comments(page)
            
            # 获取所有评论元素
            comment_elements = await self._get_comment_elements(page)
            
            self.logger.info(f"[{self.name}] 找到 {len(comment_elements)} 条评论")
            
            # 逐条处理评论
            for idx, element in enumerate(comment_elements[:self.max_comments]):
                try:
                    comment = await self._process_single_comment(
                        page, element, idx, article_url, article_id
                    )
                    if comment:
                        comments.append(comment)
                        
                except Exception as e:
                    self.logger.warning(f"[{self.name}] 第{idx+1}条评论处理失败: {e}")
                    continue
            
            self.logger.info(f"[{self.name}] 评论处理完成，成功: {len(comments)} 条")
            self.on_success(comments)
            return comments
            
        except Exception as e:
            self.logger.error(f"[{self.name}] 评论处理失败: {e}")
            raise ScreenshotError(f"评论截图失败: {e}")
        finally:
            if page:
                await self.browser.close_page(page)
    
    async def _scroll_to_comments(self, page):
        """滚动到评论区并加载更多"""
        self.logger.debug(f"[{self.name}] 正在滚动到评论区...")
        
        # 尝试点击"评论"按钮进入评论区
        comment_btn_selectors = [
            '.wbpro-feed-action .item:nth-child(2)',
            '[action-type="fl_comment"]',
            '.comment_btn',
            'text=评论'
        ]
        
        for selector in comment_btn_selectors:
            try:
                btn = await page.query_selector(selector)
                if btn:
                    await btn.click()
                    await asyncio.sleep(2)
                    self.logger.debug(f"[{self.name}] 已点击评论按钮")
                    break
            except:
                continue
        
        # 滚动页面到底部以触发评论加载
        for i in range(self.load_more_times):
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await asyncio.sleep(1.5)
            
            # 尝试点击"加载更多"
            load_more_selectors = [
                '.click_load_comment',
                'text=加载更多',
                '.more_txt'
            ]
            
            for sel in load_more_selectors:
                try:
                    load_btn = await page.query_selector(sel)
                    if load_btn and await load_btn.is_visible():
                        await load_btn.click()
                        await asyncio.sleep(1)
                        break
                except:
                    continue
            
            self.logger.debug(f"[{self.name}] 加载评论进度: {i+1}/{self.load_more_times}")
    
    async def _get_comment_elements(self, page) -> list:
        """获取页面中的所有评论DOM元素"""
        selectors = [
            '.list_li .comment_item',
            '.wbpro-comment-item',
            '[node-type="comment_list"] .item',
            '.comment-list .comment-item'
        ]
        
        for selector in selectors:
            elements = await page.query_selector_all(selector)
            if elements and len(elements) > 0:
                self.logger.debug(f"[{self.name}] 使用选择器 '{selector}' 找到 {len(elements)} 条评论")
                return elements
        
        self.logger.warning(f"[{self.name}] 未找到评论元素")
        return []
    
    async def _process_single_comment(self, page, element, index: int, 
                                    article_url: str, article_id: str) -> Optional[CommentModel]:
        """
        处理单条评论：提取数据 + 截图
        
        返回包含截图路径的CommentModel
        """
        try:
            # 提取评论文本数据
            comment_data = await element.evaluate("""
                (el) => {
                    const data = {};
                    
                    // 评论者信息
                    const userEl = el.querySelector('.W_f14, .comment_user_name, .name');
                    data.author_name = userEl ? userEl.innerText.trim() : '';
                    
                    // 评论文本
                    const textEl = el.querySelector('.comment_text, .WB_text, .txt');
                    data.content_text = textEl ? textEl.innerText.trim() : '';
                    
                    // 点赞数
                    const likeEl = el.querySelector('.count, .like_count, [action-type="like"] span');
                    data.like_count = likeEl ? parseInt(likeEl.innerText) || 0 : 0;
                    
                    // 时间
                    const timeEl = el.querySelector('.time, .func_time');
                    data.time = timeEl ? timeEl.getAttribute('title') || timeEl.innerText.trim() : '';
                    
                    return data;
                }
            """)
            
            # 生成截图文件名
            screenshot_name = generate_filename(
                f'comment_{index:03d}', '', '.png'
            )
            screenshot_path = str(self.screenshot_dir / screenshot_name)
            
            # 对单条评论进行截图
            await self._capture_comment_screenshot(element, screenshot_path)
            
            # 构建CommentModel
            comment = CommentModel(
                article_url=article_url,
                comment_id=f"comment_{index}",
                content_text=comment_data.get('content_text', ''),
                author_name=comment_data.get('author_name', ''),
                like_count=comment_data.get('like_count', 0),
                screenshot_path=screenshot_path
            )
            
            self.logger.debug(f"[{self.name}] 第{index+1}条评论处理完成: {comment.author_name[:10]}...")
            return comment
            
        except Exception as e:
            self.logger.error(f"[{self.name}] 单条评论处理异常: {e}")
            return None
    
    async def _capture_comment_screenshot(self, element, save_path: str):
        """
        截取单条评论的可视化截图
        
        包含：
        - 评论者头像和昵称
        - 评论文本内容
        - 评论配图（如有）
        - 点赞和回复按钮
        """
        try:
            # 获取评论元素的边界框
            box = await element.bounding_box()
            
            if box:
                clip = {
                    'x': box['x'],
                    'y': box['y'],
                    'width': min(box['width'], 800),
                    'height': min(box['height'], 600)
                }
                
                # 使用page.screenshot对特定区域截图
                page = element.execution_context().frame.page
                await page.screenshot(
                    path=save_path,
                    clip=clip,
                    type='png'
                )
            else:
                # 如果无法获取边界框，尝试用element.screenshot
                await element.screenshot(path=save_path)
                
            self.logger.debug(f"[{self.name}] 评论截图已保存: {save_path}")
            
        except Exception as e:
            self.logger.warning(f"[{self.name}] 单条评论截图失败: {e}")
            # 尝试备用方案：截取整个评论区作为备选
    
    async def capture_full_comment_section(self, page, save_path: str):
        """
        截取完整评论区截图（长图模式）
        
        用于需要展示整页评论的场景
        """
        try:
            # 定位评论区容器
            section_selectors = [
                '.WB_comments',
                '#comment_list',
                '.comment-section',
                '[node-type="comment_wrap"]'
            ]
            
            for selector in section_selectors:
                section = await page.query_selector(selector)
                if section:
                    box = await section.bounding_box()
                    if box:
                        clip = {
                            'x': 20,
                            'y': box['y'],
                            'width': self.browser.viewport_width - 40,
                            'height': min(box['height'] + 50, 10000)
                        }
                        
                        await page.screenshot(
                            path=save_path,
                            clip=clip,
                            type='png'
                        )
                        
                        self.logger.info(f"[{self.name}] 完整评论区截图已保存: {save_path}")
                        return True
            
            # 如果没找到评论区容器，截取下半部分页面
            viewport = page.viewport_size
            clip = {
                'x': 0,
                'y': int(viewport['height'] * 0.6),
                'width': viewport['width'],
                'height': int(viewport['height'] * 1.5)
            }
            
            await page.screenshot(path=save_path, clip=clip, type='png')
            return True
            
        except Exception as e:
            self.logger.error(f"[{self.name}] 评论区截图失败: {e}")
            return False
    
    def execute(self, *args, **kwargs):
        """同步接口"""
        self.logger.warning(f"[{self.name}] 请使用异步方法 process_comments()")
