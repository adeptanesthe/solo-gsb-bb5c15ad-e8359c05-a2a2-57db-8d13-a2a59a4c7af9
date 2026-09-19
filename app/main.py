"""FastAPI 应用入口：裁纸排版 API。"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .packer import (
    ItemTooLargeError,
    Piece,
    collect_placements,
    node_to_dict,
    pack,
    validate_tree,
)
from .schemas import (
    Placement,
    PlanRequest,
    PlanResponse,
    PlanSummary,
    SheetPlan,
)

app = FastAPI(
    title="裁纸排版 API",
    description=(
        "面向小型印刷店的铡刀式（guillotine）裁纸排版服务。\n\n"
        "- 坐标系：原纸左下角为 (0, 0)，x 向右，y 向上；\n"
        "- 每一刀贯穿当前子矩形，切缝（kerf）计入分割；\n"
        "- 成品可旋转 90°（可按成品关闭），不重叠、不越界。"
    ),
    version="1.0.0",
)


@app.get("/health", summary="健康检查")
def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/plans",
    response_model=PlanResponse,
    summary="生成裁纸排版方案",
    responses={
        200: {"description": "排版成功"},
        422: {"description": "输入尺寸/数量不合法，或成品在两个朝向下都放不进原纸"},
    },
)
def create_plan(request: PlanRequest) -> PlanResponse:
    # 展开数量为单件，未提供 id 的成品自动命名
    pieces: list[Piece] = []
    for index, item in enumerate(request.items, start=1):
        item_id = item.id or f"item_{index}"
        pieces.extend(
            Piece(item_id=item_id, width=item.width, height=item.height,
                  rotatable=item.rotatable)
            for _ in range(item.quantity)
        )

    try:
        sheets = pack(
            stock_w=request.stock.width,
            stock_h=request.stock.height,
            kerf=request.kerf,
            pieces=pieces,
        )
    except ItemTooLargeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    sheet_plans: list[SheetPlan] = []
    for sheet_index, sheet in enumerate(sheets):
        validate_tree(sheet.root, request.kerf)  # 内部自检，正常流程不会触发
        sheet_plans.append(
            SheetPlan(
                sheet_index=sheet_index,
                width=sheet.width,
                height=sheet.height,
                utilization=round(sheet.utilization, 4),
                placements=[Placement(**p) for p in collect_placements(sheet.root)],
                tree=node_to_dict(sheet.root, request.kerf),
            )
        )

    total_area = request.stock.width * request.stock.height
    used_area = sum(sheet.used_area for sheet in sheets)
    return PlanResponse(
        stock=request.stock,
        kerf=request.kerf,
        sheets=sheet_plans,
        summary=PlanSummary(
            sheets_used=len(sheets),
            total_pieces=len(pieces),
            overall_utilization=round(used_area / (total_area * len(sheets)), 4),
        ),
    )
