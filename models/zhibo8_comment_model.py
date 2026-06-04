from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime


@dataclass
class Zhibo8CommentModel:
    match_url: str = ""
    match_id: str = ""
    match_title: str = ""
    comment_id: str = ""
    author_name: str = ""
    content_text: str = ""
    like_count: int = 0
    reply_count: int = 0
    publish_time: str = ""
    screenshot_path: str = ""

    created_at: str = field(default_factory=lambda: datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

    def to_dict(self) -> dict:
        return {
            'match_url': self.match_url,
            'match_id': self.match_id,
            'match_title': self.match_title,
            'comment_id': self.comment_id,
            'author_name': self.author_name,
            'content_text': self.content_text,
            'like_count': self.like_count,
            'reply_count': self.reply_count,
            'publish_time': self.publish_time,
            'screenshot_path': self.screenshot_path,
            'created_at': self.created_at
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Zhibo8CommentModel':
        return cls(**data)
