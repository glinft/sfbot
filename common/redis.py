import redis
from redis.commands.search.query import Query
import numpy as np
from typing import List

class RedisSingleton:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance.redis = redis.Redis(*args, **kwargs)
        return cls._instance

    def create_hybrid_field(self, orgid: str, field_name: str, value: str) -> str:
        return f'(@orgid:{orgid} @{field_name}:"{value}")'

    def create_hybrid_field2(self, orgid: str, chatbotid: str, userflag: str, field_name: str, value: str) -> str:
        filter = '[1 1]'
        if userflag == 'internal':
            filter = '[0 1]'
        return f'(@orgid:{orgid} @chatbots:{{ {chatbotid} }} @public:{filter} @{field_name}:"{value}")'

    def ft_search(
        self,
        embedded_query,
        index_name: str = "sflow-index",
        vector_field: str = "text_vector",
        return_fields: list = ["id", "orgid", "category", "vector_score"],
        hybrid_fields: str = "*",
        k: int = 3,
    ) -> List[dict]:
        base_query = f'{hybrid_fields}=>[KNN {k} @{vector_field} $vector AS vector_score]'
        query = (
            Query(base_query)
               .return_fields(*return_fields)
               .sort_by("vector_score")
               .paging(0, k)
               .dialect(2)
        )
        params_dict = {"vector": np.array(embedded_query).astype(dtype=np.float32).tobytes()}
        results = self._instance.redis.ft(index_name).search(query, params_dict)
        return results.docs
