# 独立实验镜像，不替换业务镜像；安装与原CPU运行时同版本的GPU wheel。
FROM aicshub-onnx-base:pinned-64873a491e83
RUN python -m pip uninstall -y onnxruntime && python -m pip install --no-cache-dir --no-deps --retries 0 --timeout 30 --index-url https://pypi.org/simple onnxruntime-gpu==1.19.2
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 DO_NOT_TRACK=1
