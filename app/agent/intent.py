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
    # ⑤ 分层阈值：规则对当前消息是否足够自信。False 时上层可跳过 LLM 兜底复审，
    # 避免"top1 已明显领先"的低置信样本再白耗一次模型调用。
    needs_llm_fallback: bool = False


# 意图关键词映射 - 带有权重的关键词
INTENT_PATTERNS = {
    "query_order": {
        "high": ["订单号", "物流", "快递", "发货", "运单号", "配送", "咋样了", "到哪了", "到哪一步", "签收了没", "什么情况", "查一下", "查件", "派送", "催发货"],
        "medium": ["订单", "查询", "跟踪", "到哪", "状态", "单号", "发货没", "发货了吗", "到哪里", "走到哪", "进度", "快递柜", "取件", "什么时候到", "多久到", "没收到", "还没发", "一直没更新", "送到", "送达", "几点到", "揽收", "出库", "签收", "丢失", "能到", "货发"],
        "low": ["我的单", "查"],
    },
    "refund": {
        "high": ["退款", "退货", "退换", "退钱", "要钱", "退单", "怎么退", "如何退", "退款流程", "退货流程", "换货流程", "能退吗", "可以退", "退了吧", "给我退", "帮我退", "我要退", "不要了", "想退", "换新的"],
        "medium": ["售后", "拒收", "不想要", "不合适", "质量问题", "到账", "无理由", "换个", "换一个", "换颜色", "退货运费", "运费谁出", "退款单号", "退货地址", "退款金额", "退款申请", "差价", "保价", "价格保护", "退不了", "退回"],
        "low": ["退", "换", "不满意"],
    },
    "technical": {
        "high": ["怎么用", "如何", "安装", "设置", "配置", "教程", "连接不上", "没有画面", "支付失败", "开不了机", "连不上", "灯不亮", "花屏", "黑屏", "充不进电", "掉线", "搜不到", "密码忘了", "怎么连", "重复扣款", "多扣了", "付了两次", "怎么开发票", "怎么开票", "链接", "闪退"],
        "medium": ["问题", "报错", "故障", "坏了", "不工作", "没反应", "配对", "断连", "兼容", "发票", "分期", "扣款", "密码", "企业采购", "批量采购", "采购流程", "重置", "省电", "以旧换新", "回收", "采购", "支持", "批量", "多台", "团购", "想买", "对公", "解绑", "同步", "一起管理", "投屏", "恢复出厂", "定时开关机", "语音控制", "使用说明", "操作说明", "绑定", "注销", "备份", "更新", "卡死", "自动关机", "屏幕", "内存", "相机", "验证码", "登录", "导入", "清理", "打不开", "失败", "碎", "电池", "一起用"],
        "low": ["使用", "操作", "功能", "支持哪些设备", "怎么弄", "咋用", "公司买", "买几台", "买多少", "转圈"],
    },
    "promotion": {
        "high": ["优惠", "活动", "折扣", "券", "促销", "便宜", "减", "会员", "vip", "首单", "打折", "积分", "团购", "大促"],
        "medium": ["满减", "赠品", "礼包", "新人", "特价", "限时", "满就送", "最近有什么", "优惠码", "抵现", "秒杀", "福利", "新客", "包邮", "返现", "买二送一", "双十一", "618", "老带新"],
        "low": ["划算", "实惠", "性价比"],
    },
    "human": {
        "high": ["人工", "客服", "转人工", "找客服", "真人", "在线客服", "机器人", "领导"],
        "medium": ["投诉到", "找你们领导", "我要见人", "别跟机器人", "真人客服", "经理", "主管", "负责人", "座席", "上级"],
        "low": ["help"],
    },
    "complaint": {
        "high": ["投诉", "差评", "举报", "骗", "气愤", "垃圾", "骗子", "气死", "火大", "态度", "恶劣", "破玩意", "什么玩意", "再也不来", "失望透顶", "垃圾玩意", "再也不会", "打不通", "坑惨", "真服了", "太气人", "不回消息", "拖延"],
        "medium": ["不满", "生气", "失望", "太烂", "太差", "太差了", "破", "坑爹", "拖着", "坑", "不行", "敷衍", "不负责", "上当", "再也不", "效率低", "效率太低", "无语", "不讲信用"],
        "low": ["不好", "差", "糟糕", "太次", "烂"],
    },
}

