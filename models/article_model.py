from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime

@dataclass
class ArticleModel:
    """文章数据模型"""
    url: str = ""                     # 文章URL
    title: str = ""                   # 文章标题/内容摘要
    author_name: str = ""             # 作者名称
    author_id: str = ""               # 作者ID
    author_avatar: str = ""           # 作者头像URL
    content_text: str = ""            # 正文文本内容
    publish_time: str = ""            # 发布时间
    repost_count: int = 0             # 转发数
    comment_count: int = 0            # 评论数
    like_count: int = 0               # 点赞数
    keyword: str = ""                 # 关联的热搜关键词
    screenshot_path: str = ""         # 📸 浏览器渲染截图路径
    images: List[str] = field(default_factory=list)  # 文章配图列表
    
    created_at: str = field(default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    
    def to_dict(self) -> dict:
        return {
            'url': self.url,
            'title': self.title,
            'author_name': self.author_name,
            'author_id': self.author_id,
            'author_avatar': self.author_avatar,
            'content_text': self.content_text,
            'publish_time': self.publish_time,
            'repost_count': self.repost_count,
            'comment_count': self.comment_count,
            'like_count': self.like_count,
            'keyword': self.keyword,
            'screenshot_path': self.screenshot_path,
            'images': self.images,
            'created_at': self.created_at
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'ArticleModel':
        return cls(**data)
