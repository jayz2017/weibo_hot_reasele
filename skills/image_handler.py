import logging
from pathlib import Path
from typing import Any, Optional
from core.base import BaseSkill
from utils.file_utils import clean_filename, generate_filename, ensure_dir


class ImageHandler(BaseSkill):
    """
    图片处理 Skill
    
    功能：
    1. 导航栏隐藏/恢复（用于截图时清理页面）
    2. 元素级截图 (screenshot_element)
    3. 区域截图 (screenshot_clip)
    4. 全页截图 fallback (take_fullpage_fallback)
    5. 截图路径生成 (generate_screenshot_path)
    """

    def __init__(self, config: dict, logger: logging.Logger):
        paths_config = config.get('paths', {})
        self.screenshot_dir = Path(paths_config.get('screenshot_dir', './screenshots'))
        ensure_dir(str(self.screenshot_dir))
        super().__init__(config, logger)

    def _initialize(self):
        self.logger.info(f"[{self.name}] 图片处理器初始化完成")
        self.logger.info(f"[{self.name}] 截图目录: {self.screenshot_dir}")

    async def _hide_navigation_bar(self, page):
        await page.evaluate("""
            () => {
                const navSelectors = [
                    '.s-top', '.S_top', '.s-topbar', '.topbar',
                    '.gn_header', '.gnb', '.S_bg2',
                    '[class*="topbar"]',
                    '[class*="Topbar"]',
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
                            const style = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            const isTopChrome = (
                                style.position === 'fixed' ||
                                style.position === 'sticky' ||
                                rect.top < 160
                            );
                            if (!isTopChrome) return;
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

    async def screenshot_element(self, page, element, path: str) -> bool:
        """对指定元素进行截图
        
        Args:
            page: Playwright Page 对象
            element: 要截图的元素
            path: 截图保存路径
            
        Returns:
            bool: 是否成功
        """
        try:
            await element.scroll_into_view_if_needed()
            await element.screenshot(path=path)
            self.logger.debug(f"[{self.name}] 元素截图成功: {path}")
            return True
        except Exception as e:
            self.logger.error(f"[{self.name}] 元素截图失败: {e}")
            return False

    async def screenshot_clip(self, page, clip_dict: dict, path: str) -> bool:
        """按区域截图
        
        Args:
            page: Playwright Page 对象
            clip_dict: 裁剪区域字典 {'x': int, 'y': int, 'width': int, 'height': int}
            path: 截图保存路径
            
        Returns:
            bool: 是否成功
        """
        try:
            height = clip_dict.get('height', 0)
            if not (10 < height < 800):
                self.logger.warning(f"[{self.name}] 高度校验失败: {height} (要求 10 < height < 800)")
                return False

            await page.screenshot(path=path, clip=clip_dict)
            self.logger.debug(f"[{self.name}] 区域截图成功: {path}")
            return True
        except Exception as e:
            self.logger.error(f"[{self.name}] 区域截图失败: {e}")
            return False

    def generate_screenshot_path(self, keyword: str, type_: str, index: int, ext: str = '.png') -> str:
        """生成截图保存路径
        
        Args:
            keyword: 关键词
            type_: 类型 (如 'article', 'comment')
            index: 索引编号
            ext: 文件扩展名
            
        Returns:
            str: 完整的截图保存路径
        """
        safe_keyword = clean_filename(keyword)[:50]
        type_dir = self.screenshot_dir / safe_keyword / type_
        ensure_dir(str(type_dir))

        filename = generate_filename(prefix=f"{type_}_{index}", ext=ext)
        filepath = type_dir / filename

        return str(filepath)

    async def take_fullpage_fallback(self, page, path: str) -> bool:
        """全页截图 fallback 方案
        
        Args:
            page: Playwright Page 对象
            path: 截图保存路径
            
        Returns:
            bool: 是否成功
        """
        try:
            await page.screenshot(path=path, full_page=True)
            self.logger.debug(f"[{self.name}] 全页截图成功: {path}")
            return True
        except Exception as e:
            self.logger.error(f"[{self.name}] 全页截图失败: {e}")
            return False

    def execute(self, *args, **kwargs) -> Any:
        """执行技能，返回自身实例"""
        self.logger.info(f"[{self.name}] ImageHandler 就绪")
        return self
