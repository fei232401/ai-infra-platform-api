from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Query

from ..config import get_settings
from .schemas import Page, PageMeta


@dataclass(frozen=True)
class PageParams:
    limit: int
    offset: int

    def build(self, items: list[Any], total: int) -> Page[Any]:
        meta = PageMeta(
            limit=self.limit,
            offset=self.offset,
            total=total,
            count=len(items),
            has_more=self.offset + len(items) < total,
        )
        return Page(items=items, meta=meta)


def page_params(
    limit: Annotated[int | None, Query(ge=1, description="每页条数")] = None,
    offset: Annotated[int, Query(ge=0, description="偏移量")] = 0,
) -> PageParams:
    settings = get_settings()
    resolved = settings.default_page_size if limit is None else limit
    if resolved > settings.max_page_size:
        resolved = settings.max_page_size
    return PageParams(limit=resolved, offset=offset)


PageDep = Annotated[PageParams, Depends(page_params)]
