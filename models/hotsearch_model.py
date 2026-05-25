from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime

@dataclass
class HotSearchModel:
    """热榜数据模型"""
    icon_desc: str = ""           # 热词类型：新、热、新热等
    word: str = ""                # 热词内容
    num: int = 0                  # 热词出现次数（热度）
    icon: str = ""                # 热词图标URL
    small_icon_desc: str = ""     # 图标描述
    rank: int = 0                 # 排名
    note: str = ""                # 备注
    label_name: str = ""          # 标签名称
    topic_flag: int = 0           # 话题标志
    word_scheme: str = ""         # 话题方案（带#号）
    realpos: int = 0              # 实际位置
    
    def to_dict(self) -> dict:
        return {
            'icon_desc': self.icon_desc,
            'word': self.word,
            'num': self.num,
            'icon': self.icon,
            'small_icon_desc': self.small_icon_desc,
            'rank': self.rank,
            'note': self.note,
            'label_name': self.label_name,
            'topic_flag': self.topic_flag,
            'word_scheme': self.word_scheme,
            'realpos': self.realpos
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'HotSearchModel':
        return cls(
            icon_desc=data.get('icon_desc', ''),
            word=data.get('word', ''),
            num=data.get('num', 0),
            icon=data.get('icon', ''),
            small_icon_desc=data.get('small_icon_desc', ''),
            rank=data.get('rank', 0),
            note=data.get('note', ''),
            label_name=data.get('label_name', ''),
            topic_flag=data.get('topic_flag', 0),
            word_scheme=data.get('word_scheme', ''),
            realpos=data.get('realpos', 0)
        )
