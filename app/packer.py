"""铡刀式（guillotine）裁纸排版引擎。

坐标系约定：原纸左下角为 (0, 0)，x 轴向右，y 轴向上，单位与输入一致（通常为毫米）。

切缝（kerf）模型：
- 每一刀贯穿当前子矩形，并消耗 kerf 宽度的材料；
- 在切割树中，切缝体现为两个子区域之间的缝隙 [position, position + kerf]；
- 成品之间的间距因此始终 >= kerf，成品不会重叠、不会越界。

边缘修整（snap）：当余料在某一方向上的宽度不足一刀（0 < 余量 <= kerf）时，
无法再下刀分切，该细条作为修整余量并入成品所在的格子（cell）。
此时成品叶子的坐标/尺寸仍是成品本身（与 placements 完全一致），
其 cell 通过可选字段 ``cell`` 单独标注，保证切割树仍然严丝合缝地平铺整张原纸。
"""

from __future__ import annotations

from dataclasses import dataclass, field

EPS = 1e-6
"""浮点比较容差。"""


class ItemTooLargeError(ValueError):
    """单件成品在两个朝向上都放不进原纸。"""


@dataclass(frozen=True)
class Piece:
    """待排的一件成品。"""

    item_id: str
    width: float
    height: float
    rotatable: bool = True


@dataclass
class Node:
    """切割树节点。

    - ``width``/``height`` 是节点覆盖的区域（cell），切割节点与废料叶子即实际矩形；
    - 成品叶子的实际成品尺寸为 ``item_width``/``item_height``（位于 cell 左下角），
      仅当存在边缘修整余量时二者才不同（差值 <= kerf）。
    """

    x: float
    y: float
    width: float
    height: float
    kind: str = "free"  # free(空闲，最终即 waste) | product | cut
    item_id: str | None = None
    rotated: bool = False
    item_width: float = 0.0
    item_height: float = 0.0
    direction: str | None = None  # cut 节点: "vertical"(竖刀，分左右) | "horizontal"(横刀，分上下)
    position: float | None = None  # 刀缝起点坐标；vertical 为 x，horizontal 为 y
    children: list["Node"] = field(default_factory=list)

    @property
    def is_free(self) -> bool:
        return self.kind == "free"


def _split(node: Node, pw: float, ph: float, kerf: float,
           item_id: str, rotated: bool) -> None:
    """在空闲叶子 ``node`` 的左下角放置 pw×ph 的成品，并按铡刀规则切分剩余空间。

    每一刀都贯穿当前子矩形；切缝宽度 kerf 从余料中扣除。
    """
    lw = node.width - pw   # x 方向余量
    lh = node.height - ph  # y 方向余量
    cut_v = lw > kerf + EPS   # 余量足够下竖刀（分出右侧余料）
    cut_h = lh > kerf + EPS   # 余量足够下横刀（分出上方余料）

    if not cut_v and not cut_h:
        # 两个方向都无需（或无法）下刀：余量 <= kerf 的细条并入 cell 作为修整余量
        node.kind = "product"
        node.item_id = item_id
        node.rotated = rotated
        node.item_width = pw
        node.item_height = ph
        return

    if cut_v and cut_h:
        # 两个方向都要切：让面积较大的余料成为贯通长条，后续更好利用
        if lw * node.height >= lh * node.width:
            _cut_vertical(node, pw, ph, kerf, item_id, rotated)
        else:
            _cut_horizontal(node, pw, ph, kerf, item_id, rotated)
    elif cut_v:
        _cut_vertical(node, pw, ph, kerf, item_id, rotated)
    else:
        _cut_horizontal(node, pw, ph, kerf, item_id, rotated)


def _cut_vertical(node: Node, pw: float, ph: float, kerf: float,
                  item_id: str, rotated: bool) -> None:
    """在 x = node.x + pw 处下竖刀，贯穿当前子矩形的整个高度。"""
    px = node.x + pw
    left = Node(node.x, node.y, pw, node.height)
    right = Node(px + kerf, node.y, node.width - pw - kerf, node.height)
    node.kind = "cut"
    node.direction = "vertical"
    node.position = px
    node.children = [left, right]
    _split(left, pw, ph, kerf, item_id, rotated)


