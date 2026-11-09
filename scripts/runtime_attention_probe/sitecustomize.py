"""仅挂载到隔离候选，输出已加载模型的实现/精度，不输出输入或权重。"""
import json

from infinity_emb.transformer.crossencoder.torch import CrossEncoderPatched

_original_init = CrossEncoderPatched.__init__


def _probe_init(self, *args, **kwargs):
    _original_init(self, *args, **kwargs)
    model = self.model
    print("AICSHUB_ATTENTION_RUNTIME="+json.dumps({
        "attention_implementation":getattr(model.config, "_attn_implementation", None),
        "dtype":str(model.dtype),
        "attention_classes":sorted({type(module).__name__ for module in model.modules()
            if "SelfAttention" in type(module).__name__}),
    }), flush=True)


CrossEncoderPatched.__init__ = _probe_init
