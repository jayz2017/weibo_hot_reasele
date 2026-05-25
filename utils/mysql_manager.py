import pymysql
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from contextlib import contextmanager


class MySQLManager:
    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        self.logger = logger
        self.host = config.get('host', 'localhost')
        self.port = config.get('port', 3306)
        self.user = config.get('user', 'root')
        self.password = config.get('password', '')
        self.database = config.get('database', 'sport_zhiboba_text')
        self.charset = config.get('charset', 'utf8mb4')
        self._connection = None
        self._ensure_tables()

    @contextmanager
    def get_connection(self):
        conn = pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset=self.charset,
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor
        )
        try:
            yield conn
        except Exception as e:
            conn.rollback()
            self.logger.error(f"[MySQL] 事务回滚: {e}")
            raise
        finally:
            conn.close()

    def _ensure_tables(self):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS wb_article (
                        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                        url VARCHAR(500) DEFAULT '',
                        title VARCHAR(500) DEFAULT '',
                        author_name VARCHAR(200) DEFAULT '',
                        author_id VARCHAR(100) DEFAULT '',
                        content_text TEXT,
                        publish_time VARCHAR(50) DEFAULT '',
                        repost_count INT DEFAULT 0,
                        comment_count INT DEFAULT 0,
                        like_count INT DEFAULT 0,
                        keyword VARCHAR(200) DEFAULT '',
                        screenshot_path VARCHAR(500) DEFAULT '',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE KEY uk_url (url(255))
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS wb_comment (
                        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                        article_id BIGINT UNSIGNED DEFAULT 0,
                        article_url VARCHAR(500) DEFAULT '',
                        comment_id VARCHAR(200) DEFAULT '',
                        content_text TEXT,
                        author_name VARCHAR(200) DEFAULT '',
                        author_id VARCHAR(100) DEFAULT '',
                        like_count INT DEFAULT 0,
                        screenshot_path VARCHAR(500) DEFAULT '',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_article_id (article_id),
                        INDEX idx_article_url (article_url(255))
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                conn.commit()
                self.logger.info("[MySQL] 数据表初始化完成 (wb_article, wb_comment)")
        except Exception as e:
            self.logger.error(f"[MySQL] 建表失败: {e}")
            raise

    def save_article(self, article_data: Dict[str, Any]) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            url = article_data.get('url', '')
            existing = None
            if url:
                cursor.execute("SELECT id FROM wb_article WHERE url = %s", (url,))
                existing = cursor.fetchone()

            if existing:
                article_id = existing['id']
                cursor.execute("""
                    UPDATE wb_article SET
                        title=%s, author_name=%s, author_id=%s, content_text=%s,
                        publish_time=%s, repost_count=%s, comment_count=%s, like_count=%s,
                        keyword=%s, screenshot_path=%s
                    WHERE id=%s
                """, (
                    article_data.get('title', ''),
                    article_data.get('author_name', ''),
                    article_data.get('author_id', ''),
                    article_data.get('content_text', ''),
                    article_data.get('publish_time', ''),
                    article_data.get('repost_count', 0),
                    article_data.get('comment_count', 0),
                    article_data.get('like_count', 0),
                    article_data.get('keyword', ''),
                    article_data.get('screenshot_path', ''),
                    article_id
                ))
                self.logger.info(f"[MySQL] 文章已更新 id={article_id}")
            else:
                cursor.execute("""
                    INSERT INTO wb_article (url, title, author_name, author_id, content_text,
                        publish_time, repost_count, comment_count, like_count, keyword, screenshot_path)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    url,
                    article_data.get('title', ''),
                    article_data.get('author_name', ''),
                    article_data.get('author_id', ''),
                    article_data.get('content_text', ''),
                    article_data.get('publish_time', ''),
                    article_data.get('repost_count', 0),
                    article_data.get('comment_count', 0),
                    article_data.get('like_count', 0),
                    article_data.get('keyword', ''),
                    article_data.get('screenshot_path', '')
                ))
                article_id = cursor.lastrowid
                self.logger.info(f"[MySQL] 文章已插入 id={article_id}")

            conn.commit()
            return article_id

    def save_comment(self, comment_data: Dict[str, Any], article_id: int = 0) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO wb_comment (article_id, article_url, comment_id, content_text,
                    author_name, author_id, like_count, screenshot_path)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                article_id,
                comment_data.get('article_url', ''),
                comment_data.get('comment_id', ''),
                comment_data.get('content_text', ''),
                comment_data.get('author_name', ''),
                comment_data.get('author_id', ''),
                comment_data.get('like_count', 0),
                comment_data.get('screenshot_path', '')
            ))
            comment_id = cursor.lastrowid
            conn.commit()
            self.logger.info(f"[MySQL] 评论已插入 id={comment_id} article_id={article_id}")
            return comment_id

    def save_comments_batch(self, comments_data: List[Dict[str, Any]], article_id: int = 0) -> int:
        if not comments_data:
            return 0
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT content_text FROM wb_comment WHERE article_id = %s",
                (article_id,)
            )
            existing_texts = {row['content_text'] for row in cursor.fetchall()}

            rows = []
            skipped = 0
            for cd in comments_data:
                content = cd.get('content_text', '')
                if content and content in existing_texts:
                    skipped += 1
                    continue
                rows.append((
                    article_id,
                    cd.get('article_url', ''),
                    cd.get('comment_id', ''),
                    content,
                    cd.get('author_name', ''),
                    cd.get('author_id', ''),
                    cd.get('like_count', 0),
                    cd.get('screenshot_path', '')
                ))
                existing_texts.add(content)

            if not rows:
                self.logger.info(f"[MySQL] 评论全部重复，跳过 article_id={article_id} (skipped={skipped})")
                return 0

            cursor.executemany("""
                INSERT INTO wb_comment (article_id, article_url, comment_id, content_text,
                    author_name, author_id, like_count, screenshot_path)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, rows)
            conn.commit()
            count = cursor.rowcount
            self.logger.info(f"[MySQL] 批量插入评论 {count} 条 (跳过重复{skipped}条) article_id={article_id}")
            return count

    def get_article_by_url(self, url: str) -> Optional[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM wb_article WHERE url = %s", (url,))
            return cursor.fetchone()

    def get_comments_by_article_id(self, article_id: int) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM wb_comment WHERE article_id = %s ORDER BY id", (article_id,))
            return cursor.fetchall()

    def close(self):
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None