def _cut_horizontal(node: Node, pw: float, ph: float, kerf: float,
                    item_id: str, rotated: bool) -> None:
    """在 y = node.y + ph 处下横刀，贯穿当前子矩形的整个宽度。"""
    py = node.y + ph
    bottom = Node(node.x, node.y, node.width, ph)
    top = Node(node.x, py + kerf, node.width, node.height - ph - kerf)
    node.kind = "cut"
    node.direction = "horizontal"
    node.position = py
    node.children = [bottom, top]
    _split(bottom, pw, ph, kerf, item_id, rotated)


class Sheet:
    """一张原纸的排版状态。"""

    def __init__(self, width: float, height: float):
        self.width = width
        self.height = height
        self.root = Node(0.0, 0.0, width, height)
        self.free: list[Node] = [self.root]
        self.used_area = 0.0

    def best_rect(self, pw: float, ph: float) -> int | None:
        """BSSF（Best Short Side Fit）：返回最紧凑的空闲格子下标，放不下返回 None。"""
        best_idx: int | None = None
        best_key: tuple[float, float] | None = None
        for i, rect in enumerate(self.free):
            lw = rect.width - pw
            lh = rect.height - ph
            if lw < -EPS or lh < -EPS:
                continue
            key = (min(lw, lh), max(lw, lh))
            if best_key is None or key < best_key:
                best_key = key
                best_idx = i
        return best_idx

    def place(self, rect_idx: int, piece: Piece, pw: float, ph: float,
              rotated: bool, kerf: float) -> None:
        rect = self.free.pop(rect_idx)
        _split(rect, pw, ph, kerf, piece.item_id, rotated)
        # 收集切分后新产生的空闲叶子
        stack = list(rect.children)
        while stack:
            node = stack.pop()
            if node.is_free:
                self.free.append(node)
            else:
                stack.extend(node.children)
        self.used_area += pw * ph

    @property
    def utilization(self) -> float:
        return self.used_area / (self.width * self.height)


def _orientations(piece: Piece) -> list[tuple[float, float, bool]]:
    """返回 (放置宽, 放置高, 是否旋转) 列表，优先不旋转。"""
    result = [(piece.width, piece.height, False)]
    if piece.rotatable and abs(piece.width - piece.height) > EPS:
        result.append((piece.height, piece.width, True))
    return result


def _fits_stock(piece: Piece, stock_w: float, stock_h: float) -> bool:
    return any(pw <= stock_w + EPS and ph <= stock_h + EPS
               for pw, ph, _ in _orientations(piece))


def pack(stock_w: float, stock_h: float, kerf: float,
         pieces: list[Piece]) -> list[Sheet]:
    """把全部成品排进若干张原纸，返回 Sheet 列表。

    规则：成品按面积从大到小排序；逐件在所有已开纸张的空闲格子中
    做 BSSF 选位（两种朝向都尝试）；都放不下则开新纸。
    """
    if kerf < 0:
        raise ValueError("kerf 不能为负数")

    # 预检：单件在两个朝向上都放不进原纸 -> 明确报错
    for piece in pieces:
        if not _fits_stock(piece, stock_w, stock_h):
            raise ItemTooLargeError(
                f"成品 '{piece.item_id}'（{piece.width}×{piece.height}）"
                f"在旋转和未旋转两种朝向下都放不进原纸（{stock_w}×{stock_h}），"
                f"请调整成品尺寸或更换更大的原纸。"
            )

    # 面积降序（同面积取长边降序），提升大料优先的填充率
    ordered = sorted(
        pieces,
        key=lambda p: (p.width * p.height, max(p.width, p.height)),
        reverse=True,
    )

    sheets: list[Sheet] = []
    for piece in ordered:
        best: tuple[tuple[float, float], Sheet, int, float, float, bool] | None = None
        for sheet in sheets:
            for pw, ph, rotated in _orientations(piece):
                idx = sheet.best_rect(pw, ph)
                if idx is None:
                    continue
                rect = sheet.free[idx]
                key = (min(rect.width - pw, rect.height - ph),
                       max(rect.width - pw, rect.height - ph))
                if best is None or key < best[0]:
                    best = (key, sheet, idx, pw, ph, rotated)
        if best is None:
            # 所有纸都放不下，开一张新纸（预检已保证新纸一定放得下）
            sheet = Sheet(stock_w, stock_h)
            sheets.append(sheet)
            for pw, ph, rotated in _orientations(piece):
                idx = sheet.best_rect(pw, ph)
                if idx is not None:
                    best = ((0.0, 0.0), sheet, idx, pw, ph, rotated)
                    break
        assert best is not None  # 预检保证不会发生
        _, sheet, idx, pw, ph, rotated = best
        sheet.place(idx, piece, pw, ph, rotated, kerf)

    return sheets


