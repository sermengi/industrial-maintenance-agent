from __future__ import annotations

from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType[list[float]]):
    cache_ok = True

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension

    def get_col_spec(self, **_kwargs: object) -> str:
        return f"vector({self.dimension})"
