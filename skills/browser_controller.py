import asyncio
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path
from core.base import BaseSkill
from core.exceptions import BrowserError

try:
    from playwright.async_api import async_playwright, Browser, Page, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

class BrowserController(BaseSkill):
    """
    浏览器控制器 Skill 3
    
    使用 Playwright 控制真实浏览器实例，用于：
    - 页面渲染和可视化截图
    - 动态内容加载处理
    - JavaScript执行环境模拟
    
    这是文章截图和评论截图的基础依赖！
    """
    
    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError("请安装 Playwright: pip install playwright && playwright install")
        
        browser_config = config.get('browser', {})
        self.headless = browser_config.get('headless', True)
        self.viewport_width = browser_config.get('viewport', {}).get('width', 1920)
        self.viewport_height = browser_config.get('viewport', {}).get('height', 1080)
        self.screenshot_quality = browser_config.get('screenshot_quality', 90)
        self.screenshot_type = browser_config.get('screenshot_type', 'png')
        self.user_agent = browser_config.get('user_agent', 
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        self.wait_timeout = browser_config.get('wait_timeout', 10000)  # 等待超时(ms)
        
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        super().__init__(config, logger)
    
    def _initialize(self):
        """初始化Playwright"""
        self.logger.info(f"[{self.name}] 浏览器控制器初始化中...")
        self.logger.info(f"[{self.name}] 无头模式: {self.headless}, 视口: {self.viewport_width}x{self.viewport_height}")
    
    async def start_browser(self):
        """启动浏览器实例"""
        try:
            self.playwright = await async_playwright().start()
            
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-dev-shm-usage'
                ]
            )
            
            self.context = await self.browser.new_context(
                viewport={'width': self.viewport_width, 'height': self.viewport_height},
                user_agent=self.user_agent,
                locale='zh-CN',
                timezone_id='Asia/Shanghai'
            )
            
            # 加载Cookie（如果配置了）
            cookie_str = self.config.get('browser', {}).get('cookie', '')
            if cookie_str:
                await self._load_cookies(cookie_str)
                self.logger.info(f"[{self.name}] 已加载Cookie配置")
            else:
                self.logger.warning(f"[{self.name}] 未配置Cookie，微博搜索页可能需要登录")
            
            # 反检测脚本
            await self.context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                
                window.chrome = {
                    runtime: {}
                };
                
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
            """)
            
            self.logger.info(f"[{self.name}] 浏览器启动成功")
            
        except Exception as e:
            self.logger.error(f"[{self.name}] 浏览器启动失败: {e}")
            raise BrowserError(f"无法启动浏览器: {e}")
    
    async def _load_cookies(self, cookie_str: str):
        """
        从Cookie字符串加载Cookie到浏览器上下文
        
        支持格式：
        1. 浏览器导出的Cookie字符串: "key1=value1; key2=value2; ..."
        2. JSON格式: '[{"name":"key1","value":"val1","domain":".weibo.com"}, ...]'
        """
        try:
            cookies = []
            
            # 尝试JSON格式
            if cookie_str.strip().startswith('['):
                import json
                cookies = json.loads(cookie_str)
            else:
                # 解析 "key1=value1; key2=value2" 格式
                for item in cookie_str.split(';'):
                    item = item.strip()
                    if '=' in item:
                        name, value = item.split('=', 1)
                        cookies.append({
                            'name': name.strip(),
                            'value': value.strip(),
                            'domain': '.weibo.com',
                            'path': '/',
                        })
            
            if cookies:
                await self.context.add_cookies(cookies)
                self.logger.info(f"[{self.name}] 已加载 {len(cookies)} 个Cookie")
                
        except Exception as e:
            self.logger.warning(f"[{self.name}] Cookie加载失败: {e}")
    
    async def new_page(self) -> Page:
        """创建新页面"""
        if not self.context:
            await self.start_browser()
        
        page = await self.context.new_page()
        
        # 设置默认超时
        page.set_default_timeout(self.wait_timeout)
        
        return page
    
    async def navigate_to(self, page: Page, url: str, wait_for: str = 'networkidle') -> bool:
        """
        导航到指定URL
        
        Args:
            page: 页面实例
            url: 目标URL
            wait_for: 等待类型 ('networkidle', 'domcontentload', 'load')
            
        Returns:
            bool: 是否成功导航
        """
        try:
            self.logger.debug(f"[{self.name}] 正在访问: {url[:50]}...")
            
            response = await page.goto(url, wait_until=wait_for, timeout=30000)
            
            if response and response.status == 200:
                # 额外等待确保JS渲染完成
                await asyncio.sleep(2)
                return True
            else:
                self.logger.warning(f"[{self.name}] 页面加载异常，状态码: {response.status if response else 'None'}")
                return False
                
        except Exception as e:
            self.logger.error(f"[{self.name}] 导航失败: {e}")
            return False
    
    async def wait_for_content_load(self, page: Page, selector: str = '.card-wrap', timeout: int = 15000) -> bool:
        """
        等待特定内容元素加载
        
        Args:
            page: 页面实例
            selector: CSS选择器
            timeout: 超时时间(ms)
            
        Returns:
            bool: 元素是否加载成功
        """
        try:
            await page.wait_for_selector(selector, timeout=timeout)
            self.logger.debug(f"[{self.name}] 内容元素已加载: {selector}")
            return True
        except Exception as e:
            self.logger.warning(f"[{self.name}] 等待内容超时: {selector}")
            return False
    
    async def scroll_to_load_more(self, page: Page, scroll_count: int = 3, delay: int = 1000):
        """
        滚动页面以加载更多内容
        
        Args:
            page: 页面实例
            scroll_count: 滚动次数
            delay: 每次滚动间隔(ms)
        """
        for i in range(scroll_count):
            await page.evaluate('window.scrollBy(0, window.innerHeight)')
            await asyncio.sleep(delay / 1000)
            self.logger.debug(f"[{self.name}] 滚动加载进度: {i+1}/{scroll_count}")
    
    async def take_screenshot(self, page: Page, save_path: str, 
                            full_page: bool = False,
                            clip: Optional[Dict] = None) -> bool:
        """
        截取页面截图
        
        Args:
            page: 页面实例
            save_path: 保存路径
            full_page: 是否截取完整页面
            clip: 截图区域 {'x': 0, 'y': 0, 'width': 1920, 'height': 1080}
            
        Returns:
            bool: 截图是否成功
        """
        try:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            
            screenshot_options = {
                'path': save_path,
                'type': self.screenshot_type,
            }
            
            if self.screenshot_type == 'jpeg':
                screenshot_options['quality'] = self.screenshot_quality
            
            if full_page:
                screenshot_options['full_page'] = True
            
            if clip:
                screenshot_options['clip'] = clip
            
            await page.screenshot(**screenshot_options)
            
            self.logger.info(f"[{self.name}] 截图保存成功: {save_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"[{self.name}] 截图失败: {e}")
            return False
    
    async def get_page_html(self, page: Page) -> str:
        """获取页面HTML内容"""
        return await page.content()
    
    async def execute_js(self, page: Page, script: str) -> Any:
        """执行JavaScript代码"""
        return await page.evaluate(script)
    
    async def close_page(self, page: Page):
        """关闭单个页面"""
        await page.close()
    
    async def close_browser(self):
        """关闭浏览器"""
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            self.logger.info(f"[{self.name}] 浏览器已关闭")
        except Exception as e:
            self.logger.warning(f"[{self.name}] 关闭浏览器时出错: {e}")
    
    def execute(self, *args, **kwargs):
        """
        同步入口（内部调用异步方法）
        
        注意：此方法主要用于兼容BaseSkill接口
        实际使用时应直接调用异步方法
        """
        self.logger.warning(f"[{self.name}] BrowserController主要使用异步方法，请使用await controller.start_browser()")
        return None
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.start_browser()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器退出"""
        await self.close_browser()
