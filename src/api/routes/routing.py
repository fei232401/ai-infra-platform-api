from fastapi import APIRouter

from ..deps import BackendServiceDep, RequireInfer
from ..schemas import CandidateListOut, CandidateOut

router = APIRouter(prefix="/api/v1/routing", tags=["routing"])


@router.get("/candidates", response_model=CandidateListOut, summary="候选实例打分排名")
async def list_candidates(
    service: BackendServiceDep,
    _auth: RequireInfer,
    model_name: str,
    policy: str = "weighted_random",
) -> CandidateListOut:
    ranked = await service.rank_candidates(model_name, policy)
    return CandidateListOut(
        model_name=model_name,
        policy=policy,
        ranked=[CandidateOut(**candidate.snapshot()) for candidate in ranked],
    )
