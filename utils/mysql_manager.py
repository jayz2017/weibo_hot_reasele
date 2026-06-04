import pymysql
import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime
from contextlib import contextmanager
from dbutils.pooled_db import PooledDB


class MySQLManager:
    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        self.logger = logger
        self.host = config.get('host', 'localhost')
        self.port = config.get('port', 3306)
        self.user = config.get('user', 'root')
        self.password = config.get('password', '')
        self.database = config.get('database', 'sport_zhiboba_text')
        self.charset = config.get('charset', 'utf8mb4')
        self._pool = None
        self._init_pool()
        self._ensure_tables()

    def _init_pool(self):
        self._pool = PooledDB(
            creator=pymysql,
            mincached=2,
            maxcached=5,
            maxconnections=10,
            blocking=True,
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset=self.charset,
            cursorclass=pymysql.cursors.DictCursor
        )
        self.logger.info("[MySQL] 连接池初始化完成 (min=2, max=5, max_conn=10)")

    @contextmanager
    def get_connection(self):
        conn = self._pool.connection()
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
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS zb8_match_comment (
                        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                        match_url VARCHAR(500) DEFAULT '',
                        match_id VARCHAR(50) DEFAULT '',
                        match_title VARCHAR(500) DEFAULT '',
                        comment_id VARCHAR(200) DEFAULT '',
                        author_name VARCHAR(200) DEFAULT '',
                        content_text TEXT,
                        like_count INT DEFAULT 0,
                        reply_count INT DEFAULT 0,
                        publish_time VARCHAR(50) DEFAULT '',
                        screenshot_path VARCHAR(500) DEFAULT '',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE KEY uk_match_comment (match_url(255), comment_id(100)),
                        INDEX idx_match_id (match_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS wb_semantic_analysis (
                        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                        source_type VARCHAR(20) NOT NULL DEFAULT 'article',
                        source_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
                        article_id BIGINT UNSIGNED NOT NULL DEFAULT 0,
                        article_url VARCHAR(500) NOT NULL DEFAULT '',
                        keyword VARCHAR(200) NOT NULL DEFAULT '',
                        author_name VARCHAR(200) NOT NULL DEFAULT '',
                        text_hash CHAR(64) NOT NULL DEFAULT '',
                        text_length INT NOT NULL DEFAULT 0,
                        summary VARCHAR(500) NOT NULL DEFAULT '',
                        sentiment_label VARCHAR(20) NOT NULL DEFAULT '',
                        sentiment_score DECIMAL(6,4) NOT NULL DEFAULT 0,
                        primary_emotion VARCHAR(30) NOT NULL DEFAULT '',
                        stance_label VARCHAR(30) NOT NULL DEFAULT '',
                        stance_polarity DECIMAL(6,4) NOT NULL DEFAULT 0,
                        conflict_score DECIMAL(6,4) NOT NULL DEFAULT 0,
                        value_labels JSON NULL,
                        concept_labels JSON NULL,
                        keywords JSON NULL,
                        viewpoints JSON NULL,
                        emotions JSON NULL,
                        analysis_json JSON NULL,
                        analysis_version VARCHAR(50) NOT NULL DEFAULT '',
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY uk_source_analysis (source_type, source_id, analysis_version),
                        INDEX idx_article_type (article_id, source_type),
                        INDEX idx_keyword_conflict (keyword, conflict_score),
                        INDEX idx_sentiment (sentiment_label, sentiment_score),
                        INDEX idx_stance (stance_label, stance_polarity),
                        INDEX idx_emotion (primary_emotion)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                self._ensure_column(
                    cursor,
                    "wb_semantic_analysis",
                    "concept_labels",
                    "JSON NULL AFTER value_labels",
                )
                conn.commit()
                self.logger.info("[MySQL] 数据表初始化完成 (wb_article, wb_comment, zb8_match_comment, wb_semantic_analysis)")
        except Exception as e:
            self.logger.error(f"[MySQL] 建表失败: {e}")
            raise

    def _ensure_column(self, cursor, table_name: str, column_name: str, column_definition: str):
        cursor.execute(
            """
            SELECT COUNT(*) AS total
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s
            """,
            (self.database, table_name, column_name),
        )
        row = cursor.fetchone() or {}
        if int(row.get('total') or 0) == 0:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")
            self.logger.info(f"[MySQL] 已补充字段 {table_name}.{column_name}")

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

    def save_semantic_analysis(self, analysis_data: Dict[str, Any]) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            db_id = self._upsert_semantic_analysis(cursor, analysis_data)
            conn.commit()
            self.logger.info(
                "[MySQL] 语义分析已保存 source=%s:%s id=%s",
                analysis_data.get('source_type', ''),
                analysis_data.get('source_id', 0),
                db_id,
            )
            return db_id

    def save_semantic_analysis_batch(self, analyses: List[Dict[str, Any]]) -> int:
        if not analyses:
            return 0
        with self.get_connection() as conn:
            cursor = conn.cursor()
            saved = 0
            for analysis_data in analyses:
                self._upsert_semantic_analysis(cursor, analysis_data)
                saved += 1
            conn.commit()
            self.logger.info(f"[MySQL] 批量保存语义分析 {saved} 条")
            return saved

    def _upsert_semantic_analysis(self, cursor, data: Dict[str, Any]) -> int:
        source_type = data.get('source_type', 'article')
        source_id = int(data.get('source_id') or 0)
        analysis_version = data.get('analysis_version', '')
        values = (
            source_type,
            source_id,
            int(data.get('article_id') or 0),
            data.get('article_url', ''),
            data.get('keyword', ''),
            data.get('author_name', ''),
            data.get('text_hash', ''),
            int(data.get('text_length') or 0),
            data.get('summary', '')[:500],
            data.get('sentiment_label', ''),
            float(data.get('sentiment_score') or 0),
            data.get('primary_emotion', ''),
            data.get('stance_label', ''),
            float(data.get('stance_polarity') or 0),
            float(data.get('conflict_score') or 0),
            self._json_dumps(data.get('value_labels', [])),
            self._json_dumps(data.get('concept_labels', [])),
            self._json_dumps(data.get('keywords', [])),
            self._json_dumps(data.get('viewpoints', [])),
            self._json_dumps(data.get('emotions', [])),
            self._json_dumps(data.get('analysis', {})),
            analysis_version,
        )
        cursor.execute("""
            INSERT INTO wb_semantic_analysis (
                source_type, source_id, article_id, article_url, keyword, author_name,
                text_hash, text_length, summary, sentiment_label, sentiment_score,
                primary_emotion, stance_label, stance_polarity, conflict_score,
                value_labels, concept_labels, keywords, viewpoints, emotions, analysis_json, analysis_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                article_id=VALUES(article_id),
                article_url=VALUES(article_url),
                keyword=VALUES(keyword),
                author_name=VALUES(author_name),
                text_hash=VALUES(text_hash),
                text_length=VALUES(text_length),
                summary=VALUES(summary),
                sentiment_label=VALUES(sentiment_label),
                sentiment_score=VALUES(sentiment_score),
                primary_emotion=VALUES(primary_emotion),
                stance_label=VALUES(stance_label),
                stance_polarity=VALUES(stance_polarity),
                conflict_score=VALUES(conflict_score),
                value_labels=VALUES(value_labels),
                concept_labels=VALUES(concept_labels),
                keywords=VALUES(keywords),
                viewpoints=VALUES(viewpoints),
                emotions=VALUES(emotions),
                analysis_json=VALUES(analysis_json)
        """, values)
        if cursor.lastrowid:
            return cursor.lastrowid
        cursor.execute(
            "SELECT id FROM wb_semantic_analysis WHERE source_type=%s AND source_id=%s AND analysis_version=%s",
            (source_type, source_id, analysis_version),
        )
        row = cursor.fetchone()
        return row['id'] if row else 0

    def _json_dumps(self, value: Any) -> str:
        return json.dumps(value if value is not None else [], ensure_ascii=False, separators=(',', ':'))

    def get_article_by_url(self, url: str) -> Optional[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM wb_article WHERE url = %s", (url,))
            return cursor.fetchone()

    def get_comments_by_article_id(self, article_id: int, limit: int = 50) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM wb_comment WHERE article_id = %s ORDER BY id LIMIT %s",
                           (article_id, limit))
            return cursor.fetchall()

    def get_semantic_analysis_by_article_id(self, article_id: int) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT * FROM wb_semantic_analysis
                   WHERE article_id = %s
                   ORDER BY source_type = 'article' DESC, conflict_score DESC, id""",
                (article_id,),
            )
            return cursor.fetchall()

    def get_hotspot_viewpoints(
        self,
        keyword: str = None,
        article_id: int = None,
        limit: int = 30,
        min_conflict_score: float = 0.0,
    ) -> List[Dict]:
        conditions = ["conflict_score >= %s"]
        params: List[Any] = [min_conflict_score]
        if keyword:
            conditions.append("keyword LIKE %s")
            params.append(f"%{keyword}%")
        if article_id:
            conditions.append("article_id = %s")
            params.append(article_id)
        where_clause = " AND ".join(conditions)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""SELECT * FROM wb_semantic_analysis
                    WHERE {where_clause}
                    ORDER BY conflict_score DESC, ABS(stance_polarity) DESC, id DESC
                    LIMIT %s""",
                params + [limit],
            )
            return cursor.fetchall()

    def get_hotspot_semantic_summary(self, keyword: str = None, article_id: int = None) -> Dict[str, Any]:
        conditions = []
        params: List[Any] = []
        if keyword:
            conditions.append("keyword LIKE %s")
            params.append(f"%{keyword}%")
        if article_id:
            conditions.append("article_id = %s")
            params.append(article_id)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""SELECT source_type, sentiment_label, stance_label, primary_emotion,
                           COUNT(*) AS total, AVG(conflict_score) AS avg_conflict
                    FROM wb_semantic_analysis
                    {where_clause}
                    GROUP BY source_type, sentiment_label, stance_label, primary_emotion
                    ORDER BY total DESC, avg_conflict DESC""",
                params,
            )
            groups = cursor.fetchall()
            cursor.execute(
                f"""SELECT COUNT(*) AS total, AVG(conflict_score) AS avg_conflict,
                           MAX(conflict_score) AS max_conflict
                    FROM wb_semantic_analysis
                    {where_clause}""",
                params,
            )
            totals = cursor.fetchone() or {}
        return {"totals": totals, "groups": groups}

    def save_zhibo8_comment(self, comment_data: Dict[str, Any]) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            match_url = comment_data.get('match_url', '')
            comment_id = comment_data.get('comment_id', '')
            existing = None
            if match_url and comment_id:
                cursor.execute(
                    "SELECT id FROM zb8_match_comment WHERE match_url = %s AND comment_id = %s",
                    (match_url, comment_id)
                )
                existing = cursor.fetchone()

            if existing:
                db_id = existing['id']
                cursor.execute("""
                    UPDATE zb8_match_comment SET
                        author_name=%s, content_text=%s, like_count=%s,
                        reply_count=%s, publish_time=%s, screenshot_path=%s
                    WHERE id=%s
                """, (
                    comment_data.get('author_name', ''),
                    comment_data.get('content_text', ''),
                    comment_data.get('like_count', 0),
                    comment_data.get('reply_count', 0),
                    comment_data.get('publish_time', ''),
                    comment_data.get('screenshot_path', ''),
                    db_id
                ))
                self.logger.info(f"[MySQL] 直播吧评论已更新 id={db_id}")
            else:
                cursor.execute("""
                    INSERT INTO zb8_match_comment (match_url, match_id, match_title,
                        comment_id, author_name, content_text, like_count, reply_count,
                        publish_time, screenshot_path)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    comment_data.get('match_url', ''),
                    comment_data.get('match_id', ''),
                    comment_data.get('match_title', ''),
                    comment_data.get('comment_id', ''),
                    comment_data.get('author_name', ''),
                    comment_data.get('content_text', ''),
                    comment_data.get('like_count', 0),
                    comment_data.get('reply_count', 0),
                    comment_data.get('publish_time', ''),
                    comment_data.get('screenshot_path', '')
                ))
                db_id = cursor.lastrowid
                self.logger.info(f"[MySQL] 直播吧评论已插入 id={db_id}")

            conn.commit()
            return db_id

    def save_zhibo8_comments_batch(self, comments_data: List[Dict[str, Any]]) -> int:
        if not comments_data:
            return 0
        with self.get_connection() as conn:
            cursor = conn.cursor()
            first_comment = comments_data[0]
            match_url = first_comment.get('match_url', '')
            if match_url:
                cursor.execute(
                    "SELECT comment_id FROM zb8_match_comment WHERE match_url = %s",
                    (match_url,)
                )
                existing_ids = {row['comment_id'] for row in cursor.fetchall()}
            else:
                existing_ids = set()

            rows = []
            skipped = 0
            for cd in comments_data:
                cid = cd.get('comment_id', '')
                if cid and cid in existing_ids:
                    skipped += 1
                    continue
                rows.append((
                    cd.get('match_url', ''),
                    cd.get('match_id', ''),
                    cd.get('match_title', ''),
                    cd.get('comment_id', ''),
                    cd.get('author_name', ''),
                    cd.get('content_text', ''),
                    cd.get('like_count', 0),
                    cd.get('reply_count', 0),
                    cd.get('publish_time', ''),
                    cd.get('screenshot_path', '')
                ))
                existing_ids.add(cid)

            if not rows:
                self.logger.info(f"[MySQL] 直播吧评论全部重复，跳过 (skipped={skipped})")
                return 0

            cursor.executemany("""
                INSERT INTO zb8_match_comment (match_url, match_id, match_title,
                    comment_id, author_name, content_text, like_count, reply_count,
                    publish_time, screenshot_path)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, rows)
            conn.commit()
            count = cursor.rowcount
            self.logger.info(f"[MySQL] 批量插入直播吧评论 {count} 条 (跳过重复{skipped}条)")
            return count

    def get_zhibo8_comments_by_match(self, match_url: str) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM zb8_match_comment WHERE match_url = %s ORDER BY like_count DESC, id",
                (match_url,)
            )
            return cursor.fetchall()

    def get_articles(self, keyword: str = None, author_name: str = None,
                     page: int = 1, page_size: int = 20) -> tuple:
        offset = (page - 1) * page_size
        conditions = []
        params = []
        if keyword:
            conditions.append("keyword LIKE %s")
            params.append(f"%{keyword}%")
        if author_name:
            conditions.append("author_name LIKE %s")
            params.append(f"%{author_name}%")
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) as total FROM wb_article {where_clause}", params or ())
            total = cursor.fetchone()['total']
            cursor.execute(
                f"SELECT * FROM wb_article {where_clause} ORDER BY id DESC LIMIT %s OFFSET %s",
                (params or []) + [page_size, offset]
            )
            rows = cursor.fetchall()
        return total, rows

    def get_article_by_id(self, article_id: int) -> Dict:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM wb_article WHERE id = %s", (article_id,))
            return cursor.fetchone()

    def get_comments(self, article_url: str = None, author_name: str = None,
                     page: int = 1, page_size: int = 20) -> tuple:
        offset = (page - 1) * page_size
        conditions = []
        params = []
        if article_url:
            conditions.append("article_url LIKE %s")
            params.append(f"%{article_url}%")
        if author_name:
            conditions.append("author_name LIKE %s")
            params.append(f"%{author_name}%")
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) as total FROM wb_comment {where_clause}", params or ())
            total = cursor.fetchone()['total']
            cursor.execute(
                f"SELECT * FROM wb_comment {where_clause} ORDER BY id DESC LIMIT %s OFFSET %s",
                (params or []) + [page_size, offset]
            )
            rows = cursor.fetchall()
        return total, rows

    def get_zhibo8_comments(self, match_url: str = None, match_id: str = None,
                            min_likes: int = 0, page: int = 1, page_size: int = 30) -> tuple:
        offset = (page - 1) * page_size
        conditions = ["like_count >= %s"]
        params = [min_likes]
        if match_url:
            conditions.append("match_url LIKE %s")
            params.append(f"%{match_url}%")
        if match_id:
            conditions.append("match_id = %s")
            params.append(match_id)
        where_clause = f"WHERE {' AND '.join(conditions)}"

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) as total FROM zb8_match_comment {where_clause}", params)
            total = cursor.fetchone()['total']
            cursor.execute(
                f"SELECT * FROM zb8_match_comment {where_clause} ORDER BY like_count DESC LIMIT %s OFFSET %s",
                params + [page_size, offset]
            )
            rows = cursor.fetchall()
        return total, rows

    def get_zhibo8_matches(self, page: int = 1, page_size: int = 20) -> tuple:
        offset = (page - 1) * page_size
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""SELECT match_url, match_title, match_id,
                              COUNT(*) as comment_count,
                              MAX(like_count) as max_likes,
                              SUM(like_count) as total_likes,
                              MAX(created_at) as latest_comment
                              FROM zb8_match_comment
                              GROUP BY match_url, match_title, match_id
                              ORDER BY latest_comment DESC
                              LIMIT %s OFFSET %s""", (page_size, offset))
            rows = cursor.fetchall()
            cursor.execute("SELECT COUNT(DISTINCT match_url) as total FROM zb8_match_comment")
            total = cursor.fetchone()['total']
        return total, rows

    def close(self):
        if self._pool:
            try:
                self._pool.close()
            except Exception:
                pass
            self._pool = None
