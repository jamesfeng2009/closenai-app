"""protobuf 生成物的存放地（**除本文件外全是生成物，勿手改**）。

生成入口＝`scripts/gen_proto.py`；改了 `contracts/proto/*.proto` 必须重跑它，
否则 `--check` 闸会红（逐字节比对）。

为什么要动 sys.path：四个 .proto 平铺在 contracts/proto/，protoc 的 python 插件
就把生成物也平铺在这里、并让彼此以**顶层名**互引（`import closenai_common_pb2`）。
V3 15 §2（`:34`）写明生成物不许手改 ⇒ 不改生成物的 import 语句，改成把本目录挂上路径。
"""
import pathlib
import sys

_HERE = str(pathlib.Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
