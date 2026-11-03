"""M3新collection：指纹命名，独立dense与应用层BM25融合；绝不改旧collection。"""

import json
from app.config.config import settings
from app.services.embedding_service import validate_vector


class VersionedVectorIndex:
    def __init__(self, host: str, port: int, prefix: str | None = None) -> None:
        from pymilvus import MilvusClient

        self.client = MilvusClient(uri=f"http://{host}:{port}", timeout=5)
        self.prefix = prefix or settings.COLLECTION_NAME + "_m3"

    def collection(self, fingerprint: str) -> str:
        return self.prefix + "_" + fingerprint[:24]

    def ensure(self, fingerprint: str, dim: int) -> str:
        from pymilvus import DataType

        name = self.collection(fingerprint)
        if not self.client.has_collection(name):
            schema = self.client.create_schema(
                auto_id=False, enable_dynamic_field=False
            )
            schema.add_field(
                field_name="id",
                datatype=DataType.VARCHAR,
                is_primary=True,
                max_length=100,
            )
            schema.add_field(
                field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=dim
            )
            schema.add_field(
                field_name="content", datatype=DataType.VARCHAR, max_length=65535
            )
            schema.add_field(
                field_name="version_id", datatype=DataType.VARCHAR, max_length=36
            )
            schema.add_field(
                field_name="fingerprint", datatype=DataType.VARCHAR, max_length=64
            )
            indices = self.client.prepare_index_params()
            indices.add_index(
                field_name="embedding", index_type="FLAT", metric_type="COSINE"
            )
            self.client.create_collection(
                collection_name=name, schema=schema, index_params=indices
            )
        description = self.client.describe_collection(name)
        fields = {f["name"]: f for f in description["fields"]}
        if int(fields["embedding"]["params"]["dim"]) != dim:
            raise ValueError("index_dimension_mismatch")
        return name

    def write(self, version_id, chunks, vectors, fingerprint, dim):
        if len(chunks) != len(vectors):
            raise ValueError("index_count_mismatch")
        name = self.ensure(fingerprint, dim)
        rows = [
            {
                "id": c["id"],
                "content": c["content"],
                "embedding": validate_vector(v, dim),
                "version_id": version_id,
                "fingerprint": fingerprint,
            }
            for c, v in zip(chunks, vectors)
        ]
        result = self.client.upsert(collection_name=name, data=rows)
        self.client.flush(collection_name=name)
        stored = self.client.query(
            collection_name=name,
            filter="version_id == " + json.dumps(version_id),
            output_fields=["id", "fingerprint"],
            consistency_level="Strong",
            limit=16384,
        )
        if {r["id"] for r in stored} != {r["id"] for r in rows} or any(
            r["fingerprint"] != fingerprint for r in stored
        ):
            raise RuntimeError("index_verification_failed")
        return result

    def search(self, vector, fingerprint, allowed, k):
        name = self.collection(fingerprint)
        if not self.client.has_collection(name):
            raise RuntimeError("index_missing")
        if not allowed:
            return []
        result = self.client.search(
            collection_name=name,
            data=[vector],
            anns_field="embedding",
            limit=k,
            filter="fingerprint == "
            + json.dumps(fingerprint)
            + " and id in "
            + json.dumps(allowed),
            output_fields=["id", "fingerprint"],
            consistency_level="Strong",
            search_params={"metric_type": "COSINE"},
        )
        return [{"id": r["id"], "dense_score": r["distance"]} for r in result[0]]
