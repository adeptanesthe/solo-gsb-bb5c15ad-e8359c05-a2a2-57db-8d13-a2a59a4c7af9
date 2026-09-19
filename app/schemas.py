"""请求 / 响应的数据模型与输入校验。"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

# 尺寸上限，防止明显错误的输入（单位通常为毫米）
MAX_DIMENSION = 100_000.0
MAX_ITEM_TYPES = 100
MAX_QUANTITY_PER_ITEM = 10_000
MAX_TOTAL_PIECES = 100_000


class StockSpec(BaseModel):
    """原纸尺寸。"""

    width: float = Field(gt=0, le=MAX_DIMENSION, description="原纸宽度")
    height: float = Field(gt=0, le=MAX_DIMENSION, description="原纸高度")


class ItemSpec(BaseModel):
    """一种成品（尺寸 + 数量）。"""

    id: str | None = Field(default=None, min_length=1, max_length=64,
                           description="成品标识，缺省自动生成 item_1、item_2…")
    width: float = Field(gt=0, le=MAX_DIMENSION, description="成品宽度")
    height: float = Field(gt=0, le=MAX_DIMENSION, description="成品高度")
    quantity: int = Field(ge=1, le=MAX_QUANTITY_PER_ITEM, description="需求数量")
    rotatable: bool = Field(default=True, description="是否允许旋转 90° 摆放")


class PlanRequest(BaseModel):
    """POST /plans 请求体。"""

    stock: StockSpec
    kerf: float = Field(ge=0, le=1000, description="切缝宽度（每刀消耗的材料宽度）")
    items: list[ItemSpec] = Field(min_length=1, max_length=MAX_ITEM_TYPES)

    @model_validator(mode="after")
    def check_consistency(self) -> "PlanRequest":
        ids = [item.id for item in self.items if item.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("成品 id 不能重复")
        total = sum(item.quantity for item in self.items)
        if total > MAX_TOTAL_PIECES:
            raise ValueError(f"成品总数量过大（{total} 件），单次最多 {MAX_TOTAL_PIECES} 件")
        if self.kerf >= min(self.stock.width, self.stock.height):
            raise ValueError("切缝宽度必须小于原纸的宽和高，否则无法下刀分切")
        return self


class Placement(BaseModel):
    """一件成品的落位（坐标系：原纸左下角为原点，x 向右，y 向上）。"""

    item_id: str
    x: float
    y: float
    width: float
    height: float
    rotated: bool


class SheetPlan(BaseModel):
    """一张原纸的排版结果。"""

    sheet_index: int
    width: float
    height: float
    utilization: float = Field(description="面积利用率 = 成品总面积 / 原纸面积")
    placements: list[Placement]
    tree: dict = Field(description="二叉切割树，与 placements 坐标一致")


class PlanSummary(BaseModel):
    sheets_used: int
    total_pieces: int
    overall_utilization: float


class PlanResponse(BaseModel):
    stock: StockSpec
    kerf: float
    sheets: list[SheetPlan]
    summary: PlanSummary
