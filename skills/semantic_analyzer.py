# -*- coding: utf-8 -*-
import hashlib
import json
import logging
import re
from collections import Counter
from typing import Any, Dict, List, Tuple

try:
    import jieba
    import jieba.analyse
except ImportError:
    jieba = None

from core.base import BaseSkill


class SemanticAnalyzer(BaseSkill):
    """中文语义分析：情绪、价值、观念、观点和冲突潜力标注。"""

    ANALYSIS_VERSION = "semantic-heuristic-v1"

    POSITIVE_WORDS = {
        "支持", "认可", "同意", "喜欢", "期待", "值得", "优秀", "厉害", "牛", "好",
        "好看", "合理", "有趣", "精彩", "真诚", "专业", "靠谱", "赞", "舒服",
        "希望", "开心", "搞笑", "封神", "绝", "稳定", "尊重", "清醒",
    }
    NEGATIVE_WORDS = {
        "反对", "不认同", "不同意", "讨厌", "失望", "离谱", "荒谬", "尴尬", "差",
        "难看", "恶心", "糟糕", "失败", "失控", "问题", "质疑", "担心", "焦虑",
        "绝望", "奇怪", "神奇", "不行", "不对", "没用", "无聊", "割裂",
    }
    NEGATIONS = {"不", "没", "无", "非", "别", "未", "不是", "没有", "不能"}

    EMOTION_LEXICON = {
        "喜悦": {"哈哈", "笑", "开心", "有趣", "搞笑", "精彩", "好看", "封神", "绝"},
        "期待": {"期待", "希望", "等", "想看", "安排", "下一期", "以后", "未来"},
        "愤怒": {"愤怒", "生气", "离谱", "恶心", "荒谬", "讨厌", "不爽", "气"},
        "失望": {"失望", "绝望", "难受", "遗憾", "可惜", "没意思", "无聊"},
        "焦虑": {"担心", "焦虑", "害怕", "风险", "危险", "不稳", "失控"},
        "质疑": {"为什么", "凭什么", "质疑", "怀疑", "真的吗", "是不是", "哪里"},
        "嘲讽": {"呵呵", "笑死", "神奇", "就这", "阴阳", "离谱", "懂了"},
        "共鸣": {"我也是", "确实", "同感", "懂", "说到心里", "真实", "泪目"},
    }

    VALUE_LEXICON = {
        "公平正义": {"公平", "公正", "正义", "权利", "平等", "尊重", "歧视", "偏见"},
        "效率结果": {"效率", "结果", "有用", "成本", "收益", "性价比", "浪费", "价值"},
        "真实可信": {"真实", "真诚", "可信", "证据", "事实", "造假", "可信度", "逻辑"},
        "专业能力": {"专业", "能力", "水平", "技术", "经验", "质量", "稳定", "靠谱"},
        "情感共情": {"理解", "共情", "感受", "情绪", "温暖", "难受", "尊重", "体面"},
        "自由表达": {"自由", "表达", "选择", "开放", "允许", "限制", "控制"},
        "秩序规则": {"规则", "底线", "责任", "规范", "秩序", "边界", "违法", "违规"},
        "娱乐审美": {"好笑", "搞笑", "节目", "好看", "无聊", "审美", "效果", "综艺"},
        "身份立场": {"粉丝", "路人", "男性", "女性", "观众", "群体", "我们", "他们"},
    }

    CONCEPT_LEXICON = {
        "结果导向": {"结果", "效率", "有用", "解决", "收益", "成本", "价值", "效果"},
        "规则底线": {"规则", "底线", "责任", "应该", "不该", "必须", "边界", "规范"},
        "个人选择": {"选择", "自由", "喜欢", "不喜欢", "想", "愿意", "自己", "个人"},
        "群体身份": {"粉丝", "路人", "观众", "男性", "女性", "网友", "我们", "他们"},
        "专业主义": {"专业", "能力", "水平", "技术", "逻辑", "质量", "经验", "靠谱"},
        "情绪优先": {"感受", "情绪", "共情", "舒服", "难受", "开心", "失望", "绝望"},
        "娱乐至上": {"搞笑", "好笑", "节目", "效果", "好看", "无聊", "综艺", "期待"},
        "怀疑批判": {"质疑", "怀疑", "为什么", "凭什么", "问题", "离谱", "荒谬", "神奇"},
        "权威信任": {"听您的", "老师", "专家", "官方", "权威", "相信", "认可", "背书"},
    }

    STANCE_CUES = {
        "支持": {"支持", "赞同", "同意", "认可", "可以", "合理", "值得", "喜欢"},
        "反对": {"反对", "不同意", "不认同", "不该", "不应该", "不行", "没必要"},
        "质疑": {"为什么", "凭什么", "是不是", "吗", "质疑", "怀疑", "哪里"},
        "建议": {"建议", "应该", "希望", "可以考虑", "最好", "需要", "建议你"},
        "观望": {"再看", "先看", "不确定", "可能", "也许", "感觉", "似乎"},
    }

    OPINION_CUES = (
        "我觉得", "我认为", "感觉", "看法", "应该", "不应该", "必须", "没必要",
        "关键是", "核心是", "问题是", "其实", "说白了", "本质", "逻辑",
    )

    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        semantic_config = config.get("semantic_analysis", {})
        self.enabled = semantic_config.get("enabled", True)
        self.keyword_top_k = int(semantic_config.get("keyword_top_k", 8))
        self.viewpoint_top_k = int(semantic_config.get("viewpoint_top_k", 3))
        self.max_text_length = int(semantic_config.get("max_text_length", 2000))
        super().__init__(config, logger)

    def _initialize(self):
        self.logger.info(f"[{self.name}] 中文语义分析器初始化完成")
        if jieba is None:
            self.logger.warning(f"[{self.name}] 未安装 jieba，已启用正则分词降级模式")

    def execute(self, raw_data: Any, source_type: str = "article", **kwargs) -> Any:
        if isinstance(raw_data, list):
            return [self.analyze_record(item, source_type=source_type, **kwargs) for item in raw_data]
        return self.analyze_record(raw_data, source_type=source_type, **kwargs)

    def analyze_record(
        self,
        data: Any,
        source_type: str,
        source_id: int = 0,
        article_id: int = 0,
        keyword: str = "",
    ) -> Dict[str, Any]:
        row = data.to_dict() if hasattr(data, "to_dict") else dict(data or {})
        text = row.get("content_text") or row.get("title") or ""
        analysis = self.analyze_text(text)

        resolved_source_id = int(source_id or row.get("id") or 0)
        resolved_article_id = int(article_id or row.get("article_id") or (resolved_source_id if source_type == "article" else 0) or 0)
        article_url = row.get("article_url") or row.get("url") or ""
        resolved_keyword = keyword or row.get("keyword") or ""

        return {
            "source_type": source_type,
            "source_id": resolved_source_id,
            "article_id": resolved_article_id,
            "article_url": article_url,
            "keyword": resolved_keyword,
            "author_name": row.get("author_name", ""),
            "content_text": text,
            "text_hash": self._hash_text(text),
            "text_length": len(text or ""),
            "summary": analysis["summary"],
            "sentiment_label": analysis["sentiment"]["label"],
            "sentiment_score": analysis["sentiment"]["score"],
            "primary_emotion": analysis["primary_emotion"],
            "stance_label": analysis["stance"]["label"],
            "stance_polarity": analysis["stance"]["polarity"],
            "conflict_score": analysis["conflict"]["score"],
            "value_labels": analysis["value_labels"],
            "concept_labels": analysis["concept_labels"],
            "keywords": analysis["keywords"],
            "viewpoints": analysis["viewpoints"],
            "emotions": analysis["emotions"],
            "analysis": analysis,
            "analysis_version": self.ANALYSIS_VERSION,
        }

    def analyze_text(self, text: str) -> Dict[str, Any]:
        clean_text = self._normalize_text(text)
        if not clean_text:
            return self._empty_analysis()

        clipped_text = clean_text[: self.max_text_length]
        tokens = self._tokenize(clipped_text)
        token_counter = Counter(tokens)
        sentiment = self._analyze_sentiment(clipped_text, tokens)
        emotions = self._score_lexicon(clipped_text, token_counter, self.EMOTION_LEXICON)
        values = self._score_lexicon(clipped_text, token_counter, self.VALUE_LEXICON)
        concepts = self._score_lexicon(clipped_text, token_counter, self.CONCEPT_LEXICON)
        stance = self._analyze_stance(clipped_text, token_counter, sentiment)
        viewpoints = self._extract_viewpoints(clipped_text)
        keywords = self._extract_keywords(clipped_text)
        primary_emotion = self._choose_primary_emotion(emotions, sentiment)
        value_labels = [item["label"] for item in values[:3]]
        concept_labels = [item["label"] for item in concepts[:3]]
        conflict = self._score_conflict(sentiment, primary_emotion, emotions, values, concepts, stance, viewpoints)

        return {
            "version": self.ANALYSIS_VERSION,
            "summary": self._make_summary(clipped_text, viewpoints),
            "keywords": keywords,
            "tokens_top": [{"word": word, "count": count} for word, count in token_counter.most_common(12)],
            "sentiment": sentiment,
            "primary_emotion": primary_emotion,
            "emotions": emotions,
            "value_labels": value_labels,
            "values": values,
            "concept_labels": concept_labels,
            "concepts": concepts,
            "stance": stance,
            "viewpoints": viewpoints,
            "conflict": conflict,
        }

    def _empty_analysis(self) -> Dict[str, Any]:
        return {
            "version": self.ANALYSIS_VERSION,
            "summary": "",
            "keywords": [],
            "tokens_top": [],
            "sentiment": {"label": "中性", "score": 0.0, "confidence": 0.0, "positive_hits": [], "negative_hits": []},
            "primary_emotion": "中性",
            "emotions": [],
            "value_labels": [],
            "values": [],
            "concept_labels": [],
            "concepts": [],
            "stance": {"label": "中立", "polarity": 0.0, "evidence": []},
            "viewpoints": [],
            "conflict": {"score": 0.0, "level": "低", "drivers": []},
        }

    def _normalize_text(self, text: str) -> str:
        if not text:
            return ""
        value = re.sub(r"https?://\S+", "", str(text))
        value = re.sub(r"@\S+", "", value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    def _tokenize(self, text: str) -> List[str]:
        tokens = []
        raw_tokens = jieba.lcut(text) if jieba else self._regex_tokenize(text)
        for token in raw_tokens:
            token = token.strip()
            if len(token) < 2 and token not in {"赞", "牛", "好", "差"}:
                continue
            if re.fullmatch(r"[\W_]+", token):
                continue
            tokens.append(token)
        return tokens

    def _regex_tokenize(self, text: str) -> List[str]:
        words = re.findall(r"[\u4e00-\u9fff]{1,4}|[A-Za-z0-9_]+", text)
        phrase_hits = []
        lexicons = [
            self.POSITIVE_WORDS,
            self.NEGATIVE_WORDS,
            self.NEGATIONS,
            set(self.OPINION_CUES),
        ]
        for lexicon in lexicons:
            phrase_hits.extend(word for word in lexicon if word and word in text)
        return words + phrase_hits

    def _extract_keywords(self, text: str) -> List[str]:
        try:
            if jieba:
                words = [
                    word for word in jieba.analyse.extract_tags(text, topK=self.keyword_top_k * 2)
                    if len(word) >= 2 and not word.isdigit()
                ]
            else:
                words = []
        except Exception:
            words = []
        if words:
            return words[: self.keyword_top_k]
        return [word for word, _ in Counter(self._tokenize(text)).most_common(self.keyword_top_k)]

    def _analyze_sentiment(self, text: str, tokens: List[str]) -> Dict[str, Any]:
        positive_hits: List[str] = []
        negative_hits: List[str] = []
        pos_score = 0.0
        neg_score = 0.0

        for idx, token in enumerate(tokens):
            is_positive = token in self.POSITIVE_WORDS
            is_negative = token in self.NEGATIVE_WORDS
            if not is_positive and not is_negative:
                continue
            negated = self._is_negated(tokens, idx)
            if is_positive:
                if negated:
                    neg_score += 1.0
                    negative_hits.append(f"不{token}")
                else:
                    pos_score += 1.0
                    positive_hits.append(token)
            if is_negative:
                if negated:
                    pos_score += 0.8
                    positive_hits.append(f"不{token}")
                else:
                    neg_score += 1.0
                    negative_hits.append(token)

        matched_positive = self._match_phrases(text, self.POSITIVE_WORDS)
        matched_negative = self._match_phrases(text, self.NEGATIVE_WORDS)
        for word in matched_positive:
            if word not in positive_hits:
                pos_score += 1.0
                positive_hits.append(word)
        for word in matched_negative:
            if word not in negative_hits:
                neg_score += 1.0
                negative_hits.append(word)

        exclamation_bonus = min(text.count("!") + text.count("！"), 3) * 0.15
        question_bonus = min(text.count("?") + text.count("？"), 3) * 0.1
        if positive_hits:
            pos_score += exclamation_bonus
        if negative_hits:
            neg_score += exclamation_bonus + question_bonus

        total = pos_score + neg_score
        score = 0.0 if total == 0 else (pos_score - neg_score) / total
        if total == 0:
            label = "中性"
        elif abs(score) < 0.2 and pos_score > 0 and neg_score > 0:
            label = "复杂"
        elif score >= 0.2:
            label = "正向"
        elif score <= -0.2:
            label = "负向"
        else:
            label = "中性"

        confidence = min(1.0, total / max(len(tokens) * 0.12, 1.0))
        return {
            "label": label,
            "score": round(score, 4),
            "confidence": round(confidence, 4),
            "positive_hits": positive_hits[:8],
            "negative_hits": negative_hits[:8],
        }

    def _is_negated(self, tokens: List[str], idx: int) -> bool:
        start = max(0, idx - 2)
        return any(token in self.NEGATIONS for token in tokens[start:idx])

    def _score_lexicon(self, text: str, token_counter: Counter, lexicon: Dict[str, set]) -> List[Dict[str, Any]]:
        scored = []
        total_tokens = max(sum(token_counter.values()), 1)
        for label, words in lexicon.items():
            evidence = []
            raw = 0
            for word in words:
                count = token_counter.get(word, 0)
                if not count and word in text:
                    count = 1
                if count:
                    raw += count
                    evidence.append(word)
            if raw:
                scored.append({
                    "label": label,
                    "score": round(min(1.0, raw / max(total_tokens * 0.08, 1.0)), 4),
                    "evidence": evidence[:8],
                })
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored

    def _choose_primary_emotion(self, emotions: List[Dict[str, Any]], sentiment: Dict[str, Any]) -> str:
        if not emotions:
            return sentiment["label"]

        negative_emotions = {"愤怒", "失望", "焦虑", "质疑", "嘲讽"}
        positive_emotions = {"喜悦", "期待", "共鸣"}
        score = float(sentiment.get("score") or 0)
        if score <= -0.2:
            for item in emotions:
                if item["label"] in negative_emotions:
                    return item["label"]
        if score >= 0.2:
            for item in emotions:
                if item["label"] in positive_emotions:
                    return item["label"]
        return emotions[0]["label"]

    def _match_phrases(self, text: str, phrases: set) -> List[str]:
        return [
            phrase for phrase in phrases
            if phrase and (len(phrase) >= 2 or phrase in {"赞", "牛"}) and phrase in text
        ]

    def _analyze_stance(self, text: str, token_counter: Counter, sentiment: Dict[str, Any]) -> Dict[str, Any]:
        scores: Dict[str, float] = {}
        evidence: List[str] = []
        for label, cues in self.STANCE_CUES.items():
            score = 0.0
            for cue in cues:
                count = token_counter.get(cue, 0)
                if not count and cue in text:
                    count = 1
                if count:
                    score += count
                    evidence.append(cue)
            if score:
                scores[label] = score

        if not scores:
            if sentiment["score"] >= 0.35:
                return {"label": "支持", "polarity": 0.6, "evidence": sentiment["positive_hits"][:5]}
            if sentiment["score"] <= -0.35:
                return {"label": "反对", "polarity": -0.6, "evidence": sentiment["negative_hits"][:5]}
            return {"label": "中立", "polarity": 0.0, "evidence": []}

        label = max(scores, key=scores.get)
        polarity_map = {"支持": 0.8, "反对": -0.8, "质疑": -0.45, "建议": 0.25, "观望": 0.05}
        if label == "支持" and sentiment["score"] < -0.2:
            label = "复杂"
        if label == "反对" and sentiment["score"] > 0.2:
            label = "复杂"
        return {
            "label": label,
            "polarity": polarity_map.get(label, 0.0),
            "evidence": list(dict.fromkeys(evidence))[:8],
        }

    def _extract_viewpoints(self, text: str) -> List[Dict[str, Any]]:
        sentences = [s.strip() for s in re.split(r"[。！？!?；;\n]", text) if s.strip()]
        candidates: List[Tuple[int, str, List[str]]] = []
        for sentence in sentences:
            signals = [cue for cue in self.OPINION_CUES if cue in sentence]
            if not signals and len(sentence) < 10:
                continue
            score = len(signals) * 3 + min(len(sentence), 80) // 20
            if any(word in sentence for word in self.POSITIVE_WORDS | self.NEGATIVE_WORDS):
                score += 2
            if any(word in sentence for word in ("但是", "不过", "然而", "反而", "除了")):
                score += 2
            candidates.append((score, sentence[:180], signals))

        candidates.sort(key=lambda item: item[0], reverse=True)
        result = []
        seen = set()
        for score, sentence, signals in candidates:
            if sentence in seen:
                continue
            seen.add(sentence)
            result.append({"text": sentence, "signals": signals, "weight": score})
            if len(result) >= self.viewpoint_top_k:
                break
        return result

    def _score_conflict(
        self,
        sentiment: Dict[str, Any],
        primary_emotion: str,
        emotions: List[Dict[str, Any]],
        values: List[Dict[str, Any]],
        concepts: List[Dict[str, Any]],
        stance: Dict[str, Any],
        viewpoints: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        drivers = []
        sentiment_strength = abs(float(sentiment["score"]))
        if sentiment_strength >= 0.55:
            drivers.append(f"{sentiment['label']}情绪强")
        if sentiment["label"] == "复杂":
            drivers.append("正负评价并存")
        if primary_emotion and primary_emotion not in {"中性", "正向", "负向", "复杂"}:
            drivers.append(f"主要情绪:{primary_emotion}")
        if len(values) >= 2:
            drivers.append("多价值维度并存")
        if concepts:
            drivers.append(f"观念:{concepts[0]['label']}")
        if stance["label"] in {"反对", "质疑", "复杂"}:
            drivers.append(f"立场:{stance['label']}")
        if viewpoints:
            drivers.append("存在明确观点句")

        score = (
            sentiment_strength * 0.35
            + min(len(emotions), 3) * 0.12
            + min(len(values), 3) * 0.10
            + min(len(concepts), 3) * 0.08
            + abs(float(stance["polarity"])) * 0.25
            + min(len(viewpoints), 2) * 0.06
        )
        score = round(min(1.0, score), 4)
        if score >= 0.7:
            level = "高"
        elif score >= 0.4:
            level = "中"
        else:
            level = "低"
        return {"score": score, "level": level, "drivers": drivers[:6]}

    def _make_summary(self, text: str, viewpoints: List[Dict[str, Any]]) -> str:
        if viewpoints:
            return viewpoints[0]["text"][:160]
        return text[:160]

    def _hash_text(self, text: str) -> str:
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