def _r(value: float) -> float:
    """输出坐标统一保留 3 位小数。"""
    return round(value, 3)


def node_to_dict(node: Node, kerf: float) -> dict:
    """把切割树序列化为 JSON 友好的字典。"""
    base = {"x": _r(node.x), "y": _r(node.y),
            "width": _r(node.width), "height": _r(node.height)}
    if node.kind == "cut":
        return {
            "type": "cut",
            "direction": node.direction,
            "position": _r(node.position),
            **base,
            "children": [node_to_dict(child, kerf) for child in node.children],
        }
    if node.kind == "product":
        data = {
            "type": "product",
            "item_id": node.item_id,
            "rotated": node.rotated,
            **base,
            "width": _r(node.item_width),
            "height": _r(node.item_height),
        }
        # 边缘修整余量：cell 大于成品本身时单独标注
        if (node.width - node.item_width > EPS
                or node.height - node.item_height > EPS):
            data["cell"] = base
        return data
    # 未使用的空闲区域即废料
    return {"type": "waste", **base}


def collect_placements(root: Node) -> list[dict]:
    """从切割树收集成品清单（保证与树严格一致）。"""
    placements: list[dict] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.kind == "product":
            placements.append({
                "item_id": node.item_id,
                "x": _r(node.x),
                "y": _r(node.y),
                "width": _r(node.item_width),
                "height": _r(node.item_height),
                "rotated": node.rotated,
            })
        else:
            stack.extend(node.children)
    return placements


def validate_tree(node: Node, kerf: float, tol: float = 1e-6) -> None:
    """内部自检：切割树必须严丝合缝地平铺（0,0,stock_w,stock_h）。

    - cut 节点：一刀贯穿当前子矩形，两个子区域之间恰为 kerf 宽的刀缝；
    - 叶子：product 的 cell 覆盖其区域，成品位于 cell 左下角且不超出 cell；
    - 任何区域不得重叠、不得越界。
    """
    if node.kind == "cut":
        assert node.direction in ("vertical", "horizontal")
        assert len(node.children) == 2
        first, second = node.children
        if node.direction == "vertical":
            pos = node.position
            assert node.x + EPS < pos, "竖刀位置必须在子矩形内部"
            assert pos + kerf <= node.x + node.width + EPS, "刀缝不得超出子矩形"
            _check_region(first, node.x, node.y, pos - node.x, node.height, tol)
            _check_region(second, pos + kerf, node.y,
                          node.x + node.width - pos - kerf, node.height, tol)
        else:
            pos = node.position
            assert node.y + EPS < pos, "横刀位置必须在子矩形内部"
            assert pos + kerf <= node.y + node.height + EPS, "刀缝不得超出子矩形"
            _check_region(first, node.x, node.y, node.width, pos - node.y, tol)
            _check_region(second, node.x, pos + kerf,
                          node.width, node.y + node.height - pos - kerf, tol)
        for child in node.children:
            validate_tree(child, kerf, tol)
    elif node.kind == "product":
        assert node.item_width <= node.width + tol
        assert node.item_height <= node.height + tol
        assert node.item_width > 0 and node.item_height > 0
    else:
        assert node.kind == "free"


def _check_region(node: Node, x: float, y: float,
                  w: float, h: float, tol: float) -> None:
    assert w > tol and h > tol, "子区域尺寸必须为正"
    assert abs(node.x - x) <= tol and abs(node.y - y) <= tol
    assert abs(node.width - w) <= tol and abs(node.height - h) <= tol
