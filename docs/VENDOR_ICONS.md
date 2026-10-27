# 厂商图标替换说明（模型广场）

> 记录「模型广场」中厂商标识的替换范围、来源与一致性规范，便于后续维护与合规核对。

## 1. 替换范围

**已替换：模型广场的 10 个厂商品牌标识**（原为字母/缩写色块：`豆`、`千`、`DS`、`智`、`K`、`混`、`M`、`Mi`、`GPT`、`C`）。

同一份 Logo 同时用于三个展示位，尺寸按位置缩放：

| 位置 | 容器 | Logo 显示尺寸 |
| --- | --- | --- |
| 厂商卡片网格（`/models`） | 44×44 圆角方块 | 28×28 |
| 厂商详情页头部 | 56×56 | 36×36 |
| 接入配置页头部 | 36×36 | 22×22 |

**未替换（有意保留）**：项目内的通用功能图标（侧栏导航、更多操作、反馈点赞/点踩、引用上标、折叠箭头等）。这些是 UI 图标，不是软件品牌标识，混入品牌 Logo 反而会破坏一致性。

## 2. 图标来源

统一取自 **Lobe Icons** 的静态 PNG 包：`@lobehub/icons-static-png@1.97.0`（MIT License）。
- 包地址：https://unpkg.com/browse/@lobehub/icons-static-png@1.97.0/
- 图标为 640×640 透明 PNG，提供 `light` / `dark` 两套主题版本。
- Lobe Icons 汇总各品牌官方视觉资产；品牌名称与商标归各自所有者。

| 厂商 | 本地文件 | 上游 slug | 备注 |
| --- | --- | --- | --- |
| 豆包 | `doubao.png` / `doubao-dark.png` | `doubao-color` | 明暗同图 |
| 千问 | `qwen.png` / `qwen-dark.png` | `qwen-color` | |
| DeepSeek | `deepseek.png` / `deepseek-dark.png` | `deepseek-color` | |
| 智谱 | `zhipu.png` / `zhipu-dark.png` | `zhipu-color` | |
| Kimi | `kimi.png` / `kimi-dark.png` | `kimi`（单色） | 彩色版的 K 为白色，浅色背景不可见，故用单色版 |
| 腾讯混元 | `hunyuan.png` / `hunyuan-dark.png` | `hunyuan-color` | |
| MiniMax | `minimax.png` / `minimax-dark.png` | `minimax-color` | |
| 小米 MiMo | `mimo.png` / `mimo-dark.png` | — | Lobe 仅提供 MiMo **字标**，小尺寸不可读；改用**小米官方 mi 标识**：https://www.mi.com/favicon.ico（32×32，本地放大到 128×128） |
| GPT | `openai.png` / `openai-dark.png` | `openai` | 单色结，明暗两版 |
| Claude | `claude.png` / `claude-dark.png` | `claude-color` | 明暗同图 |

> 商标声明：以上标识仅用于**识别对应厂商与产品**，不表示与任何厂商存在合作或背书关系。商标归各自所有者。

## 3. 显示一致性规范

- **容器统一**：所有 Logo 都放在固定尺寸的圆角方块中（44/56/36px），背景使用 `--color-surface-secondary`，加 1px `--color-border-light` 边框。
- **光学尺寸统一**：按位置分别限制为 28/36/22px，`object-fit: contain`，不拉伸、不裁切。
- **主题适配**：浅色主题使用 `light` 版本，深色主题（`html.dark`）切换为 `dark` 版本；容器随主题使用 surface 变量，避免深色下黑标消失、浅色下白标消失。
- **对齐统一**：容器在卡片/标题行内 `flex-shrink: 0`，不参与文本截断。
- **降级**：若某厂商未配置 `logo`（例如「自定义端点」），回退为品牌色字母块。

## 4. 维护方式

1. 替换 `frontend/src/assets/vendors/` 下同名文件即可，无需改代码。
2. 新增厂商时，在 `frontend/src/data/modelVendors.js` 的 `LOGOS` 映射中登记 `{ light, dark }`，并在目录对象中补 `id`。
3. 回归：
   ```bash
   cd frontend
   npm run test:unit   # 校验每家厂商都有明/暗两套 logo
   npm run build
   ```
4. 视觉核对：桌面浅色 / 桌面深色 / 390px 移动端各看一次（重点看 Kimi、OpenAI 在深色下是否可见）。
