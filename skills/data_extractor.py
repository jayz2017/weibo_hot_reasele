import re
import logging
from typing import Dict, Any, List, Optional
from core.base import BaseSkill
from models.article_model import ArticleModel
from models.comment_model import CommentModel

class DataExtractor(BaseSkill):
    """
    数据提取器 Skill 6
    
    功能：
    1. 从HTML/文本中提取结构化数据
    2. 内容清洗（去除广告、特殊字符）
    3. 关键词提取和标签化
    4. 数据格式标准化
    """
    
    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        extract_config = config.get('extract', {})
        self.max_text_length = extract_config.get('max_text_length', 5000)
        self.remove_patterns = [
            r'http[s]?://\S+',           # URL链接
            r'#[^#]+#',                   # 话题标签（可选保留）
            r'@\w+',                      # @用户
            r'\s{2,}',                    # 多余空白
        ]
        super().__init__(config, logger)
    
    def _initialize(self):
        self.logger.info(f"[{self.name}] 数据提取器初始化完成")
    
    def execute(self, raw_data: Any) -> Any:
        """执行数据提取"""
        if isinstance(raw_data, list):
            return [self._process_item(item) for item in raw_data]
        return self._process_item(raw_data)
    
    def clean_text(self, text: str) -> str:
        """
        清洗文本内容
        
        去除：URL、多余空格、特殊字符等
        """
        if not text:
            return ""
        
        cleaned = text
        
        for pattern in self.remove_patterns:
            cleaned = re.sub(pattern, '', cleaned)
        
        # 清理其他杂项
        cleaned = cleaned.strip()
        cleaned = re.sub(r'\n+', '\n', cleaned)
        cleaned = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9\s，。！？、；：""''（）【】《》\n]', '', cleaned)
        
        if len(cleaned) > self.max_text_length:
            cleaned = cleaned[:self.max_text_length] + "..."
        
        return cleaned
    
    def extract_keywords(self, text: str, top_n: int = 5) -> List[str]:
        """
        简单的关键词提取（基于词频）
        
        实际项目中可替换为jieba分词等高级方案
        """
        import jieba.analyse
        try:
            keywords = jieba.analyse.extract_tags(text, topK=top_n)
            return keywords
        except ImportError:
            words = re.findall(r'[\u4e00-\u9fa5]{2,}', text)
            from collections import Counter
            word_counts = Counter(words)
            return [word for word, _ in word_counts.most_common(top_n)]
    
    def format_article_for_export(self, article: ArticleModel) -> Dict[str, Any]:
        """格式化文章数据用于导出"""
        return {
            '标题': article.title[:100],
            '作者': article.author_name,
            '发布时间': article.publish_time,
            '关键词': article.keyword,
            '内容摘要': article.content_text[:200] + '...' if len(article.content_text) > 200 else article.content_text,
            '转发数': article.repost_count,
            '评论数': article.comment_count,
            '点赞数': article.like_count,
            '截图路径': article.screenshot_path,
            '采集时间': article.created_at
        }
    
    def format_comment_for_export(self, comment: CommentModel) -> Dict[str, Any]:
        """格式化评论数据用于导出"""
        return {
            '评论者': comment.author_name,
            '评论内容': comment.content_text[:100] + '...' if len(comment.content_text) > 100 else comment.content_text,
            '点赞数': comment.like_count,
            '截图路径': comment.screenshot_path,
            '采集时间': comment.created_at
        }
    
    def _process_item(self, item: Any) -> Any:
        """处理单个数据项"""
        if isinstance(item, ArticleModel):
            item.content_text = self.clean_text(item.content_text)
            return item
        elif isinstance(item, CommentModel):
            item.content_text = self.clean_text(item.content_text)
            return item
        elif isinstance(item, str):
            return self.clean_text(item)
        return item
