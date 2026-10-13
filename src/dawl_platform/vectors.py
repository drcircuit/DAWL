"""Exact, reversible token-to-input-embedding lookup; never contextual embeddings."""
import hashlib
from importlib.metadata import version
from pathlib import Path

import numpy as np

from dawl_node.common import read_json, sha256

LOOKUP_CONTRACT = "token-input-embedding-lookup/v1; no positions, contextual layers, pooling or rounding"


class EmbeddingTable:
    def __init__(self, path, descriptor, provider="cpu"):
        self.descriptor = descriptor
        if (descriptor["schema"] != "dawl.embedding-table/1" or descriptor["dtype"] != "float32-le"
                or descriptor["contract"] != LOOKUP_CONTRACT
                or len(descriptor["shape"]) != 2 or any(type(x) is not int or x <= 0 for x in descriptor["shape"])
                or descriptor["bytes"] != int(np.prod(descriptor["shape"])) * 4):
            raise ValueError("invalid embedding table contract")
        path = Path(path)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if path.stat().st_size != descriptor["bytes"] or digest != descriptor["sha256"]:
            raise ValueError("embedding table differs from pinned hash")
        self.matrix = np.memmap(path, dtype="<f4", mode="r", shape=tuple(descriptor["shape"]))
        if not np.isfinite(self.matrix).all():
            raise ValueError("embedding table contains nonfinite values")
        self.provider = provider
        self.session = None
        self.runtime = {"provider": "numpy-cpu", "numpy": np.__version__}
        if provider != "cpu":
            if provider != "directml":
                raise ValueError("unsupported vector provider")
            import onnx
            import onnxruntime as ort
            from onnx import helper, numpy_helper, TensorProto
            if "DmlExecutionProvider" not in ort.get_available_providers():
                raise ValueError("DirectML provider unavailable; no implicit CPU substitution")
            # Gather performs no floating-point arithmetic. Indices are int64, table bytes fp32.
            graph = helper.make_graph([helper.make_node("Gather", ["table", "ids"], ["vectors"], axis=0)],
                "DAWL exact lookup", [helper.make_tensor_value_info("ids", TensorProto.INT64, ["tokens"])],
                [helper.make_tensor_value_info("vectors", TensorProto.FLOAT, ["tokens", descriptor["shape"][1]])],
                [numpy_helper.from_array(np.asarray(self.matrix), "table")])
            model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)], ir_version=8)
            options = ort.SessionOptions()
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            options.intra_op_num_threads = 1
            options.enable_profiling = True
            options.profile_file_prefix = str(path.parent / "directml-profile")
            self.session = ort.InferenceSession(model.SerializeToString(), sess_options=options,
                providers=[("DmlExecutionProvider", {"device_id": 0}), "CPUExecutionProvider"])
            self.runtime = {"provider": "directml", "onnxruntime": ort.__version__, "onnx": onnx.__version__,
                            "numpy": np.__version__, "session_providers": self.session.get_providers()}

    def payload(self, ids):
        ids = np.asarray(ids)
        if ids.ndim != 1 or ids.dtype.kind not in "ui" or not 1 <= ids.size <= 16384:
            raise ValueError("invalid bounded token vector")
        if int(ids.min()) < 0 or int(ids.max()) >= self.matrix.shape[0]:
            raise ValueError("token outside embedding vocabulary")
        if self.session:
            value = self.session.run(["vectors"], {"ids": ids.astype(np.int64)})[0]
        else:
            value = self.matrix[ids]
        return value.astype("<f4", copy=False).tobytes(order="C")

    def finish_profile(self):
        if not self.session:
            return None
        path = Path(self.session.end_profiling())
        events = read_json(path)
        providers = {}
        for event in events:
            args = event.get("args", {})
            if args.get("provider") and args.get("op_name") == "Gather":
                providers[args["provider"]] = providers.get(args["provider"], 0) + 1
        if not providers.get("DmlExecutionProvider"):
            raise ValueError("profile does not establish GPU Gather execution")
        return {"profile_sha256": sha256(path.read_bytes()), "gather_events_by_provider": providers}
