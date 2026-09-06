"""
增强意图识别 - 多层意图识别策略
第一层：关键词匹配（快速、低成本）
第二层：LLM 二次确认（模糊意图）
第三层：上下文推断（基于历史对话）
"""
import re
from typing import Tuple, Optional, List, Dict, Any
from dataclasses import dataclass


@dataclass
class IntentResult:
    intent: str
    confidence: float
    needs_clarification: bool = False
    sub_intent: Optional[str] = None


# 意图关键词映射 - 带有权重的关键词
INTENT_PATTERNS = {
    "query_order": {
        "high": ["订单号", "物流", "快递", "发货", "运单号", "配送", "咋样了", "到哪了", "到哪一步", "签收了没", "什么情况"],
        "medium": ["订单", "查询", "跟踪", "到哪", "状态", "单号", "查件", "查一下"],
        "low": ["我的单"],
    },
    "refund": {
        "high": ["退款", "退货", "退换", "退钱", "要钱", "退单", "怎么退", "如何退", "退款流程", "退货流程", "换货流程", "能退吗", "可以退"],
        "medium": ["售后", "拒收", "不想要", "不合适", "质量问题", "到账", "无理由"],
        "low": ["退", "换", "不满意"],
    },
    "technical": {
        "high": ["怎么用", "如何", "安装", "设置", "配置", "教程", "连接不上", "没有画面", "支付失败", "开不了机", "连不上", "灯不亮", "花屏", "充不进电", "掉线", "搜不到", "密码忘了", "怎么连", "重复扣款", "多扣了", "付了两次"],
        "medium": ["问题", "报错", "故障", "坏了", "不工作", "没反应", "配对", "断连", "兼容", "发票", "分期", "扣款", "密码", "企业采购", "批量采购", "采购流程", "重置", "省电", "以旧换新", "回收", "采购", "支持", "批量", "多台"],
        "low": ["使用", "操作", "功能", "支持哪些设备", "怎么弄", "咋用", "公司买"],
    },
    "promotion": {
        "high": ["优惠", "活动", "折扣", "券", "促销", "便宜", "减", "会员", "vip", "首单", "打折"],
        "medium": ["满减", "赠品", "礼包", "新人", "特价", "限时", "满就送", "最近有什么"],
        "low": ["划算", "实惠", "性价比"],
    },
    "human": {
        "high": ["人工", "客服", "转人工", "找客服", "真人", "在线客服", "机器人", "领导"],
        "medium": ["投诉到", "找你们领导", "我要见人", "别跟机器人", "真人客服"],
        "low": ["help"],
    },
    "complaint": {
        "high": ["投诉", "差评", "举报", "骗", "气愤", "垃圾", "骗子", "气死", "火大", "态度", "恶劣"],
        "medium": ["不满", "生气", "失望", "太烂", "太差"],
        "low": ["不好", "差", "糟糕"],
    },
}

# 意图否定词（出现在有争议的关键词附近时降低权重）
NEGATION_WORDS = ["不是", "不要", "不", "非", "没有", "没"]