# 意图否定词（出现在有争议的关键词附近时降低权重）
# 注意：移除单字"不"，避免误伤"不合适/不想要/不工作"等正向词；
# 仅保留明确的否定短语，且只在紧邻关键词时生效。
NEGATION_WORDS = ["不是", "不要", "并非", "无需", "不用", "没有"]


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
        # 第一层：关键词匹配（否定词已在 _keyword_scores 中逐词处理）
        scores = self._keyword_scores(message)
        top_margin = 0.0  # top1 与 top2 的得分差，用于分层阈值（默认 0 → 需 LLM）
        keyword_result: Optional[IntentResult] = None

        # 选出得分最高的意图
        if scores:
            sorted_positive = sorted((s for s in scores.values() if s > 0), reverse=True)
            # top1 与 top2 的得分差：仅一个正分意图时视为"明显领先"（top_margin=top1），
            # 无正分归 general 且置 0（需 LLM 兜底）。
            if len(sorted_positive) >= 2:
                top_margin = sorted_positive[0] - sorted_positive[1]
            elif len(sorted_positive) == 1:
                top_margin = sorted_positive[0]
            best_intent = max(scores, key=scores.get)
            max_score = scores[best_intent]
            if max_score <= 0:
                keyword_result = IntentResult(intent="general", confidence=0.0)
            else:
                confidence = min(max_score / 4.0, 1.0)
                # 如果有两个意图得分接近，降低置信度（沿用原行为，避免易混淆被误判）。
                if len(sorted_positive) > 1 and sorted_positive[0] - sorted_positive[1] < 0.5:
                    confidence *= 0.7
                keyword_result = IntentResult(
                    intent=best_intent,
                    confidence=round(confidence, 2),
                )
        else:
            keyword_result = IntentResult(intent="general", confidence=0.0)
        if context and keyword_result.confidence < 0.3:
            context_intent = self._context_inference(context, message)
            if context_intent:
                keyword_result.intent = context_intent
                keyword_result.confidence = 0.5
                top_margin = 100.0  # 上下文推断已定意图，视为高置信，不再触发 LLM

        # 第四层：延续性判断（③ 放宽：从「len<15 且 conf<0.2」放宽为「len<20 且 conf<0.4」，
        # 让"那…""大概多久"等更多追问直接沿用上一意图，而不是掉进澄清/LLM 兜底）
        if history_intent and self._is_continuation(message):
            if len(message) < 20 and keyword_result.confidence < 0.4:
                keyword_result.intent = history_intent
                keyword_result.confidence = 0.6
                top_margin = 100.0  # 延续已定意图，视为高置信

        # ⑤ 分层阈值：是否值得 LLM 兜底复审
        #  - general（无领域词）→ 不走 LLM，由澄清/模板/检索接管；
        #  - 有正分且 top1 领先 top2 ≥0.5 且置信度达标 → 规则已明显自信，不调 LLM；
        #  - 其余低置信意图 → 保留 LLM 兜底机会，由上层决定是否触发。
        keyword_result.needs_llm_fallback = (
            keyword_result.intent != "general"
            and not (
                top_margin >= 0.5
                and keyword_result.confidence >= 0.3
            )
        )
        # ⑥ 假 general 兜底：规则未命中领域词但含明确疑问句式时，仍开放 LLM 复审，
        # 避免"怎么绑定银行卡/账号怎么注销"等泛化请求被 general 吞掉、
        # 连 LLM 二次确认的机会都没有。
        # 仅对 ≥4 字消息生效，避免"咋/多少"等超短疑问也白耗一次模型调用。
        if (
            keyword_result.intent == "general"
            and self._is_question_style(message)
            and len(message.strip()) >= 4
        ):
            keyword_result.needs_llm_fallback = True

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

        # 检测是否有退款动作词（用于决定订单号加分是否生效）
        # 需排除被否定的情况，如"我不是要退款"
        has_refund_action = any(
            keyword in message_lower and not self._is_keyword_negated(message, keyword)
            for keyword in self.patterns["refund"]["high"]
        )

        # 明确订单号即属于订单查询语境，即使用户只问金额或支付方式。
        # 去掉末尾 \b 以兼容订单号后紧跟中文的情况（如 ORD20260903现在咋样了）
        # 同时支持顺丰/京东/圆通/中通等快递单号
        has_order_id = bool(re.search(r"(?:ORD|ord)[-_a-zA-Z0-9]*\d{4,}", message_lower))
        has_tracking_no = bool(re.search(r"(?:SF|JD|YT|ZTO|STO|YD|EMS|sf|jd|yt|zto|sto|yd|ems)[-_a-zA-Z0-9]*\d{4,}", message_lower))
        if (has_order_id or has_tracking_no) and not has_refund_action:
            scores["query_order"] = 2.0

        # “有个问题/帮个忙”等没有领域信息，应该澄清而不是误判为技术或订单。
        generic_request = re.fullmatch(
            r"(?:我)?(?:有个?|有一个)?问题[，, ]*(?:能)?(?:帮忙|帮个忙|帮)?(?:吗|么|呀)?[？?]?",
            message_lower.strip(),
        )
        if generic_request:
            return {}

        # “有问题我再问你”等收尾寒暄不是技术问题，应归 general。
        closing_request = re.match(
            r"^有.{0,3}问题.{0,4}(?:再|还|回头|以后).{0,3}(?:问|联系|找你|麻烦)",
            message_lower.strip(),
        )
        if closing_request:
            return {}

        # “这个问题我自己查/我回头再查”等自助解决、收尾语不是技术问题，应归 general。
        self_service_request = re.match(
            r"^这个?问题.{0,6}(?:自己|我来|我查|查查|回头|再说|百度|搜索)",
            message_lower.strip(),
        )
        if self_service_request:
            return {}

        for intent, levels in self.patterns.items():
            score = 0.0

            # 高权重关键词
            for keyword in levels.get("high", []):
                if keyword.lower() in message_lower and not self._is_keyword_negated(message, keyword):
                    score += 2.0

            # 中权重关键词
            for keyword in levels.get("medium", []):
                if keyword.lower() in message_lower and not self._is_keyword_negated(message, keyword):
                    score += 1.0

            # 低权重关键词
            for keyword in levels.get("low", []):
                # 单字"退"仅在与 钱/款/货/单/换 组合时才算退款语义，
                # 避免"闪退/退休/后退"等非退款词被误判为 refund。
                if keyword == "退" and not re.search(r"退[钱款货单换]", message):
                    continue
                # "差价"中的"差"不是负面情绪，避免"退差价"被误判为 complaint。
                if keyword == "差" and "差价" in message:
                    continue
                if keyword.lower() in message_lower and not self._is_keyword_negated(message, keyword):
                    score += 0.3

            if score > 0:
                scores[intent] = scores.get(intent, 0.0) + score

        # 售后/质量问题 + 负面情绪词 → 投诉倾向加权：
        # "你们这售后真糟糕/这质量真不行"等虽含售后/质量（refund 词），
        # 但用户是在抱怨而非申请退款，叠加负面情绪后应归 complaint。
        # 注意仅对"情绪词"生效，纯陈述（如"有质量问题要退货"）不触发。
        if any(kw in message_lower for kw in ("售后", "质量问题")):
            negative_sentiment = ("糟糕", "太差", "很差", "太烂", "不满", "敷衍", "不行", "垃圾", "差评", "气死", "失望", "坑", "烂", "无语", "火大")
            if any(w in message_lower for w in negative_sentiment):
                scores["complaint"] = scores.get("complaint", 0.0) + 2.0

        # ⑦ complaint 强情绪词加权：complaint 在 INTENT_PATTERNS 中排最后，
        # 与发货/退货/物流/客服等高分业务词打平时永远输给 dict 顺序靠前的意图。
        # 命中强情绪词（非"投诉/差评"等动作词，避免压过转人工）即补权，让投诉意图胜出。
        strong_emotion = ("气死", "太气人", "垃圾", "骗子", "真服了", "坑惨", "火大", "气愤", "失望", "敷衍", "打不通", "上当", "坑爹", "骗", "生气", "不满", "无语", "不讲信用")
        if any(w in message_lower for w in strong_emotion):
            scores["complaint"] = scores.get("complaint", 0.0) + 2.0

        # 明确转人工（转人工/找客服/真人）优先于退款等业务动作，
        # 避免「转人工处理退款」被 refund 高分压过。
        has_human_action = any(
            kw in message_lower for kw in ("转人工", "找客服", "真人", "人工客服")
        )

        # 当用户明确说退款/退货时，业务动作优先于查单（即使含订单号）。
        # 但若同时要转人工，则转人工优先，退款不再加权。
        if has_refund_action and not has_human_action:
            scores["refund"] = scores.get("refund", 0.0) + 3.0
            # 退款动作存在时，降低订单号对查单意图的加分，避免压过退款
            if scores.get("query_order", 0) > 0:
                scores["query_order"] = min(scores["query_order"], 1.0)

        # 服务时间/联系方式问询（几点/下班/热线/电话/号码）不是转人工诉求：
        # 「你们的客服几点下班」「客服热线怎么打」「客服电话打不通」只是
        # 问信息或吐槽，不应被判为 human。
        # 仅当消息不含强转人工词（人工/真人/转/领导/接人/找客服/在线客服）时降权，
        # 避免「人工客服几点上班」「在线客服在吗」等真实转人工场景被误伤。
        if scores.get("human", 0) > 0 and self._is_service_info_query(message):
            scores["human"] = max(scores["human"] - 2.0, 0.0)

        return scores

    def _is_service_info_query(self, message: str) -> bool:
        """判断消息是否为服务时间/联系方式问询且不含强转人工诉求。"""
        info_words = ("几点", "下班", "上班", "什么时候", "时间", "热线", "电话", "号码", "邮箱")
        strong_human = ("人工", "真人", "转", "领导", "接人", "找客服", "在线客服")
        has_info = any(kw in message for kw in info_words)
        has_strong = any(kw in message for kw in strong_human)
        return has_info and not has_strong

    def _is_keyword_negated(self, message: str, keyword: str) -> bool:
        """判断某个关键词在消息中是否被否定词修饰。
        否定词紧邻关键词（或仅隔助词/代词）时才生效，
        避免"我不要了退钱"中"不要"误伤"退钱"。"""
        idx = message.find(keyword)
        if idx <= 0:
            return False

        # 如果当前关键词是某个更长关键词的子串，且更长关键词在消息中出现并被否定，
        # 则当前关键词也视为被否定（如"转人工"被否定时，"人工"也算被否定）。
        all_keywords = set()
        for levels in self.patterns.values():
            for level_words in levels.values():
                all_keywords.update(level_words)
        for longer in all_keywords:
            if longer != keyword and keyword in longer and longer in message:
                longer_idx = message.find(longer)
                # 确保当前关键词的出现位置在更长关键词内部
                if longer_idx <= idx < longer_idx + len(longer):
                    if self._is_keyword_negated(message, longer):
                        return True

        # 助词/代词：否定词与关键词之间仅隔这些字时，仍视为否定修饰
        particles = {"要", "给", "想", "来", "去", "能", "可以", "我", "他", "她", "的", "地"}
        # 收集所有已知关键词，用于排除"否定词+关键词"本身就是正向词的情况
        all_known_keywords = set()
        for levels in self.patterns.values():
            for level_words in levels.values():
                all_known_keywords.update(level_words)

        # 检查关键词前 6 个字符窗口（允许否定词与关键词间隔多个助词）
        window = message[max(0, idx - 6):idx]
        window_start = max(0, idx - 6)
        for neg in NEGATION_WORDS:
            neg_pos = window.rfind(neg)
            if neg_pos < 0:
                continue
            neg_end = window_start + neg_pos + len(neg)
            gap = message[neg_end:idx]
            # 否定词与关键词之间的字符必须全部是助词
            if gap and not all(ch in particles for ch in gap):
                continue
            # 否定词后紧跟的字符
            after_neg = message[neg_end:neg_end + 1] if neg_end < len(message) else ""
            # "否定词+已知正向词"（如不合适、不想要、不工作）不算否定
            if after_neg and any(kw.startswith(neg + after_neg) for kw in all_known_keywords):
                continue
            # "有没有" 中的 "没有" 不算否定
            if neg == "没有":
                no_start = window_start + neg_pos
                if no_start >= 1 and message[no_start - 1:no_start] == "有":
                    continue
            return True
        return False

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

    def _is_question_style(self, message: str) -> bool:
        """判断消息是否含明确疑问句式（假 general 的 LLM 复审开关）。

        仅匹配强疑问标记，避免"在吗/有人吗"等纯寒暄也触发模型调用。
        """
        question_markers = ("怎么", "如何", "为什么", "为啥", "怎样", "咋", "能不能", "能否", "什么", "哪个", "哪里", "多少", "几点")
        return any(m in message for m in question_markers)

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
