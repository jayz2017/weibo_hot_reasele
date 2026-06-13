import unittest
import sys
import types

from models.article_model import ArticleModel
from models.comment_model import CommentModel
from utils.weibo_url_utils import (
    extract_weibo_status_key,
    normalize_weibo_detail_url,
    same_weibo_detail_url,
)


class FakeMySQL:
    def __init__(self):
        self.saved_comments = []

    def save_comments_batch_return_rows(self, comments_data, article_id):
        self.saved_comments.append((article_id, comments_data))
        return [
            {
                "id": index + 10,
                "article_id": article_id,
                "article_url": item["article_url"],
                "content_text": item["content_text"],
                "author_name": item["author_name"],
            }
            for index, item in enumerate(comments_data)
        ]


class FakeLogger:
    def info(self, *args, **kwargs):
        pass


class CommentSemanticLinkTest(unittest.TestCase):
    def test_weibo_detail_urls_normalize_to_same_status_key(self):
        self.assertEqual(
            normalize_weibo_detail_url("https://app.weibo.com/t/feed/R3JVkln8g?refer_flag=1001030103_"),
            "https://weibo.com/detail/R3JVkln8g",
        )
        self.assertEqual(extract_weibo_status_key("https://weibo.com/5275824148/R3JVkln8g?foo=1"), "R3JVkln8g")
        self.assertTrue(
            same_weibo_detail_url(
                "https://weibo.com/5275824148/R3JVkln8g?refer_flag=1001030103_",
                "https://weibo.com/detail/R3JVkln8g#comment",
            )
        )

    def test_pipeline_saves_comments_matched_by_normalized_weibo_url(self):
        if "jieba" not in sys.modules:
            jieba_stub = types.ModuleType("jieba")
            analyse_stub = types.ModuleType("jieba.analyse")
            jieba_stub.lcut = lambda text: str(text).split()
            analyse_stub.extract_tags = lambda text, topK=20: []
            jieba_stub.analyse = analyse_stub
            sys.modules["jieba"] = jieba_stub
            sys.modules["jieba.analyse"] = analyse_stub

        from core.pipeline import PipelineManager

        pipeline = object.__new__(PipelineManager)
        pipeline.mysql = FakeMySQL()
        pipeline.logger = FakeLogger()

        article = ArticleModel(url="https://app.weibo.com/t/feed/R3JVkln8g?refer_flag=1001030103_")
        comments = [
            CommentModel(
                article_url="https://weibo.com/5275824148/R3JVkln8g?foo=bar",
                content_text="支持这个观点",
                author_name="alice",
            ),
            CommentModel(
                article_url="https://weibo.com/5275824148/OTHER",
                content_text="不属于这篇",
                author_name="bob",
            ),
        ]

        rows = PipelineManager._save_article_comments(pipeline, article, 81, comments)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], 10)
        self.assertEqual(rows[0]["article_id"], 81)
        self.assertEqual(rows[0]["content_text"], "支持这个观点")
        self.assertEqual(len(pipeline.mysql.saved_comments[0][1]), 1)


if __name__ == "__main__":
    unittest.main()
