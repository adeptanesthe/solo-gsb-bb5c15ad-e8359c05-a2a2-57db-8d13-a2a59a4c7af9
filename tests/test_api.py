"""裁纸排版 API 测试：结构、约束与切割树一致性。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

TOL = 1e-2  # 输出保留 3 位小数后的校验容差


# ---------------------------------------------------------------------------
# 校验辅助：对 API 返回的 JSON 做独立校验
# ---------------------------------------------------------------------------

def tree_products(node: dict) -> list[dict]:
    """收集切割树中的全部成品叶子。"""
    if node["type"] == "product":
        return [node]
    if node["type"] == "waste":
        return []
    products: list[dict] = []
    for child in node["children"]:
        products.extend(tree_products(child))
    return products


def validate_tree(node: dict, kerf: float, region: tuple | None = None) -> None:
    """校验切割树严格平铺原纸：每刀贯穿当前子矩形，刀缝恰为 kerf 宽。"""
    if region is None:
        region = (node["x"], node["y"], node["width"], node["height"])
    rx, ry, rw, rh = region

    if node["type"] == "cut":
        assert node["x"] == pytest.approx(rx, abs=TOL)
        assert node["y"] == pytest.approx(ry, abs=TOL)
        assert node["width"] == pytest.approx(rw, abs=TOL)
        assert node["height"] == pytest.approx(rh, abs=TOL)
        assert len(node["children"]) == 2, "切割节点必须是二叉"
        first, second = node["children"]
        pos = node["position"]
        if node["direction"] == "vertical":
            assert rx < pos and pos + kerf <= rx + rw + TOL, "竖刀必须贯穿当前子矩形"
            validate_tree(first, kerf, (rx, ry, pos - rx, rh))
            validate_tree(second, kerf, (pos + kerf, ry, rx + rw - pos - kerf, rh))
        else:
            assert node["direction"] == "horizontal"
            assert ry < pos and pos + kerf <= ry + rh + TOL, "横刀必须贯穿当前子矩形"
            validate_tree(first, kerf, (rx, ry, rw, pos - ry))
            validate_tree(second, kerf, (rx, pos + kerf, rw, ry + rh - pos - kerf))
    elif node["type"] == "product":
        # 成品位于 cell 左下角；无 cell 字段时成品矩形即节点区域
        cell = node.get("cell", node)
        assert cell["x"] == pytest.approx(rx, abs=TOL)
        assert cell["y"] == pytest.approx(ry, abs=TOL)
        assert cell["width"] == pytest.approx(rw, abs=TOL)
        assert cell["height"] == pytest.approx(rh, abs=TOL)
        assert node["x"] == pytest.approx(cell["x"], abs=TOL)
        assert node["y"] == pytest.approx(cell["y"], abs=TOL)
        assert 0 < node["width"] <= cell["width"] + TOL
        assert 0 < node["height"] <= cell["height"] + TOL
        # 修整余量不得超过一个切缝宽
        assert cell["width"] - node["width"] <= kerf + TOL
        assert cell["height"] - node["height"] <= kerf + TOL
    else:
        assert node["type"] == "waste"
        assert node["x"] == pytest.approx(rx, abs=TOL)
        assert node["y"] == pytest.approx(ry, abs=TOL)
        assert node["width"] == pytest.approx(rw, abs=TOL)
        assert node["height"] == pytest.approx(rh, abs=TOL)


def validate_sheet(sheet: dict, stock_w: float, stock_h: float, kerf: float,
                   expected_items: dict[str, int]) -> None:
    """对单张纸做完整校验：树结构、坐标一致性、间距、边界、利用率。"""
    assert sheet["width"] == pytest.approx(stock_w)
    assert sheet["height"] == pytest.approx(stock_h)

    tree = sheet["tree"]
    assert tree["x"] == 0 and tree["y"] == 0
    assert tree["width"] == pytest.approx(stock_w)
    assert tree["height"] == pytest.approx(stock_h)
    validate_tree(tree, kerf)

    # 树中的成品叶子与 placements 完全一致（坐标、尺寸、旋转标记）
    leaves = sorted(
        (p["item_id"], p["x"], p["y"], p["width"], p["height"], p["rotated"])
        for p in tree_products(tree)
    )
    placed = sorted(
        (p["item_id"], p["x"], p["y"], p["width"], p["height"], p["rotated"])
        for p in sheet["placements"]
    )
    assert leaves == placed, "切割树与 placements 坐标不一致"

    # 成品不越界，且两两之间在 x 或 y 方向上至少间隔一个切缝宽
    placements = sheet["placements"]
    for p in placements:
        assert p["x"] >= -TOL and p["y"] >= -TOL
        assert p["x"] + p["width"] <= stock_w + TOL
        assert p["y"] + p["height"] <= stock_h + TOL
    for i, a in enumerate(placements):
        for b in placements[i + 1:]:
            sep_x = (a["x"] + a["width"] + kerf <= b["x"] + TOL
                     or b["x"] + b["width"] + kerf <= a["x"] + TOL)
            sep_y = (a["y"] + a["height"] + kerf <= b["y"] + TOL
                     or b["y"] + b["height"] + kerf <= a["y"] + TOL)
            assert sep_x or sep_y, f"成品间距不足切缝宽: {a} vs {b}"

    # 利用率 = 成品总面积 / 原纸面积
    used = sum(p["width"] * p["height"] for p in placements)
    assert sheet["utilization"] == pytest.approx(used / (stock_w * stock_h), abs=1e-3)

    # 每种成品的数量符合预期（空字典表示跳过数量校验）
    if expected_items:
        counts: dict[str, int] = {}
        for p in placements:
            counts[p["item_id"]] = counts.get(p["item_id"], 0) + 1
        for item_id, qty in expected_items.items():
            counts[item_id] = counts.get(item_id, 0) - qty
        assert all(v == 0 for v in counts.values()), f"成品数量不符: {counts}"


def make_plan(payload: dict) -> dict:
    response = client.post("/plans", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_basic_single_sheet():
    data = make_plan({
        "stock": {"width": 900, "height": 640},
        "kerf": 3,
        "items": [{"id": "A4", "width": 210, "height": 297, "quantity": 4}],
    })
    assert data["summary"]["sheets_used"] == 1
    assert data["summary"]["total_pieces"] == 4
    sheet = data["sheets"][0]
    validate_sheet(sheet, 900, 640, 3, {"A4": 4})
    assert sheet["utilization"] == pytest.approx(4 * 210 * 297 / (900 * 640), abs=1e-3)


def test_rotation_is_used_when_needed():
    # 290 > 220 竖放放不下，旋转后 290×210 可以放下
    data = make_plan({
        "stock": {"width": 300, "height": 220},
        "kerf": 3,
        "items": [{"id": "card", "width": 210, "height": 290, "quantity": 1}],
    })
    placement = data["sheets"][0]["placements"][0]
    assert placement["rotated"] is True
    assert placement["width"] == pytest.approx(290)
    assert placement["height"] == pytest.approx(210)
    validate_sheet(data["sheets"][0], 300, 220, 3, {"card": 1})


def test_rotation_disabled():
    data = make_plan({
        "stock": {"width": 300, "height": 220},
        "kerf": 3,
        "items": [{"id": "card", "width": 210, "height": 200, "quantity": 1,
                   "rotatable": False}],
    })
    placement = data["sheets"][0]["placements"][0]
    assert placement["rotated"] is False
    assert placement["width"] == pytest.approx(210)


def test_rotation_disabled_and_not_fitting_is_rejected():
    response = client.post("/plans", json={
        "stock": {"width": 300, "height": 220},
        "kerf": 3,
        "items": [{"id": "card", "width": 210, "height": 290, "quantity": 1,
                   "rotatable": False}],
    })
    assert response.status_code == 422
    assert "放不进原纸" in response.json()["detail"]


def test_kerf_is_counted_between_items():
    # 100 + 3(切缝) + 100 = 203：两件刚好一张
    data = make_plan({
        "stock": {"width": 203, "height": 100},
        "kerf": 3,
        "items": [{"id": "sq", "width": 100, "height": 100, "quantity": 2}],
    })
    assert data["summary"]["sheets_used"] == 1
    validate_sheet(data["sheets"][0], 203, 100, 3, {"sq": 2})

    # 切缝改为 4：100 + 4 + 100 = 204 > 203，必须两张
    data = make_plan({
        "stock": {"width": 203, "height": 100},
        "kerf": 4,
        "items": [{"id": "sq", "width": 100, "height": 100, "quantity": 2}],
    })
    assert data["summary"]["sheets_used"] == 2
    for sheet in data["sheets"]:
        validate_sheet(sheet, 203, 100, 4, {"sq": 1})


def test_edge_trim_sliver_is_handled():
    # 第二件放入后剩余 3mm（恰好一个切缝宽）的细条，无法再下刀，
    # 应作为边缘修整余量处理，且仍排在同一张纸上
    data = make_plan({
        "stock": {"width": 206, "height": 100},
        "kerf": 3,
        "items": [{"id": "sq", "width": 100, "height": 100, "quantity": 2}],
    })
    assert data["summary"]["sheets_used"] == 1
    validate_sheet(data["sheets"][0], 206, 100, 3, {"sq": 2})


def test_multiple_sheets():
    data = make_plan({
        "stock": {"width": 450, "height": 320},
        "kerf": 3,
        "items": [{"id": "A4", "width": 210, "height": 297, "quantity": 10}],
    })
    # 每张纸横向 2 件（210+3+210=423 ≤ 450），共需 5 张
    assert data["summary"]["sheets_used"] == 5
    assert data["summary"]["total_pieces"] == 10
    for sheet in data["sheets"]:
        validate_sheet(sheet, 450, 320, 3, {"A4": 2})


def test_mixed_items_and_auto_ids():
    data = make_plan({
        "stock": {"width": 900, "height": 640},
        "kerf": 3,
        "items": [
            {"id": "poster", "width": 420, "height": 297, "quantity": 3},
            {"width": 210, "height": 148, "quantity": 6},
            {"id": "card", "width": 90, "height": 54, "quantity": 25},
        ],
    })
    expected = {"poster": 3, "item_2": 6, "card": 25}
    assert data["summary"]["total_pieces"] == 34
    total = 0.0
    for sheet in data["sheets"]:
        validate_sheet(sheet, 900, 640, 3, {})
        total += sum(p["width"] * p["height"] for p in sheet["placements"])
    counts: dict[str, int] = {}
    for sheet in data["sheets"]:
        for p in sheet["placements"]:
            counts[p["item_id"]] = counts.get(p["item_id"], 0) + 1
    assert counts == expected
    assert data["summary"]["overall_utilization"] == pytest.approx(
        total / (900 * 640 * data["summary"]["sheets_used"]), abs=1e-3)


def test_exact_fit_full_sheet():
    data = make_plan({
        "stock": {"width": 297, "height": 210},
        "kerf": 3,
        "items": [{"id": "A4", "width": 210, "height": 297, "quantity": 1}],
    })
    sheet = data["sheets"][0]
    assert sheet["utilization"] == pytest.approx(1.0)
    assert sheet["tree"]["type"] == "product"
    validate_sheet(sheet, 297, 210, 3, {"A4": 1})


def test_item_too_large_both_orientations():
    response = client.post("/plans", json={
        "stock": {"width": 900, "height": 640},
        "kerf": 3,
        "items": [{"id": "big", "width": 950, "height": 700, "quantity": 1}],
    })
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "big" in detail and "放不进原纸" in detail


def test_item_too_large_one_dimension():
    # 700 > 640 且 700 < 900，但另一边 650 旋转后仍超出 -> 报错
    response = client.post("/plans", json={
        "stock": {"width": 900, "height": 640},
        "kerf": 3,
        "items": [{"id": "odd", "width": 700, "height": 650, "quantity": 1}],
    })
    assert response.status_code == 422
    assert "放不进原纸" in response.json()["detail"]


@pytest.mark.parametrize("payload", [
    # 原纸尺寸非法
    {"stock": {"width": 0, "height": 640}, "kerf": 3,
     "items": [{"width": 100, "height": 100, "quantity": 1}]},
    {"stock": {"width": -900, "height": 640}, "kerf": 3,
     "items": [{"width": 100, "height": 100, "quantity": 1}]},
    # 切缝非法
    {"stock": {"width": 900, "height": 640}, "kerf": -1,
     "items": [{"width": 100, "height": 100, "quantity": 1}]},
    {"stock": {"width": 900, "height": 640}, "kerf": 640,
     "items": [{"width": 100, "height": 100, "quantity": 1}]},
    # 成品尺寸 / 数量非法
    {"stock": {"width": 900, "height": 640}, "kerf": 3,
     "items": [{"width": 0, "height": 100, "quantity": 1}]},
    {"stock": {"width": 900, "height": 640}, "kerf": 3,
     "items": [{"width": 100, "height": 100, "quantity": 0}]},
    {"stock": {"width": 900, "height": 640}, "kerf": 3,
     "items": [{"width": 100, "height": 100, "quantity": -2}]},
    {"stock": {"width": 900, "height": 640}, "kerf": 3,
     "items": [{"width": 100, "height": 100, "quantity": 1.5}]},
    # 成品列表为空 / 缺失字段
    {"stock": {"width": 900, "height": 640}, "kerf": 3, "items": []},
    {"stock": {"width": 900, "height": 640}, "kerf": 3},
    {"kerf": 3, "items": [{"width": 100, "height": 100, "quantity": 1}]},
    # 成品 id 重复
    {"stock": {"width": 900, "height": 640}, "kerf": 3,
     "items": [{"id": "a", "width": 100, "height": 100, "quantity": 1},
               {"id": "a", "width": 50, "height": 50, "quantity": 1}]},
])
def test_invalid_input_returns_422(payload):
    response = client.post("/plans", json=payload)
    assert response.status_code == 422


def test_zero_kerf_is_allowed():
    data = make_plan({
        "stock": {"width": 200, "height": 100},
        "kerf": 0,
        "items": [{"id": "sq", "width": 100, "height": 100, "quantity": 2}],
    })
    assert data["summary"]["sheets_used"] == 1
    validate_sheet(data["sheets"][0], 200, 100, 0, {"sq": 2})


def test_decimal_dimensions():
    data = make_plan({
        "stock": {"width": 450.5, "height": 320.25},
        "kerf": 2.5,
        "items": [{"id": "label", "width": 99.5, "height": 60.75, "quantity": 8}],
    })
    for sheet in data["sheets"]:
        validate_sheet(sheet, 450.5, 320.25, 2.5, {})


def test_large_batch_stays_consistent():
    # 较大批量：全部纸张都必须通过完整校验
    data = make_plan({
        "stock": {"width": 1020, "height": 720},
        "kerf": 5,
        "items": [
            {"id": "a", "width": 150, "height": 100, "quantity": 40},
            {"id": "b", "width": 210, "height": 99, "quantity": 25},
            {"id": "c", "width": 80, "height": 80, "quantity": 60},
        ],
    })
    assert data["summary"]["total_pieces"] == 125
    counts: dict[str, int] = {}
    for sheet in data["sheets"]:
        validate_sheet(sheet, 1020, 720, 5, {})
        for p in sheet["placements"]:
            counts[p["item_id"]] = counts.get(p["item_id"], 0) + 1
    assert counts == {"a": 40, "b": 25, "c": 60}


def test_randomized_fuzz_fixed_seed():
    # 固定种子的随机模糊测试：所有纸张都必须通过全部不变量校验
    import random

    rng = random.Random(20260919)
    for _ in range(30):
        stock_w = round(rng.uniform(200, 1500), 1)
        stock_h = round(rng.uniform(200, 1500), 1)
        kerf = rng.choice([0, 1, 2.5, 3, 5])
        items = [
            {
                "id": f"it{i}",
                "width": round(rng.uniform(10, stock_w * 0.9), 1),
                "height": round(rng.uniform(10, stock_h * 0.9), 1),
                "quantity": rng.randint(1, 20),
                "rotatable": rng.random() > 0.2,
            }
            for i in range(rng.randint(1, 4))
        ]
        data = make_plan({"stock": {"width": stock_w, "height": stock_h},
                          "kerf": kerf, "items": items})
        total = 0
        for sheet in data["sheets"]:
            validate_sheet(sheet, stock_w, stock_h, kerf, {})
            total += len(sheet["placements"])
        assert total == sum(it["quantity"] for it in items)
