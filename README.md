# 裁纸排版 API（SheetCut Planner）

面向小型印刷店的铡刀式（guillotine）裁纸排版服务。给定原纸尺寸、切缝宽度和成品清单（尺寸 + 数量），自动生成多张原纸的排版方案：每张纸给出成品坐标、旋转标记、面积利用率，以及与坐标完全一致的二叉切割树（每一刀都贯穿当前子矩形，可直接指导裁切顺序）。

## 一键启动

```bash
docker compose up --build
```

唯一需要的一条命令：依赖安装已包含在镜像构建中，无需本机安装任何 Python 环境。

- **API 地址**：http://localhost:8000
- **交互式文档（Swagger UI）**：http://localhost:8000/docs
- **健康检查**：http://localhost:8000/health

### 配置（可选）

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8000` | 宿主机映射端口，如 `PORT=9000 docker compose up --build` |
| `UVICORN_WORKERS` | `1` | 服务进程数，小店日常使用无需调整 |

不使用 Compose 时也可以直接构建运行：`docker build -t sheetcut . && docker run -p 8000:8000 sheetcut`

## API 使用

### `POST /plans`

请求体：

```json
{
  "stock": { "width": 900, "height": 640 },
  "kerf": 3,
  "items": [
    { "id": "A4",   "width": 210, "height": 297, "quantity": 4 },
    { "id": "card", "width": 90,  "height": 54,  "quantity": 20, "rotatable": true }
  ]
}
```

| 字段 | 说明 |
| --- | --- |
| `stock.width` / `stock.height` | 原纸宽 / 高，正数（单位任意，通常为毫米，全请求保持一致） |
| `kerf` | 切缝宽度：每一刀消耗的材料宽度，`>= 0` 且小于原纸宽、高 |
| `items[].id` | 成品标识，可选（缺省自动命名 `item_1`、`item_2`…），不可重复 |
| `items[].width` / `items[].height` | 成品宽 / 高，正数 |
| `items[].quantity` | 需求数量，整数 `>= 1` |
| `items[].rotatable` | 是否允许旋转 90° 摆放，默认 `true` |

响应（节选）：

```json
{
  "stock": { "width": 900, "height": 640 },
  "kerf": 3,
  "sheets": [
    {
      "sheet_index": 0,
      "width": 900,
      "height": 640,
      "utilization": 0.4838,
      "placements": [
        { "item_id": "A4", "x": 0, "y": 0, "width": 210, "height": 297, "rotated": false }
      ],
      "tree": { "type": "cut", "direction": "vertical", "position": 423, "...": "..." }
    }
  ],
  "summary": { "sheets_used": 1, "total_pieces": 24, "overall_utilization": 0.4838 }
}
```

### 坐标与切割树约定

- **坐标系**：原纸左下角为 `(0, 0)`，x 向右，y 向上；`placements` 中即每件成品的左下角坐标与宽高。
- **切缝计入分割**：每一刀消耗 `kerf` 宽的材料。任意两件成品之间在 x 或 y 方向上至少间隔一个切缝宽，成品不重叠、不越界。
- **二叉切割树 `tree`**：与 `placements` 坐标严格一致，按先父后子的顺序即裁切顺序。
  - `cut` 节点：一刀**贯穿当前子矩形**。`direction: "vertical"` 表示在 `x = position` 处下竖刀（分左右），`"horizontal"` 表示在 `y = position` 处下横刀（分上下）；刀缝为 `[position, position + kerf]`，`children` 依次为刀缝两侧（左/右 或 下/上）的子区域。
  - `product` 叶子：一件成品，`x/y/width/height` 与 `placements` 完全一致，`rotated` 为旋转标记。
  - `waste` 叶子：无法再利用的废料区域。
  - **边缘修整余量**：当剩余细条不足一刀宽（`0 < 余量 <= kerf`）时无法下刀，该细条并入成品所在格子，成品叶子上会多一个 `cell` 字段标注其格子范围（成品本身坐标尺寸不变，仍与 `placements` 一致）。

### 错误返回

- 输入尺寸 / 数量不合法（非正数、空清单、数量非整数、id 重复等）：`422`，`detail` 指明不合法的字段。
- 单件成品在**旋转和未旋转两种朝向下都放不进原纸**：`422`，`detail` 明确指出是哪种成品及其尺寸，例如：
  `成品 'big'（950.0×700.0）在旋转和未旋转两种朝向下都放不进原纸（900.0×640.0），请调整成品尺寸或更换更大的原纸。`

### 示例

```bash
curl -X POST http://localhost:8000/plans \
  -H 'Content-Type: application/json' \
  -d '{"stock":{"width":900,"height":640},"kerf":3,"items":[{"id":"A4","width":210,"height":297,"quantity":4}]}'
```

## 本地开发

```bash
pip install -r requirements-dev.txt
pytest                  # 运行测试（含切割树一致性、间距、边界等校验）
uvicorn app.main:app --reload --port 8000
```

## 项目结构

```
app/
  main.py      # FastAPI 入口与 /plans 接口
  schemas.py   # 请求/响应模型与输入校验
  packer.py    # 铡刀式排版引擎（BSSF 选位 + 二叉切割树）
tests/
  test_api.py  # 接口、约束与随机模糊测试
Dockerfile
docker-compose.yml
```
