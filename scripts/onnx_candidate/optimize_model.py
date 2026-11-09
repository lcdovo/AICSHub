"""仅对已校验的本地FP16 ONNX作离线图融合，输出独立候选与完整hash。"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def validate_architecture(config: dict) -> tuple[int, int]:
    if (config.get("model_type") != "xlm-roberta"
            or config.get("architectures") != ["XLMRobertaForSequenceClassification"]
            or config.get("num_hidden_layers") != 24
            or config.get("num_attention_heads") != 16 or config.get("hidden_size") != 1024):
        raise ValueError("unverified_fusion_architecture")
    return 16, 1024


def validate_fusions(counts: dict) -> dict:
    values = [counts.get(name, 0) for name in ("Attention", "MultiHeadAttention")]
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("invalid_fusion_counts")
    attention = sum(values)
    return {"fused_attention_layers": attention, "required_attention_layers": 24,
            "attention_fusion_complete": attention == 24,
            "status": "FUSED_NOT_VALIDATED" if attention == 24 else "PARTIAL_FUSION_NOT_ACCEPTED",
            "whole_m5_pass": False, "business_switch_approved": False}


def main() -> None:
    import onnx
    from onnxruntime.transformers.optimizer import optimize_by_fusion
    from onnxruntime.transformers.fusion_options import FusionOptions

    source, output = Path("/onnx-model"), Path("/onnx-output/model")
    if output.exists():
        raise ValueError("fusion_output_already_exists")
    heads, hidden = validate_architecture(json.loads((source / "config.json").read_bytes()))
    output.mkdir()
    began = time.monotonic()
    options = FusionOptions("bert")
    # 不启用近似GELU、量化、截断或静态batch；仅实际图融合。
    options.enable_gelu_approximation = False
    options.use_multi_head_attention = True
    # 权重外置后protobuf仅含图结构，可在相同内存上限内尝试符号形状推断。
    options.enable_shape_inference = True
    options.enable_embed_layer_norm = False
    # 独立子进程将大权重存为外部文件，退出归还protobuf内存；融合阶段只载图与按需小张量。
    subprocess.run([sys.executable, __file__, "--externalize", str(source / "model.onnx"), str(output)],
                   check=True, timeout=180)
    os.chdir(output)
    graph = onnx.load(str(output / "source.onnx"), load_external_data=False)
    print(json.dumps({"stage": "fusion_start", "options": vars(options)}), flush=True)
    model = optimize_by_fusion(graph, model_type="bert", num_heads=heads, hidden_size=hidden,
                               optimization_options=options)
    counts = model.get_fused_operator_statistics()
    node_counts = dict(Counter(node.op_type for node in model.model.graph.node))
    assessment = validate_fusions(counts)
    model.save_model_to_file(str(output / "model.onnx"), use_external_data_format=True)
    del model
    for path in source.iterdir():
        if path.is_file() and (path.suffix == ".json" or path.name == "sentencepiece.bpe.model"):
            shutil.copyfile(path, output / path.name)
    onnx.checker.check_model(str(output / "model.onnx"))
    files = []
    for path in sorted(output.iterdir()):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                    digest.update(block)
            files.append({"path": path.name, "bytes": path.stat().st_size, "sha256": digest.hexdigest()})
    report = {**assessment, "seconds": time.monotonic() - began,
              "fused_operator_counts": counts, "node_counts": node_counts,
              "options": vars(options), "ort_opt_level": 0, "dtype": "fp16", "device": "cuda",
              "source_revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e", "files": files,
              "numeric_validation_executed": False, "pressure_status": "NOT_RUN"}
    Path("/onnx-output/fusion.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    # check_export只接受明确标记未验的完整产物；完整融合情况单独保留，不冒称实际推理通过。
    Path("/onnx-output/export.json").write_text(json.dumps({**report, "status": "EXPORTED_NOT_VALIDATED"}, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "counts": counts, "seconds": report["seconds"]}), flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--externalize":
        import onnx
        source_path, output_path = Path(sys.argv[2]), Path(sys.argv[3])
        graph = onnx.load(str(source_path))
        onnx.save_model(graph, str(output_path / "source.onnx"), save_as_external_data=True,
                        all_tensors_to_one_file=True, location="original.weights.bin", size_threshold=1024)
        print(json.dumps({"stage": "external_weights_saved"}), flush=True)
    else:
        main()
