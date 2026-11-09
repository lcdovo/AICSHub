"""隔离GPU镜像内的小图验证，禁止CPU执行器回退。"""
import json
import numpy as np
import torch
from onnx import helper, TensorProto
import onnxruntime as ort


model = helper.make_model(helper.make_graph(
    [helper.make_node("MatMul", ["a", "b"], ["out"])], "gpu-smoke",
    [helper.make_tensor_value_info("a", TensorProto.FLOAT, [2,2]),
     helper.make_tensor_value_info("b", TensorProto.FLOAT, [2,2])],
    [helper.make_tensor_value_info("out", TensorProto.FLOAT, [2,2])]),
    opset_imports=[helper.make_opsetid("", 17)])
model.ir_version = 9
options = ort.SessionOptions()
options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
session = ort.InferenceSession(model.SerializeToString(), sess_options=options,
    providers=["CUDAExecutionProvider"])
session.disable_fallback()
if session.get_providers()[0] != "CUDAExecutionProvider":
    raise RuntimeError("cuda_provider_not_active")
value = np.ones((2,2), dtype=np.float32)
result = session.run(None, {"a":value, "b":value})[0]
if not np.array_equal(result, np.full((2,2), 2, dtype=np.float32)):
    raise RuntimeError("cuda_smoke_wrong_value")
print(json.dumps({"status":"PASS_TINY_GRAPH_ONLY", "ort":ort.__version__,
    "providers":session.get_providers(), "cpu_fallback_disabled":True,
    "torch_cuda":torch.version.cuda, "model_inference_verified":False}))
