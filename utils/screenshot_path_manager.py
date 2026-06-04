"""
统一的截图路径管理器

所有截图的保存路径都从此处获取，避免硬编码分散在各个 Skill 中。
通过修改 config.yaml 中的 screenshot 节点配置即可改变截图保存位置。
"""
import logging
from pathlib import Path
from typing import Dict, Any, Optional


class ScreenshotPathManager:
    """统一管理所有截图的保存路径"""

    DEFAULT_CONFIG = {
        'base_dir': './data/screenshots',
        'articles_subdir': 'articles',
        'comments_subdir': 'comments',
        'zhibo8_subdir': 'zhibo8_comments',
        'format': 'png',
        'quality': 90,
        'delay_before_screenshot': 2000,
    }

    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        self.logger = logger
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        self._ensure_dirs()

    def _resolve_base_dir(self) -> Path:
        base = self.config.get('base_dir') or self.config.get('screenshot_dir') or './data/screenshots'
        # 兼容旧配置 keys
        if not self.config.get('base_dir') and self.config.get('screenshot_dir'):
            base = self.config['screenshot_dir']
        return Path(base).resolve()

    def _ensure_dirs(self):
        base = self._resolve_base_dir()
        for sub in ['articles_subdir', 'comments_subdir', 'zhibo8_subdir']:
            d = base / self.config[sub]
            d.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"[ScreenshotPathManager] 截图根目录: {base}")

    @property
    def base_dir(self) -> Path:
        return self._resolve_base_dir()

    @property
    def articles_dir(self) -> Path:
        return self.base_dir / self.config['articles_subdir']

    @property
    def comments_dir(self) -> Path:
        return self.base_dir / self.config['comments_subdir']

    @property
    def zhibo8_dir(self) -> Path:
        return self.base_dir / self.config['zhibo8_subdir']

    @property
    def image_format(self) -> str:
        return self.config.get('format', 'png')

    @property
    def quality(self) -> int:
        return int(self.config.get('quality', 90))

    @property
    def delay_before_screenshot(self) -> int:
        return int(self.config.get('delay_before_screenshot', 2000))

    def get_path(self, category: str, filename: str) -> str:
        """
        根据类别获取截图完整路径
        :param category: articles / comments / zhibo8 / 自定义子目录
        :param filename: 文件名（不含扩展名会自动添加）
        :return: 完整的截图文件路径
        """
        category_dirs = {
            'articles': self.articles_dir,
            'comments': self.comments_dir,
            'zhibo8': self.zhibo8_dir,
        }
        target_dir = category_dirs.get(category, self.base_dir / category)
        target_dir.mkdir(parents=True, exist_ok=True)

        if not filename.lower().endswith(f".{self.image_format}"):
            filename = f"{filename}.{self.image_format}"
        return str(target_dir / filename)

    def get_dir(self, category: str) -> str:
        """获取类别目录路径"""
        return str(self.get_path(category, '').rsplit(sep := ('\\' if '\\' in str(self.base_dir) else '/'), 1)[0])
