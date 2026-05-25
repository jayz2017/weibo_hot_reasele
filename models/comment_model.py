from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime

@dataclass
class CommentModel:
    """评论数据模型"""
    article_url: str = ""             # 所属文章URL
    comment_id: str = ""              # 评论ID
    content_text: str = ""            # 评论文本内容
    author_name: str = ""             # 评论者名称
    author_id: str = ""               # 评论者ID
    author_avatar: str = ""           # 评论者头像URL
    like_count: int = 0               # 点赞数
    reply_to: str = ""                # 回复的评论ID（楼中楼）
    screenshot_path: str = ""         # 📸 截图路径
    images: List[str] = field(default_factory=list)  # 评论中的图片
    
    created_at: str = field(default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    
    def to_dict(self) -> dict:
        return {
            'article_url': self.article_url,
            'comment_id': self.comment_id,
            'content_text': self.content_text,
            'author_name': self.author_name,
            'author_id': self.author_id,
            'author_avatar': self.author_avatar,
            'like_count': self.like_count,
            'reply_to': self.reply_to,
            'screenshot_path': self.screenshot_path,
            'images': self.images,
            'created_at': self.created_at
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'CommentModel':
        return cls(**data)