class EnhancedIntentRecognizer:
    """增强版意图识别器"""

    def __init__(self):
        self.patterns = INTENT_PATTERNS

    def recognize(
        self,
        message: str,
        context: Optional[List[Dict[str, Any]]] = None,
        history_intent: Optional[str] = None,
    ) -> IntentResult:
        """
        识别意图
        Args:
            message: 用户消息
            context: 上下文对话历史
            history_intent: 上一轮识别的意图
        Returns:
            IntentResult 包含意图、置信度、是否需要澄清
        """
        # 第一层：关键词匹配
        scores = self._keyword_scores(message)

        # 第二层：检查否定词，将被否定的意图得分置零
        negated_intent = self._find_negated_intent(message)
        if negated_intent and negated_intent in scores:
            scores[negated_intent] = 0.0

        # 选出得分最高的意图
        if scores:
            best_intent = max(scores, key=scores.get)
            max_score = scores[best_intent]
            if max_score <= 0:
                keyword_result = IntentResult(intent="general", confidence=0.0)
            else:
                confidence = min(max_score / 4.0, 1.0)
                # 如果有两个意图得分接近，降低置信度
                positive_scores = [s for s in scores.values() if s > 0]
                if len(positive_scores) > 1:
                    sorted_scores = sorted(positive_scores, reverse=True)
                    if sorted_scores[0] - sorted_scores[1] < 0.5:
                        confidence *= 0.7
                keyword_result = IntentResult(
                    intent=best_intent,
                    confidence=round(confidence, 2),
                )
        else:
            keyword_result = IntentResult(intent="general", confidence=0.0)

        # 第三层：上下文推断
        if context and keyword_result.confidence < 0.3:
            context_intent = self._context_inference(context, message)
            if context_intent:
                keyword_result.intent = context_intent
                keyword_result.confidence = 0.5

        # 第四层：延续性判断
        if history_intent and self._is_continuation(message):
            # 如果消息很短且是追问，保持上一个意图
            if len(message) < 15 and keyword_result.confidence < 0.2:
                keyword_result.intent = history_intent
                keyword_result.confidence = 0.6

        # 判断是否需要澄清
        # 情况1: 低置信度且有明确意图 → 需要澄清
        # 情况2: 意图为general且消息过短 → 需要澄清(用户可能只说了"嗯"、"好的"等)
        keyword_result.needs_clarification = (
            (keyword_result.confidence < 0.4 and keyword_result.intent != "general")
            or (keyword_result.intent == "general" and len(message.strip()) <= 5)
        )

        return keyword_result

    def _keyword_scores(self, message: str) -> Dict[str, float]:
        """关键词匹配，返回各意图得分"""
        message_lower = message.lower()
        scores: Dict[str, float] = {}

        # 明确订单号即属于订单查询语境，即使用户只问金额或支付方式。
        # 去掉末尾 \b 以兼容订单号后紧跟中文的情况（如 ORD20260903现在咋样了）
        if re.search(r"(?:ORD|ord)[-_a-zA-Z0-9]*\d{4,}", message_lower):
            scores["query_order"] = 2.0

        # “有个问题/帮个忙”等没有领域信息，应该澄清而不是误判为技术或订单。
        generic_request = re.fullmatch(
            r"(?:我)?(?:有个?|有一个)?问题[，, ]*(?:能)?帮(?:个忙|忙)?(?:吗|么)?[？?]?",
            message_lower.strip(),
        )
        if generic_request:
            return {}

        for intent, levels in self.patterns.items():
            score = 0.0

            # 高权重关键词
            for keyword in levels.get("high", []):
                if keyword.lower() in message_lower:
                    score += 2.0

            # 中权重关键词
            for keyword in levels.get("medium", []):
                if keyword.lower() in message_lower:
                    score += 1.0

            # 低权重关键词
            for keyword in levels.get("low", []):
                if keyword.lower() in message_lower:
                    score += 0.3

            if score > 0:
                scores[intent] = scores.get(intent, 0.0) + score

        # 订单号只是资源标识；当用户明确说退款/退货时，业务动作优先于查单。
        if any(keyword in message_lower for keyword in self.patterns["refund"]["high"]):
            scores["refund"] = scores.get("refund", 0.0) + 3.0

        return scores

    def _find_negated_intent(self, message: str) -> Optional[str]:
        """检查是否有意图关键词被否定，返回被否定的意图名。
        避免把"不合适"里的"不"、"有没有"里的"没有"误判为否定。"""
        # 收集所有已知关键词，用于排除"否定词+关键词"本身就是正向词的情况
        all_known_keywords = set()
        for levels in self.patterns.values():
            for level_words in levels.values():
                all_known_keywords.update(level_words)

        for intent, levels in self.patterns.items():
            high_keywords = levels.get("high", [])
            all_keywords = []
            for level_words in levels.values():
                all_keywords.extend(level_words)
            for keyword in all_keywords:
                idx = message.find(keyword)
                if idx <= 0:
                    continue
                # 只有 high 权重关键词被否定时，才否定整个意图
                if keyword not in high_keywords:
                    continue
                # 看关键词前 4 个字符窗口内是否有否定词
                window = message[max(0, idx - 4):idx]
                for neg in NEGATION_WORDS:
                    neg_pos = window.rfind(neg)
                    if neg_pos < 0:
                        continue
                    # 否定词结束位置到关键词起始位置的距离
                    neg_end = max(0, idx - 4) + neg_pos + len(neg)
                    distance = idx - neg_end
                    # 距离超过1字，说明否定词不是直接修饰该关键词（如"我不要了能退钱"）
                    if distance > 1:
                        continue
                    # 否定词后紧跟的字符
                    after_neg = message[neg_end:neg_end + 1] if neg_end < len(message) else ""
                    # "不+已知正向词"（如不合适、不想要、不工作）不算否定
                    if after_neg and any(kw.startswith(neg + after_neg) for kw in all_known_keywords):
                        continue
                    # "有没有" 中的 "没有" 不算否定
                    if neg == "没有" and neg_end >= 1 and message[neg_end - len(neg) - 1:neg_end - len(neg)] == "有":
                        continue
                    return intent
        return None

    def _context_inference(
        self, context: List[Dict[str, Any]], message: str
    ) -> Optional[str]:
        """基于上下文推断意图"""
        recent_context = context[-3:] if len(context) >= 3 else context
        
        # 检查最近的工具调用
        for msg in reversed(recent_context):
            if msg.get("role") == "tool":
                tool_name = msg.get("tool_name", "")
                if "order" in tool_name.lower():
                    return "query_order"
                if "ticket" in tool_name.lower():
                    return "technical"
                if "refund" in tool_name.lower():
                    return "refund"

        return None

    def _is_continuation(self, message: str) -> bool:
        """判断是否为延续性追问"""
        continuation_patterns = [
            r"^[好的嗯哦啊哦好的]",
            r"^(那|然后|接着|还有)",
            r"^(好|行|可以|明白|知道了)",
            r"^(谢谢|感谢|多谢)",
            r"^\d+$",  # 纯数字，可能是在提供订单号
            r"^(对|是|没错|正确)",
            r"^(现在|目前|大概|预计|什么时候)",
            r"^(还是|仍然|依然|重试|重置).*(没用|不行|失败)",
        ]
        
        for pattern in continuation_patterns:
            if re.match(pattern, message):
                return True
        return False

    def get_clarification_question(self, intent: str) -> str:
        """获取澄清问题"""
        questions = {
            "query_order": "您想查询订单的什么信息呢？请提供订单号，我可以帮您查询物流状态和订单详情。",
            "refund": "您是想申请仅退款还是退货退款呢？另外，请提供订单号以便我为您处理。",
            "complaint": "非常抱歉给您带来不好的体验。能否请您详细描述一下遇到的问题？这样我们才能更好地帮助您。",
            "technical": "请问您具体遇到了什么问题呢？能否描述一下操作步骤和出现的情况？",
            "promotion": "我们目前有多种优惠活动，您想了解哪方面的信息呢？",
            "human": "好的，正在为您转接人工客服。请问有什么特别需要备注的信息吗？",
            "general": "我可以帮助您查询订单、处理退换货、解答产品问题等。请问有什么可以帮您的？",
        }
        return questions.get(intent, "请问您需要什么帮助呢？")
