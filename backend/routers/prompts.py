from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from supabase import Client

from backend.auth import CurrentUser, get_current_user
from backend.deps import get_db
from backend.schemas import PromptCreate, PromptOut, PromptUpdate, RefineRequest, RefineResponse
from mockup_generator.db import products_repo, prompts_repo
from mockup_generator.prompts import refine as refine_engine
from mockup_generator.prompts.refine import RefineFailed

router = APIRouter(prefix="/api", tags=["prompts"])


@router.get("/prompts", response_model=list[PromptOut])
def list_prompts(categoryid: str, user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    return [PromptOut(**vars(p)) for p in prompts_repo.list_by_category(db, categoryid)]


@router.post("/prompts", response_model=PromptOut, status_code=status.HTTP_201_CREATED)
def create_prompt(payload: PromptCreate, user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    p = prompts_repo.create(db, categoryid=payload.categoryid, label=payload.label,
                            body=payload.body, is_default=payload.is_default, updated_by=user.id)
    return PromptOut(**vars(p))


@router.post("/prompts/refine", response_model=RefineResponse)
def refine(payload: RefineRequest,
           user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    if not payload.instruction or not payload.instruction.strip():
        raise HTTPException(status_code=400, detail="Instruction is empty.")
    category_name = None
    if payload.categoryid:
        category_name = next(
            (name for cid, name in products_repo.list_categories(db) if cid == payload.categoryid),
            None,
        )
    try:
        refined = refine_engine.refine_prompt(payload.instruction, category_name, kind=payload.kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RefineFailed as exc:
        raise HTTPException(status_code=502, detail="Refine produced no text.") from exc
    return RefineResponse(refined=refined)


@router.patch("/prompts/{prompt_id}", response_model=PromptOut)
def update_prompt(prompt_id: int, payload: PromptUpdate,
                  user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    p = prompts_repo.update(db, prompt_id, label=payload.label, body=payload.body,
                            is_default=payload.is_default, updated_by=user.id)
    return PromptOut(**vars(p))


@router.delete("/prompts/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_prompt(prompt_id: int, user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_db)):
    prompts_repo.delete(db, prompt_id)
